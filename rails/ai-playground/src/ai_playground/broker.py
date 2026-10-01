"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API (never Ollama directly). The broker
owns the one-heavy-model VRAM policy and @role/wildcard resolution.

Two generation paths:
  * ``chat`` — buffered (POST /v1/chat), a single JSON body. Powers the non-streaming
    ``/api/rag/ask`` fallback.
  * ``chat_stream`` — async NDJSON stream (POST /v1/chat/stream) that yields assistant
    content deltas so the rail can relay tokens live over its WebSocket. This is the
    additive broker endpoint the platform gained for streaming rails.

``embed`` (POST /v1/embed) powers cosine retrieval; it always runs locally through the
broker even when generation is flipped to NVIDIA NIM.

Buffered/streaming both go through the broker on purpose: the platform gateway buffers
HTTP, so the browser gets live tokens only over the rail's WebSocket (fed by chat_stream).
Base URL + default model are env-overridable so the same code runs standalone and in the
container (``host.docker.internal``).
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

BROKER_URL = os.environ.get("AI_PLAYGROUND_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")


def _broker_token() -> str:
    """The broker's shared control-plane token: BROKER_AUTH_TOKEN_FILE (a path, contents
    stripped) wins over BROKER_AUTH_TOKEN (the literal). The file form keeps the secret out
    of `docker inspect`, crash dumps and /proc/<pid>/environ. A configured-but-unreadable
    file yields "" — no header, so the broker 401s visibly — rather than silently falling
    back to the environment the deployment believes it has moved off. See RAIL_CONTRACT.md.
    """
    path = os.environ.get("BROKER_AUTH_TOKEN_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return os.environ.get("BROKER_AUTH_TOKEN", "").strip()


# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = _broker_token()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
DEFAULT_MODEL = os.environ.get("AI_PLAYGROUND_CHAT_MODEL", "@ai-playground")
EMBED_MODEL = os.environ.get("AI_PLAYGROUND_EMBED_MODEL", "@embed")
# A cold heavy-model load can take a while; keep a generous default.
DEFAULT_TIMEOUT = float(os.environ.get("AI_PLAYGROUND_BROKER_TIMEOUT", "600"))


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


def chat(model: str | None, messages: list[dict], *, options: dict | None = None,
         fmt: str | dict | None = None, keep_alive: str | int = "10m") -> str:
    """Buffered chat. Returns the assistant message content (str)."""
    payload: dict = {"model": model or DEFAULT_MODEL, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    resp = _post("/v1/chat", payload)
    return (resp.get("message") or {}).get("content", "") or ""


def _stream_status_detail(exc: httpx.HTTPStatusError) -> str:
    """The detail for a raise_for_status() failure on a STREAMED response: the status code.

    The code and not `_post`'s `exc.response.text`, because on a `client.stream()` response
    nothing has been read yet and that property raises `httpx.ResponseNotRead` — a
    `StreamError`/`RuntimeError`, NOT an `httpx.HTTPError`, so the very `except` clause that
    called it would not catch it. Copying `_post`'s message verbatim swaps one unmapped escape
    for another, and `BrokerError` also being a `RuntimeError` is no help to the caller: a
    sibling class is no more catchable by `except BrokerError` than the original was.

    `aread()` is the documented way to make the body readable, and it does work — but only
    INSIDE the `async with client.stream(...)`. By the time a mapping clause outside it runs,
    `__aexit__` has already closed the response and `aread()` raises `httpx.StreamClosed`, which
    is the same trap a second time (also not an `httpx.HTTPError`). Both measured against httpx
    0.28 before this was written, and locked by tests/test_broker_stream.py. So the body is
    genuinely not available here and the code is the whole of what can be reported; the broker's
    own wording still reaches callers, because it arrives as an in-band NDJSON error frame.

    `.response` and `.status_code` are read defensively: a stand-in exception type carries
    neither.
    """
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return str(code) if code is not None else (str(exc) or type(exc).__name__)


async def chat_stream(model: str | None, messages: list[dict], *, options: dict | None = None,
                      keep_alive: str | int = "10m") -> AsyncIterator[str]:
    """Stream assistant content deltas from the broker's NDJSON /v1/chat/stream. Yields
    only non-empty ``message.content`` pieces (reasoning/thinking frames are skipped).

    Every httpx escape is mapped onto `BrokerError`, using `_post`'s two clauses in `_post`'s
    order (`HTTPStatusError` first, since it subclasses `HTTPError`), so this facade keeps the
    same contract buffered and streamed. `ws_rag` catches `Exception`, so this rail survived
    without the mapping where the two rails that guard on `BrokerError` alone did not — but a
    caller should not need the broadest possible handler to get a typed error out of a typed
    facade, and the next caller will not have one. The in-band `BrokerError` below is a
    `RuntimeError`, so no httpx-typed clause re-wraps it.
    """
    payload: dict = {"model": model or DEFAULT_MODEL, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            async with client.stream("POST", BROKER_URL + "/v1/chat/stream", json=payload,
                                     headers=_AUTH) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    frame = json.loads(line)
                    # A frame that is valid JSON but NOT an object (a bare list, number,
                    # string or null) makes .get raise AttributeError, and none of the
                    # clauses below catch that. Measured: a bare HTTP 500 out of
                    # smb-partner's /api/scenario/generate, and the raw Python string
                    # "'list' object has no attribute 'get'" shown to the user on the
                    # WebSocket paths. It is a malformed frame, so it is reported as one.
                    if not isinstance(frame, dict):
                        raise BrokerError(
                            "broker POST /v1/chat/stream sent a non-object frame: "
                            f"{line[:120]!r}")
                    if frame.get("error"):
                        raise BrokerError(str(frame["error"]))
                    tok = (frame.get("message") or {}).get("content") or ""
                    if tok:
                        yield tok
                    if frame.get("done"):
                        break
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker POST /v1/chat/stream -> {_stream_status_detail(exc)}") from exc
    except httpx.HTTPError as exc:
        # str(httpx.ReadTimeout()) is EMPTY, so the most likely mid-answer failure of all
        # rendered as "unreachable: " with nothing after the colon. Name the class instead.
        # str() not truthiness: an Exception INSTANCE is always truthy, so `exc or ...`
        # happily formats the empty string. Caught by the test below, not by review.
        detail = str(exc) or type(exc).__name__
        raise BrokerError(f"broker POST /v1/chat/stream unreachable: {detail}") from exc
    except json.JSONDecodeError as exc:
        # Reached by a graceful end of stream with a partial final line, NOT by a severed
        # hop: a severed chunked stream raises httpx.RemoteProtocolError, which is an
        # httpx.HTTPError, so the clause above fires for that. Measured, because the
        # original attribution here was wrong.
        raise BrokerError(f"broker POST /v1/chat/stream sent a malformed frame: {exc}") from exc


def embed(text: str | list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed a string or list of strings via the broker (/v1/embed). Returns a list of
    vectors aligned with the input, tolerant of the couple of response shapes."""
    resp = _post("/v1/embed", {"model": model or EMBED_MODEL, "input": text})
    if isinstance(resp.get("embeddings"), list):
        return resp["embeddings"]
    if isinstance(resp.get("embedding"), list):
        return [resp["embedding"]]
    data = resp.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and "embedding" in data[0]:
        return [d["embedding"] for d in data]
    raise BrokerError("embed response had no vectors")


def models() -> list[dict]:
    """List the models the broker can serve (Ollama tags). Used by the Embedding Lab to show
    which broker embedders are pulled. Tolerant of the two response shapes."""
    resp = _get("/v1/models")
    if isinstance(resp, dict):
        got = resp.get("models")
        if isinstance(got, list):
            return got
    return resp if isinstance(resp, list) else []


def roles() -> list[dict]:
    """The broker's role table (each role + the concrete model it resolves to). Lets the rail
    name the actual model behind a @role (e.g. @ai-playground -> nemotron-3-nano:4b)."""
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


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth)."""
    return _get("/v1/status")


def up() -> bool:
    try:
        return bool(_get("/v1/status").get("ollama_reachable"))
    except BrokerError:
        return False
