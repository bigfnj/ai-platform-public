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
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from app import gpu, media, voice
from app.config import BrokerSettings
from app.ollama import OllamaClient, resolve_ollama_model
from app.registry import classify
from app.upstream import UpstreamClient, UpstreamError

# Non-Ollama image backends the media worker can load. A media @role (e.g. @recipe-icon)
# resolves to one of these via roles.json, expanded WITHOUT Ollama glob resolution.
MEDIA_IMAGE_BACKENDS = ("sdxl-turbo", "flux-schnell")

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
    async def hold(self, model: str | None = None, source: str | None = None) -> AsyncIterator[None]:
        self._seq += 1
        seq = self._seq
        # Keep the running task so an admin can cancel this job (waiting -> drop before it runs;
        # active -> abort the in-flight call). Excluded from the JSON `jobs()` view.
        job = {"seq": seq, "model": model, "source": source, "state": "waiting",
               "since": time.time(), "task": asyncio.current_task()}
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
            caps = []
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
        present. Powers the admin 'Rails' model picker."""
        roles = self.settings.roles()
        try:
            tags = await self.ollama.tags()
        except Exception:  # noqa: BLE001 - a read view must never raise
            tags = []
        installed = {m.get("name", "") for m in tags}
        # A delegated role resolves against the UPSTREAM's inventory, not this card's. Each
        # remote is read once here, not once per role, and a remote that is down degrades that
        # role to resolved=None rather than failing the whole view.
        remote_tags: dict[str, set[str]] = {}
        remote_loaded: dict[str, set[str]] = {}
        out: list[dict[str, Any]] = []
        for role, pattern in sorted(roles.items()):
            up_name, ref = self.settings.delegate_ref(pattern)
            if up_name is not None:
                if up_name not in remote_tags:
                    try:
                        remote_tags[up_name] = {
                            str(m.get("name", "")) for m in await self._upstream(up_name).models()
                        }
                    except UpstreamError:
                        remote_tags[up_name] = set()
                if up_name not in remote_loaded:
                    try:
                        st = await self._upstream(up_name).status()
                        remote_loaded[up_name] = {
                            str(m.get("name") or m.get("model") or "")
                            for m in (st.get("loaded") or [])
                            if isinstance(m, dict)
                        } | {m for m in (st.get("loaded") or []) if isinstance(m, str)}
                    except UpstreamError:
                        remote_loaded[up_name] = set()
                names = remote_tags[up_name]
                try:
                    resolved = resolve_ollama_model(ref, lambda: [{"name": n} for n in names])
                except ValueError:
                    resolved = None
                out.append({
                    "role": role, "pattern": pattern, "upstream": up_name,
                    "resolved": resolved,
                    "installed": bool(resolved) and resolved in names,
                    # Residency on the REMOTE card. Without it a rail's chip resolver, which
                    # compares against THIS box's loaded list, calls a perfectly resident remote
                    # model 'missing' — a red dot on a working rail, which is the same class of
                    # lie the four-state contract exists to prevent, just inverted.
                    "loaded": bool(resolved) and resolved in remote_loaded[up_name],
                    "class": self._class(resolved) if resolved else None,
                })
                continue
            try:
                resolved: str | None = resolve_ollama_model(ref, lambda: tags)
            except ValueError:
                resolved = None  # a glob with no installed match
            out.append({
                "role": role,
                "pattern": pattern,
                "upstream": "local",
                "resolved": resolved,
                "installed": bool(resolved) and resolved in installed,
                "class": self._class(resolved) if resolved else None,
            })
        return out

    async def upstreams_view(self) -> list[dict[str, Any]]:
        """Every broker a role may name, `local` first. Reachability is probed via /healthz,
        which is token-exempt everywhere — so an unreachable box and one that rejects our token
        are distinguishable, and the panel can say which."""
        out: list[dict[str, Any]] = [{"name": "local", "url": None, "reachable": True}]
        for name, spec in sorted(self.settings.upstreams().items()):
            client = self._upstream(name)
            authed = True
            try:
                await client.models()
            except UpstreamError:
                authed = False
            out.append({"name": name, "url": spec["url"],
                        "reachable": await client.healthy(), "authorized": authed})
        return out

    async def models_view(self, upstream: str | None = None) -> list[dict[str, Any]]:
        """Installed models on `upstream` (default: this box). Powers the admin picker once a
        rail has been pointed off-site: the choices have to come from the box that will run it."""
        if not upstream or upstream == "local":
            return await self.list_models()
        if upstream not in self.settings.upstreams():
            raise UpstreamError(f"unknown upstream {upstream!r}")
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
        """Resolve a model reference to a concrete LOCAL model. A leading '@' is a ROLE/class
        alias ('@chat', '@reasoning') expanded via the broker's role map; the result (a plain
        name or a glob) is then glob-resolved. Only the Ollama backend globs (per design); a
        plain name passes straight through with no tags() round-trip.

        Delegation is NOT handled here — see _split(). Callers that can delegate must use that;
        this stays the local-only path so the media and voice routes, which have no remote
        equivalent, keep exactly their previous behaviour."""
        if model.startswith("@"):
            model = self.settings.roles().get(model[1:], model[1:])
        if not any(c in model for c in "*?[]"):
            return model
        tags = await self.ollama.tags()
        return resolve_ollama_model(model, lambda: tags)

    def _upstream(self, name: str) -> UpstreamClient:
        spec = self.settings.upstreams()[name]
        return UpstreamClient(name, spec["url"], spec.get("token", ""),
                              timeout=self.settings.ollama_timeout)

    async def _split(self, model: str) -> tuple[UpstreamClient | None, str]:
        """Expand a reference and decide WHERE it runs: `(upstream_or_None, model_ref)`.

        A role may name a registered remote broker (`offsite::mistral-small3*:24b`). When it
        does, the glob is deliberately left UNRESOLVED and handed over as-is: the upstream knows
        what it has installed and this box does not, so resolving here would match against the
        wrong inventory — and on a small card would usually match nothing at all, turning a
        perfectly good delegation into a missing model.
        """
        if model.startswith("@"):
            model = self.settings.roles().get(model[1:], model[1:])
        name, ref = self.settings.delegate_ref(model)
        if name is None:
            return None, await self._resolve(ref)
        return self._upstream(name), ref

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
            # A DELEGATED role runs on another box, so auditing it against this card's tags()
            # would report every correctly configured off-site role as missing — the exact wall
            # of false warnings this audit exists to avoid. What IS worth saying is when the
            # named upstream is gone, because then the role resolves nowhere at all.
            up_name, _ref = self.settings.delegate_ref(pattern)
            if up_name is not None:
                continue
            if "::" in pattern:
                out.append(f"@{role} -> '{pattern}' names an upstream that is not registered "
                           f"in upstreams.json; it will be resolved locally and will not match")
                continue
            # Only decorates a role that is ALREADY wrong. An inherited default that is
            # installed and fits is the normal case and must stay silent, or the audit turns
            # into a wall of notes about roles nobody needs to touch.
            src = "" if role in overlay else " [inherited from DEFAULT_ROLES; roles.json does not set it]"
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
                out.append(f"@{role} -> '{pattern}' matches NO installed model{src}")
                continue
            except Exception as exc:  # noqa: BLE001 - a diagnostic must not block startup
                # Still best-effort, but it SAYS so now. A check that quietly skips a role is
                # indistinguishable from one that passed it.
                out.append(f"@{role} -> '{pattern}' could not be checked "
                           f"({exc.__class__.__name__}){src}")
                continue
            size = installed.get(name) or installed.get(f"{name}:latest")
            if size is None:
                out.append(f"@{role} -> '{pattern}' resolves to '{name}', which is NOT installed{src}")
                continue
            need_mib = size // (1024 * 1024)
            if total_mib and need_mib > total_mib:
                out.append(f"@{role} -> '{name}' needs ~{need_mib // 1024} GB but the card has "
                           f"{total_mib // 1024} GB{src}")
        if out and total_mib:
            out.append(f"the role map does not fit this {total_mib // 1024} GB card — size one "
                       f"with deploy/installer/modelplan.ps1 -VramGb {total_mib // 1024}")
        return out

    def _resolve_media(self, model: str) -> str:
        """Expand a leading @role via the role map (no Ollama globbing — media backends
        aren't Ollama models). Returns the concrete backend name (the worker validates it)."""
        if model.startswith("@"):
            model = self.settings.roles().get(model[1:], model[1:])
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
        up, model = await self._split(model)
        keep_alive = self.settings.default_load_keep_alive if keep_alive is None else keep_alive
        if up is not None:
            # No local gate and no local eviction: warming a model on another box cannot
            # contend for this card, and holding the single slot for it would stall local work.
            return {**await up.load(model, keep_alive=keep_alive), "upstream": up.name}
        if self._class(model) == "embed":
            # Embedders load via /api/embed; they don't evict heavy models.
            await self.ollama.embed(model, " ", keep_alive=keep_alive)
            return {"model": model, "class": "embed", "evicted": [], "keep_alive": keep_alive}
        async with self.gate.hold(model=model, source=rail):
            evicted = await self._evict_other_heavy(keep=model)
            await self.ollama.generate_warm(model, keep_alive=keep_alive)
            return {"model": model, "class": "heavy", "evicted": evicted, "keep_alive": keep_alive}

    async def unload(self, model: str) -> dict[str, Any]:
        up, model = await self._split(model)
        if up is not None:
            return {**await up.unload(model), "upstream": up.name}
        # `ollama stop` reliably evicts any model (heavy or embedder) from VRAM.
        await self.ollama.stop(model)
        return {"model": model, "unloaded": True}

    def cancel_job(self, seq: int) -> bool:
        """Cancel a queued/active GPU job by its queue seq (admin action via the gateway)."""
        return self.gate.cancel(seq)

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
        up, model = await self._split(model)
        if up is not None:
            return await up.chat(model, messages, options=options, keep_alive=keep_alive,
                                 format=format, think=think)
        async with self.gate.hold(model=model, source=rail):
            await self._evict_other_heavy(keep=model)
            return await self.ollama.chat(
                model, messages, options=options, keep_alive=keep_alive,
                format=format, think=think,
            )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming twin of ``chat``: same heavy gate + one-heavy-model eviction, but
        yields Ollama chunks as they arrive so a rail can relay tokens live over a
        WebSocket. The GPU gate is held for the whole stream (the job stays 'active' in
        the queue until the last token), exactly like the buffered path."""
        rail = self._rail_for(model)
        up, model = await self._split(model)
        if up is not None:
            # Relayed frame-for-frame, and WITHOUT the gate: the queue models this card's single
            # heavy slot, and a remote generation occupies none of it.
            async for chunk in up.chat_stream(model, messages, options=options,
                                              keep_alive=keep_alive, format=format):
                yield chunk
            return
        async with self.gate.hold(model=model, source=rail):
            await self._evict_other_heavy(keep=model)
            async for chunk in self.ollama.chat_stream(
                model, messages, options=options, keep_alive=keep_alive, format=format
            ):
                yield chunk

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        # Embeddings are light and coexist with a heavy model, so no gate.
        up, model = await self._split(model)
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
