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
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, NamedTuple

from app import gpu, media, voice
from app.config import BrokerSettings
from app.ollama import OllamaClient, rank_key, resolve_ollama_model
from app.registry import classify
from app.upstream import UpstreamClient, UpstreamError  # noqa: F401  (UpstreamError re-exported)
from platform_core.model_catalog import category_of

# Non-Ollama image backends the media worker can load. A media @role (e.g. @recipe-icon)
# resolves to one of these via roles.json, expanded WITHOUT Ollama glob resolution.
MEDIA_IMAGE_BACKENDS = ("sdxl-turbo", "flux-schnell")
# Ceiling on each remote read that roles_view makes for a delegated role. A read view has to
# answer well inside its callers' timeouts (30 s for every rail, 20 s for rail_smoke); see the
# probe in roles_view for why a slow remote must not be allowed to blank every chip.
_VIEW_PROBE_TIMEOUT = 8.0

# Per-rail @role -> friendly rail name, so a queued job can show which rail it's for. Rails
# call the broker with their own per-rail role, so the role identifies the rail (no per-rail
# wiring needed). Generic classes (@chat, @vision, …) and manual loads map to no rail.
ROLE_RAIL = {
    "edu": "EDU-Suite", "iep": "IEP", "job-aid": "Job Aid",
    "finance-chat": "Finance", "finance-fraud": "Finance",
    "recipe": "Recipe Book", "recipe-vision": "Recipe Book", "recipe-icon": "Recipe Book",
    "bouquet-vision": "Bouquet", "bouquet-writer": "Bouquet",
    "terminal-fun": "Terminal Fun",
    "ai-playground": "AI Playground",
}


def _strip_latest(name: str) -> str:
    """Drop an explicit ``:latest`` TAG -- only that tag, never any tag containing the word.

    Ollama reports an untagged pull as ``name:latest``, so ``bge-m3`` and ``bge-m3:latest`` are
    one model. Same narrow rule as the rails' generated ``modelstate._same()``: widening it made
    ``qwen3:8b-latest`` satisfy a role pinned to ``qwen3:4b``.
    """
    return name[: -len(":latest")] if name.endswith(":latest") else name


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


class UnknownRoleError(RuntimeError):
    """A caller named an '@role' that is in no role map.

    Its own type because of what shipped instead: `_resolve` returned the literal string, so
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


class Broker:
    def __init__(self, settings: BrokerSettings) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings.ollama_base_url, timeout=settings.ollama_timeout)
        self.gate = GpuGate()
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

    async def aclose(self) -> None:
        await self.ollama.aclose()

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

    async def list_models(self) -> list[dict[str, Any]]:
        models = await self.ollama.tags()
        disabled = self.settings.disabled()   # admin availability flag (hot-read)
        out: list[dict[str, Any]] = []
        for m in models:
            name = m.get("name", "")
            cls = self._class(name)
            # Only generative models can be vision-capable; skip the /api/show probe for
            # embedders (they never are, and probing them is wasted work).
            caps = await self._capabilities(name, m.get("digest")) if cls != "embed" else []
            out.append({
                "name": m.get("name"),
                "size": m.get("size"),
                "class": cls,
                "family": (m.get("details") or {}).get("family"),
                "parameter_size": (m.get("details") or {}).get("parameter_size"),
                "capabilities": caps,
                "vision": "vision" in caps,
                "disabled": name in disabled,
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
        roles = self.settings.roles()
        try:
            tags = await self.ollama.tags()
        except Exception:  # noqa: BLE001 - a read view must never raise
            tags = []
        installed = {m.get("name", "") for m in tags}
        # Probed once per upstream for the whole sweep, not once per delegated role: these are
        # network round trips and several roles commonly share a box. Cached as
        # (inventory, {resident model names}, {remote role -> row}); inventory is None when the
        # remote could not be read at all.
        #
        # BOUNDED, because this is a read view and its callers are impatient: every rail's
        # /v1/roles client gives up at 30 s and rail_smoke at 20 s. Each remote read gets
        # _VIEW_PROBE_TIMEOUT, the reads for one box run together, and all boxes are probed at
        # once, so a sweep costs at most healthz (5 s) plus one bounded read, however many
        # upstreams there are. Read in series at the client's 30 s default, one slow remote took
        # /v1/roles past those timeouts, and then EVERY chip on every rail went red, local ones
        # included, because the rails read a failed view as "broker unreachable".
        Probe = tuple[list[dict[str, Any]] | None, set[str], dict[str, dict[str, Any]]]
        remote: dict[str, Probe] = {}
        delegated = [self.settings.delegate_ref(p) for p in roles.values() if "::" in p]
        wants_roles = {name for name, rest in delegated if name and rest.startswith("@")}

        async def _bounded(coro):
            return await asyncio.wait_for(coro, timeout=_VIEW_PROBE_TIMEOUT)

        async def _probe(name: str) -> Probe:
            # healthy() reads /healthz, which is exempt from the token gate on every broker, so
            # it still answers when our token is wrong. That distinction is the point: "the box
            # is down" and "the box refuses us" need different fixes. It is also the cheap gate:
            # a box that is down is not dialled again for the reads.
            try:
                client = self._upstream(name)
                if not await client.healthy():
                    return None, set(), {}
            except Exception:  # noqa: BLE001 - a read view must never raise
                return None, set(), {}
            reads = [_bounded(client.models()), _bounded(client.status())]
            if name in wants_roles:
                reads.append(_bounded(client.roles()))
            got = await asyncio.gather(*reads, return_exceptions=True)
            models_r, status_r = got[0], got[1]
            roles_r = got[2] if len(got) > 2 else []
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
            return inventory, resident, rroles

        names = sorted({name for name, _rest in delegated if name})
        for name, probe in zip(names, await asyncio.gather(*(_probe(n) for n in names))):
            remote[name] = probe

        out: list[dict[str, Any]] = []
        for role, pattern in sorted(roles.items()):
            # BEFORE the media branch: a delegated pattern is the upstream's business, and
            # asking whether it is installed HERE is the wrong question in both cases.
            if "::" in pattern:
                up_name, rest = self.settings.delegate_ref(pattern)
                if up_name is not None:
                    inventory, resident, rroles = remote.get(up_name, (None, set(), {}))
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
            # installed either, whatever the interpreter path says.
            if pattern in MEDIA_IMAGE_BACKENDS:
                media_py = str(getattr(self.settings, "media_python", "") or "")
                out.append({"role": role, "pattern": pattern, "resolved": pattern,
                            "installed": bool(getattr(self.settings, "media_enabled", True))
                            and bool(media_py) and Path(media_py).exists(),
                            "class": "image", "upstream": "local",
                            "serving": pattern, "fallback": None})
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
                                                     announce=False, tags=tags)
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
        return out

    async def upstreams_view(self) -> list[dict[str, Any]]:
        """The registry plus live reachability, for the admin panel. Tokens are NEVER
        included — the panel needs to know a box is configured and answering, not how to
        authenticate to it."""
        out: list[dict[str, Any]] = [{"name": "local", "url": "", "healthy": True}]
        for name, spec in sorted(self.settings.upstreams().items()):
            try:
                ok = await self._upstream(name).healthy()
            except Exception:  # noqa: BLE001 - a read view must never raise
                ok = False
            out.append({"name": name, "url": spec["url"], "healthy": ok})
        return out

    async def models_view(self, upstream: str | None = None) -> list[dict[str, Any]]:
        """Installed models on THIS box, or on a named upstream. The admin's model picker
        needs the remote's inventory to offer a sensible choice for a delegated role — this
        box's list is exactly the wrong one to show there."""
        if not upstream or upstream == "local":
            return await self.list_models()
        if upstream not in self.settings.upstreams():
            raise ValueError(f"unknown upstream {upstream!r}")
        return await self._upstream(upstream).models()

    async def status(self) -> dict[str, Any]:
        reachable = True
        version = None
        loaded: list[dict[str, Any]] = []
        try:
            version = await self.ollama.version()
            loaded = await self.list_loaded()
        except Exception:  # noqa: BLE001 - status must never raise
            reachable = False
        return {
            "ollama_reachable": reachable,
            "ollama_version": version,
            "loaded": loaded,
            "heavy_loaded": [m["name"] for m in loaded if m["class"] == "heavy"],
            "gpu": await gpu.vram(),
            "queue": self.gate.depth(),
            # Persistent, because a job lives for seconds: a marker that appears for four of
            # them is not a report. This lets the widget stay warning-toned with an empty queue.
            "fallbacks": {"count": self._fallback_count, "last": self._last_fallback},
            # Live job queue (active + waiting), oldest first: {seq, model, source(rail), state}.
            "jobs": self.gate.jobs(),
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
        for m in await self.ollama.ps():
            name = m.get("name", "")
            if name and name != keep and self._class(name) == "heavy":
                await self.ollama.stop(name)
                evicted.append(name)
        return evicted

    async def _resolve(self, model: str) -> str:
        """Resolve a model reference to a concrete installed model. A leading '@' is a
        ROLE/class alias ('@chat', '@reasoning') expanded via the broker's role map; the
        result (a plain name or a glob) is then glob-resolved. Only the Ollama backend
        globs (per design); a plain name passes straight through with no tags() round-trip."""
        name, _fb = await self.resolve_ref(model, substitute=False)
        return name

    def _upstream(self, name: str) -> UpstreamClient:
        spec = self.settings.upstreams()[name]
        return UpstreamClient(name, spec["url"], spec.get("token", ""),
                              timeout=self.settings.ollama_timeout)

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
        value = self.settings.roles().get(ref[1:], ref) if ref.startswith("@") else ref
        # Same free pre-check as the guard in resolve_ref: no model name contains `::`, so the
        # registry is only read for a reference that could actually be delegated.
        if "::" in value:
            up_name, rest = self.settings.delegate_ref(value)
            if up_name is not None:
                return self._upstream(up_name), rest, None
        return (None, *await self.resolve_ref(ref, substitute=substitute))

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

    async def _plan_substitute(self, role: str, wanted: str, reason: str) -> Fallback:
        """Choose the best installed, ENABLED, capability-compatible substitute, or raise.

        Deliberately SILENT. Read views (roles_view, audit_roles) call this to DESCRIBE what
        would happen, and a diagnostic that writes a "we are degraded" line every time an admin
        opens the Rails tab is a line nobody reads by Tuesday. Announcing is _announce's job,
        on the path that actually runs.
        """
        category = category_of(wanted, klass=self._class(wanted))
        disabled = self.settings.disabled()
        tags = await self.ollama.tags()
        self._digests = {str(m.get("name") or ""): m.get("digest") for m in tags}

        scored: list[tuple[tuple[Any, ...], str]] = []
        for m in tags:
            name = str(m.get("name") or "")
            if not name or name == wanted or name in disabled:
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

    async def installed_snapshot(self, ref: str) -> list[dict[str, Any]] | None:
        """One installed-model read to hand to several `resolve_ref` calls in ONE request.

        `OllamaClient.tags()` is an uncached GET, and `resolve_ref` reads it for every
        '@role' as well as every glob -- not glob-only. So a surface that resolves the
        same ref twice on purpose (the /openai/v1 chat path does; see `openai_compat`)
        was paying two `GET /api/tags` for one answer, on the hot path.

        `None` means "let `resolve_ref` fetch its own". This is a HINT, not a second copy
        of the resolution rules: if it disagrees with `resolve_ref`'s early returns in
        either direction the resolved answer is identical and only a round trip is won or
        lost.

        DO NOT hold the result across requests. A snapshot that outlives a load, an unload
        or a pull can resolve a glob to a model that is no longer there, and the caller
        then meets a 404 from Ollama for a name the broker just said was fine. Per-request
        only -- that is the whole safety argument for reusing it at all.
        """
        if not ref.startswith("@") and not any(c in ref for c in "*?[]"):
            return None
        return await self.ollama.tags()

    async def resolve_ref(self, ref: str, *, substitute: bool, announce: bool = True,
                          tags: list[dict[str, Any]] | None = None
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
        this made 24 round trips where one would do.

        DELEGATION IS NOT HANDLED HERE -- see `route()`, and the guard immediately below.
        """
        if not ref.startswith("@"):
            if not any(c in ref for c in "*?[]"):
                return ref, None
            tags = tags if tags is not None else await self.ollama.tags()
            return resolve_ollama_model(ref, lambda: tags), None

        role = ref[1:]
        roles = self.settings.roles()
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
            up_name, _rest = self.settings.delegate_ref(pattern)
            if up_name is not None:
                raise DelegatedRefError(ref, up_name)

        # BEFORE the installed check, or every image role falls back to a chat model: a media
        # backend is loaded by the media worker from the HF cache and never appears in tags().
        if pattern in MEDIA_IMAGE_BACKENDS:
            return pattern, None

        tags = tags if tags is not None else await self.ollama.tags()
        installed = {str(m.get("name") or "") for m in tags}
        self._digests = {str(m.get("name") or ""): m.get("digest") for m in tags}
        try:
            name = resolve_ollama_model(pattern, lambda: tags)
        except ValueError:
            if not substitute:
                raise
            fb = await self._plan_substitute(role, pattern, "not installed")
            if announce:
                self._announce(fb)
            return fb.model, fb

        # `:latest` tolerance is mandatory: Ollama reports an untagged pull that way, so
        # @embed -> 'bge-m3' is installed as 'bge-m3:latest'. audit_roles already does this.
        if name not in installed and f"{name}:latest" not in installed:
            if not substitute:
                return name, None
            fb = await self._plan_substitute(role, name, "not installed")
            if announce:
                self._announce(fb)
            return fb.model, fb

        if name in self.settings.disabled():
            if not substitute:
                return name, None
            fb = await self._plan_substitute(role, name, "disabled")
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
            # correctly configured image role as missing.
            if pattern in MEDIA_IMAGE_BACKENDS:
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
                name = await self._resolve(f"@{role}")
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
                           f"{await self._would_fall_back(role, pattern, 'not installed')}{src}")
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
                           f"{await self._would_fall_back(role, name, 'not installed')}{src}")
                continue
            # The audit reads `disabled` and the hot path no longer refuses on it, so a role
            # pointing at a disabled model is a CONFIGURATION fault that has to be named
            # somewhere. Startup is that somewhere -- this is one of the five things that stop
            # a working fallback from making a broken map look healthy.
            if name in self.settings.disabled():
                out.append(f"@{role} -> '{name}' is DISABLED by the admin"
                           f"{await self._would_fall_back(role, name, 'disabled')}{src}")
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

    async def _would_fall_back(self, role: str, wanted: str, reason: str) -> str:
        """The ', will fall back to X' clause for an audit line, or the refusal.

        Calls _plan_substitute and NOT _announce: this is a read view. Announcing here would
        write a 'we are degraded' line per bad role on every startup and every Rails-tab poll,
        and a stream nobody can act on is a stream nobody reads.
        """
        try:
            fb = await self._plan_substitute(role, wanted, reason)
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

    async def resolve_for_external(self, model: str, *,
                                   tags: list[dict[str, Any]] | None = None
                                   ) -> tuple[str, Fallback | None]:
        """Resolve a model reference for an OUTSIDE caller, honouring the admin's
        disable flag.

        `tags` is a pre-fetched installed-model list from `installed_snapshot()`, for the
        caller that resolves up front and then makes a broker call that resolves again.
        Valid within ONE request and nowhere else; the reasons are on that method.

        `_resolve` deliberately does not check `disabled`: a rail only ever picks from
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
        """
        if model.startswith("@"):
            value = self.settings.roles().get(model[1:], model)
            if "::" in value:
                up_name, _rest = self.settings.delegate_ref(value)
                if up_name is not None:
                    raise DelegatedRefError(model, up_name)
            return await self.resolve_ref(model, substitute=True, tags=tags)
        resolved, _fb = await self.resolve_ref(model, substitute=False, tags=tags)
        if resolved in self.settings.disabled():
            raise ModelDisabledError(resolved)
        return resolved, None

    async def openai_chat(self, body: dict[str, Any], *,
                          tags: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Buffered OpenAI chat completion under the GPU gate.

        `tags` is the caller's per-request `installed_snapshot()`, so the resolve below
        reuses the one the surface already paid for instead of repeating the GET."""
        ref = str(body.get("model") or "")
        rail = self._rail_for(ref)
        model, fb = await self.resolve_for_external(ref, tags=tags)
        async with self.gate.hold(model=model, source=rail or "openai-compat",
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            return await self.ollama.openai_chat({**body, "model": model})

    async def openai_chat_stream(self, body: dict[str, Any], *,
                                 tags: list[dict[str, Any]] | None = None
                                 ) -> AsyncIterator[bytes]:
        """Streaming twin. The gate is held for the WHOLE stream, exactly like
        `chat_stream`: the job stays 'active' in the queue until the last frame, so a
        second caller queues behind it rather than racing it onto the card.

        `tags` as in `openai_chat`: the surface's own snapshot, reused for this request."""
        ref = str(body.get("model") or "")
        rail = self._rail_for(ref)
        model, fb = await self.resolve_for_external(ref, tags=tags)
        async with self.gate.hold(model=model, source=rail or "openai-compat",
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            async for chunk in self.ollama.openai_chat_stream({**body, "model": model}):
                yield chunk

    async def openai_embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        """Embeddings are light and coexist with a heavy model, so no gate -- the same
        call `embed()` makes ungated. Still resolved and disable-checked."""
        model, _fb = await self.resolve_for_external(str(body.get("model") or ""))
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
            if m.get("name") and m["name"] not in disabled
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
    ) -> dict[str, Any]:
        rail = self._rail_for(model)
        up, model, fb = await self.route(model, substitute=True)
        if up is not None:
            # No gate and no eviction: this runs on another card, so holding the single local
            # slot would serialise local work behind something that cannot contend with it.
            return await up.chat(model, messages, options=options, keep_alive=keep_alive,
                                 format=format, think=think)
        async with self.gate.hold(model=model, source=rail,
                                  fallback=fb.as_dict() if fb is not None else None):
            await self._evict_other_heavy(keep=model)
            out = await self.ollama.chat(
                model, messages, options=options, keep_alive=keep_alive,
                format=format, think=think,
            )
        # PLATFORM dialect only, and only ON a substitution. Ollama's /api/chat response has
        # no "fallback" key, so there is no collision; a test asserts it is ABSENT on the
        # happy path, or the field decays into decoration nobody reads.
        if fb is not None and isinstance(out, dict):
            out = {**out, "fallback": fb.as_dict()}
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
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming twin of ``chat``: same heavy gate + one-heavy-model eviction, but
        yields Ollama chunks as they arrive so a rail can relay tokens live over a
        WebSocket. The GPU gate is held for the whole stream (the job stays 'active' in
        the queue until the last token), exactly like the buffered path.

        ``think`` is forwarded exactly as the buffered twin forwards it, and for the same
        reason -- a caller that asks for a `format` turns thinking off, and a broker that
        drops that instruction delivers a `<think>` preamble as content. This path is the
        one openmaic streams through, so it was the half that mattered."""
        rail = self._rail_for(model)
        up, model, fb = await self.route(model, substitute=True)
        if up is not None:
            # Relayed frame-for-frame, gate-free for the same reason as chat(). No leading
            # fallback frame: a delegated role never substitutes.
            #
            # `think` is NOT relayed: UpstreamClient.chat_stream has no such parameter
            # (its buffered `chat` does). So a DELEGATED streaming role still runs at the
            # remote model's own thinking default, and a `format` request cannot turn it
            # off from here. Named rather than invented, because the remote is another
            # broker whose own /v1/chat/stream contract is what would have to accept it.
            async for chunk in up.chat_stream(model, messages, options=options,
                                              keep_alive=keep_alive, format=format):
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
            async for chunk in self.ollama.chat_stream(
                model, messages, options=options, keep_alive=keep_alive, format=format,
                think=think,
            ):
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

    async def embed_image(self, images: list[str], model: str | None = None) -> dict[str, Any]:
        """CPU image embeddings (SigLIP) for retrieval-grounding. Runs in the media
        worker but WITHOUT the GPU gate/eviction: it never touches the GPU, so it must
        not disturb a resident heavy model — evicting gemma3 to embed on CPU and then
        reloading it would be pure thrash. Cheap and safe to run alongside a chat."""
        self._require_media()
        spec: dict[str, Any] = {"op": "embed_image", "images": images}
        if model:
            spec["model"] = model
        return await media.run_media_job(
            python_exe=self.settings.media_python,
            spec=spec,
            timeout=self.settings.media_timeout,
        )

    async def tts_light(
        self,
        text: str,
        *,
        voice: str | None = None,
        lang_code: str | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        """Kokoro-82M read-aloud. Same no-gate path as embed_image(), for the same reason:
        it is CPU/ONNX and never touches the card, so holding the GPU gate or evicting the
        resident chat model to speak a sentence would be pure thrash. ~350 MB, loaded and
        dropped per call by the worker exiting.

        This is what makes read-aloud offerable platform-wide — a rail can voice a block of
        text without the request ever queueing behind, or displacing, someone's chat.

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
        return await media.run_media_job(
            python_exe=self._media_python_for("kokoro_tts"),
            spec=spec,
            timeout=self.settings.media_timeout,
        )

    async def transcribe(
        self,
        audio_b64: str,
        *,
        suffix: str | None = None,
        language: str | None = None,
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
        return await media.run_media_job(
            python_exe=self._media_python_for("transcribe"),
            spec=spec,
            timeout=self.settings.media_timeout,
        )

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

    async def _run_media(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Gate + evict all heavy models (media needs the whole card) + run the
        worker, which exits to reclaim VRAM."""
        self._require_media()
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
    ) -> dict[str, Any]:
        """Generate images. Caller owns the full prompt. ``model`` picks the backend
        the worker loads (``sdxl-turbo`` | ``flux-schnell``), or a media @role that resolves
        to one (e.g. ``@recipe-icon``)."""
        rail = self._rail_for(model)
        model = self._resolve_media(model)
        return await self._run_media({
            "op": "image",
            "media_core_src": self.settings.media_core_src,
            "prompts": prompts,
            "negative_prompt": negative_prompt,
            "steps": steps,
            "size": size,
            "model": model,
            "source": rail,
        })

    async def tts(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize speech (XTTS v2) for an ordered segment list (one combined wav)."""
        return await self._run_media({
            "op": "tts",
            "media_core_src": self.settings.media_core_src,
            "voices_dir": self.settings.media_voices_dir,
            "segments": segments,
        })

    async def tts_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize many independent clips to SEPARATE wavs in one worker (XTTS
        loaded once). Returns {"audios": [<b64 wav>, ...]} aligned with ``items``."""
        return await self._run_media({
            "op": "tts_batch",
            "media_core_src": self.settings.media_core_src,
            "voices_dir": self.settings.media_voices_dir,
            "items": items,
        })

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

    async def voice_synthesize(self, voice_id: str, text: str) -> dict[str, Any]:
        """Synthesize ``text`` in ``voice_id`` via its engine's venv. Takes the GPU
        gate and evicts ALL heavy models (the engine needs the whole card), then runs
        the short-lived engine subprocess — mirroring ``_run_media``."""
        self._require_voice()
        reg = voice.load_registry(self.settings.voice_registry_path())
        v = next((x for x in reg.get("voices", []) if x.get("voice_id") == voice_id), None)
        if v is None:
            raise ValueError(f"unknown voice {voice_id!r}")
        ecfg = self.settings.voice_engines().get(v.get("engine"))
        if ecfg is None:
            raise ValueError(f"engine {v.get('engine')!r} not configured for {voice_id!r}")
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
        return {
            "voice_id": voice_id,
            "engine": v.get("engine"),
            "audio": base64.b64encode(wav).decode("ascii"),
            "evicted": evicted,
        }
