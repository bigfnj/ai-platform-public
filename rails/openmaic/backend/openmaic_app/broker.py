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

from .config import settings

# Read through Settings, not os.environ. Reading the environment directly here while config.py
# also declared a broker_url made two sources of truth for one value: Settings loads a .env file
# and this module did not, so OPENMAIC_BROKER_URL set in .env was honoured by everything that
# printed it and ignored by the module that actually dials.
BROKER_URL = settings.broker_url.rstrip("/")


def _broker_token() -> str:
    """The broker's shared control-plane token: BROKER_AUTH_TOKEN_FILE (a path, contents
    stripped) wins over BROKER_AUTH_TOKEN (the literal). The file form keeps the secret out
    of `docker inspect`, crash dumps and /proc/<pid>/environ. A configured-but-unreadable
    file yields "" — no header, so the broker 401s visibly — rather than silently falling
    back to the environment the deployment believes it has moved off. See RAIL_CONTRACT.md.

    This rail read only the literal until 2026-10-08, so on a stack switched to the file form
    (which reads ONLY the path) every model call here would have gone out tokenless while RC005,
    which passes on either name, stayed green.
    """
    path = os.environ.get("BROKER_AUTH_TOKEN_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return os.environ.get("BROKER_AUTH_TOKEN", "").strip()


# Broker control-plane token (one platform-wide shared secret). Unprefixed on purpose: a rail
# that accepted only its own prefixed spelling would work under whichever compose file was bent
# to match it and be silently tokenless under the other. Empty => no header (broker not enforcing).
_TOK = _broker_token()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
DEFAULT_TIMEOUT = settings.broker_timeout


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

def _cli_requester(model: str, requester: str | None) -> str | None:
    """The name a call to ``model`` may carry to the broker as ``requester``, or None.

    The broker answers a /v1/chat or /v1/chat/stream call from a platform owner's own Claude
    CLI subscription when its ``requester`` names an owner and its @role is switched on there.
    It sees only the role, so this rail decides which calls may carry a name: the reasoning
    slot's own role (``settings.llm_model``, ``@openmaic`` by default) and nothing else. The app
    picks the model per request, so a concrete name or another ``@role`` chosen in its model
    selector never rides the name on a switch the reasoning chip does not describe.
    """
    return requester if requester and requester.strip() and model == settings.llm_model else None


async def chat(model: str, messages: list[dict], *, options: dict | None = None,
               fmt: str | dict | None = None, think: bool | None = None,
               keep_alive: str | int = "30m", requester: str | None = None) -> dict:
    """Buffered chat. Returns the broker's raw response (Ollama /api/chat shape).

    ``requester`` is who the shim may say is waiting (api/llm.py ``_shim_requester``), kept only
    on the reasoning role (``_cli_requester``). Absent, the field is left off the wire."""
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    if think is not None:
        payload["think"] = think
    who = _cli_requester(model, requester)
    if who:
        payload["requester"] = who
    return await _apost("/v1/chat", payload)


async def chat_stream(model: str, messages: list[dict], *, options: dict | None = None,
                      fmt: str | dict | None = None, think: bool | None = None,
                      keep_alive: str | int = "30m",
                      requester: str | None = None) -> AsyncIterator[dict]:
    """Yield the broker's raw NDJSON frames from /v1/chat/stream, one decoded dict per line.

    Raw frames rather than content deltas because the caller has to see ``done`` and any
    ``error`` key itself: an error that happens AFTER streaming starts arrives as a final frame
    with HTTP 200 already sent, so a caller that only checks the status code reports a truncated
    answer as a complete one.

    ``fmt`` mirrors the buffered path and is not optional in practice. Without it a streamed
    request for JSON gets prose back — a 200 full of "Here's the JSON:" that fails to parse — and
    the byte-identical non-streamed request works, which makes it look like a model problem.

    ``requester`` as in ``chat``. A named stream the Claude CLI answers carries frames of the
    broker's own (a leading ``cli``, or ``cli_fallback`` when it fell back before the first
    token, neither with ``message.content``) and, when the CLI stops after its first token, a
    final ``error`` frame with ``done_reason: "cli_interrupted"``. They are yielded like any
    other frame; api/llm.py ``_sse`` skips the first two and reports the last as an error.
    """
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    if think is not None:
        payload["think"] = think
    who = _cli_requester(model, requester)
    if who:
        payload["requester"] = who
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
