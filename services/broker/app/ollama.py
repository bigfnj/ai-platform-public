"""Thin async client over the Ollama HTTP API.

Only the calls the broker needs. This is the one module that knows Ollama's wire
format; everything above it speaks the broker's own vocabulary so the backend
can be swapped (vLLM, a remote box) later without touching the API layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import re
import shutil
from typing import Any, AsyncIterator, Callable

import httpx

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0


def _normalize_keep_alive(value: str | int | None) -> str | int | None:
    """Coerce keep_alive to what Ollama accepts.

    Ollama wants either an integer number of seconds (with -1 = keep resident
    forever, 0 = unload now) or a Go duration string like "5m". A plain-integer
    *string* such as "-1" is NOT a valid duration and makes Ollama return a 400
    with an empty body, so convert those to ints; leave real durations alone.
    """
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value  # a duration like "5m" / "1h30m"
    return value


_GLOB_CHARS = "*?[]"
_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def _version_key(name: str) -> tuple[int, ...]:
    """Version tuple from the family part of a model name (before ':').
    'mistral-small3.2:24b' -> (3, 2); 'qwen3:30b-a3b' -> (3,); 'gpt-oss:20b' -> (0,)."""
    family = name.split(":", 1)[0]
    found = _VERSION_RE.findall(family)
    if not found:
        return (0,)
    return tuple(int(p) for p in found[-1].split("."))


def _param_size(model: dict[str, Any]) -> float:
    """Billions of parameters, from Ollama's details.parameter_size ('30.5B'),
    falling back to a leading number in the tag (':30b-a3b' -> 30). 0.0 if unknown."""
    ps = (model.get("details") or {}).get("parameter_size")
    if isinstance(ps, str):
        m = re.match(r"\s*([\d.]+)", ps)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    name = model.get("name", "")
    tag = name.split(":", 1)[1] if ":" in name else ""
    m = re.match(r"(\d+(?:\.\d+)?)", tag)
    return float(m.group(1)) if m else 0.0


def rank_key(model: dict[str, Any]) -> tuple[Any, ...]:
    """The broker's ONE definition of "best" among installed candidates: highest version,
    then largest parameter size.

    Extracted from resolve_ollama_model's own sort so the model-fallback resolver ranks
    substitutes exactly as a glob does. Two orderings would mean a role and its own pattern
    disagreeing about which installed model is better, which is the kind of difference nobody
    notices until it picks the wrong one.
    """
    return (_version_key(model.get("name", "")), _param_size(model))


def resolve_ollama_model(pattern: str, tags_fn: Callable[[], list[dict[str, Any]]]) -> str:
    """Resolve a model glob to a concrete installed Ollama model name.

    A plain name (no glob character) is returned unchanged — ``tags_fn`` is not even
    called. A glob is matched (fnmatch) against installed model names; among the
    matches the HIGHEST VERSION wins, tie-broken by the LARGEST parameter size. Raises
    ValueError loudly (listing what's installed) if a glob matches nothing.

    Lives in the Ollama client on purpose: globbing is Ollama-specific, so it only
    ever runs on the Ollama backend and the broker's other layers stay provider-agnostic.

    FOOTGUN (locked by tests/test_llm_client.py): version — not capability — decides,
    so an UNSCOPED family glob can pick a smaller, newer release: ``llama3*`` resolves
    to ``llama3.2:3b`` over the more capable ``llama3.1:8b``. Scope the glob with the
    size tag (e.g. ``mistral-small3*:24b``) so every match is the same size and only
    the version floats.
    """
    if not any(c in pattern for c in _GLOB_CHARS):
        return pattern
    models = tags_fn()
    matches = [m for m in models if fnmatch.fnmatch(m.get("name", ""), pattern)]
    if not matches:
        installed = sorted(m.get("name", "") for m in models)
        raise ValueError(
            f"no installed Ollama model matches pattern {pattern!r}. Installed: {installed}"
        )
    matches.sort(key=rank_key, reverse=True)
    return matches[0].get("name", "")


class OllamaClient:
    def __init__(self, base_url: str, timeout: float | None = 600.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def version(self) -> str:
        resp = await self._client.get("/api/version")
        resp.raise_for_status()
        return resp.json().get("version", "unknown")

    async def tags(self) -> list[dict[str, Any]]:
        """Installed models."""
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def ps(self) -> list[dict[str, Any]]:
        """Currently loaded models (with VRAM footprint)."""
        resp = await self._client.get("/api/ps")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def show(self, model: str) -> dict[str, Any]:
        """Model metadata from /api/show — notably ``capabilities`` (e.g. ["completion",
        "vision", "tools"]). Reads the manifest only; does NOT load the model into VRAM."""
        resp = await self._client.post("/api/show", json={"model": model})
        resp.raise_for_status()
        return resp.json()

    async def generate_warm(self, model: str, keep_alive: str | int) -> dict[str, Any]:
        """Warm a *generative* model into VRAM with no actual generation.

        Empty prompt + a positive keep_alive (-1 = resident forever, "5m", ...)
        loads the model. NOTE: keep_alive=0 here is an unreliable *unload* (it can
        be a no-op); use ``stop()`` to evict instead.
        """
        resp = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": "", "keep_alive": _normalize_keep_alive(keep_alive)},
        )
        resp.raise_for_status()
        return resp.json()

    async def stop(self, model: str) -> None:
        """Evict a model from VRAM, reliably.

        ``ollama stop`` is the dependable eviction path (the empty-prompt
        keep_alive=0 API call is a known no-op in practice). Falls back to the API
        if the CLI isn't on PATH (e.g. a remote Ollama). Best-effort: never raises.
        """
        exe = shutil.which("ollama")
        if exe is not None:
            # Spawn and wait in separate try blocks so the finally cannot touch an unbound
            # `proc`; same shape as media.py, voice.py and gpu.py.
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    exe, "stop", model,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                proc = None
            if proc is not None:
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=30.0)
                    if proc.returncode == 0:
                        return
                # OSError as well as the timeout, or a pipe error out of communicate()
                # escapes the "never raises" contract above and turns a best-effort
                # VRAM reclaim into a failed job. Falling through reaches the HTTP
                # unload, which is the documented fallback.
                except (OSError, asyncio.TimeoutError):
                    pass
                finally:
                    # Reap on every abandoned path. This is the most consequential of the
                    # four spawn sites: stop() is called from _evict_other_heavy() before
                    # EVERY media/image/tts/voice job, and its entire purpose is to free
                    # VRAM. A CancelledError during /v1/cancel — a BaseException, which
                    # `except asyncio.TimeoutError` never sees — orphaned an `ollama stop`
                    # while the GPU gate released underneath it, which is precisely the
                    # failure media.py's comment describes: the next job then loads a second
                    # heavy model onto a card the broker believes is empty.
                    if proc.returncode is None:
                        proc.kill()
                        with contextlib.suppress(BaseException):
                            await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT)
        # Fallback: API unload (may be a no-op, but better than nothing remotely).
        try:
            await self._client.post("/api/generate", json={"model": model, "keep_alive": 0})
        except httpx.HTTPError:
            pass

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
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = _normalize_keep_alive(keep_alive)
        if format is not None:
            payload["format"] = format
        if think is not None:
            payload["think"] = think
        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()

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
        """Streaming twin of ``chat``: keeps Ollama's token stream ON and yields each
        NDJSON chunk as it arrives. Each chunk is one Ollama frame
        ({"message": {"content": "..."}, "done": false} ... final {"done": true}).
        Used only by the broker's streaming endpoint; the buffered ``chat`` is unchanged.

        ``think`` matters MORE here than on the buffered twin: the streaming path is the one
        openmaic uses, and it is the caller that turns thinking off whenever it asks for a
        format. Dropped here, that instruction was inert and a reasoning model's ``<think>``
        preamble arrived as course text with no error anywhere."""
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = _normalize_keep_alive(keep_alive)
        if format is not None:
            payload["format"] = format
        if think is not None:
            payload["think"] = think
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if line:
                    yield json.loads(line)

    # --- OpenAI-compatible pass-through -------------------------------------
    #
    # Ollama serves its OWN OpenAI-compatible surface at /v1/* on this same port,
    # so the broker forwards the caller's body rather than translating it. Only
    # `model` is rewritten (@role / glob -> concrete). Everything the caller sent
    # -- tools, tool_choice, vision content parts, response_format, seed, stop,
    # logprobs -- reaches Ollama untouched and keeps working as Ollama improves.
    #
    # Translating instead would mean re-deriving that surface here and letting it
    # rot: every parameter not explicitly mapped would be silently dropped, which
    # is the failure mode a client cannot see.

    async def openai_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """Buffered OpenAI chat completion. `body` is the caller's, model already resolved."""
        resp = await self._client.post("/v1/chat/completions", json={**body, "stream": False})
        resp.raise_for_status()
        return resp.json()

    async def openai_chat_stream(self, body: dict[str, Any]) -> AsyncIterator[bytes]:
        """Streaming twin. Relays Ollama's SSE frames through unparsed.

        Deliberately not parsed and re-serialised: an OpenAI client reads the wire
        format, and every re-encode is a chance to change it. `[DONE]` included --
        it is Ollama's to send and the client's to see.

        `aiter_bytes`, not `aiter_raw`: raw yields the body still wearing its
        transfer Content-Encoding. The broker re-frames this into its OWN response
        with its own headers and does not forward that encoding, so relaying raw
        would hand a client gzip bytes labelled `text/event-stream`.
        """
        async with self._client.stream(
            "POST", "/v1/chat/completions", json={**body, "stream": True}
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    async def openai_embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post("/v1/embeddings", json=body)
        resp.raise_for_status()
        return resp.json()

    async def embed(
        self,
        model: str,
        text: str | list[str],
        *,
        keep_alive: str | int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "input": text}
        if keep_alive is not None:
            payload["keep_alive"] = _normalize_keep_alive(keep_alive)
        resp = await self._client.post("/api/embed", json=payload)
        resp.raise_for_status()
        return resp.json()
