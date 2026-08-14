"""Broker orchestration: the serialized GPU gate + one-heavy-model policy.

This is the keystone. Every heavy GPU operation (chat, load) passes through a
single async gate so two apps can never both trigger a 15 GB load at once. Before
a heavy model is served or loaded, any *other* heavy model is evicted, leaving at
most one heavy model resident. Embedding models are light and may stay loaded
alongside, so embeds do not take the heavy gate.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app import gpu, media
from app.config import BrokerSettings
from app.ollama import OllamaClient, resolve_ollama_model
from app.registry import classify

# Non-Ollama image backends the media worker can load. A media @role (e.g. @recipe-icon)
# resolves to one of these via roles.json, expanded WITHOUT Ollama glob resolution.
MEDIA_IMAGE_BACKENDS = ("sdxl-turbo", "flux-schnell")

# Per-rail @role -> friendly rail name, so a queued job can show which rail it's for. Rails
# call the broker with their own per-rail role, so the role identifies the rail (no per-rail
# wiring needed). Generic classes (@chat, @vision, …) and manual loads map to no rail.
ROLE_RAIL = {
    "edu": "EDU-Suite", "iep": "IEP",
    "recipe": "Recipe Book", "recipe-vision": "Recipe Book", "recipe-icon": "Recipe Book",
    "terminal-fun": "Terminal Fun",
    "ai-playground": "AI Playground",
    "smb-partner-rag": "SMB Partner Enablement", "smb-partner-voice": "SMB Partner Enablement",
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
        out: list[dict[str, Any]] = []
        for role, pattern in sorted(roles.items()):
            try:
                resolved: str | None = resolve_ollama_model(pattern, lambda: tags)
            except ValueError:
                resolved = None  # a glob with no installed match
            out.append({
                "role": role,
                "pattern": pattern,
                "resolved": resolved,
                "installed": bool(resolved) and resolved in installed,
                "class": self._class(resolved) if resolved else None,
            })
        return out

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
            # is the only way a client can tell the GPU is busy on media.
            "media": {"enabled": self.settings.media_enabled, "active": self._media_active},
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
        if model.startswith("@"):
            model = self.settings.roles().get(model[1:], model[1:])
        if not any(c in model for c in "*?[]"):
            return model
        tags = await self.ollama.tags()
        return resolve_ollama_model(model, lambda: tags)

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
        model = await self._resolve(model)
        keep_alive = self.settings.default_load_keep_alive if keep_alive is None else keep_alive
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
    ) -> dict[str, Any]:
        rail = self._rail_for(model)
        model = await self._resolve(model)
        async with self.gate.hold(model=model, source=rail):
            await self._evict_other_heavy(keep=model)
            return await self.ollama.chat(
                model, messages, options=options, keep_alive=keep_alive, format=format
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
        model = await self._resolve(model)
        async with self.gate.hold(model=model, source=rail):
            await self._evict_other_heavy(keep=model)
            async for chunk in self.ollama.chat_stream(
                model, messages, options=options, keep_alive=keep_alive, format=format
            ):
                yield chunk

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        # Embeddings are light and coexist with a heavy model, so no gate.
        model = await self._resolve(model)
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

    # --- media (gated; VRAM reclaimed by the worker process exiting) ---------

    def _require_media(self) -> None:
        if not self.settings.media_enabled:
            raise RuntimeError("media is disabled (set BROKER_MEDIA_ENABLED=true)")

    async def _run_media(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Gate + evict all heavy models (media needs the whole card) + run the
        worker, which exits to reclaim VRAM."""
        self._require_media()
        async with self.gate.hold(model=spec.get("model"), source=spec.get("source")):
            evicted = await self._evict_other_heavy()
            self._media_active = {"op": spec.get("op"), "model": spec.get("model")}
            try:
                result = await media.run_media_job(
                    python_exe=self.settings.media_python,
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
