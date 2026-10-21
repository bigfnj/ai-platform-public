"""Broker orchestration: the serialized GPU gate + one-heavy-model policy.

This is the keystone. Every heavy GPU operation (chat, load) passes through a
single async gate so two apps can never both trigger a 15 GB load at once. Before
a heavy model is served or loaded, any *other* heavy model is evicted, leaving at
most one heavy model resident. Embedding models are light and may stay loaded
alongside, so embeds do not take the heavy gate.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import time
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, NamedTuple

import anyio

from app import cli_backend, gpu, media, voice
from app.cli_backend import (
    SENDS_IMAGES,
    STREAMS_TEXT,
    ClaudeCliClient,
    CliUnavailable,
    answer_frames,
    resolve_cli_exe,
    settle,
)
from app.config import BrokerSettings, CliRoles
from app.ollama import OllamaClient, rank_key, resolve_ollama_model
from app.registry import classify
from app.upstream import UpstreamClient, UpstreamError  # noqa: F401  (UpstreamError re-exported)
from platform_core.model_catalog import category_of

# Non-Ollama image backends the media worker can load. A media @role (e.g. @recipe-icon)
# resolves to one of these via roles.json, expanded WITHOUT Ollama glob resolution.
MEDIA_IMAGE_BACKENDS = ("sdxl-turbo", "flux-schnell")


def image_backend(name: str) -> str | None:
    """The MEDIA_IMAGE_BACKENDS entry `name` spells in any case (`FLUX-Schnell` is
    flux-schnell, as the worker, which lowercases, always drew it), or None for a name that is
    not one. An image call hands the worker, the job queue and its answer this spelling.

    The ONE test of "is this an image backend", for a name a caller sent and for a role's
    pattern alike: /v1/image and the OpenAI images route, the roles view, resolve_ref and the
    role audit all ask it, so a role pinned `FLUX-Schnell` by hand renders and reads installed,
    and never one without the other."""
    folded = name.lower() if isinstance(name, str) else ""
    return next((b for b in MEDIA_IMAGE_BACKENDS if b == folded), None)
# Ceiling on each remote read that roles_view makes for a delegated role. A read view has to
# answer well inside its callers' timeouts (30 s for every rail, 20 s for rail_smoke); see the
# probe in roles_view for why a slow remote must not be allowed to blank every chip.
_VIEW_PROBE_TIMEOUT = 8.0
# Caps on the per-requester CLI outcome map (`last_by_requester`). Requesters are owners (every
# other caller is refused before the CLI) and roles come from the eligible allowlist, both small
# configured sets. Both are hot-read, though, and can change for as long as the broker runs, so
# the map is bounded anyway: the least recently updated requester, or role, goes first.
_CLI_HISTORY_REQUESTERS = 16
_CLI_HISTORY_ROLES = 32
# How often a streamed CLI answer asks whether its caller is still there while it waits for the
# next frame (Broker._watch). Seconds; a module attribute so a test can shorten it.
_GONE_TICK_S = 2.0

# Per-rail @role -> friendly rail name, so a queued job can show which rail it's for. Rails
# call the broker with their own per-rail role, so the role identifies the rail (no per-rail
# wiring needed). Generic classes (@chat, @vision, …) and manual loads map to no rail.
ROLE_RAIL = {
    "edu": "EDU-Suite", "iep": "IEP", "job-aid": "Job Aid",
    "finance-chat": "Finance", "finance-fraud": "Finance",
    "recipe": "Recipe Book", "recipe-vision": "Recipe Book", "recipe-icon": "Recipe Book",
    "bouquet-vision": "Bouquet Builder", "bouquet-writer": "Bouquet Builder",
    "terminal-fun": "Terminal Fun",
    "ai-playground": "AI Playground",
    # The rest of DEFAULT_ROLES' per-rail roles. Missing, their jobs showed `source: null` in
    # the queue (the RAG warms read as "Platform"), so nobody could tell which rail was waiting.
    # The generic classes (chat, chat-fast, embed, ...) belong to no rail and stay out.
    # Labels are each rail's manifest `label`; tests/test_role_map.py holds this map to every
    # role a rails/*/rail.json declares, so a new rail's role cannot be left out again.
    "co-worker-synthesis": "Co-Worker",
    "smb-partner-rag": "SMB Partner", "gemini-cx-rag": "Gemini CX",
    "servicenow-rag": "ServiceNow", "openmaic": "OpenMAIC",
    "ai-voice-normalize": "Voice Studio",
    "watch-builder-designer": "Watch Builder",
}


def _strip_latest(name: str) -> str:
    """Drop an explicit ``:latest`` TAG -- only that tag, never any tag containing the word.

    Ollama reports an untagged pull as ``name:latest``, so ``bge-m3`` and ``bge-m3:latest`` are
    one model. Same narrow rule as the rails' generated ``modelstate._same()``: widening it made
    ``qwen3:8b-latest`` satisfy a role pinned to ``qwen3:4b``.
    """
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _is_disabled(name: str, disabled: set[str] | frozenset[str]) -> bool:
    """Whether the admin disabled `name`, with an explicit ``:latest`` dropped on BOTH sides.

    The set holds what the Models tab wrote, list_models' names, so an untagged pull is in it as
    ``qwen3:latest``, while a role pinned ``qwen3`` and an OpenAI caller asking for ``qwen3`` hand
    the checks the bare name. A plain ``in`` served the disabled model to both, with no fallback.
    A hand-written ``qwen3`` in disabled.json is the same model the other way round. Every check
    of the set goes through here, so the listing, the resolver and the audit cannot disagree.
    """
    key = _strip_latest(name)
    return any(_strip_latest(d) == key for d in disabled)


class _RemoteProbe(NamedTuple):
    """One remote box as a roles sweep read it.

    `raw_models` is the box's /v1/models rows as it sent them (dict rows only), for the
    Rails tab's picker; `inventory` is the same list reshaped for rank_key, for resolving a
    delegated glob. Both are None when the box could not be read.
    """
    healthy: bool
    raw_models: list[dict[str, Any]] | None
    inventory: list[dict[str, Any]] | None
    resident: frozenset[str]
    roles: dict[str, dict[str, Any]]


#: A box that is down, refused the probe, or was never probed.
_UNREAD = _RemoteProbe(False, None, None, frozenset(), {})


class DelegatedRefError(RuntimeError):
    """A delegated reference reached the LOCAL resolver.

    This should be unreachable, and that is the point. Delegation branches in `route()`, above
    `resolve_ref`, because a delegated glob must travel to the upstream UNRESOLVED — only that
    box knows its own inventory. If a dispatch path is ever added that calls `resolve_ref`
    directly, the ref arrives here still carrying its `upstream::` prefix, and resolving it
    locally would match against the wrong inventory or, under `substitute=True`, quietly serve
    a local model for a role the operator deliberately sent off-site.

    So this raises instead. A loud failure on a path somebody forgot to convert is worth far
    more than a silent wrong-box answer that reports success.
    """

    def __init__(self, ref: str, upstream: str) -> None:
        super().__init__(
            f"{ref!r} is delegated to upstream {upstream!r} and cannot be resolved locally; "
            f"this path must call Broker.route() rather than resolve_ref()")
        self.ref = ref
        self.upstream = upstream


class ModelDisabledError(RuntimeError):
    """An external caller named a model the admin has disabled.

    Its own type because the OpenAI surface has to answer 403 with a specific error
    code here; folding it into the generic 502 would tell a client 'the backend
    broke' about a deliberate, recoverable policy decision it could surface to a
    user as 'pick another model'.
    """

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} is disabled by the administrator")
        self.model = model


class ImageEmbedderNotAllowed(ValueError):
    """A caller named an image embedder outside BROKER_IMAGE_EMBED_MODELS (a 400)."""


class NotAnImageModel(ValueError):
    """An image call named a model that is not one of MEDIA_IMAGE_BACKENDS (a 400).

    The worker draws SDXL for every name that is not FLUX, so without this `@chat` (or
    "dall-e-3") rendered an SDXL image the job queue labelled with the chat model."""


class UnknownRoleError(RuntimeError):
    """A caller named an '@role' that is in no role map.

    Its own type because of what shipped instead: the resolver returned the literal string, so
    `@nosuchrole` reached Ollama as a model NAMED "nosuchrole", and the caller read a 502
    "backend unreachable" wrapping a 404. `set_role()` has always refused to CREATE an unknown
    role; this is the read half finally agreeing with the write half.
    """

    def __init__(self, role: str, known: list[str]) -> None:
        super().__init__(f"no such role '@{role}'. Known roles: {', '.join(known)}")
        self.role = role
        self.known = known


class NoSubstituteError(RuntimeError):
    """A role's model cannot run and nothing installed shares its capability.

    A 4xx the admin can fix (pull a model, or re-enable one), not a 502 saying the backend
    broke. Refusing is the only correct answer here: substituting across capabilities is how
    @vision comes back with a confident description of a photo no model ever saw.
    """

    def __init__(self, role: str, wanted: str, category: str, reason: str) -> None:
        super().__init__(
            f"@{role} resolves to '{wanted}', which is {reason}, and nothing installed can "
            f"substitute for it ({category}). Pull a {category} model, or re-enable one.")
        self.role = role
        self.wanted = wanted
        self.category = category
        self.reason = reason


class ClientGone(RuntimeError):
    """The caller hung up before a CLI call started, or before the local fallback after one
    failed: nobody is left to read the answer. Raised instead of starting either, so a rail
    that gave up does not spend the owner's subscription, or load a model on the shared card,
    for a reply that goes nowhere. NOT a fallback reason: nothing falls back. /v1/chat answers
    it with a 499 that nobody receives."""


@dataclass
class _CliAttempt:
    """One call's way through the CLI path, from `Broker._cli_begin` to its outcome.

    `who` is the requester stripped exactly as `cli_decision` matched it and NOT case-folded,
    because the owner check is exact: one value for the in-flight entry AND the outcome, so the
    two can never name the same call differently. `token` keys the in-flight entry, taken
    before anything can raise so every exit can name it. `cli_model` is the `--model` the call
    passes ("" for none) once `_cli_slot` chose it, None until then; `client` is the client
    built for it, carrying what is left of the budget. `switches` is the switch-file read the
    latest decision judged the call by (`cli_effective`), handed to the re-decision and the
    model choice, so a call that never waited reads the switch file and roles.json once.
    `token_read` is likewise the token-file read (`cli_token()`) that decision checked, handed
    to the re-decision and to the client, so such a call reads the token file once too. Kept
    out of the repr: it holds the owner's credential."""

    model: str
    who: str
    rail: str | None
    token: int
    cli_model: str | None = None
    client: ClaudeCliClient | None = None
    switches: CliRoles | None = None
    token_read: tuple[str, str] | None = field(default=None, repr=False)


class Fallback(NamedTuple):
    """What actually ran, when it is not what the role asked for."""

    model: str        # what will actually run
    requested: str    # what the caller asked for, e.g. "@vision"
    original: str     # what the role resolved to before substitution
    reason: str       # "disabled" | "not installed"
    category: str

    def as_dict(self) -> dict[str, str]:
        return {"model": self.model, "requested": self.requested, "original": self.original,
                "reason": self.reason, "category": self.category}

    def headers(self) -> dict[str, str]:
        """Response headers, for BOTH dialects.

        Header-borne rather than body-borne because the two surfaces disagree about body
        shape and must not be made to agree: the OpenAI body belongs to somebody else's
        spec. OpenAI itself ships metadata this way (x-ratelimit-*), so it is idiomatic there
        rather than invented. Sanitised, because a CR/LF in a header value makes Starlette
        raise and would turn a degraded call into a 500.
        """
        def clean(v: str) -> str:
            return str(v).replace("\r", " ").replace("\n", " ")[:200]
        return {
            "X-Model-Fallback": clean(f"{self.requested} -> {self.model}"),
            "X-Model-Fallback-Original": clean(self.original),
            "X-Model-Fallback-Reason": clean(self.reason),
            "X-Model-Fallback-Category": clean(self.category),
        }


class GpuGate:
    """A single-slot async gate that also reports how deep the queue is, and tracks each
    queued/active job (model + rail + state) so the UI can show a live JOB QUEUE."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.waiting = 0
        self.active = 0
        self._seq = 0
        self._jobs: dict[int, dict[str, Any]] = {}  # seq -> job, FIFO by seq

    @asynccontextmanager
    async def hold(self, model: str | None = None, source: str | None = None,
                   fallback: dict[str, str] | None = None) -> AsyncIterator[None]:
        self._seq += 1
        seq = self._seq
        # Keep the running task so an admin can cancel this job (waiting -> drop before it runs;
        # active -> abort the in-flight call). Excluded from the JSON `jobs()` view.
        job = {"seq": seq, "model": model, "source": source, "state": "waiting",
               "since": time.time(), "task": asyncio.current_task(),
               # None for the overwhelming majority of jobs; a dict when this job is
               # running a SUBSTITUTE, so the queue widget can mark it rather than showing
               # a model nobody asked for with no explanation.
               "fallback": fallback}
        self._jobs[seq] = job
        self.waiting += 1
        entered = False
        try:
            async with self._lock:
                entered = True
                self.waiting -= 1
                self.active += 1
                job["state"] = "active"
                job["since"] = time.time()
                try:
                    yield
                finally:
                    self.active -= 1
                    self._jobs.pop(seq, None)
        finally:
            # Only runs if we were cancelled while still waiting on the lock.
            if not entered:
                self.waiting -= 1
                self._jobs.pop(seq, None)

    def depth(self) -> dict[str, int]:
        return {"active": self.active, "waiting": self.waiting}

    def jobs(self) -> list[dict[str, Any]]:
        """Active + waiting jobs, oldest first (the active one leads). Drops the task handle."""
        return [
            {k: v for k, v in j.items() if k != "task"}
            for j in sorted(self._jobs.values(), key=lambda j: j["seq"])
        ]

    def cancel(self, seq: int) -> bool:
        """Cancel a queued/active job by seq. Cancelling the task drops a waiting job before
        it runs, or aborts the in-flight call for the active one. Returns whether it was found."""
        job = self._jobs.get(seq)
        if job is None:
            return False
        task = job.get("task")
        if task is not None and not task.done():
            task.cancel()
        return True


# --- where Broker's methods live --------------------------------------------
# In the class below, except the Claude CLI backend's (its decision, its slot, the attempt's
# outcomes, its client, cli_state, and the hang-up checks its calls began with), which are
# written in broker_cli.py, beside this file. They were here until 2026-10-16, when this file
# came within 1,049 bytes of two thirds of publish.json's data_max_bytes.
#
# broker_cli.py is RUN in this module's namespace, not imported: _methods_from() execs it with
# this module's globals and makes every method of the `class Broker` it writes one of Broker's
# own, so Broker's MRO, the methods in its __dict__ and every attribute lookup are what they
# were. For the reason tools/rail_conformance.py runs its rule files: tests point this module's
# names at fixtures (_GONE_TICK_S, _CLI_HISTORY_REQUESTERS and _ROLES, _VIEW_PROBE_TIMEOUT,
# "app.broker.resolve_cli_exe"), and a method reads them when it is called. Imported, the file
# would hold its own binding of each, so a patch naming app.broker would pass while the method
# read the real value: a trap that fails in silence. Run here, every method reads these names.
#
# What that asks of a file of methods. It imports nothing (but `from __future__ import
# annotations`): a method reads this module's names, so a name it needs is imported or defined
# here. Its class binds no name the class below binds: the decorator refuses one, a TypeError
# at import, where a silent replacement would change what runs. And it runs once.
# services/broker/tests/test_broker_methods.py holds each.

#: The files Broker's methods were taken from besides this one, in the order they ran.
_METHOD_FILES: list[Path] = []


def _methods_from(name: str) -> Callable[[type], type]:
    """A class decorator: every method of the `class Broker` written in `name` (a file beside
    this one), run in this module's namespace, becomes one of the decorated class's own (see
    above). The container's dunders (__module__, __dict__, __doc__, ...) stay with it."""
    path = Path(__file__).with_name(name)
    ns: dict[str, Any] = {}
    exec(compile(path.read_bytes(), str(path), "exec"), globals(), ns)
    written = {k: v for k, v in vars(ns["Broker"]).items()
               if not (k.startswith("__") and k.endswith("__"))}

    def adopt(cls: type) -> type:
        taken = sorted(set(written) & set(vars(cls)))
        if taken:
            raise TypeError(f"{name} writes {', '.join(taken)}, which {cls.__name__} has "
                            f"already: one would silently replace the other")
        for k, v in written.items():
            setattr(cls, k, v)
        _METHOD_FILES.append(path)
        return cls
    return adopt


@_methods_from("broker_cli.py")
class Broker:
    def __init__(self, settings: BrokerSettings) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings.ollama_base_url, timeout=settings.ollama_timeout)
        self.gate = GpuGate()
        # The UNGATED media jobs (Kokoro read-aloud, Whisper dictation, SigLIP image
        # embedding) skip the GPU gate on purpose, but each still spawns a fresh 350-500 MB
        # interpreter, and nothing bounded how many ran at once: a burst of read-aloud clicks
        # across a classroom was that many interpreters in RAM together. This caps them
        # without making them wait behind a GPU job, which is the reason they are ungated.
        self._light_media = asyncio.Semaphore(max(1, int(settings.light_media_concurrency)))
        self._embed_hints = settings.embed_hints()
        self._media_active: dict[str, Any] | None = None  # {op, model} while a media job runs
        # Capability cache (name/digest -> ["completion","vision",...]) so the model list
        # doesn't re-probe /api/show every poll. Keyed by digest; stable until a re-pull.
        self._caps_cache: dict[str, list[str]] = {}
        # name -> digest from the last tags() read, so _is_capable can key the capability
        # cache the same way list_models does rather than probing by name every time.
        self._digests: dict[str, Any] = {}
        # Model-fallback telemetry. A job holding the gate carries its own marker, but a job
        # lives for seconds -- a badge that appears for four of them is not a report. These
        # persist on /v1/status so the widget can stay warning-toned with the queue empty.
        self._fallback_count = 0
        self._last_fallback: dict[str, str] | None = None
        # Claude CLI backend. The executable is resolved ONCE: it is a filesystem fact, not a
        # policy one, and re-probing PATH on every chat would put a disk walk in the hot path.
        # Everything policy-shaped (owners, roles, token) is read per call so it can change
        # without a restart.
        self._cli_exe = resolve_cli_exe(settings.cli_exe) if settings.cli_enabled else None
        self._cli_calls = 0
        self._cli_fallbacks = 0
        self._last_cli_fallback: dict[str, str] | None = None
        # Streamed CLI answers cut off AFTER their first token (see chat_stream): never a
        # fallback, because the rail had already shown the text, so counted on their own.
        self._cli_interruptions = 0
        self._last_cli_interruption: dict[str, Any] | None = None
        # reason -> count for calls the CLI was NOT given, so "ready, but nothing changed"
        # has an answer. See cli_state.
        self._cli_refusals: dict[str, int] = {}
        # In-flight CLI calls, keyed by a per-call token from `_cli_active_seq`. The CLI takes
        # no GPU gate, so it never appears in status()["jobs"]: without this an owner's
        # thirty-second call is invisible to the platform UI, which then reads "idle" while a
        # subscription is being spent. Keyed by token rather than by role because two calls
        # for the same role can overlap, and each must remove its OWN entry, not the other's.
        self._cli_active: dict[int, dict[str, Any]] = {}
        self._cli_active_seq = 0
        # At most `cli_concurrency` CLI calls run at once; see Broker.chat for how a call over
        # the limit waits. `_cli_waiting` counts the calls waiting for a slot, which appear in
        # neither `active` (nothing is running for them yet) nor the GPU queue.
        self._cli_limit = max(1, int(settings.cli_concurrency))
        self._cli_slots = asyncio.Semaphore(self._cli_limit)
        self._cli_waiting = 0
        # role -> {ok, at[, reason], requester[, model]} for the latest CLI attempt per role.
        # `fallbacks.last` is a single slot for the whole backend that only failures write: it
        # cannot say @iep has recovered since, and the next @edu failure overwrites it. Still
        # one slot per ROLE, so with two owners the latest may be the other one's; `requester`
        # is what lets the gateway show each owner only their own.
        self._cli_last_by_role: dict[str, dict[str, Any]] = {}
        # requester -> role -> that requester's latest outcome for the role. `last_by_role` keeps
        # the latest per role WHOEVER called, so owner B's call replaces owner A's there, and a
        # gateway showing each owner only their own then has nothing for A. This keeps A's.
        # Bounded by _CLI_HISTORY_REQUESTERS / _CLI_HISTORY_ROLES (see _record_cli_outcome).
        self._cli_last_by_requester: dict[str, dict[str, dict[str, Any]]] = {}

    async def aclose(self) -> None:
        await self.ollama.aclose()
        if self._vram is not None:
            await self._vram.aclose()
        # The per-box clients (see _upstream), replaced ones included. Only this loop's: a
        # client from another loop cannot be closed from here, and is simply dropped.
        if self._upstream_loop is not None:
            held = ([*self._upstream_clients.values(), *self._upstream_retired]
                    if self._upstream_loop is asyncio.get_running_loop() else [])
            self._upstream_loop = None
            for client in held:
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001 - one bad close must not keep the rest open
                    pass

    # --- classification helpers --------------------------------------------

    def _class(self, name: str) -> str:
        return classify(name, self._embed_hints)

    # --- read views ---------------------------------------------------------

    async def _capabilities(self, name: str, digest: str | None) -> list[str]:
        """Ollama's declared capabilities for a model (cached by digest). Empty on any
        error so a probe failure just means 'no special capability', never a raise."""
        key = digest or name
        cached = self._caps_cache.get(key)
        if cached is not None:
            return cached
        try:
            info = await self.ollama.show(name)
            caps = [str(c).lower() for c in (info.get("capabilities") or [])]
        except Exception:  # noqa: BLE001 - a read view must never raise
            # Do NOT cache a failed probe. It was harmless while this only decorated a UI badge:
            # one transient error meant a missing "vision" chip until restart. It stops being
            # harmless the moment resolution reads it -- a single Ollama hiccup during startup
            # would mark a vision model blind for the life of the process, and @vision would
            # refuse every substitute with no way back short of a restart.
            return []
        self._caps_cache[key] = caps
        return caps

    async def list_models(self, tags: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """This box's installed models, classified, with their capabilities. `tags` is an
        /api/tags answer the caller already holds (roles_inventory's sweep read one), so the
        list is not read a second time."""
        models = tags if tags is not None else await self.ollama.tags()
        disabled = self.settings.disabled()   # admin availability flag (hot-read)
        out: list[dict[str, Any]] = []
        classes = [self._class(m.get("name", "")) for m in models]

        async def _caps(m: dict[str, Any], cls: str) -> list[str]:
            # Only generative models can be vision-capable; skip the /api/show probe for
            # embedders (they never are, and probing them is wasted work).
            return await self._capabilities(m.get("name", ""), m.get("digest")) \
                if cls != "embed" else []

        # Together, not one after another: on a cold cache (a restart, a new pull) the serial
        # loop was one /api/show round trip per installed model before the list could answer.
        all_caps = await asyncio.gather(*(_caps(m, c) for m, c in zip(models, classes)))
        for m, cls, caps in zip(models, classes, all_caps):
            name = m.get("name", "")
            out.append({
                "name": m.get("name"),
                "size": m.get("size"),
                "class": cls,
                "family": (m.get("details") or {}).get("family"),
                "parameter_size": (m.get("details") or {}).get("parameter_size"),
                "capabilities": caps,
                "vision": "vision" in caps,
                "disabled": _is_disabled(name, disabled),
            })
        return out

    async def list_loaded(self) -> list[dict[str, Any]]:
        loaded = await self.ollama.ps()
        return [
            {
                "name": m.get("name"),
                "class": self._class(m.get("name", "")),
                "size_vram": m.get("size_vram"),
                "expires_at": m.get("expires_at"),
            }
            for m in loaded
        ]

    async def roles_view(self) -> list[dict[str, Any]]:
        """Every role (class + per-rail) with its stored pattern and the concrete installed
        model it currently resolves to. ``resolved`` is None when the pattern is a glob that
        matches nothing installed; ``installed`` is whether the resolved name is actually
        present. A DELEGATED role is judged the same way on the box that runs it: resolved
        against the remote's own inventory, with ``loaded`` from the remote's resident set.
        Powers the admin 'Rails' model picker and every rail's model chips."""
        rows, _probes = await self._roles_sweep()
        return rows

    async def roles_inventory(self) -> dict[str, Any]:
        """roles_view, plus every registered box's health and installed models, read by ONE
        probe per box: ``{"roles", "upstreams", "models"}``.

        The Rails tab needs all three. Fetched as /v1/roles, /v1/upstreams and
        /v1/models?upstream=X, every delegated remote was dialled for /healthz and /v1/models
        TWICE per load: once by this sweep and again by the other two reads. Here the sweep
        also reads the boxes no role runs on (health and inventory only, for the picker), and
        the upstream rows and inventories come out of what it read.

        ``upstreams`` matches upstreams_view: ``local`` first, ``{name, url, healthy}``, never
        a token. ``models`` is keyed by box, ``local`` included; a box that could not be read
        has ``[]``. Like every read view, this never raises for one box being dark.

        ``cli`` is `cli_summary()`: the Claude switches' state, here so the tab draws them from
        this same one read instead of a second probe (/v1/status would also spawn nvidia-smi).
        """
        registry = self.settings.upstreams()
        # ONE local /api/tags for the whole read: the sweep resolves every role against it and
        # the picker's local list is built from it. Each used to read its own, so every Rails
        # tab load asked Ollama for the list twice. Shared as a task rather than read first, so
        # the remote probes still run while it is out.
        tags_read = asyncio.ensure_future(self.ollama.tags())
        (rows, probes), local = await asyncio.gather(
            self._roles_sweep(also_probe=set(registry), tags_read=tags_read, registry=registry),
            self._local_models_for_view(tags_read))
        upstreams: list[dict[str, Any]] = [{"name": "local", "url": "", "healthy": True}]
        models: dict[str, list[dict[str, Any]]] = {"local": local}
        for name, spec in sorted(registry.items()):
            p = probes.get(name, _UNREAD)
            upstreams.append({"name": name, "url": spec["url"], "healthy": p.healthy})
            models[name] = p.raw_models or []
        return {"roles": rows, "upstreams": upstreams, "models": models,
                "cli": self.cli_summary()}

    async def _local_models_for_view(
        self, tags_read: Awaitable[list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            tags = await tags_read if tags_read is not None else None
            return await self.list_models(tags=tags)
        except Exception:  # noqa: BLE001 - a read view must never raise
            return []

    async def roles_view_local(self) -> list[dict[str, Any]]:
        """roles_view without the delegated rows, and without dialling any remote box.

        For callers that only ever ask about THIS card: the Models tab's "in use by" footprint
        and its Delete guard skip delegated rows anyway (their names are the remote's models),
        yet they used to wait on every remote's probe, up to the view bound per read, to get
        rows they then threw away.
        """
        rows, _probes = await self._roles_sweep(local_only=True)
        return rows

    async def _roles_sweep(
        self, also_probe: set[str] | frozenset[str] = frozenset(),
        local_only: bool = False,
        tags_read: Awaitable[list[dict[str, Any]]] | None = None,
        registry: dict[str, dict[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, _RemoteProbe]]:
        """roles_view's rows, and what each remote probe read.

        Probes every box a delegated role runs on, plus `also_probe` (boxes read for their
        health and inventory only). roles_view passes nothing, so the chip polls every rail
        makes do not start paying for boxes no role uses. `local_only` probes nothing and
        leaves the delegated rows out. `tags_read` is a local /api/tags read the caller shares
        with something else (roles_inventory's picker list); without it the sweep reads its own.
        `registry` is upstreams.json as the caller read it (roles_inventory); without it the
        sweep reads it, once, and only when a role or `also_probe` names a box.
        """
        roles = self.settings.roles()
        # ONE read of each settings file for the whole sweep, handed to every resolve below.
        # resolve_ref read roles.json and disabled.json again for each role, and the substitute
        # planner disabled.json once more: about 51 synchronous file reads in the event loop per
        # /v1/roles, which every rail's chips poll.
        disabled = self.settings.disabled()
        # upstreams.json the same way: it was read once per delegated role to split its value,
        # once per probe to build the box's client, and once more per delegated row.
        if registry is None:
            wanted = also_probe or any("::" in p for p in roles.values())
            registry = self.settings.upstreams() if wanted else {}
        # Probed once per upstream for the whole sweep, not once per delegated role: these are
        # network round trips and several roles commonly share a box. Cached as
        # (inventory, {resident model names}, {remote role -> row}); inventory is None when the
        # remote could not be read at all.
        #
        # BOUNDED, because this is a read view and its callers are impatient: every rail's
        # /v1/roles client gives up at 30 s and rail_smoke at 20 s. Each remote read gets
        # _VIEW_PROBE_TIMEOUT, the health probe that gates them included, the reads for one box
        # run together, and all boxes are probed at once, so a sweep costs at most two bounds
        # (healthz, then the reads), however many upstreams there are. The health probe used to
        # carry only httpx's 5 s, which is per socket operation, not per call, so a box that
        # trickled its /healthz could outlast it several times over. Read in series at the client's 30 s default, one slow remote took
        # /v1/roles past those timeouts, and then EVERY chip on every rail went red, local ones
        # included, because the rails read a failed view as "broker unreachable".
        remote: dict[str, _RemoteProbe] = {}
        delegated = [self.settings.delegate_ref(p, registry) for p in roles.values() if "::" in p]
        delegated_names = {name for name, _rest in delegated if name}
        wants_roles = {name for name, rest in delegated if name and rest.startswith("@")}

        async def _bounded(coro):
            return await asyncio.wait_for(coro, timeout=_VIEW_PROBE_TIMEOUT)

        async def _probe(name: str) -> _RemoteProbe:
            # healthy() reads /healthz, which is exempt from the token gate on every broker, so
            # it still answers when our token is wrong. That distinction is the point: "the box
            # is down" and "the box refuses us" need different fixes. It is also the cheap gate:
            # a box that is down is not dialled again for the reads.
            try:
                client = self._upstream(name, registry)
                if not await _bounded(client.healthy()):
                    return _UNREAD
            except Exception:  # noqa: BLE001 - a read view must never raise; a timeout is down
                return _UNREAD
            reads = {"models": _bounded(client.models())}
            # Residency matters only on a box a role RUNS on; an idle box is read for the
            # picker's inventory alone, and its /v1/status would be a round trip nothing uses.
            if name in delegated_names:
                reads["status"] = _bounded(client.status())
            if name in wants_roles:
                reads["roles"] = _bounded(client.roles())
            got = dict(zip(reads, await asyncio.gather(*reads.values(),
                                                       return_exceptions=True)))
            models_r, status_r = got["models"], got.get("status")
            roles_r = got.get("roles", [])
            # The remote's OWN inventory, so a delegated glob can be expanded to the concrete
            # model that box will serve. Reshaped for rank_key, which reads Ollama's
            # details.parameter_size: a broker's /v1/models flattens it to the top level, and
            # without this a size tie-break here could disagree with the remote's own. A
            # `details` that is not a dict (a remote that does not speak this broker's format)
            # is replaced rather than passed on: rank_key would raise on it, out of a read view.
            inventory: list[dict[str, Any]] | None = None
            if isinstance(models_r, list):
                inventory = []
                for m in models_r:
                    if isinstance(m, dict):
                        d = m.get("details")
                        inventory.append({"name": str(m.get("name") or ""),
                                          "details": d if isinstance(d, dict) and d
                                          else {"parameter_size": m.get("parameter_size")}})
            # The remote's resident set, so a delegated chip can reach LOADED. Unreadable
            # residency is simply "not loaded".
            resident: set[str] = set()
            if isinstance(status_r, dict):
                resident = {_strip_latest(str(m.get("name") or ""))
                            for m in (status_r.get("loaded") or []) if isinstance(m, dict)}
            rroles = {str(r.get("role")): r for r in roles_r
                      if isinstance(r, dict)} if isinstance(roles_r, list) else {}
            raw = ([m for m in models_r if isinstance(m, dict)]
                   if isinstance(models_r, list) else None)
            return _RemoteProbe(True, raw, inventory, frozenset(resident), rroles)

        async def _installed() -> list[dict[str, Any]]:
            try:
                return await (tags_read if tags_read is not None else self.ollama.tags())
            except Exception:  # noqa: BLE001 - a read view must never raise
                return []

        names = [] if local_only else sorted(delegated_names | set(also_probe))
        # This box's list is read WHILE the remotes are probed, not before them: the two are
        # independent and the view waits for both, so in series a sweep cost their sum.
        # It is read ONCE, and every role below resolves against it, substitutes included
        # (resolve_ref hands it on to _plan_substitute). The live box once refetched it per
        # falling-back role: about 15 reads at ~0.35 s, a 6-7 s /v1/roles.
        tags, *probes = await asyncio.gather(_installed(), *(_probe(n) for n in names))
        remote.update(zip(names, probes))
        installed = {m.get("name", "") for m in tags}

        out: list[dict[str, Any]] = []
        for role, pattern in sorted(roles.items()):
            # BEFORE the media branch: a delegated pattern is the upstream's business, and
            # asking whether it is installed HERE is the wrong question in both cases.
            if "::" in pattern:
                up_name, rest = self.settings.delegate_ref(pattern, registry)
                if up_name is not None:
                    if local_only:
                        continue
                    probe = remote.get(up_name, _UNREAD)
                    inventory, resident, rroles = probe.inventory, probe.resident, probe.roles
                    # Expanded against the list the REMOTE reports, by the same resolver and
                    # ranking the remote applies when route() hands it the glob whole -- so this
                    # names what that box will actually run. Expanding against THIS box's
                    # inventory would be wrong; leaving the glob unexpanded meant the `loaded`
                    # comparison below could never match and a delegated chip sat on COLD
                    # forever, however warm the remote was.
                    inv: list[dict[str, Any]] = inventory or []
                    concrete: str | None
                    if rest.startswith("@"):
                        # A remote ROLE (`box::@chat`): that box expands it from its own map,
                        # which no inventory here can reproduce. So ask it: its /v1/roles row
                        # names the model it will run and whether that model is installed.
                        # Without this the row could never read installed, and the slot sat red
                        # for a value set_role and the gateway both accept.
                        rrow = rroles.get(rest[1:], {})
                        concrete = rrow.get("resolved") or None
                        there = bool(rrow.get("installed")) and concrete is not None
                    else:
                        try:
                            # A concrete name passes straight through without reading the list.
                            concrete = resolve_ollama_model(rest, lambda: inv)
                        except Exception:  # noqa: BLE001 - ValueError: a glob with no remote
                            concrete = None  # match; anything else: a remote's odd inventory
                        there = concrete is not None and _strip_latest(concrete) in {
                            _strip_latest(m["name"]) for m in inv}
                    out.append({
                        "role": role, "pattern": pattern,
                        # None for a glob that matched nothing remote (or no list to match),
                        # exactly as the local branch reports a glob with no installed match.
                        # Never the raw glob: a consumer that expands it against its OWN
                        # inventory names a local near-miss for a role that runs elsewhere.
                        "resolved": concrete,
                        # `installed` means "this role can serve", judged on the box that runs
                        # it: the remote reported a model the pattern resolves to. Reachable is
                        # NOT enough -- a remote without a match fails every call, so green
                        # would be a lie, and red sends someone to pull it on the right box.
                        # An unreadable inventory is the same answer: a model we could not see
                        # is not one we can vouch for, and the usual cause, a token the remote
                        # refuses, fails every delegated call as well.
                        "installed": there,
                        # Residency on the REMOTE card, by the concrete name, `:latest`-tolerant
                        # like resolve_ref's installed check and the rails' _same().
                        "loaded": there and _strip_latest(concrete or "") in resident,
                        "class": None, "upstream": up_name,
                        "serving": concrete,
                        # Never a fallback: substituting a local model for a role the operator
                        # deliberately sent off-site would un-delegate it silently.
                        "fallback": None,
                    })
                    continue
            # A media backend is loaded by the media worker from the HF cache and NEVER appears
            # in Ollama's tags(), so the generic path reported every correctly configured image
            # role as `installed: false, class: heavy` -- permanently red in the Rails tab, and
            # enough to make "every role resolves" unusable as a health assertion without an
            # exception list bolted on outside. audit_roles and resolve_ref already know this;
            # the read view was the last place that did not.
            #
            # `installed` still has to mean "this role can serve", so it is whether the media
            # worker can START: its interpreter exists. Reported True unconditionally, a box
            # with no media venv showed the image chip green while every icon job failed (seen
            # on the 8 GB box, 2026-10-01). The weights are not checked; the first job fetches
            # them. The guard on an empty value matters: Path("") is ".", which always exists.
            # media_enabled=false makes every image job raise "media is disabled", so it is not
            # installed either, whatever the interpreter path says. In any case, as /v1/image
            # draws it: `pattern` stays as written, `resolved` and `serving` are the backend.
            backend = image_backend(pattern)
            if backend is not None:
                media_py = str(getattr(self.settings, "media_python", "") or "")
                out.append({"role": role, "pattern": pattern, "resolved": backend,
                            "installed": bool(getattr(self.settings, "media_enabled", True))
                            and bool(media_py) and Path(media_py).exists(),
                            "class": "image", "upstream": "local",
                            "serving": backend, "fallback": None})
                continue
            try:
                resolved: str | None = resolve_ollama_model(pattern, lambda: tags)
            except ValueError:
                resolved = None  # a glob with no installed match
            # What will actually SERVE this role. Planned, never announced: this is a read
            # view and the Rails tab polls it, so announcing here would write one
            # "we are degraded" line per bad role per poll.
            serving, fb = resolved, None
            try:
                serving, fb = await self.resolve_ref(f"@{role}", substitute=True,
                                                     announce=False, tags=tags,
                                                     roles=roles, disabled=disabled,
                                                     upstreams=registry)
            except Exception:  # noqa: BLE001 - a read view must never raise
                serving = resolved
            out.append({
                "role": role,
                "pattern": pattern,
                "resolved": resolved,
                "installed": bool(resolved) and resolved in installed,
                "class": self._class(resolved) if resolved else None,
                # `resolved` stays what the ADMIN CONFIGURED, so the Rails picker keeps
                # showing their own choice. `serving` is what would actually run. Two fields
                # rather than one, because collapsing them is how the Rails tab would start
                # reporting a substitute as the setting and an admin would "fix" a role that
                # was never wrong.
                # Always present, so a consumer can read it unconditionally rather than
                # treating its absence as "local" and its absence-on-an-old-broker as a bug.
                "upstream": "local",
                "serving": serving,
                "fallback": fb.as_dict() if fb is not None else None,
            })
        return out, remote

    async def upstreams_view(self) -> list[dict[str, Any]]:
        """The registry plus live reachability, for the admin panel. Tokens are NEVER
        included — the panel needs to know a box is configured and answering, not how to
        authenticate to it.

        Every box is probed at once, each under its own bound (_VIEW_PROBE_TIMEOUT, like every
        remote read in a view). Probed in turn, N dark boxes cost N health timeouts back to back
        on every Admin > Rails read. The rows keep their order: ``local``, then by name."""
        registry = sorted(self.settings.upstreams().items())

        async def _healthy(name: str) -> bool:
            try:
                return await asyncio.wait_for(self._upstream(name).healthy(),
                                              timeout=_VIEW_PROBE_TIMEOUT)
            except Exception:  # noqa: BLE001 - a read view must never raise; a timeout is down
                return False

        health = await asyncio.gather(*(_healthy(name) for name, _spec in registry))
        return [{"name": "local", "url": "", "healthy": True},
                *({"name": name, "url": spec["url"], "healthy": ok}
                  for (name, spec), ok in zip(registry, health))]

    async def models_view(self, upstream: str | None = None) -> list[dict[str, Any]]:
        """Installed models on THIS box, or on a named upstream. The admin's model picker
        needs the remote's inventory to offer a sensible choice for a delegated role — this
        box's list is exactly the wrong one to show there."""
        if not upstream or upstream == "local":
            return await self.list_models()
        if upstream not in self.settings.upstreams():
            raise ValueError(f"unknown upstream {upstream!r}")
        # Bounded like every other read of a remote in a view (_VIEW_PROBE_TIMEOUT), not on
        # the client's 30 s read timeout: a box that accepts the connection and never answers
        # would otherwise hold the admin's picker past the gateway's own patience.
        try:
            return await asyncio.wait_for(self._upstream(upstream).models(),
                                          timeout=_VIEW_PROBE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise UpstreamError(f"upstream {upstream!r} did not list its models within "
                                f"{_VIEW_PROBE_TIMEOUT:g} s") from exc

    #: The card's VRAM reads (gpu.VramCache): one nvidia-smi per VRAM_TTL_S however many polls
    #: ask, and one for polls that ask together. Class-level None so that a Broker built without
    #: __init__ (several tests make one) builds its own on first use.
    _vram: gpu.VramCache | None = None

    def _vram_cache(self) -> gpu.VramCache:
        if self._vram is None:
            self._vram = gpu.VramCache()
        return self._vram

    async def _ollama_status(self) -> tuple[Any, Any]:
        """``(version, loaded)`` for status(), each a value or the exception it raised. The
        resident list is asked for only once the version has answered: a version that failed
        is the answer for both, so a down Ollama is dialled once per poll, not twice (a refused
        connect to 127.0.0.1 takes about 2 s on Windows)."""
        try:
            version = await self.ollama.version()
        except Exception as exc:  # noqa: BLE001 - status must never raise
            return exc, exc
        try:
            return version, await self.list_loaded()
        except Exception as exc:  # noqa: BLE001
            return version, exc

    async def status(self) -> dict[str, Any]:
        # Ollama's reads and the card's together, not one after the other: they are
        # independent, and the poll used to wait for their sum, nvidia-smi (~180 ms) last of
        # all. Ollama's two are in series (_ollama_status). Each failure is its own: Ollama down
        # still reports the card, and a card read that raised reports none.
        (version, loaded), card = await asyncio.gather(
            self._ollama_status(), self._vram_cache().read(), return_exceptions=True)
        # status must never raise. Ollama is reachable when both of its reads answered; the
        # version is kept when only the resident list failed, as it was when read first.
        reachable = not isinstance(version, BaseException) and not isinstance(loaded, BaseException)
        if isinstance(version, BaseException):
            version = None
        if not reachable:
            loaded = []
        return {
            "ollama_reachable": reachable,
            "ollama_version": version,
            "loaded": loaded,
            "heavy_loaded": [m["name"] for m in loaded if m["class"] == "heavy"],
            "gpu": None if isinstance(card, BaseException) else card,
            "queue": self.gate.depth(),
            # Persistent, because a job lives for seconds: a marker that appears for four of
            # them is not a report. This lets the widget stay warning-toned with an empty queue.
            "fallbacks": {"count": self._fallback_count, "last": self._last_fallback},
            # Live job queue (active + waiting), oldest first: {seq, model, source(rail), state}.
            "jobs": self.gate.jobs(),
            # The off-box chat backend: whether it is ready, and if not, which of the five
            # ways it can be configured into doing nothing currently applies.
            "cli": self.cli_state(),
            # active is {op, model} while a media (image/tts) worker is running, else null.
            # Media models run in a short-lived subprocess and never show in Ollama's ps, so this
            # is the only way a client (e.g. BrokerTray) can tell the GPU is busy on media.
            # active is {op, model} while a media (image/tts) worker is running, else null.
            # `image_python` / `speech_python` are surfaced because the worker runs under a
            # DIFFERENT interpreter than the broker, configured by env (BROKER_MEDIA_PYTHON /
            # BROKER_TTS_PYTHON). When one is wrong the only symptom is an opaque 502 from a
            # subprocess, and there was previously no way to see which interpreter was used
            # without reading the service registry. `exists` catches the common case: a path
            # that was renamed, or an env var the service never picked up.
            "media": {
                "enabled": self.settings.media_enabled,
                "active": self._media_active,
                "image_python": self.settings.media_python,
                "image_python_exists": Path(self.settings.media_python).exists(),
                "speech_python": self.settings.tts_python or self.settings.media_python,
                "speech_python_exists": Path(
                    self.settings.tts_python or self.settings.media_python).exists(),
                "speech_isolated": bool(self.settings.tts_python),
                # The torch-free path the platform's own voice actually uses (tts_light,
                # transcribe). Resolved exactly as the jobs resolve it (_media_python_for), so
                # this names the interpreter a job will run. Without these, a box whose Kokoro
                # and Whisper work showed only `speech_python_exists: false` (that is XTTS), and
                # a box where they did NOT work gave no way to see why short of the registry.
                "kokoro_python": self._media_python_for("kokoro_tts"),
                "kokoro_python_exists": Path(self._media_python_for("kokoro_tts")).exists(),
                "kokoro_model_exists": bool(self.settings.kokoro_model_path) and Path(
                    self.settings.kokoro_model_path).exists(),
                "kokoro_voices_exist": bool(self.settings.kokoro_voices_path) and Path(
                    self.settings.kokoro_voices_path).exists(),
                "whisper_python": self._media_python_for("transcribe"),
                "whisper_python_exists": Path(self._media_python_for("transcribe")).exists(),
            },
        }

    # --- policy -------------------------------------------------------------

    async def _evict_other_heavy(self, keep: str | None = None) -> list[str]:
        """Unload every resident heavy model except ``keep`` (all of them when
        ``keep`` is None, e.g. before a media job that needs the whole card).

        Uses ``ollama stop`` — the empty-prompt keep_alive=0 path is a known
        no-op, so the old eviction here was likely not freeing VRAM at all.
        Returns the evicted model names.
        """
        evicted: list[str] = []
        # Compared with the implicit `:latest` dropped on both sides. A role pinned untagged
        # (`qwen3`) resolves to `qwen3` while Ollama lists it resident as `qwen3:latest`, and a
        # plain `!=` then unloaded the very model this call was about to serve: a ~15 GB reload
        # on every request.
        kept = _strip_latest(keep) if keep else None
        for m in await self.ollama.ps():
            name = m.get("name", "")
            if name and _strip_latest(name) != kept and self._class(name) == "heavy":
                await self.ollama.stop(name)
                evicted.append(name)
        return evicted

    #: The event loop `_upstream_clients` belongs to. Class-level so that a Broker built without
    #: __init__ (several tests make one) starts with no cache rather than an AttributeError.
    _upstream_loop: asyncio.AbstractEventLoop | None = None

    def _upstream(self, name: str,
                  registry: dict[str, dict[str, str]] | None = None) -> UpstreamClient:
        """The client for a registered box: one per box, reused by every call to it. `registry`
        is an `upstreams()` answer the caller already holds (route, a sweep); None reads it.

        Built per call, every sweep paid an httpx client (an SSL context plus the certifi
        bundle, ~0.45-0.5 s) per remote read; see UpstreamClient. Now it is paid once per box.

        upstreams.json is hot-read, so the held client is checked against the CURRENT url and
        token on every call, and an edit gets a new one. The client it replaces is RETIRED, not
        closed outright, because a delegated stream may still be reading through it: it closes
        itself once no call is using it (UpstreamClient.retire), now if none is. Until then it is
        kept here, so the broker's own close (aclose) still reaches one that is busy at shutdown.
        It used to be closed only with the broker, so every edit left a pool open.

        Per running event loop: an httpx client's pooled connections belong to the loop that
        opened them, and reused from another one they fail ("Event loop is closed"). Production
        runs one loop; each asyncio.run, and each request a TestClient makes outside a `with`
        block, is a new one. Clients from a previous loop are dropped, not closed: closing them
        needs the loop they belong to.

        Every caller is a coroutine (route() and the views' probes), so a loop is always
        running here; one that is not is a RuntimeError, not a client nobody would close.
        """
        spec = (self.settings.upstreams() if registry is None else registry)[name]
        url, token = spec["url"], spec.get("token", "")
        loop = asyncio.get_running_loop()
        if loop is not self._upstream_loop:
            self._upstream_loop = loop
            self._upstream_clients: dict[str, UpstreamClient] = {}
            self._upstream_retired: list[UpstreamClient] = []
        client = self._upstream_clients.get(name)
        if client is None or not client.serves(url, token):
            if client is not None:
                client.retire()
                self._upstream_retired = [c for c in (*self._upstream_retired, client)
                                          if c.busy]
            client = UpstreamClient(name, url, token, timeout=self.settings.ollama_timeout)
            self._upstream_clients[name] = client
        return client

    async def route(self, ref: str, *, substitute: bool
                    ) -> tuple[UpstreamClient | None, str, Fallback | None]:
        """WHERE a reference runs, and what to call it there.

        The dispatch layer sits ABOVE resolve_ref rather than inside it, because the two have
        opposite jobs. resolve_ref answers "what name will serve this HERE", and under
        `substitute=True` it may answer with a different model than asked for. Both are wrong
        for a delegated role: the glob has to reach the upstream untouched, since only that box
        knows what it has installed, and substituting a LOCAL model for a role the operator
        deliberately sent off-site would silently un-delegate it while reporting success.

        So a delegated ref returns here with its glob whole and no fallback -- there is no such
        thing as falling back across a box boundary -- and never reaches the local resolver.
        Everything else is handed straight to resolve_ref unchanged.

        Returns `(upstream_or_None, model_ref, fallback_or_None)`. The upstream is None for
        local work, which is the overwhelmingly common case.

        A CONCRETE `box::model` ref delegates too, not just an `@role` whose VALUE carries the
        prefix. That is deliberate and worth stating, because it is wider than "an admin
        repoints a role": any caller holding an inference-scoped broker token can address a
        REGISTERED upstream directly, without an admin repointing anything. It is useful --
        testing a remote before committing a role to it, and a power user picking a box per
        call -- and it is bounded by the registry, so a caller can only reach boxes the
        operator already registered and cannot name a new one. It also cannot be silent: on
        the OpenAI surface the same string is refused outright, and everywhere else the answer
        carries the upstream's own model name. If you want roles-only, the check belongs here
        (reject `"::" in ref` when `ref` does not start with `@`), not at the call sites.
        """
        # ONE read of the role map per call, handed to resolve_ref, which read it again on every
        # @role chat; and one of the registry for a delegated one, handed to _upstream.
        roles = self.settings.roles() if ref.startswith("@") else None
        value = roles.get(ref[1:], ref) if roles is not None else ref
        # Same free pre-check as the guard in resolve_ref: no model name contains `::`, so the
        # registry is only read for a reference that could actually be delegated.
        registry = None
        if "::" in value:
            registry = self.settings.upstreams()
            up_name, rest = self.settings.delegate_ref(value, registry)
            if up_name is not None:
                return self._upstream(up_name, registry), rest, None
        return (None, *await self.resolve_ref(ref, substitute=substitute, roles=roles,
                                              upstreams=registry))

    async def _is_capable(self, name: str, category: str) -> bool:
        """Can `name` actually stand in for a model in `category`?

        The curated category is EDITORIAL -- it answers "what is this good at", not "what can
        it DO" -- and two files in this repo prove the gap. platform_core.model_catalog files
        `gemma4*:12b` and `gemma3*` as 'chat', while deploy/installer/model-catalog.json lists
        gemma4:12b under VISION and deploy/installer/roles.lean.json points @vision straight at
        gemma3:4b because it is small AND multimodal.

        Trusting the label alone therefore fails in BOTH directions: it would refuse every
        @vision substitution on the lean 8 GB box that needs this most, and one mis-catalogued
        entry would let a text model answer a question about a picture. So the category RANKS
        candidates and the BACKEND decides.
        """
        if category == "vision":
            digest = self._digests.get(name)
            return "vision" in await self._capabilities(name, digest)
        if category == "embed":
            return self._class(name) == "embed"
        if category == "image":
            return name in MEDIA_IMAGE_BACKENDS
        # chat / reasoning / code / other: any generative model can stand in. Refusing across
        # those would mean claiming a reasoning model cannot hold a conversation.
        return self._class(name) == "heavy"

    async def _plan_substitute(self, role: str, wanted: str, reason: str, *,
                               tags: list[dict[str, Any]] | None = None,
                               disabled: set[str] | None = None) -> Fallback:
        """Choose the best installed, ENABLED, capability-compatible substitute, or raise.

        Deliberately SILENT. Read views (roles_view, audit_roles) call this to DESCRIBE what
        would happen, and a diagnostic that writes a "we are degraded" line every time an admin
        opens the Rails tab is a line nobody reads by Tuesday. Announcing is _announce's job,
        on the path that actually runs.

        `tags` is the installed list a caller already holds (resolve_ref, audit_roles), so
        planning a substitute does not fetch the same list a second time. `disabled` is the
        same for disabled.json (the roles sweep reads it once for every role).
        """
        category = category_of(wanted, klass=self._class(wanted))
        disabled = self.settings.disabled() if disabled is None else disabled
        tags = tags if tags is not None else await self.ollama.tags()
        self._digests = {str(m.get("name") or ""): m.get("digest") for m in tags}

        scored: list[tuple[tuple[Any, ...], str]] = []
        for m in tags:
            name = str(m.get("name") or "")
            if not name or name == wanted or _is_disabled(name, disabled):
                continue
            if not await self._is_capable(name, category):
                continue
            # Prefer a candidate the catalog files in the SAME category, then fall back to the
            # broker's own definition of "best installed" -- the identical ordering a glob uses,
            # so a role and its own pattern cannot disagree about which model is better.
            same = category_of(name, klass=self._class(name)) == category
            scored.append(((same, *rank_key(m)), name))
        if not scored:
            raise NoSubstituteError(role, wanted, category, reason)
        scored.sort(reverse=True)
        return Fallback(model=scored[0][1], requested=f"@{role}", original=wanted,
                        reason=reason, category=category)

    def _announce(self, fb: Fallback) -> None:
        """Say it. Every substitution, unconditionally, unguarded, un-deduplicated.

        Not wrapped in a try/except and not rate-limited on purpose. A control that runs
        degraded has to say so on EVERY run, and a log line that cannot fail is worth nothing.
        The volume is proportional to the damage and stops the moment the map is fixed.
        """
        self._fallback_count += 1
        self._last_fallback = fb.as_dict()
        print(f"[broker] MODEL FALLBACK: {fb.requested} -> '{fb.original}' is {fb.reason}; "
              f"serving '{fb.model}' ({fb.category}) instead", file=sys.stderr, flush=True)

    async def resolve_ref(self, ref: str, *, substitute: bool, announce: bool = True,
                          tags: list[dict[str, Any]] | None = None,
                          roles: dict[str, str] | None = None,
                          disabled: set[str] | None = None,
                          upstreams: dict[str, dict[str, str]] | None = None,
                          ) -> tuple[str, Fallback | None]:
        """Resolve a model reference for EVERY path: chat, stream, load, embed, the OpenAI
        surface, roles_view and audit_roles.

        One function, because the alternative is the warm/serve split this platform cannot
        afford: /v1/load warming the model a role names while /v1/chat serves a different one
        doubles VRAM on the single card the whole one-heavy-model policy exists to protect.

        `substitute` is a POLICY switch, not a resolution detail. True only for an '@role':
        "@vision" means "whatever the admin points vision at", so re-pointing it under failure
        is inside the promise. A CONCRETE name means that model, and answering with another is
        the same lie as returning a wav labelled audio/mpeg. A bare glob is not substituted
        either -- it keeps its ValueError, which audit_roles already reports precisely.

        `announce=False` is for READ VIEWS. roles_view powers the Rails tab, and announcing
        there would write one "we are degraded" line per bad role every time it loads -- a
        stream nobody can act on is a stream nobody reads. The line belongs on the path that
        actually runs a request. (An earlier version of this comment said the tab POLLS. It
        does not: AdminPage loads on mount and on Apply. The decision is still right; its
        stated premise was wrong.)

        `tags` lets a caller that ALREADY fetched the installed list pass it in.
        `OllamaClient.tags()` is an uncached GET, so roles_view iterating 24 roles without
        this made 24 round trips where one would do. `roles` and `disabled` are the same for
        roles.json and disabled.json: hot-read files, each a synchronous read in the event loop,
        which the roles sweep read once per role (about 51 reads per /v1/roles) until it began
        reading each once and handing them in. `upstreams` is upstreams.json the same way, read
        here only for a role whose value carries `::`. None reads the file, as every other
        caller wants.

        DELEGATION IS NOT HANDLED HERE -- see `route()`, and the guard immediately below.
        """
        if not ref.startswith("@"):
            if not any(c in ref for c in "*?[]"):
                return ref, None
            tags = tags if tags is not None else await self.ollama.tags()
            return resolve_ollama_model(ref, lambda: tags), None

        role = ref[1:]
        roles = self.settings.roles() if roles is None else roles
        if role not in roles:
            raise UnknownRoleError(role, sorted(roles))
        pattern = roles[role]

        # Delegation normally arrives as a ROLE VALUE, which is why the check sits here, after
        # the role map has been consulted. It is NOT role-only though: route() honours a
        # concrete `box::model` from a caller too (see its docstring). This branch is still
        # worth having because the role path is the common one, and the `::` test keeps it
        # free -- no Ollama model name contains a double colon, so for every local role this is
        # one substring scan and no registry read. Reaching the body means a dispatch path
        # called resolve_ref directly on a delegated role -- see DelegatedRefError for why that
        # must be loud rather than resolved here.
        if "::" in pattern:
            up_name, _rest = self.settings.delegate_ref(pattern, upstreams)
            if up_name is not None:
                raise DelegatedRefError(ref, up_name)

        # BEFORE the installed check, or every image role falls back to a chat model: a media
        # backend is loaded by the media worker from the HF cache and never appears in tags().
        # Named as the backend spells itself, whatever case the role map wrote.
        backend = image_backend(pattern)
        if backend is not None:
            return backend, None

        tags = tags if tags is not None else await self.ollama.tags()
        installed = {str(m.get("name") or "") for m in tags}
        self._digests = {str(m.get("name") or ""): m.get("digest") for m in tags}
        try:
            name = resolve_ollama_model(pattern, lambda: tags)
        except ValueError:
            if not substitute:
                raise
            fb = await self._plan_substitute(role, pattern, "not installed", tags=tags,
                                             disabled=disabled)
            if announce:
                self._announce(fb)
            return fb.model, fb

        # `:latest` tolerance is mandatory: Ollama reports an untagged pull that way, so
        # @embed -> 'bge-m3' is installed as 'bge-m3:latest'. audit_roles already does this.
        if name not in installed and f"{name}:latest" not in installed:
            if not substitute:
                return name, None
            fb = await self._plan_substitute(role, name, "not installed", tags=tags,
                                             disabled=disabled)
            if announce:
                self._announce(fb)
            return fb.model, fb

        if disabled is None:
            disabled = self.settings.disabled()
        if _is_disabled(name, disabled):
            if not substitute:
                return name, None
            fb = await self._plan_substitute(role, name, "disabled", tags=tags, disabled=disabled)
            if announce:
                self._announce(fb)
            return fb.model, fb
        return name, None

    async def audit_roles(self) -> list[str]:
        """Check the whole role map against what is installed and what the card can hold.

        Once at startup, not per call: what this catches is a misconfigured MAP, and naming all
        of it in one place is the only version of this warning anyone acts on.

        Two ways a role is already wrong before a single request arrives. It can name a model
        that is not installed, which a user meets as a red "missing" chip and nothing else. Or
        it can name one that is installed but bigger than the card, which they meet as a load
        that fails or thrashes. Both are configuration rather than runtime, and both stay
        invisible until somebody clicks the rail — which is how the 24 GB role map this repo
        ships reached an 8 GB laptop and sat there, every role pointing at a model that neither
        fit nor existed locally, with roughly 60 GB of pointless pulls as the suggested fix.

        Best-effort by construction: no nvidia-smi, no Ollama, or any error at all and the audit
        goes quiet. A diagnostic must never be the reason the broker fails to come up.
        """
        try:
            tags = await self.ollama.tags()
        except Exception:  # noqa: BLE001 - never block startup on a diagnostic
            return []
        installed = {str(m.get("name") or ""): int(m.get("size") or 0) for m in tags}
        card = await gpu.vram()
        total_mib = int(card.get("total_mib") or 0) if card else 0

        out: list[str] = []
        # Which roles this box actually SET, so a finding can say whether the bad value was
        # configured here or inherited. DEFAULT_ROLES is sized for a 24 GB card, so on a lean
        # install every role the local map omits silently resolves to a pin nobody chose —
        # and RC027 cannot see it, because it only checks the rails the installer ships.
        try:
            overlay = set(self.settings.overlay_roles())
        except Exception:  # noqa: BLE001 - provenance is a nicety; never fail the audit for it
            overlay = set()
        for role, pattern in sorted(self.settings.roles().items()):
            # Media backends are not Ollama models and never appear in tags(); they are loaded
            # by the media worker from the HF cache. Auditing them here would report every
            # correctly configured image role as missing. In any case, as /v1/image draws it.
            if image_backend(pattern) is not None:
                continue
            # Only decorates a role that is ALREADY wrong. An inherited default that is
            # installed and fits is the normal case and must stay silent, or the audit turns
            # into a wall of notes about roles nobody needs to touch.
            #
            # ABOVE the delegation branch, not below it. It sat after, and the delegated branch
            # read `src` regardless: on the FIRST iteration that is an UnboundLocalError, and
            # audit_roles runs unguarded in the lifespan — so one role pointing at an
            # unregistered box stopped the broker from starting at all. A malformed
            # upstreams.json does it to every delegated role at once, because upstreams()
            # degrades to {}. On later iterations it was quieter and worse: `src` still held
            # the PREVIOUS role's provenance.
            src = "" if role in overlay else " [inherited from DEFAULT_ROLES; roles.json does not set it]"
            # A delegated role is audited against the REGISTRY, not this card. The one finding
            # worth making is a role pointing at a box nobody registered: delegate_ref falls
            # back to local on an unknown name, so the pattern stays whole, matches no
            # installed model, and the operator sees a role they believe is off-site reported
            # as simply missing. Say which it is.
            if "::" in pattern:
                up_name = pattern.partition("::")[0]
                if up_name not in self.settings.upstreams():
                    known = sorted(self.settings.upstreams()) or ["(none registered)"]
                    out.append(f"@{role} -> {pattern}{src}: upstream '{up_name}' is not "
                               f"registered in upstreams.json, so this role resolves LOCALLY "
                               f"and will not match anything; registered: {', '.join(known)}")
                continue
            try:
                # The list fetched above, not a fresh GET per role: resolving without it refetched
                # it for every glob, about 21 identical /api/tags calls on each startup.
                name, _fb = await self.resolve_ref(f"@{role}", substitute=False, tags=tags)
            except ValueError:
                # A GLOB that matches nothing raises instead of returning a name, and this
                # arm used to be a bare `except Exception: continue`. That made the audit mute
                # for precisely the map it was written for: every shipped role is a scoped glob
                # (resolve_ollama_model's own docstring tells you to write them that way), so on
                # a box with none of them installed every role raised here and startup reported
                # nothing at all. A plain NAME never had the problem — it is returned unresolved
                # and caught by the `size is None` check below — which is why the tests, all of
                # which use plain names, agreed the audit worked.
                out.append(f"@{role} -> '{pattern}' matches NO installed model"
                           f"{await self._would_fall_back(role, pattern, 'not installed', tags=tags)}{src}")
                continue
            except Exception as exc:  # noqa: BLE001 - a diagnostic must not block startup
                # Still best-effort, but it SAYS so now. A check that quietly skips a role is
                # indistinguishable from one that passed it.
                out.append(f"@{role} -> '{pattern}' could not be checked "
                           f"({exc.__class__.__name__}){src}")
                continue
            size = installed.get(name) or installed.get(f"{name}:latest")
            if size is None:
                out.append(f"@{role} -> '{pattern}' resolves to '{name}', which is NOT installed"
                           f"{await self._would_fall_back(role, name, 'not installed', tags=tags)}{src}")
                continue
            # The audit reads `disabled` and the hot path no longer refuses on it, so a role
            # pointing at a disabled model is a CONFIGURATION fault that has to be named
            # somewhere. Startup is that somewhere -- this is one of the five things that stop
            # a working fallback from making a broken map look healthy.
            if _is_disabled(name, self.settings.disabled()):
                out.append(f"@{role} -> '{name}' is DISABLED by the admin"
                           f"{await self._would_fall_back(role, name, 'disabled', tags=tags)}{src}")
                continue
            need_mib = size // (1024 * 1024)
            if total_mib and need_mib > total_mib:
                out.append(f"@{role} -> '{name}' needs ~{need_mib // 1024} GB but the card has "
                           f"{total_mib // 1024} GB{src}")
        # NOTE: "a role a rail calls must exist in the map" is checked STATICALLY, in
        # tests/test_role_map.py, not here. roles() always merges DEFAULT_ROLES underneath the
        # overlay, so the only way a ROLE_RAIL entry can be missing is for DEFAULT_ROLES itself
        # to lack it -- which a test catches at build time rather than at somebody's startup.
        # Putting it here instead made every small synthetic map in the audit tests report
        # thirteen bogus lines, which is the noise that gets an audit ignored.
        if out and total_mib:
            out.append(f"the role map does not fit this {total_mib // 1024} GB card — size one "
                       f"with deploy/installer/modelplan.ps1 -VramGb {total_mib // 1024}")
        return out

    async def _would_fall_back(self, role: str, wanted: str, reason: str, *,
                               tags: list[dict[str, Any]] | None = None) -> str:
        """The ', will fall back to X' clause for an audit line, or the refusal.

        Calls _plan_substitute and NOT _announce: this is a read view. Announcing here would
        write a 'we are degraded' line per bad role on every startup and every Rails-tab poll,
        and a stream nobody can act on is a stream nobody reads.
        """
        try:
            fb = await self._plan_substitute(role, wanted, reason, tags=tags)
        except NoSubstituteError:
            return ", and NOTHING installed can substitute — every call will fail"
        except Exception:  # noqa: BLE001 - a diagnostic must never block startup
            return ""
        return f", will fall back to '{fb.model}' ({fb.category})"

    def _resolve_media(self, model: str) -> str:
        """Expand a leading @role via the role map (no Ollama globbing — media backends
        aren't Ollama models). Returns the concrete backend name (the worker validates it).

        Raises UnknownRoleError on an unmapped role, same as resolve_ref. The sync twin was the
        asymmetric half once already (it was the last place a `_comment` key could be read back
        as a role); it does not get to be again.
        """
        if model.startswith("@"):
            role = model[1:]
            roles = self.settings.roles()
            if role not in roles:
                raise UnknownRoleError(role, sorted(roles))
            model = roles[role]
        # Media is never delegated, and this is the last ref-expanding path that had no guard.
        # A media backend is loaded by THIS box's media worker out of its own HF cache, so
        # there is nothing to forward and the remote has no concept of the name. The gateway
        # already 400s an attempt to delegate an image slot, but PUT /v1/roles/{role} on the
        # broker reaches set_role directly, and set_role only checks that the upstream is
        # REGISTERED — not that the role is one that can be delegated at all. Without this the
        # prefixed string went to the worker and came back as a confusing 502 about a backend
        # name nobody wrote.
        if "::" in model:
            up_name, _rest = self.settings.delegate_ref(model)
            if up_name is not None:
                raise DelegatedRefError(model, up_name)
        return model

    def sweep_media_temp(self) -> tuple[int, int]:
        """At startup: remove the media and voice jobs' directories in the system temp
        (`broker-media-*`, media.run_media_job; `broker-voice-*`, voice.run_voice_job) that a
        broker which died mid-job left behind (an NSSM stop, the watchdog at shutdown, a crash:
        the job's own cleanup never ran), or that a file filter held past the cleanup. A
        transcribe's holds the recording, up to 15 MB of base64; a voice job's, the text being
        read aloud.

        Each kind only once untouched for an hour, or three of its own job timeouts
        (BROKER_MEDIA_TIMEOUT, BROKER_VOICE_TIMEOUT) if that is longer: a live job's directory
        goes unwritten for up to one timeout while its worker computes, and the system temp is
        shared with every broker run under the same account, whose timeouts may be longer than
        this one's. Never raises (a boot must not fail over leftovers), and a kind that fails
        does not stop the other; logs, per kind, what it removed and what it could not.
        `(removed, left)`, both kinds together."""
        root, hour, s = tempfile.gettempdir(), cli_backend._TEMP_STALE_S, self.settings
        removed = left = 0
        for kind, name, timeout in (("media", media.JOB_DIR_NAME, s.media_timeout),
                                    ("voice", voice.JOB_DIR_NAME, s.voice_timeout)):
            try:
                gone, held = media.sweep_job_dirs(
                    root, older_than=max(hour, 3 * float(timeout)), name=name)
            except Exception as exc:  # noqa: BLE001 - leftovers are never a reason not to start
                print(f"[broker] {kind} temp sweep of {root} failed "
                      f"({exc.__class__.__name__}: {exc})", file=sys.stderr, flush=True)
                continue
            if gone or held:
                print(f"[broker] {kind} temp sweep: removed {gone} stale {kind} job "
                      f"director{'y' if gone == 1 else 'ies'} under {root}"
                      + (f"; {held} would not go and stay for the next start" if held else ""),
                      file=sys.stderr, flush=True)
            removed, left = removed + gone, left + held
        return removed, left

    # --- Claude CLI backend -------------------------------------------------
    # Written in broker_cli.py, and Broker's own all the same (see "where Broker's methods live").

    def _rail_for(self, model_ref: str | None) -> str | None:
        """Friendly rail name for a queued job, derived from a per-rail @role (else None —
        a generic class role or a manual/concrete load isn't attributable to one rail)."""
        if model_ref and model_ref.startswith("@"):
            return ROLE_RAIL.get(model_ref[1:])
        return None

    # --- GPU operations (gated) --------------------------------------------

    async def load(self, model: str, keep_alive: str | int | None = None) -> dict[str, Any]:
        rail = self._rail_for(model)
        # The SAME resolver chat uses. If these two disagreed, the tray would warm the model
        # the role names while chat served a substitute -- two heavy models resident on the
        # one card the whole one-heavy-model policy exists to protect.
        up, model, _fb = await self.route(model, substitute=True)
        keep_alive = self.settings.default_load_keep_alive if keep_alive is None else keep_alive
        if up is not None:
            # Warming happens on the remote's own card, under its own gate. Nothing is
            # evicted here because nothing was loaded here.
            out = await up.load(model, keep_alive=keep_alive)
            return {**out, "upstream": up.name}
        if self._class(model) == "embed":
            # Embedders load via /api/embed; they don't evict heavy models.
            await self.ollama.embed(model, " ", keep_alive=keep_alive)
            return {"model": model, "class": "embed", "evicted": [], "keep_alive": keep_alive}
        async with self.gate.hold(model=model, source=rail):
            evicted = await self._evict_other_heavy(keep=model)
            await self.ollama.generate_warm(model, keep_alive=keep_alive)
            return {"model": model, "class": "heavy", "evicted": evicted, "keep_alive": keep_alive}

    async def unload(self, model: str) -> dict[str, Any]:
        # `ollama stop` reliably evicts any model (heavy or embedder) from VRAM.
        up, model, _fb = await self.route(model, substitute=False)
        if up is not None:
            out = await up.unload(model)
            return {**out, "upstream": up.name}
        await self.ollama.stop(model)
        return {"model": model, "unloaded": True}

    def cancel_job(self, seq: int) -> bool:
        """Cancel a queued/active GPU job by its queue seq (admin action via the gateway)."""
        return self.gate.cancel(seq)

    # --- OpenAI-compatible surface (gated, role-aware) ----------------------

    async def resolve_for_external(self, model: str, *, substitute: bool = True
                                   ) -> tuple[str, Fallback | None]:
        """Resolve a model reference for an OUTSIDE caller, honouring the admin's
        disable flag.

        `resolve_ref` deliberately does not refuse a `disabled` model: a rail only ever picks from
        the already-filtered `list_models()` view, so the flag has been advisory and
        nothing could bypass it. An external OpenAI client never sees that view and
        names whatever model it likes, so without this the Admin > Models Disable
        toggle would go on rendering as 'off' while still serving every request.

        Checks the RESOLVED name, not the reference: disabling `gemma4:26b` must also
        refuse `@vision` and `gemma4*:26b` when they land on it, or the toggle means
        only 'disabled for callers who spell it out'.

        An '@role' now SUBSTITUTES rather than refusing. "@vision" means "whatever the admin
        points vision at", so re-pointing it when that model cannot run is inside the promise
        the role makes. A model the caller SPELLED OUT still gets the 403: answering a request
        for a named model with a different one is the same lie as returning a wav labelled
        audio/mpeg.

        DELEGATED ROLES ARE REFUSED HERE, deliberately, rather than silently served locally.
        Delegation is a property of a role, and this surface exists for clients that name a
        concrete model, so only an '@role' could be delegated at all. Two things make serving
        it wrong: `disabled` is a LOCAL policy with no meaning on another box, and the OpenAI
        response shape has nowhere to say "this ran somewhere else". Refusing names the reason;
        resolving locally would quietly run the wrong card and report success.

        `substitute=False` is for EMBEDDINGS, and only for them. Every generative role may be
        re-pointed under failure; an embedder may not, for the reason `embed()` gives: two
        embedders write vectors in different spaces, so a substitute returns confidently ranked
        nonsense and poisons whatever index it touches. A role then resolves exactly and takes
        the same disable check as a spelled-out name.

        Each settings file is read once per call: the role map read for the delegation check is
        the one resolve_ref resolves against, the registry read to split a `::` value the one its
        guard splits with, and disabled.json read once for resolve_ref and the check below.
        """
        roles: dict[str, str] | None = None
        registry: dict[str, dict[str, str]] | None = None
        if model.startswith("@"):
            roles = self.settings.roles()
            value = roles.get(model[1:], model)
            if "::" in value:
                registry = self.settings.upstreams()
                up_name, _rest = self.settings.delegate_ref(value, registry)
                if up_name is not None:
                    raise DelegatedRefError(model, up_name)
            if substitute:
                return await self.resolve_ref(model, substitute=True, roles=roles,
                                              upstreams=registry)
        disabled = self.settings.disabled()
        resolved, _fb = await self.resolve_ref(model, substitute=False, roles=roles,
                                               disabled=disabled, upstreams=registry)
        if _is_disabled(resolved, disabled):
            raise ModelDisabledError(resolved)
        return resolved, None

    async def openai_chat(self, body: dict[str, Any], *,
                          resolved: tuple[str, Fallback | None] | None = None,
                          client_gone: Callable[[], Awaitable[bool]] | None = None
                          ) -> dict[str, Any]:
        """Buffered OpenAI chat completion under the GPU gate.

        `resolved` is `resolve_for_external`'s answer for `body["model"]`, which the route
        already holds: it resolves up front to answer a refusal with a real status code, and
        that resolution is the one that runs, so the request reads each settings file and the
        installed list once and the fallback headers it sent name the model that serves. None
        resolves here (an in-process caller).

        `client_gone` (the route's `request.is_disconnected`) is asked every _GONE_TICK_S while
        the call waits for the gate or Ollama answers (`_while_there`): once the caller has hung
        up, the call is cancelled, which closes Ollama's request (and Ollama stops generating:
        see Broker.chat), and ClientGone is raised once the gate is free."""
        return await self._while_there(self._openai_chat_gated(body, resolved), client_gone,
                                       every=_GONE_TICK_S,
                                       doing="the local model was answering")

    async def _openai_chat_gated(self, body: dict[str, Any],
                                 resolved: tuple[str, Fallback | None] | None
                                 ) -> dict[str, Any]:
        """openai_chat's call: the gate, the eviction and Ollama's answer."""
        ref = str(body.get("model") or "")
        rail = self._rail_for(ref)
        model, fb = resolved if resolved is not None else await self.resolve_for_external(ref)
        async with self.gate.hold(model=model, source=rail or "openai-compat",
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            return await self.ollama.openai_chat({**body, "model": model})

    async def openai_chat_stream(self, body: dict[str, Any], *,
                                 resolved: tuple[str, Fallback | None] | None = None
                                 ) -> AsyncIterator[bytes]:
        """Streaming twin. The gate is held for the WHOLE stream, exactly like
        `chat_stream`: the job stays 'active' in the queue until the last frame, so a
        second caller queues behind it rather than racing it onto the card.

        `resolved` as in `openai_chat`: the route's own resolution, the one that runs."""
        ref = str(body.get("model") or "")
        rail = self._rail_for(ref)
        model, fb = resolved if resolved is not None else await self.resolve_for_external(ref)
        async with self.gate.hold(model=model, source=rail or "openai-compat",
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            # Closed INSIDE the gate. A plain `async for` left Ollama's stream to the async-
            # generator finalizer when the route closed this one: the gate came back first, and
            # the next job could take the card while Ollama still generated for nobody.
            async with aclosing(self.ollama.openai_chat_stream({**body, "model": model})) as inner:
                async for chunk in inner:
                    yield chunk

    async def openai_embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        """Embeddings are light and coexist with a heavy model, so no gate -- the same
        call `embed()` makes ungated. Still resolved and disable-checked.

        NEVER substituted, like `embed()`. This route used to resolve `@embed` the generative
        way, so with bge-m3 missing it answered 200 with nomic vectors and no fallback header
        while /v1/embed, asked the same thing, refused: one platform, two answers, and the
        silent one writes another model's vectors into a caller's index."""
        model, _fb = await self.resolve_for_external(str(body.get("model") or ""),
                                                     substitute=False)
        return await self.ollama.openai_embeddings({**body, "model": model})

    async def openai_models(self) -> list[dict[str, Any]]:
        """Installed models in OpenAI's listing shape, disabled ones omitted.

        Omitted rather than flagged: the listing is what a client offers as a picker,
        and OpenAI's model object has nowhere to put 'disabled'. A model that cannot
        be used must not be offered.
        """
        disabled = self.settings.disabled()
        return [
            {
                "id": m["name"],
                "object": "model",
                "created": 0,
                "owned_by": (m.get("family") or "library"),
            }
            for m in await self.list_models()
            if m.get("name") and not _is_disabled(m["name"], disabled)
        ]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: str | dict[str, Any] | None = None,
        think: bool | None = None,
        requester: str | None = None,
        client_gone: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """`client_gone` answers "has the caller hung up?" without blocking (the route passes
        Starlette's `request.is_disconnected`). Asked only on the CLI path, at the three points
        where a rail that already gave up would otherwise cost something: when a call that
        waited for a CLI slot gets one, every `_GONE_TICK_S` while the CLI answers (the call is
        then stopped and its tree killed, `_while_there`), and before the local fallback after a
        CLI failure. Each time the answer is ClientGone, never a fallback. None (an in-process
        caller) means nobody can hang up.

        And since 2026-10-15 on the local path, every `_GONE_TICK_S` while the call waits for the
        GPU gate or Ollama answers (`_local_chat` inside `_while_there`): a buffered answer for a
        caller who left held the gate, and the card, to its end. Cancelling the call closes
        Ollama's request; Ollama cancels its runner request with its client's context, and the
        bundled llama-server cancels the tasks of a closed connection (read in their source,
        2026-10-15, not run), so the card stops too."""
        rail = self._rail_for(model)
        # The CLI runs no GPU work, so it takes no gate and evicts nothing -- same reasoning
        # as a delegated role. Unlike delegation, it DOES fall back to local, because the
        # reason a delegated role must not ("a same-named model on the wrong box is not the
        # same answer") does not apply: this is the same question asked of a different model,
        # which is exactly what the existing substitution machinery already announces.
        cli_fallback: dict[str, str] | None = None
        cli_failure = ""
        att = self._cli_begin(model, requester, rail)
        if att is not None:
            try:
                async with self._cli_slot(att, requester, client_gone):
                    out = await self._while_there(
                        att.client.chat(messages, format=format, options=options,
                                        think=think),
                        client_gone, every=_GONE_TICK_S)
                    self._cli_served(att, out.get("cli") or {})
                    return out
            except ClientGone:
                # Counted with the refusals (the call never reached the CLI, or the caller left
                # before its answer came), and NOT a fallback: nothing runs for it, locally or
                # anywhere else.
                self._cli_refusals["client_gone"] = self._cli_refusals.get("client_gone", 0) + 1
                print(f"[broker] CLI: {model} stopped, the caller hung up before the CLI "
                      f"answered", file=sys.stderr, flush=True)
                raise
            except Exception as exc:  # noqa: BLE001
                # EVERY exception, not just CliUnavailable. "Every failure is a fallback" is
                # the property that makes this safe to switch on, and a guarantee that holds
                # only for the errors somebody anticipated is not a guarantee. A ValueError
                # from an env value with embedded NULs, a NotImplementedError from a selector
                # event loop under --reload, a TypeError from a malformed usage block: each
                # would otherwise surface as a 502 on a rail that worked the day before.
                cli_fallback, cli_failure = self._cli_failed(att, exc)
        if cli_fallback is not None:
            await self._cli_fallback_starts(model, cli_fallback, cli_failure, client_gone)
        up, model, fb = await self.route(model, substitute=True)
        if up is not None:
            # No gate and no eviction: this runs on another card, so holding the single local
            # slot would serialise local work behind something that cannot contend with it.
            out = await up.chat(model, messages, options=options, keep_alive=keep_alive,
                                format=format, think=think)
            if cli_fallback is not None and isinstance(out, dict):
                out = {**out, "cli_fallback": cli_fallback}
            return out
        out = await self._while_there(
            self._local_chat(model, messages, rail, fb, options=options, keep_alive=keep_alive,
                             format=format, think=think),
            client_gone, every=_GONE_TICK_S, doing="the local model was answering")
        # PLATFORM dialect only, and only ON a substitution. Ollama's /api/chat response has
        # no "fallback" key, so there is no collision; a test asserts it is ABSENT on the
        # happy path, or the field decays into decoration nobody reads.
        if fb is not None and isinstance(out, dict):
            out = {**out, "fallback": fb.as_dict()}
        # A SEPARATE key from `fallback`, not a reuse of it. That one means "a different
        # MODEL served this role"; this means "the CLI could not, so the local model did".
        # Collapsing them would make the Rails tab report a model substitution that never
        # happened, which is the same confusion `resolved` vs `serving` exists to prevent.
        if cli_fallback is not None and isinstance(out, dict):
            out = {**out, "cli_fallback": cli_fallback}
        return out

    async def _local_chat(self, model: str, messages: list[dict[str, str]], rail: str | None,
                          fb: Fallback | None, *, options: dict[str, Any] | None,
                          keep_alive: str | int | None, format: str | dict[str, Any] | None,
                          think: bool | None) -> dict[str, Any]:
        """chat's local answer: the gate, the eviction and Ollama's buffered reply. Its own
        method so `_while_there` can run it in a task and cancel it for a caller who left."""
        async with self.gate.hold(model=model, source=rail,
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            out = await self.ollama.chat(
                model, messages, options=options, keep_alive=keep_alive,
                format=format, think=think,
            )
        return out

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: str | dict[str, Any] | None = None,
        think: bool | None = None,
        requester: str | None = None,
        client_gone: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming twin of ``chat``: same heavy gate + one-heavy-model eviction, but
        yields Ollama chunks as they arrive so a rail can relay tokens live over a
        WebSocket. The GPU gate is held for the whole stream (the job stays 'active' in
        the queue until the last token), exactly like the buffered path.

        ``think`` is forwarded exactly as the buffered twin forwards it, and for the same
        reason -- a caller that asks for a `format` turns thinking off, and a broker that
        drops that instruction delivers a `<think>` preamble as content. This path is the
        one openmaic streams through, so it was the half that mattered.

        **The Claude CLI**, for an owner's `@role` (`requester` and `client_gone` as on
        ``chat``; the same decision, slot, re-decision and hung-up checks, through the same
        helpers). It takes no GPU gate and holds its CLI slot until the last frame. A plain
        text call streams token by token (ClaudeCliClient.chat_stream); a `format` or images
        go to the buffered client, whose answer is then sent as one content frame, because a
        JSON answer is validated whole and an image call is stream-json in both directions.
        The frames, in order: `{"cli": {"backend": "claude", "model": ...}}` before the
        first content frame, Ollama's content frames, and the CLI's done frame with its `cli`
        usage block. Then the stream's own rule, the one thing that differs from ``chat``:

        * a failure BEFORE the first content frame is a fallback, exactly as there: the
          stream goes on with `{"cli_fallback": {"backend": "ollama", "reason": ...}}` and
          then the local (or delegated) answer, so the rail sees one complete answer;
        * a failure AFTER it is an interruption (_cli_interrupted): the stream ends with an
          `error` line marked `cli_interrupted`, and no local answer starts.

        A caller who hangs up mid-stream ends it (ClientGone; /v1/chat/stream writes nothing
        more), noticed by the idle tick (_watch between frames, _while_there during a buffered
        answer), by Starlette's own cancellation, or by a write that fails, after which the
        route closes this generator. Every way, the CLI's process tree is killed before the
        slot is given back, and the hang-up is counted as `client_gone`. The
        OpenAI-dialect stream (openai_chat_stream) never reaches the CLI: its callers are
        outside clients, and its wire shape has no place for `cli_fallback` or
        `cli_interrupted`."""
        rail = self._rail_for(model)
        cli_fallback: dict[str, str] | None = None
        cli_failure = ""
        att = self._cli_begin(model, requester, rail)
        if att is not None:
            begun = False   # a content frame went out: from here a failure is an interruption
            chars = 0       # how much of the answer the rail has been sent
            try:
                async with self._cli_slot(att, requester, client_gone, stream=True):
                    if format is not None or any(m.get("images") for m in messages):
                        answer = await self._while_there(
                            att.client.chat(messages, format=format, options=options,
                                            think=think),
                            client_gone, every=_GONE_TICK_S)
                        self._cli_served(att, answer.get("cli") or {})
                        whole, done = answer_frames(answer)
                        yield {"cli": {"backend": "claude", "model": whole["model"]}}
                        begun = True
                        chars = len(whole["message"]["content"])
                        yield whole
                        yield done
                    else:
                        lead = True
                        stream = att.client.chat_stream(messages, options=options, think=think)
                        async with aclosing(self._watch(stream, client_gone,
                                                        every=_GONE_TICK_S)) as frames:
                            async for frame in frames:
                                if lead:
                                    yield {"cli": {"backend": "claude",
                                                   "model": frame.get("model") or "claude-cli"}}
                                    lead = False
                                if frame.get("done"):
                                    self._cli_served(att, frame.get("cli") or {})
                                else:
                                    begun = True
                                    chars += len(str((frame.get("message") or {})
                                                     .get("content") or ""))
                                yield frame
                return
            except ClientGone as hung_up:
                # Counted with the refusals, as on chat(): before the first frame the call
                # never reached the CLI (it hung up waiting for a slot), after it the caller
                # cut the answer off. Neither is a fallback, and nothing else runs for it.
                self._cli_refusals["client_gone"] = self._cli_refusals.get("client_gone", 0) + 1
                print(f"[broker] CLI: {model} stream stopped "
                      f"({'mid-answer' if begun else 'before the answer began'}): {hung_up}",
                      file=sys.stderr, flush=True)
                raise
            except (asyncio.CancelledError, GeneratorExit) as stopped:
                # The same hang-up, delivered one of two other ways. Under ASGI 2.3 (what
                # uvicorn reports) Starlette hears the disconnect itself and cancels the
                # stream, so nothing above raised ClientGone. And when the stream ends in
                # Starlette's send() instead, a write that failed (2.4) or a cancellation that
                # landed there, the route closes this generator (GeneratorExit, at a yield;
                # ClosingStreamingResponse). Counted the same once the connection says it has
                # gone; a shutdown cancels too and an in-process caller closes too, and neither
                # is a hang-up. The slot is back and the tree dead by now: _watch and
                # _while_there end only after the reap.
                if await self._hung_up(client_gone):
                    self._cli_refusals["client_gone"] = (
                        self._cli_refusals.get("client_gone", 0) + 1)
                    how = ("cancelled" if isinstance(stopped, asyncio.CancelledError)
                           else "closed")
                    print(f"[broker] CLI: {model} stream stopped "
                          f"({'mid-answer' if begun else 'before the answer began'}): the "
                          f"caller hung up and the server {how} the stream",
                          file=sys.stderr, flush=True)
                raise
            except Exception as exc:  # noqa: BLE001 -- every failure, as on chat()
                if begun:
                    yield self._cli_interrupted(att, exc, chars)
                    return
                cli_fallback, cli_failure = self._cli_failed(att, exc)
        if cli_fallback is not None:
            await self._cli_fallback_starts(model, cli_fallback, cli_failure, client_gone)
            # A LEADING frame, before the local answer's first token, and NOT keyed "error":
            # every rail's reader raises on that key and skips a frame with no
            # message.content, the same reasoning as the `fallback` frame below.
            yield {"cli_fallback": cli_fallback}
        up, model, fb = await self.route(model, substitute=True)
        if up is not None:
            # Relayed frame-for-frame, gate-free for the same reason as chat(). No leading
            # fallback frame: a delegated role never substitutes.
            #
            # `think` IS relayed now. Until 2026-10-05 it was dropped at this hop, so a delegated
            # streaming role ran at the remote model's own thinking default and a thinking model
            # streamed an empty answer. Verified first that the remote broker's own
            # /v1/chat/stream accepts and honours the field (it does) — the condition this
            # comment used to name as the reason not to relay it.
            #
            # Closed WITH this stream (aclosing), so the remote's stream and the pooled
            # connection it holds end when the route closes this one, not whenever the
            # async-generator finalizer gets to them.
            async with aclosing(up.chat_stream(model, messages, options=options,
                                               keep_alive=keep_alive, format=format,
                                               think=think)) as remote:
                async for chunk in remote:
                    yield chunk
            return
        async with self.gate.hold(model=model, source=rail,
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            # A LEADING frame, before the first token. Verified against all three rail
            # readers (gemini-cx, smb-partner-enablement, ai-playground): each does
            # `tok = (frame.get("message") or {}).get("content") or ""` then `if tok:`, so
            # an extra dict frame is skipped. Deliberately NOT keyed "error" -- all three
            # RAISE on that key.
            if fb is not None:
                yield {"fallback": fb.as_dict()}
            # Closed INSIDE the gate (aclosing). A plain `async for` left Ollama's stream to the
            # async-generator finalizer when the route closed this one: the gate came back a few
            # loop turns before Ollama's stream closed, and the next job could take the card
            # while it still generated for nobody.
            async with aclosing(self.ollama.chat_stream(
                model, messages, options=options, keep_alive=keep_alive, format=format,
                think=think,
            )) as inner:
                async for chunk in inner:
                    yield chunk

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        # Embeddings are light and coexist with a heavy model, so no gate.
        #
        # substitute=False here is DELIBERATE and must stay. Every other generative path
        # substitutes a same-capability model rather than failing, but an embedder has no
        # "same capability": two embedders produce vectors in different spaces. Substituting
        # one would write an index with model A and query it with model B, which does not
        # error -- it returns confidently ranked nonsense, and keeps doing so long after the
        # real model is back, because the corrupted vectors are already on disk. A hard
        # failure that says "@embed is not installed" is strictly better than a silent one
        # that poisons every RAG rail's corpus. Same reasoning for embed_image (SigLIP).
        up, model, _fb = await self.route(model, substitute=False)
        if up is not None:
            return await up.embed(model, text)
        return await self.ollama.embed(model, text)

    async def embed_image(self, images: list[str], model: str | None = None,
                          client_gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
        """CPU image embeddings (SigLIP) for retrieval-grounding. Runs in the media
        worker but WITHOUT the GPU gate/eviction: it never touches the GPU, so it must
        not disturb a resident heavy model — evicting gemma3 to embed on CPU and then
        reloading it would be pure thrash. Cheap and safe to run alongside a chat."""
        self._require_media()
        allowed = self.settings.image_embed_model_list()
        if model and model not in allowed:
            raise ImageEmbedderNotAllowed(
                f"image embedder {model!r} is not allowed on this broker; "
                f"allowed: {', '.join(allowed) or '(none)'} (BROKER_IMAGE_EMBED_MODELS)")
        spec: dict[str, Any] = {"op": "embed_image", "images": images}
        if model or allowed:
            spec["model"] = model or allowed[0]
        # A caller who hangs up stops the job and gives its light-media slot back, as tts_light's.
        return await self._while_there(
            self._run_light_media(self.settings.media_python, spec), client_gone,
            every=_GONE_TICK_S, doing="its images were being embedded")

    async def tts_light(
        self,
        text: str,
        *,
        voice: str | None = None,
        lang_code: str | None = None,
        speed: float | None = None,
        client_gone: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """Kokoro-82M read-aloud. Same no-gate path as embed_image(), for the same reason:
        it is CPU/ONNX and never touches the card, so holding the GPU gate or evicting the
        resident chat model to speak a sentence would be pure thrash. ~350 MB, loaded and
        dropped per call by the worker exiting.

        This is what makes read-aloud offerable platform-wide — a rail can voice a block of
        text without the request ever queueing behind, or displacing, someone's chat.

        `client_gone` (the route's `request.is_disconnected`) is asked every _GONE_TICK_S while
        the job runs (`_while_there`), and once the caller has hung up the job is cancelled,
        which kills its worker (media.run_media_job), and ClientGone is raised. A plain route
        never hears a disconnect, so a read the browser stopped (the gateway lets go of its
        request then) used to synthesise to its end, holding a light-media slot. Waiting for
        that slot counts too: the job is the whole call.

        Distinct from tts(): that one is XTTS voice-cloning with per-segment timings for
        highlight-sync, is GPU-gated, and stays exactly as it is.
        """
        self._require_media()
        if not self.settings.kokoro_model_path or not self.settings.kokoro_voices_path:
            raise RuntimeError(
                "Kokoro is not configured (set BROKER_KOKORO_MODEL_PATH and "
                "BROKER_KOKORO_VOICES_PATH on the broker service)"
            )
        spec: dict[str, Any] = {
            "op": "kokoro_tts",
            "text": text,
            "model_path": self.settings.kokoro_model_path,
            "voices_path": self.settings.kokoro_voices_path,
        }
        # Fall back to the platform default rather than the worker's own literal, so the
        # shipped voice is settable server-side (BROKER_KOKORO_VOICE) for every rail at once.
        # Voice and language travel together: a Spanish voice under lang 'a' is garbled.
        spec["voice"] = voice or self.settings.kokoro_voice
        spec["lang_code"] = lang_code or self.settings.kokoro_lang_code
        if speed is not None:
            spec["speed"] = speed
        return await self._while_there(
            self._run_light_media(self._media_python_for("kokoro_tts"), spec), client_gone,
            every=_GONE_TICK_S, doing="its read was being synthesised")

    async def transcribe(
        self,
        audio_b64: str,
        *,
        suffix: str | None = None,
        language: str | None = None,
        client_gone: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """faster-whisper speech-to-text. Same no-gate path as tts_light(), and for a
        sharper reason: the user has just stopped talking and is waiting. Queueing that
        behind the GPU gate, or evicting the model they are about to ask a question, would
        make dictation feel worse than typing. CPU/int8 keeps it off the card entirely.

        Returns {"text", "language", "duration", "model"}.
        """
        self._require_media()
        spec: dict[str, Any] = {
            "op": "transcribe",
            "audio_b64": audio_b64,
            "model": self.settings.whisper_model,
            "device": self.settings.whisper_device,
            "compute_type": self.settings.whisper_compute_type,
        }
        if suffix:
            spec["suffix"] = suffix
        if language:
            spec["language"] = language
        # A caller who hangs up (a rail that timed out: dictation never aborts) stops the job
        # and gives its light-media slot back, as tts_light's.
        return await self._while_there(
            self._run_light_media(self._media_python_for("transcribe"), spec), client_gone,
            every=_GONE_TICK_S, doing="its audio was being transcribed")

    async def _run_light_media(self, python_exe: str, spec: dict[str, Any]) -> dict[str, Any]:
        """One ungated CPU media job, at most BROKER_LIGHT_MEDIA_CONCURRENCY at a time."""
        async with self._light_media:
            return await media.run_media_job(python_exe=python_exe, spec=spec,
                                             timeout=self.settings.media_timeout)

    # --- media (gated; VRAM reclaimed by the worker process exiting) ---------

    def _require_media(self) -> None:
        if not self.settings.media_enabled:
            raise RuntimeError("media is disabled (set BROKER_MEDIA_ENABLED=true)")

    # Three interpreters, because three stacks that cannot share a dependency set.
    #
    # Speech (Coqui XTTS) vs image (diffusers): cannot share a transformers version.
    # Kokoro vs image: a HARD numpy split — kokoro-onnx requires numpy>=2.0.2 while
    # simple-lama-inpainting (image inpainting) requires numpy<2.0.0. Installing Kokoro into
    # the media venv upgrades numpy under the image path, which is the exact way this venv
    # was broken once before. Each op therefore names its own interpreter, and an unset one
    # falls back to the media venv so single-venv installs keep working.
    _SPEECH_OPS = ("tts", "tts_batch")
    _KOKORO_OPS = ("kokoro_tts",)
    _WHISPER_OPS = ("transcribe",)

    def _media_python_for(self, op: str | None) -> str:
        if op in self._WHISPER_OPS:
            # Whisper is torch-free like Kokoro and verified to share its venv, so it falls
            # back to the Kokoro interpreter before the media one. Its own setting exists so
            # the two can be split later without touching any call site.
            return (self.settings.whisper_python or self.settings.kokoro_python
                    or self.settings.media_python)
        if op in self._KOKORO_OPS and self.settings.kokoro_python:
            return self.settings.kokoro_python
        if op in self._SPEECH_OPS and self.settings.tts_python:
            return self.settings.tts_python
        return self.settings.media_python

    async def _run_media(self, spec: dict[str, Any],
                         client_gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
        """Gate + evict all heavy models (media needs the whole card) + run the
        worker, which exits to reclaim VRAM.

        `client_gone` (the route's `request.is_disconnected`) is asked every _GONE_TICK_S while
        the job waits for the gate or runs (`_while_there`, as tts_light's is): once the caller
        has hung up, the job's task is cancelled, so a waiting job leaves the queue and a running
        one has its worker killed and its directory removed (media.run_media_job's finally), and
        ClientGone is raised only once the gate is free again. A plain route never hears a
        disconnect, so a caller who left (a rail that timed out) held the whole card until its
        job ended or BROKER_MEDIA_TIMEOUT. An admin's /v1/cancel cancels the same task."""
        self._require_media()
        return await self._while_there(self._gated_media(spec), client_gone, every=_GONE_TICK_S,
                                       doing=f"its {spec.get('op')} job waited for the GPU or ran")

    async def _gated_media(self, spec: dict[str, Any]) -> dict[str, Any]:
        """_run_media's job: the gate, the eviction and the worker."""
        async with self.gate.hold(model=spec.get("model"), source=spec.get("source")):
            evicted = await self._evict_other_heavy()
            self._media_active = {"op": spec.get("op"), "model": spec.get("model")}
            try:
                result = await media.run_media_job(
                    python_exe=self._media_python_for(spec.get("op")),
                    spec=spec,
                    timeout=self.settings.media_timeout,
                )
            finally:
                self._media_active = None
        result["evicted"] = evicted
        return result

    async def image(
        self,
        prompts: list[str],
        *,
        negative_prompt: str | None = None,
        steps: int = 4,
        size: int = 512,
        model: str = "sdxl-turbo",
        client_gone: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """Generate images. Caller owns the full prompt. ``model`` picks the backend
        the worker loads (``sdxl-turbo`` | ``flux-schnell``), or a media @role that resolves
        to one (e.g. ``@recipe-icon``). Anything else is refused (NotAnImageModel), before the
        gate and its eviction."""
        rail = self._rail_for(model)
        asked, model = model, self._resolve_media(model)
        # The worker draws SDXL for every name that is not FLUX, so a chat role or an OpenAI model
        # name rendered an image labelled with a model that draws none. Refused here, where the
        # resolved name is first known: the worker never starts and nothing is evicted. In any
        # case, though (image_backend): the worker always took `FLUX-Schnell` as FLUX, and an
        # exact comparison here made it a 400.
        backend = image_backend(model)
        if backend is None:
            shown = asked if asked == model else f"{asked} (resolved to {model!r})"
            raise NotAnImageModel(f"{shown} is not an image model; this broker draws with "
                                  f"{' or '.join(MEDIA_IMAGE_BACKENDS)}, or a role set to one")
        model = backend
        return await self._run_media({
            "op": "image",
            "media_core_src": self.settings.media_core_src,
            "prompts": prompts,
            "negative_prompt": negative_prompt,
            "steps": steps,
            "size": size,
            "model": model,
            "source": rail,
        }, client_gone)

    async def tts(self, segments: list[dict[str, Any]],
                  client_gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
        """Synthesize speech (XTTS v2) for an ordered segment list (one combined wav)."""
        return await self._run_media({
            "op": "tts",
            "media_core_src": self.settings.media_core_src,
            "voices_dir": self.settings.media_voices_dir,
            "segments": segments,
        }, client_gone)

    async def tts_batch(self, items: list[dict[str, Any]],
                        client_gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
        """Synthesize many independent clips to SEPARATE wavs in one worker (XTTS
        loaded once). Returns {"audios": [<b64 wav>, ...]} aligned with ``items``."""
        return await self._run_media({
            "op": "tts_batch",
            "media_core_src": self.settings.media_core_src,
            "voices_dir": self.settings.media_voices_dir,
            "items": items,
        }, client_gone)

    # --- voice studio (ai-voice rail; gated, per-engine venv) ----------------

    def _require_voice(self) -> None:
        if not self.settings.voice_enabled:
            raise RuntimeError("voice is disabled (set BROKER_VOICE_ENABLED=true)")

    def voice_catalog(self) -> dict[str, Any]:
        """EVERY registered voice, each flagged runnable or not. No GPU.

        This used to filter to configured engines, which made a registered-but-unwired voice
        (piper's patrick-stewart) simply vanish from the rail with no explanation — so the
        rail kept a hand-maintained `seed/voice-library.json` alongside, purely to have
        something that listed it. That snapshot then drifted: it showed the voice that does
        NOT work and omitted slj-xtts, which does.

        Reporting availability instead of hiding it removes the reason that second file
        existed. The rail is containerized and cannot read the registry itself (it is a host
        path, half of it gitignored), so this endpoint is the only place that can tell the
        whole truth — and it now does.
        """
        reg = voice.load_registry(self.settings.voice_registry_path())
        engines = self.settings.voice_engines()
        fields = ("voice_id", "display_name", "engine", "kind", "language", "source")
        out = []
        for v in reg.get("voices", []):
            entry = {k: v[k] for k in fields if k in v}
            engine = v.get("engine")
            entry["runnable"] = engine in engines
            entry["unavailable_reason"] = (
                None if entry["runnable"]
                else f"engine {engine!r} is not installed on this broker"
            )
            out.append(entry)
        return {"voices": out}

    async def voice_synthesize(self, voice_id: str, text: str,
                               client_gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
        """Synthesize ``text`` in ``voice_id`` via its engine's venv. Takes the GPU
        gate and evicts ALL heavy models (the engine needs the whole card), then runs
        the short-lived engine subprocess — mirroring ``_run_media``, a caller who hangs up
        (`client_gone`) included: the job is cancelled, which kills the engine and removes its
        directory (voice.run_voice_job's finally), and ClientGone is raised once the gate is
        free."""
        self._require_voice()
        reg = voice.load_registry(self.settings.voice_registry_path())
        v = next((x for x in reg.get("voices", []) if x.get("voice_id") == voice_id), None)
        if v is None:
            raise ValueError(f"unknown voice {voice_id!r}")
        ecfg = self.settings.voice_engines().get(v.get("engine"))
        if ecfg is None:
            raise ValueError(f"engine {v.get('engine')!r} not configured for {voice_id!r}")

        async def gated() -> tuple[bytes, list[str]]:
            async with self.gate.hold(model=f"voice:{voice_id}", source="Voice Studio"):
                evicted = await self._evict_other_heavy()
                self._media_active = {"op": "voice", "model": voice_id}
                try:
                    wav = await voice.run_voice_job(
                        python_exe=ecfg["python"], entry=ecfg["entry"], cwd=ecfg.get("cwd"),
                        voice_id=voice_id, text=text, timeout=self.settings.voice_timeout,
                    )
                finally:
                    self._media_active = None
            return wav, evicted

        wav, evicted = await self._while_there(
            gated(), client_gone, every=_GONE_TICK_S,
            doing="its voice job waited for the GPU or ran")
        return {
            "voice_id": voice_id,
            "engine": v.get("engine"),
            "audio": base64.b64encode(wav).decode("ascii"),
            "evicted": evicted,
        }
