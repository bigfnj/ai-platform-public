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
    neither (the suites swap `httpx` for a no-network shim).
    """
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return str(code) if code is not None else (str(exc) or type(exc).__name__)


async def chat_stream(model: str, messages: list[dict], *, options: dict | None = None,
                      keep_alive: str | int = "30m") -> AsyncIterator[str]:
    """Stream assistant content deltas from the broker's NDJSON /v1/chat/stream.

    Every httpx escape is mapped onto `BrokerError`, using `_post`'s two clauses in `_post`'s
    order (`HTTPStatusError` first, since it subclasses `HTTPError`). That parity is the point:
    callers guard on `BrokerError` alone, so an unmapped raise out of here does not cost one
    turn, it escapes the handler AND the outer `except WebSocketDisconnect` and kills the socket
    with no error frame. (The V-16 entry said "the whole conversation, not the answer".
    That overstated it: all three shipped frontends open a NEW socket per ask and close it
    on done or error, so the socket only ever carried one turn.) FOUR escapes are live: a
    mid-answer 401 from `raise_for_status()` after a broker token rotation, a connect/read
    failure from `stream()` itself, `json.loads` on a partial final line, and a frame that
    parses as valid JSON but is NOT an object (measured: that one reached the user as the
    raw string "'list' object has no attribute 'get'", and 500d the HTTP fallback route).
    Note a SEVERED hop is not the JSONDecodeError case, despite what this said before: it
    raises httpx.RemoteProtocolError, so the second clause takes it. The in-band
    `BrokerError` below is a `RuntimeError`, so no httpx-typed clause re-wraps it, which is
    why there is no `except Exception` here.
    """
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
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


def tts_light(text: str, *, voice: str | None = None, lang_code: str | None = None,
              speed: float | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Kokoro-82M TTS via the broker's ``/v1/tts_light``.

    Deliberately NOT ``/v1/tts``: that path takes the full GPU gate and calls
    ``_evict_other_heavy()`` with no ``keep``, so every utterance would evict this rail's
    answer model and destroy the co-residency the whole design rests on. ``tts_light`` skips
    both the gate and the eviction (the ``embed_image()`` precedent) and at ~350 MB Kokoro
    coexists with the resident LLM and embedder.
    """
    payload: dict = {"text": text}
    if voice:
        payload["voice"] = voice
    if lang_code:
        payload["lang_code"] = lang_code
    if speed is not None:
        payload["speed"] = speed
    return _post("/v1/tts_light", payload, timeout=timeout)


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth, media availability)."""
    return _get("/v1/status")


def media_enabled() -> bool:
    """Whether the broker's media worker is available, i.e. whether Kokoro can be served."""
    try:
        return bool((status().get("media") or {}).get("enabled"))
    except BrokerError:
        return False


def models() -> list[dict]:
    """Every model installed in Ollama, as the broker reports it. Used to tell a model that is
    merely cold apart from one that is not installed at all."""
    resp = _get("/v1/models")
    if isinstance(resp, dict) and isinstance(resp.get("models"), list):
        return resp["models"]
    return resp if isinstance(resp, list) else []


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
    cost. Called at boot only when GEMINI_CX_WARM_ON_BOOT is set (see config.WARM_ON_BOOT).
    Failure is non-fatal — the first chat will load it anyway."""
    return _post("/v1/load", {"model": model, "keep_alive": keep_alive})
