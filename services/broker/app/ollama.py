"""Thin async client over the Ollama HTTP API.

Only the calls the broker needs. This is the one module that knows Ollama's wire
format; everything above it speaks the broker's own vocabulary so the backend
can be swapped (vLLM, a remote box) later without touching the API layer.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import shutil
from typing import Any, AsyncIterator, Callable

import httpx


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
    matches.sort(key=lambda m: (_version_key(m.get("name", "")), _param_size(m)), reverse=True)
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
            try:
                proc = await asyncio.create_subprocess_exec(
                    exe, "stop", model,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30.0)
                if proc.returncode == 0:
                    return
            except (OSError, asyncio.TimeoutError):
                pass
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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = _normalize_keep_alive(keep_alive)
        if format is not None:
            payload["format"] = format
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
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming twin of ``chat``: keeps Ollama's token stream ON and yields each
        NDJSON chunk as it arrives. Each chunk is one Ollama frame
        ({"message": {"content": "..."}, "done": false} ... final {"done": true}).
        Used only by the broker's streaming endpoint; the buffered ``chat`` is unchanged."""
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = _normalize_keep_alive(keep_alive)
        if format is not None:
            payload["format"] = format
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if line:
                    yield json.loads(line)

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
