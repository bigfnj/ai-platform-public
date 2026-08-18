"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API (never Ollama directly). The broker owns the
one-heavy-model VRAM policy and @role/wildcard resolution.

Two paths this rail uses:
  * ``chat`` / ``chat_stream`` — the grounded answer, via ``@gemini-cx-rag`` (heavy).
  * ``embed``                  — retrieval, via ``@embed`` (light; stays resident alongside).

The broker is Ollama-native, NOT OpenAI-compatible: the route is ``/v1/chat`` (no
``/completions``), sampling goes in ``options`` with ``num_predict`` rather than
``max_tokens``, and the reply is at ``message.content`` rather than ``choices[0]``.
"""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

BROKER_URL = os.environ.get("GEMINI_CX_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")
# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
DEFAULT_TIMEOUT = float(os.environ.get("GEMINI_CX_BROKER_TIMEOUT", "600"))


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

    keep_alive defaults to 30m so the answer model stays resident between questions — a
    question deck invites rapid successive asks, and a cold load per click would dominate.
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


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth)."""
    return _get("/v1/status")


def roles() -> list[dict]:
    """The broker's role table (each role plus the concrete model it resolves to)."""
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


def warm(model: str, keep_alive: str | int = "30m") -> dict[str, Any]:
    """Ask the broker to load a model now, so the first question does not pay the cold-load
    cost. Failure is non-fatal — the first chat will load it anyway."""
    return _post("/v1/load", {"model": model, "keep_alive": keep_alive})
