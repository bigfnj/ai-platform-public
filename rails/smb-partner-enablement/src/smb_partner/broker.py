"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API (never Ollama directly). The broker owns
the one-heavy-model VRAM policy and @role/wildcard resolution.

Three paths this rail uses:
  * ``chat`` / ``chat_stream`` — the RAG answer, via ``@smb-partner-rag`` (heavy).
  * ``embed``                  — retrieval, via ``@embed`` (light; stays resident alongside).
  * ``tts_light``              — Kokoro-82M TTS, via the broker's media worker WITHOUT eviction.

``tts_light`` uses the ``embed_image()`` precedent: Kokoro (~350 MB ONNX) coexists with the
RAG LLM on the same card, so there is no GPU gate and no model swap per utterance. The legacy
``tts()`` path (XTTS v2) is still present for completeness but is not used by this rail.
"""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

BROKER_URL = os.environ.get("SMB_PARTNER_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")
# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
DEFAULT_TIMEOUT = float(os.environ.get("SMB_PARTNER_BROKER_TIMEOUT", "600"))


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


def _post(path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    try:
        resp = httpx.post(BROKER_URL + path, json=payload, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker POST {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker POST {path} unreachable: {exc}") from exc
    return resp.json()


def _get(path: str, timeout: float = 30.0) -> dict:
    try:
        resp = httpx.get(BROKER_URL + path, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker GET {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker GET {path} unreachable: {exc}") from exc
    return resp.json()


def chat(model: str, messages: list[dict], *, options: dict | None = None,
         fmt: str | dict | None = None, keep_alive: str | int = "30m") -> str:
    """Buffered chat. Returns the assistant message content.

    keep_alive defaults to 30m so the RAG model stays resident between partner questions —
    this rail's whole premise is that the model is already warm when someone speaks.
    """
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    resp = _post("/v1/chat", payload)
    return (resp.get("message") or {}).get("content", "") or ""


async def chat_stream(model: str, messages: list[dict], *, options: dict | None = None,
                      keep_alive: str | int = "30m") -> AsyncIterator[str]:
    """Stream assistant content deltas from the broker's NDJSON /v1/chat/stream."""
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        async with client.stream("POST", BROKER_URL + "/v1/chat/stream", json=payload,
                                 headers=_AUTH) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                frame = json.loads(line)
                if frame.get("error"):
                    raise BrokerError(str(frame["error"]))
                tok = (frame.get("message") or {}).get("content") or ""
                if tok:
                    yield tok
                if frame.get("done"):
                    break


def embed(text: str | list[str], *, model: str) -> list[list[float]]:
    """Embed a string or list of strings via the broker. Returns vectors aligned with input."""
    resp = _post("/v1/embed", {"model": model, "input": text})
    if isinstance(resp.get("embeddings"), list):
        return resp["embeddings"]
    if isinstance(resp.get("embedding"), list):
        return [resp["embedding"]]
    data = resp.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and "embedding" in data[0]:
        return [d["embedding"] for d in data]
    raise BrokerError("embed response had no vectors")


def tts(segments: list[dict], *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """XTTS v2 TTS — evicts all heavy models before running. Legacy path, not used by this rail."""
    return _post("/v1/tts", {"segments": segments}, timeout=timeout)


def tts_light(
    text: str,
    *,
    voice: str | None = None,
    lang_code: str | None = None,
    speed: float | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Kokoro-82M TTS — synthesizes without evicting the resident RAG model.
    Returns {"audio_b64", "sample_rate"}. Raises BrokerError if media is disabled."""
    payload: dict = {"text": text}
    if voice:
        payload["voice"] = voice
    if lang_code:
        payload["lang_code"] = lang_code
    if speed is not None:
        payload["speed"] = speed
    return _post("/v1/tts_light", payload, timeout=timeout)


def transcribe(
    audio_b64: str,
    *,
    suffix: str | None = None,
    language: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """faster-whisper STT — transcribes one utterance without evicting the resident RAG model.
    Returns {"text", "language", "duration"}. Raises BrokerError if media is disabled."""
    payload: dict = {"audio_b64": audio_b64}
    if suffix:
        payload["suffix"] = suffix
    if language:
        payload["language"] = language
    return _post("/v1/transcribe", payload, timeout=timeout)


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth, media availability)."""
    return _get("/v1/status")


def installed_models() -> list[dict]:
    """Every model installed in Ollama, as the broker reports it. Needed to tell a model that
    is merely cold apart from one that is not installed at all — the distinction the chips'
    blue-vs-red states exist to show."""
    resp = _get("/v1/models")
    if isinstance(resp, dict) and isinstance(resp.get("models"), list):
        return resp["models"]
    return resp if isinstance(resp, list) else []


def roles() -> list[dict]:
    """The broker's role table (each role + the concrete model it resolves to)."""
    resp = _get("/v1/roles")
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return resp["roles"]
    return resp if isinstance(resp, list) else []


def resolved_model(name: str) -> str:
    """Resolve a leading-@ role to its concrete model name; pass a concrete name through."""
    if not name or not name.startswith("@"):
        return name
    role = name[1:]
    try:
        for r in roles():
            if r.get("role") == role and r.get("resolved"):
                return r["resolved"]
    except BrokerError:
        pass
    return name


def media_enabled() -> bool:
    """Whether the broker's media worker (XTTS / image) is available for TTS."""
    try:
        return bool((status().get("media") or {}).get("enabled"))
    except BrokerError:
        return False


def warm(model: str, keep_alive: str | int = "30m") -> dict[str, Any]:
    """Ask the broker to load a model now. Used at boot so the first partner question does
    not pay the cold-load cost. Failure is non-fatal — the first chat will load it anyway."""
    return _post("/v1/load", {"model": model, "keep_alive": keep_alive})
