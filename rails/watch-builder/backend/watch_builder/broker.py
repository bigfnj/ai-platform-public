"""Broker facade — the canonical surface every rail exposes.

`roles()`, `models()`, `status()` and a `BrokerError`. That surface is the reason
`modelstate.py` can be byte-identical across rails instead of each one adapting to its own
transport, function names and exception type. RC022 checks it. See "The rail template" in
docs/RAIL_CONTRACT.md.

urllib rather than httpx, on purpose. This rail's dependency set is fastapi + uvicorn +
pydantic + pillow, and a status chip is not worth an HTTP client. `chat()` uses the same
transport for the same reason.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from watch_builder.config import settings

_TIMEOUT = 10.0

# Generation is slow and the caller is a person waiting on a job. Separate from _TIMEOUT
# because a status chip that hangs for three minutes is a broken header, while a design pass
# that gives up after ten seconds is a broken feature.
#
# What EVERY chat call waits. It must outlast the broker's Claude CLI timeout
# (BROKER_CLI_TIMEOUT: 180 by default, 300 on the platform box) PLUS the local answer the broker
# falls back to when the CLI fails or hangs, because both happen inside the same broker request.
# At 180 s an owner's hung CLI call failed the variant instead of falling back. 480 = a 300 s CLI
# timeout plus 180 s for a cold mistral-small load and a 3000-token document. The AI features run
# as background jobs (jobs.py), so no HTTP request has to fit around these calls and no call is
# given less than this: each one carries the person who asked.
CHAT_TIMEOUT = 480.0


class BrokerError(RuntimeError):
    """The broker could not be read. Callers degrade rather than failing the page."""


def _request(path: str, payload: dict | None = None, timeout: float = _TIMEOUT) -> Any:
    url = settings.broker_url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    token = (settings.broker_auth_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            json.JSONDecodeError) as exc:
        raise BrokerError(f"{path}: {exc}") from exc


def _rows(payload: Any, key: str) -> list[dict]:
    """Tolerate both response shapes: a bare list, or a dict wrapping one under `key`."""
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return payload if isinstance(payload, list) else []


def roles() -> list[dict]:
    """Role -> concrete model, as the broker resolves it."""
    return _rows(_request("/v1/roles"), "roles")


def models() -> list[dict]:
    """The broker's raw model list (what is installed)."""
    return _rows(_request("/v1/models"), "models")


def status() -> dict:
    """Loaded models plus the live job queue."""
    got = _request("/v1/status")
    return got if isinstance(got, dict) else {}


def _cli_requester(model: str, requester: str | None) -> str | None:
    """The name a call to ``model`` may carry to the broker as ``requester``, or None.

    The broker answers a call from a platform owner's own Claude CLI subscription when its
    ``requester`` names an owner and its @role is switched on there. It sees only the role, so
    this rail decides which calls may carry a name: the designer's own role
    (``settings.designer_model``) and nothing else.
    """
    return requester if requester and model == settings.designer_model else None


def chat(messages: list[dict], *, model: str, max_tokens: int,
         temperature: float = 0.7, requester: str | None = None) -> str:
    """One completion, returned as text, waited for for the whole CHAT_TIMEOUT.

    ``requester`` is the person who asked (the AI routes capture ``ident.user`` when they
    create the job, and every call of that job passes it), kept only on the designer role.
    Absent, the field is left off the wire.

    Deliberately non-streaming. The caller needs a whole JSON document before it can do
    anything with it, so streaming would buy a progress bar and cost a partial-JSON parser.

    When the broker answers locally, its gate is FIFO and held per call, so this occupies the
    GPU for the duration of one generation and releases it. A variation pass is therefore N
    queue entries rather than one long lock, and someone else's question interleaves instead of
    waiting for all N. A call the broker answers from the Claude CLI takes no GPU gate at all.
    """
    payload = {
        "model": model,
        "messages": messages,
        # The broker speaks Ollama's dialect: sampling lives under `options`, and the output
        # cap is `num_predict`. These were sent at the TOP level (the OpenAI place, with an
        # OpenAI `stream` flag beside them), where the broker's request model drops unknown
        # fields without a word, so the designer's token cap never reached the model. The
        # broker now logs any top-level field it drops; nothing here should appear in it.
        "options": {"num_predict": max_tokens, "temperature": temperature},
    }
    who = _cli_requester(model, requester)
    if who:
        payload["requester"] = who
    got = _request("/v1/chat", payload, timeout=CHAT_TIMEOUT)

    # The platform dialect answers {"message": {"content": ...}}; be tolerant of the
    # OpenAI-shaped {"choices":[{"message":{"content":...}}]} too, since the broker now
    # speaks both and which one answers depends on the prefix the URL was built with.
    if isinstance(got, dict):
        msg = got.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
        choices = got.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                inner = first.get("message")
                if isinstance(inner, dict) and isinstance(inner.get("content"), str):
                    return inner["content"]
        if isinstance(got.get("response"), str):
            return got["response"]
    raise BrokerError("chat: no content in response")
