"""A buffered chat on the local model whose caller hangs up frees the GPU gate.

Buffered `/v1/chat` on the local model and buffered `/openai/v1/chat/completions` hold the GPU gate
(after evicting every other heavy model) for the whole Ollama call. `Broker.chat` asked its
caller's connection only on the CLI path and `Broker.openai_chat` not at all, so a rail that timed
out on a long answer held the gate, and every job queued behind it, until Ollama finished. Now the
gated call runs inside `_while_there`, asked every _GONE_TICK_S: a hang-up cancels it, which closes
Ollama's request, and the route answers 499 once the gate is free.

Through the real app and the real Broker; only Ollama is a stand-in, whose answer runs until it is
cancelled or told to answer, so what is asserted is what a caller who left leaves behind: no
request to Ollama still open, the gate's slot and its queue empty.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import broker as broker_mod
from app.broker import Broker
from app.config import BrokerSettings

MODEL = "llama3.1:8b"
ASK = [{"role": "user", "content": "a long answer, please"}]
ROUTES = {
    "v1": ("/v1/chat", {"model": MODEL, "messages": ASK}),
    "openai": ("/openai/v1/chat/completions", {"model": MODEL, "messages": ASK}),
}


@pytest.fixture
def served(monkeypatch, tmp_path):
    """The real app over a real Broker (no CLI), whose routing and eviction are fixed and whose
    Ollama answers only when `answer` is set, recording a cancellation."""
    from app.main import app
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES"):
        monkeypatch.delenv(key, raising=False)
    settings = BrokerSettings(cli_enabled=False, auth_token="",
                              tokens_file=str(tmp_path / "tokens.json"))
    b = Broker(settings)
    seen = {"asked": [], "cancelled": [], "answer": asyncio.Event()}

    def ollama(kind):
        async def answer(*_a, **_kw):
            seen["asked"].append(kind)
            try:
                await seen["answer"].wait()
            except asyncio.CancelledError:
                seen["cancelled"].append(kind)
                raise
            if kind == "openai":
                return {"id": "x", "object": "chat.completion", "model": MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant",
                                                             "content": "done"}}]}
            return {"model": MODEL, "message": {"role": "assistant", "content": "done"},
                    "done": True}
        return answer

    async def route(model, substitute=True):
        return None, MODEL, None

    async def resolve_for_external(ref, substitute=True):
        return MODEL, None

    async def no_eviction(*_a, **_kw):
        return []

    monkeypatch.setattr(b, "route", route)
    monkeypatch.setattr(b, "resolve_for_external", resolve_for_external)
    monkeypatch.setattr(b, "_evict_other_heavy", no_eviction)
    monkeypatch.setattr(b.ollama, "chat", ollama("v1"))
    monkeypatch.setattr(b.ollama, "openai_chat", ollama("openai"))
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.1)
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    return SimpleNamespace(app=app, broker=b, seen=seen)


def _post(app, route: str, gone: asyncio.Event, sent: list) -> asyncio.Future:
    """POST ROUTES[route] through the raw ASGI interface as uvicorn delivers it (spec 2.3):
    receive() answers http.disconnect once `gone` is set and never before."""
    path, payload = ROUTES[route]
    body = json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 11500),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    return asyncio.ensure_future(app(scope, receive, send))


async def _until(cond, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_buffered_local_chat_whose_caller_hangs_up_frees_the_gate(served, route):
    s = served

    async def scenario():
        gone, sent = asyncio.Event(), []
        call = _post(s.app, route, gone, sent)
        assert await _until(lambda: s.seen["asked"]), "the chat never reached Ollama"
        assert s.broker.gate.depth() == {"active": 1, "waiting": 0}
        gone.set()
        try:
            await asyncio.wait_for(asyncio.shield(call), 3)
        except asyncio.TimeoutError:
            s.seen["answer"].set()      # let the old route end, so nothing outlives the test
            await asyncio.wait_for(call, 10)
            return None
        return sent

    sent = asyncio.run(scenario())
    assert sent is not None, f"{route}: the gate was still held 3 s after the caller hung up"
    assert s.seen["cancelled"] == [route], "Ollama's request was never let go"
    assert s.broker.gate.depth() == {"active": 0, "waiting": 0} and s.broker.gate.jobs() == []
    assert sent[0]["status"] == 499, sent[:1]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_buffered_local_chat_whose_caller_stays_is_answered(served, route):
    """The witness: asking must not cost a caller who waits for the answer."""
    s = served

    async def scenario():
        sent = []
        call = _post(s.app, route, asyncio.Event(), sent)
        assert await _until(lambda: s.seen["asked"])
        await asyncio.sleep(0.35)       # several ticks asked, and answered "still there"
        s.seen["answer"].set()
        await asyncio.wait_for(call, 10)
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 200, sent[:2]
    assert s.seen["cancelled"] == []
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"done" in body
