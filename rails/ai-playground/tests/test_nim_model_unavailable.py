"""A retired or unentitled NVIDIA model says so, in words that name the fix.

NVIDIA has end-of-lifed this rail's default cloud model twice (nim.py keeps the history). Both
times the key still authenticated, and the failure reached the operator as
`502 {"detail": "Error code: 410 - {...}"}`, which reads as "the NVIDIA integration is broken"
when the whole fix is one env var. The second time the deep smoke check found it, and the
message it printed still had to be decoded.

These tests pin the translation: a 410 names the retired model and the variable to change; a 404
"Not found for account" names the entitlement trap WITHOUT echoing the account id to the
browser; anything else passes through untouched, so this cannot become a place where an
unrelated failure gets mislabelled as a model problem.

Hermetic: every NVIDIA response here is built in-process. Nothing reaches the network.
Run tests/mutate_nim_unavailable.py after changing nim.py's error path or the model default.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from ai_playground import nim
from ai_playground.api import app as appmod

MODEL = "vendor/some-instruct-model"
ACCOUNT = "acct-0123456789abcdef-SHOULD-NOT-LEAK"
EOL = ("The model 'vendor/some-instruct-model' has reached its end of life on "
       "2026-09-28T08:00:00Z and is no longer available.")
USER = {"X-Platform-User": "owner-one"}


def _status_error(status: int, body, cls=openai.APIStatusError) -> openai.APIStatusError:
    """The exception openai raises for a non-2xx answer, built the way the SDK builds it."""
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    if isinstance(body, dict):
        response = httpx.Response(status, request=request, json=body)
    else:
        response = httpx.Response(status, request=request, text=str(body))
    return cls(f"Error code: {status} - {body}", response=response, body=body)


GONE = {"type": "about:blank", "title": "Gone", "status": 410, "detail": EOL}
UNENTITLED = {"status": 404, "title": "Not Found",
              "detail": f"Function 'f-1': Not found for account '{ACCOUNT}'"}


class RaisingClient:
    """Stands in for AsyncOpenAI: every completion raises the given error. Records closing."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def __aenter__(self) -> "RaisingClient":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def _create(self, **kwargs):
        raise self.exc


@pytest.fixture
def nvidia(monkeypatch):
    """nim wired to a client that answers with the given error; a key and a model are set."""
    def install(exc: Exception) -> RaisingClient:
        fake = RaisingClient(exc)
        monkeypatch.setattr(nim, "API_KEY", "test-key")
        monkeypatch.setattr(nim, "CHAT_MODEL", MODEL)
        monkeypatch.setattr(nim, "AsyncOpenAI", lambda **kwargs: fake)
        return fake

    return install


def _drain_stream():
    async def drain():
        return [tok async for tok in nim.chat_stream([{"role": "user", "content": "hi"}])]
    return asyncio.run(drain())


def test_a_retired_model_names_itself_and_the_fix(nvidia):
    fake = nvidia(_status_error(410, GONE))
    with pytest.raises(nim.ModelUnavailable) as err:
        asyncio.run(nim.probe())
    msg = str(err.value)
    assert "retired" in msg and MODEL in msg
    assert "AI_PLAYGROUND_NVIDIA_CHAT_MODEL" in msg, "the message must name what to change"
    assert "2026-09-28T08:00:00Z" in msg, "NVIDIA's own end-of-life date was dropped"
    assert isinstance(err.value.__cause__, openai.APIStatusError), "the original was lost"
    assert fake.closed


def test_the_streaming_path_says_so_too(nvidia):
    """The demo's answer path, not just the toggle's probe: a user who was already switched to
    NIM before the retirement hits it here, in the WebSocket's error frame."""
    fake = nvidia(_status_error(410, GONE))
    with pytest.raises(nim.ModelUnavailable, match="retired"):
        _drain_stream()
    assert fake.closed


def test_an_unentitled_model_says_so_without_the_account_id(nvidia):
    nvidia(_status_error(404, UNENTITLED, cls=openai.NotFoundError))
    with pytest.raises(nim.ModelUnavailable) as err:
        asyncio.run(nim.probe())
    msg = str(err.value)
    assert "not entitled" in msg and MODEL in msg
    assert "/v1/models" in msg, "the listing trap is the point of this message"
    assert ACCOUNT not in msg, "the NVIDIA account id was sent toward the browser"


def test_the_streaming_path_names_the_entitlement_trap_too(nvidia):
    nvidia(_status_error(404, UNENTITLED, cls=openai.NotFoundError))
    with pytest.raises(nim.ModelUnavailable, match="not entitled"):
        _drain_stream()


def test_a_plain_404_is_left_alone(nvidia):
    """A typo'd model id or a wrong base URL answers `404 page not found`, with no account in
    it. Calling that an entitlement problem would send the operator to the wrong fix."""
    nvidia(_status_error(404, "404 page not found\n", cls=openai.NotFoundError))
    with pytest.raises(openai.NotFoundError) as err:
        asyncio.run(nim.probe())
    assert not isinstance(err.value, nim.ModelUnavailable)


@pytest.mark.parametrize("status,cls", [
    (401, openai.AuthenticationError),
    (429, openai.RateLimitError),
    (500, openai.InternalServerError),
])
def test_every_other_failure_passes_through_unchanged(nvidia, status, cls):
    original = _status_error(status, {"status": status, "detail": "upstream said no"}, cls=cls)
    nvidia(original)
    with pytest.raises(cls) as err:
        asyncio.run(nim.probe())
    assert err.value is original


def test_the_probe_route_shows_the_retirement_where_the_toggle_can_read_it(api, nvidia):
    """End to end through the real route and the real nim.probe. RagDemo.tsx shows the first
    160 characters of the error body, so the model id and the cause have to land inside them;
    a message that buried them after NVIDIA's boilerplate would be truncated away."""
    nvidia(_status_error(410, GONE))
    appmod._NIM_PROBE_LIMIT.reset()
    try:
        resp = TestClient(api).post("/api/nim/probe", headers=USER)
    finally:
        appmod._NIM_PROBE_LIMIT.reset()
    assert resp.status_code == 502
    shown = resp.text[:160]
    assert "retired" in shown and MODEL in shown, f"the toggle would show: {shown!r}"
