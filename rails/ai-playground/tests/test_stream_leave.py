"""A client that leaves ends the read: /ws/rag's heartbeat wrapper and the socket behind it.

`_stream_with_heartbeat` keeps the next read in a task of its own while it pings. Until
2026-10-14 a ping that raised, because the client had gone, left that read running: the rail
went on reading /v1/chat/stream for nobody until the next frame, so the broker's client-gone
check never fired, a Claude CLI call kept its slot to its first token, and a CLI timeout started
a local fallback no one would read. What these pin:

* a ping that raises cancels the read in flight, and the stream's own `finally` has run by the
  time the wrapper's exception reaches its caller; released afterwards, it reads nothing;
* a caller that stops reading mid-answer (its token send raised) closes the stream too, because
  /ws/rag holds the wrapper under `aclosing`; the local branch and the NIM branch alike.

Offline: the stream is a generator that records what was read and when it was closed.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from ai_playground import broker, corpora, nim
from ai_playground.api import app as appmod
from conftest import StubWS, ws_endpoint
# What the loop still runs, sampled inside it, and a run that is the loop's one task on every
# Python (tools/tests/loop_tasks.py; conftest puts tools/tests on sys.path).
from loop_tasks import others, run_alone

HITS = [{"source": "a.md", "score": 0.9, "text": "context"}]


class SilentStream:
    """One streamed answer that says nothing until `release` is set, then its tokens. Records
    each token read, and whether its `finally` (the HTTP request's unwinding) ran."""

    def __init__(self, tokens: tuple[str, ...] = ("late", "later"), silent: bool = True):
        self.tokens = tokens
        self.silent = silent
        self.release = asyncio.Event()
        self.reads = 0
        self.closed = False

    def __call__(self, *_args, **_kwargs):
        return self._gen()

    async def _gen(self):
        try:
            if self.silent:
                await self.release.wait()
            for tok in self.tokens:
                self.reads += 1
                yield tok
        finally:
            self.closed = True


class GoneWS(StubWS):
    """By the time a frame of type `fail_on` is sent the client has left: the send raises
    WebSocketDisconnect, as Starlette 1.7's does for the server's OSError (code 1006)."""

    def __init__(self, fail_on: str, **kw) -> None:
        super().__init__(**kw)
        self.fail_on = fail_on

    async def send_json(self, data: dict) -> None:
        if data.get("type") == self.fail_on:
            raise WebSocketDisconnect(1006)
        await super().send_json(data)


def test_a_ping_to_a_client_that_left_ends_the_read():
    async def run():
        s = SilentStream()
        ws = GoneWS("ping")
        ws.accepted = True
        with pytest.raises(WebSocketDisconnect):
            async for _ in appmod._stream_with_heartbeat(ws, s(), heartbeat=0.01):
                pass
        closed_on_return = s.closed
        s.release.set()                 # the first token arrives after all
        await asyncio.sleep(0.05)
        return closed_on_return, s.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the stream was still open when the wrapper gave up"
    assert reads == 0 and pending == []


def test_a_caller_that_stops_reading_closes_the_stream():
    async def run():
        s = SilentStream(silent=False)
        gen = appmod._stream_with_heartbeat(StubWS(), s(), heartbeat=5)
        first = await gen.__anext__()
        await gen.aclose()
        return first, s.closed, s.reads, others()

    first, closed, reads, pending = run_alone(run(), 10)
    assert first == "late" and closed and reads == 1 and pending == []


@pytest.mark.parametrize("backend", ["local", "nim"])
@pytest.mark.parametrize("fail_on", ["ping", "token"])
def test_ws_rag_a_client_gone_mid_answer_ends_the_read(api, monkeypatch, backend, fail_on):
    """`ping`: gone during the silence before the first token. `token`: gone after it."""
    s = SilentStream(silent=fail_on == "ping")
    monkeypatch.setattr(broker, "chat_stream", s)
    monkeypatch.setattr(nim, "chat_stream", s)
    monkeypatch.setattr(nim, "available", lambda: True)
    monkeypatch.setattr(appmod, "_charge_nim", lambda *_a: None)
    monkeypatch.setattr(corpora, "retrieve", lambda cid, q, k, owner: HITS)
    monkeypatch.setattr(appmod, "HEARTBEAT_S", 0.01)
    ws = GoneWS(fail_on, headers={"x-platform-user": "owner-one"},
                inbox=[{"question": "what is NIM?", "corpus": 1, "backend": backend}])

    async def run():
        await ws_endpoint(api, "/ws/rag")(ws)
        closed_on_return = s.closed
        s.release.set()
        await asyncio.sleep(0.05)
        return closed_on_return, s.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the stream was still open when the socket's handler returned"
    assert reads == (0 if fail_on == "ping" else 1) and pending == []
    assert ws.close_code is not None
