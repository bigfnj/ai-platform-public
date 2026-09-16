"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker, never Ollama directly. That is not a style preference
here: the broker owns the one-heavy-model VRAM policy, and course generation is a burst of many
LLM calls. A rail that dialled Ollama itself would evict whatever another rail had resident,
repeatedly, on a card with room for one heavy model.

The broker is Ollama-native, NOT OpenAI-compatible: the route is ``/v1/chat`` (no
``/completions``), sampling goes in ``options`` with ``num_predict`` rather than ``max_tokens``,
and the reply is at ``message.content`` rather than ``choices[0]``. OpenMAIC speaks the OpenAI
shape, so api/llm.py translates between the two — this module stays deliberately broker-shaped
and does no translating of its own.

``roles()``, ``models()``, ``status()`` and ``BrokerError`` are the canonical facade every rail
must expose (RC022); modelstate.py is generated against exactly that surface and cannot read a
rail that renames them.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

BROKER_URL = os.environ.get("OPENMAIC_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")
# Broker control-plane token (one platform-wide shared secret). Unprefixed on purpose: a rail
# that accepted only its own prefixed spelling would work under whichever compose file was bent
# to match it and be silently tokenless under the other. Empty => no header (broker not enforcing).
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
DEFAULT_TIMEOUT = float(os.environ.get("OPENMAIC_BROKER_TIMEOUT", "300"))


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


def _get(path: str, timeout: float = 30.0) -> Any:
    try:
        resp = httpx.get(BROKER_URL + path, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker GET {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker GET {path} unreachable: {exc}") from exc
    return resp.json()


async def _apost(path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(BROKER_URL + path, json=payload, headers=_AUTH)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker POST {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker POST {path} unreachable: {exc}") from exc
    return resp.json()


# --- the canonical facade (RC022) -------------------------------------------------------------

def status() -> dict:
    """Broker/GPU status passthrough: loaded models, VRAM, queue depth, media availability."""
    resp = _get("/v1/status")
    return resp if isinstance(resp, dict) else {}


def models() -> list[dict]:
    """Every model installed in Ollama, as the broker reports it. This is what tells a model
    that is merely cold apart from one that is not installed at all."""
    resp = _get("/v1/models")
    if isinstance(resp, dict) and isinstance(resp.get("models"), list):
        return resp["models"]
    return resp if isinstance(resp, list) else []


def roles() -> list[dict]:
    """The broker's role table: each role plus the concrete model it currently resolves to."""
    resp = _get("/v1/roles")
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return resp["roles"]
    return resp if isinstance(resp, list) else []


# --- inference --------------------------------------------------------------------------------

def resolved_model(name: str) -> str:
    """Resolve a leading-@ role to its concrete model name; pass a concrete name through.

    Uses the read-only role table rather than an inference call, so asking "what would this
    slot use?" never costs a model load.
    """
    if not name or not name.startswith("@"):
        return name
    role = name[1:]
    try:
        for r in roles():
            if r.get("role") == role and r.get("resolved"):
                return str(r["resolved"])
    except BrokerError:
        pass
    return name


async def chat(model: str, messages: list[dict], *, options: dict | None = None,
               fmt: str | dict | None = None, keep_alive: str | int = "30m") -> dict:
    """Buffered chat. Returns the broker's raw response (Ollama /api/chat shape)."""
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    return await _apost("/v1/chat", payload)


async def chat_stream(model: str, messages: list[dict], *, options: dict | None = None,
                      keep_alive: str | int = "30m") -> AsyncIterator[dict]:
    """Yield the broker's raw NDJSON frames from /v1/chat/stream, one decoded dict per line.

    Raw frames rather than content deltas because the caller has to see ``done`` and any
    ``error`` key itself: an error that happens AFTER streaming starts arrives as a final frame
    with HTTP 200 already sent, so a caller that only checks the status code reports a truncated
    answer as a complete one.
    """
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options:
        payload["options"] = options
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            async with client.stream("POST", BROKER_URL + "/v1/chat/stream",
                                     json=payload, headers=_AUTH) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise BrokerError(f"broker stream -> {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker stream unreachable: {exc}") from exc


async def embed(model: str, inputs: list[str]) -> list[list[float]]:
    """Text embeddings. Returns one vector per input, in order."""
    resp = await _apost("/v1/embed", {"model": model, "input": inputs})
    vectors = resp.get("embeddings")
    if isinstance(vectors, list):
        return vectors
    # Some backends answer a single-vector call unwrapped; normalise so callers never branch.
    single = resp.get("embedding")
    return [single] if isinstance(single, list) else []


def up() -> bool:
    """Whether the broker is reachable at all. Never raises — used by liveness probes."""
    try:
        return bool(_get("/healthz", timeout=5.0).get("status") == "ok")
    except (BrokerError, AttributeError):
        return False
