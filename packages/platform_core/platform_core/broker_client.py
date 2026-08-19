"""Async client apps use to reach the GPU/Model Broker.

This is the ONLY sanctioned way for a platform app to do model work. Apps must
not import Ollama SDKs or hit ``localhost:11434`` themselves; routing everything
through the broker is what enforces the one-heavy-model VRAM policy.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def _auth_headers() -> dict[str, str]:
    """Bearer header for the broker's control-plane token (BROKER_AUTH_TOKEN), or empty if unset."""
    tok = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


class BrokerClient:
    """Thin async wrapper over the broker's HTTP API.

    Use as an async context manager, or pass a shared ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11500",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = 600.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._auth = _auth_headers()
        # Long default timeout: a cold heavy-model load can take a while.
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def __aenter__(self) -> "BrokerClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._auth:
            kwargs["headers"] = {**self._auth, **(kwargs.get("headers") or {})}
        try:
            resp = await self._client.request(method, path, **kwargs)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise BrokerError(f"broker {method} {path} -> {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise BrokerError(f"broker {method} {path} unreachable: {exc}") from exc
        return resp.json()

    # --- read ---------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/status")

    async def models(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/models")

    async def ps(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/ps")

    async def roles(self) -> dict[str, Any]:
        """Every model role with its pattern + the concrete model it resolves to."""
        return await self._request("GET", "/v1/roles")

    async def set_role(self, role: str, model: str) -> dict[str, Any]:
        """Repoint a role to a new model name / glob (persisted to the broker's roles.json)."""
        return await self._request("PUT", f"/v1/roles/{role}", json={"model": model})

    async def disabled(self) -> list[str]:
        """Admin-disabled model names (availability control; still served if a role uses one)."""
        return (await self._request("GET", "/v1/disabled")).get("disabled", [])

    async def set_disabled(self, names: list[str]) -> dict[str, Any]:
        """Replace the full disabled-name set (persisted to the broker's disabled.json)."""
        return await self._request("PUT", "/v1/disabled", json={"names": names})

    # --- GPU control --------------------------------------------------------

    async def load(self, model: str, *, keep_alive: str | int = -1) -> dict[str, Any]:
        return await self._request("POST", "/v1/load", json={"model": model, "keep_alive": keep_alive})

    async def unload(self, model: str) -> dict[str, Any]:
        return await self._request("POST", "/v1/unload", json={"model": model})

    async def cancel(self, seq: int) -> dict[str, Any]:
        """Cancel a queued/active GPU job by its queue seq."""
        return await self._request("POST", "/v1/cancel", json={"seq": seq})

    # --- inference ----------------------------------------------------------

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if format is not None:
            payload["format"] = format
        return await self._request("POST", "/v1/chat", json=payload)

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        return await self._request("POST", "/v1/embed", json={"model": model, "input": text})

    # --- media --------------------------------------------------------------

    async def image(
        self,
        prompts: list[str],
        *,
        negative_prompt: str | None = None,
        steps: int = 4,
        size: int = 512,
    ) -> dict[str, Any]:
        """Generate images (SDXL-Turbo). Returns {"images": [<b64 png>|None, ...]}.
        Caller owns the full prompt; the broker imposes no template."""
        payload: dict[str, Any] = {"prompts": prompts, "steps": steps, "size": size}
        if negative_prompt is not None:
            payload["negative_prompt"] = negative_prompt
        return await self._request("POST", "/v1/image", json=payload)

    async def tts_light(
        self,
        text: str,
        *,
        voice: str | None = None,
        lang_code: str | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        """Kokoro-82M speech. Returns {"audio_b64", "sample_rate", "voice", "lang"}.

        This is the path platform-wide voice runs on, and the reason is structural: unlike
        ``tts()`` below it takes neither the GPU gate nor an eviction, so it can be called
        mid-conversation from any rail without queueing behind a chat completion or unloading
        the model the user is talking to.

        Every argument is optional. Omitted voice/lang resolve from the broker's own settings
        (BROKER_KOKORO_VOICE / BROKER_KOKORO_LANG_CODE), which is what makes the platform voice
        changeable for everyone without redeploying a caller. The response echoes what was
        actually used.

        Pass voice and lang_code TOGETHER or not at all: Kokoro voice ids are language-scoped by
        prefix, so a Spanish voice under the English phonemiser produces noise, not an accent.
        """
        payload: dict[str, Any] = {"text": text}
        if voice:
            payload["voice"] = voice
        if lang_code:
            payload["lang_code"] = lang_code
        if speed is not None:
            payload["speed"] = speed
        return await self._request("POST", "/v1/tts_light", json=payload)

    async def transcribe(
        self,
        audio_b64: str,
        *,
        suffix: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """faster-whisper speech-to-text. Returns {"text", "language", "duration", "model"}.

        Runs CPU/int8 in the media worker, so it is off the card entirely: it neither evicts the
        resident model nor waits behind GPU work. That matters more here than for TTS — the user
        has just stopped speaking and is watching for their words to appear.

        ``language`` is a hint (e.g. "es"); leave it unset to let whisper detect. It is only
        honoured by a MULTILINGUAL model — an English-only ``.en`` build ignores it silently and
        returns English-shaped nonsense, which is why the broker default is guarded by a test.
        """
        payload: dict[str, Any] = {"audio_b64": audio_b64}
        if suffix:
            payload["suffix"] = suffix
        if language:
            payload["language"] = language
        return await self._request("POST", "/v1/transcribe", json=payload)

    async def tts(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize speech (XTTS v2) for an ordered segment list. Returns
        {"audio_b64", "sample_rate", "timings"}. Each segment is
        {"lang": "en"|"es"|"pause", "text": ..., "duration"?: ...}."""
        return await self._request("POST", "/v1/tts", json={"segments": segments})

    async def tts_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize many independent clips to separate wavs in one XTTS load.
        Returns {"audios": [<b64 wav>, ...]}. Each item is {"lang":.., "text":..}."""
        return await self._request("POST", "/v1/tts_batch", json={"items": items})
