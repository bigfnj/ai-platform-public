"""With OPENMAIC_CLI_REQUESTER unset, OpenMAIC never names a `requester`, whoever its caller says
it is.

The broker serves a /v1/chat call from a platform owner's own Claude CLI subscription when the
call's `requester` names that owner. Every generation call here comes from the openmaic-app
container through the LLM shim, carrying the service token and no platform identity
(api/llm.py `llm_caller`), so nobody vouches for who is waiting on it. The @openmaic role is
also course-builder's condense role, which DOES name its presser, so on a box that has not set
OPENMAIC_CLI_REQUESTER, switching @openmaic on for course-builder must not quietly switch it on
for this rail. That holds only while this rail sends no requester at all; these tests read the
bodies its broker client puts on the wire, buffered and streamed, with an owner's name wherever
a caller could put one: the X-Platform-User header, and the request body's OpenAI `user` field
and a `requester` key, which a passthrough that mapped `user` or copied unknown keys onto the
broker payload would forward. The owner is also the app's only recent user (the proxy's ledger,
api/proxy.py), so the unset setting is the one thing keeping the name off.
(tests/test_cli_requester.py is the setting's other side.)
"""
import json
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from openmaic_app import broker
from openmaic_app.api import llm, proxy
from openmaic_app.config import settings

OWNER = "owner-one"
HDR = {"X-Platform-User": OWNER, "X-Platform-Admin": "1"}
BODY = {"model": "@openmaic", "messages": [{"role": "user", "content": "a synthetic lesson"}]}
# The shared service token, as openmaic-app sends it on every call (api/llm.py llm_caller).
TOKEN = {"Authorization": "Bearer a-synthetic-service-token"}

# Where a caller can name somebody: (headers, extra body fields). The first case names nobody,
# so a check that fails on every case would show here as a failure with no name anywhere.
CALLERS = {
    "nobody": (TOKEN, {}),
    "header": ({**TOKEN, **HDR}, {}),
    "openai-user": (TOKEN, {"user": OWNER}),
    "requester-key": (TOKEN, {"requester": OWNER}),
    "everywhere": ({**TOKEN, **HDR}, {"user": OWNER, "requester": OWNER}),
}


@pytest.fixture(autouse=True)
def _service_token(monkeypatch):
    monkeypatch.setattr(llm, "_SHIM_TOKEN", TOKEN["Authorization"][len("Bearer "):])


@pytest.fixture(autouse=True)
def _setting_unset_and_owner_alone(monkeypatch):
    """The box has NOT set OPENMAIC_CLI_REQUESTER, and the owner is the app's only user."""
    monkeypatch.setattr(settings, "cli_requester", "")
    monkeypatch.setattr(proxy, "_seen", {})
    proxy.note_user(OWNER)
    assert settings.cli_requester == ""


def _named(payload: dict) -> list[str]:
    """Every place in a broker payload that carries the owner's name."""
    return [k for k, v in payload.items() if v == OWNER or (k == "requester")]


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_a_buffered_completion_names_no_requester(monkeypatch, caller):
    headers, extra = CALLERS[caller]
    sent = []

    async def fake_apost(path, payload, timeout=None):
        sent.append((path, payload))
        return {"model": "m:1", "message": {"role": "assistant", "content": "ok"},
                "done": True, "done_reason": "stop"}

    monkeypatch.setattr(broker, "_apost", fake_apost)
    r = TestClient(llm.shim).post("/v1/chat/completions", headers=headers, json={**BODY, **extra})
    assert r.status_code == 200, r.text
    ((path, payload),) = sent
    assert path == "/v1/chat" and payload["model"] == "@openmaic"
    assert not _named(payload), payload


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_a_streamed_completion_names_no_requester(monkeypatch, caller):
    headers, extra = CALLERS[caller]
    sent = []

    class _Resp:
        status_code = 200

        async def aiter_lines(self):
            yield json.dumps({"model": "m:1", "message": {"role": "assistant",
                                                          "content": "ok"}, "done": False})
            yield json.dumps({"done": True, "done_reason": "stop"})

        async def aread(self):
            return b""

    class _Ctx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            sent.append((url, json))
            return _Ctx()

    # Only the broker module's view of httpx changes; the real module is untouched.
    monkeypatch.setattr(broker, "httpx", types.SimpleNamespace(
        AsyncClient=_Client, HTTPError=httpx.HTTPError, HTTPStatusError=httpx.HTTPStatusError))
    r = TestClient(llm.shim).post("/v1/chat/completions", headers=headers,
                                  json={**BODY, **extra, "stream": True})
    assert r.status_code == 200 and "data: [DONE]" in r.text, r.text
    ((url, payload),) = sent
    assert url.endswith("/v1/chat/stream") and payload["model"] == "@openmaic"
    assert not _named(payload), payload


def test_the_named_check_sees_a_name_where_one_is():
    """_named is the assertion every case above rests on, so it must find what it looks for."""
    assert _named({"model": "@openmaic", "requester": OWNER}) == ["requester"]
    assert _named({"model": "@openmaic", "user": OWNER}) == ["user"]
    assert _named({"model": "@openmaic", "requester": None}) == ["requester"]
    assert _named({"model": "@openmaic", "messages": []}) == []
