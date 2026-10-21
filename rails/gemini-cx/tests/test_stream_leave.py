"""A client that leaves ends the broker read: /ws/ask's heartbeat wrapper and the socket behind it.

`_stream_with_heartbeat` keeps the next broker read in a task of its own while it pings. Until
2026-10-14 a ping that raised, because the client had gone, left that read running: the rail
went on reading /v1/chat/stream for nobody until the next frame, so the broker's client-gone
check never fired, a Claude CLI call kept its slot to its first token, and a CLI timeout started
a local fallback no one would read. What these pin:

* a ping that raises cancels the read in flight, and the broker stream's own `finally` has run
  by the time the wrapper's exception reaches its caller; released afterwards, it reads nothing;
* a caller that stops reading mid-answer (its token send raised) closes the stream too, because
  the route holds the wrapper under `aclosing`;
* a send to a client that left raises WebSocketDisconnect in Starlette 1.7 (its OSError, code
  1006), not RuntimeError, so the socket ends without a traceback in the log.

Offline: the broker is a generator that records what was read and when it was closed.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from starlette.websockets import WebSocketDisconnect

from gemini_cx import api, broker, config
from gemini_cx.api import create_api
# What the loop still runs, sampled inside it, and a run that is the loop's one task on every
# Python (tools/tests/loop_tasks.py; conftest puts tools/tests on sys.path).
from loop_tasks import others, run_alone


class SilentBroker:
    """One /v1/chat/stream answer that says nothing until `release` is set, then its tokens.
    Records each token read, and whether its `finally` (the HTTP request's unwinding) ran."""

    def __init__(self, tokens: tuple[str, ...] = ("late", "later"), silent: bool = True):
        self.tokens = tokens
        self.silent = silent
        self.release = asyncio.Event()
        self.reads = 0
        self.closed = False

    def chat_stream(self, *_args, **_kwargs):
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


class GoneSocket:
    """A socket whose client has left by the time a frame of type `fail_on` is sent: the send
    raises WebSocketDisconnect as Starlette 1.7's does (an OSError from the server, code 1006)."""

    def __init__(self, request: dict, fail_on: str):
        self.headers = {"x-platform-user": "owner-one"}
        self.inbox = [json.dumps(request)]
        self.fail_on = fail_on
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        if self.inbox:
            return self.inbox.pop(0)
        raise WebSocketDisconnect(1006)

    async def send_json(self, data: dict) -> None:
        if data.get("type") == self.fail_on:
            raise WebSocketDisconnect(1006)
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def test_a_ping_to_a_client_that_left_ends_the_broker_read():
    async def run():
        b = SilentBroker()
        ws = GoneSocket({}, fail_on="ping")
        with pytest.raises(WebSocketDisconnect):
            async for _ in api._stream_with_heartbeat(ws, b.chat_stream(), heartbeat=0.01):
                pass
        closed_on_return = b.closed
        b.release.set()                 # the broker's first token arrives after all
        await asyncio.sleep(0.05)
        return closed_on_return, b.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the broker stream was still open when the wrapper gave up"
    assert reads == 0 and pending == []


def test_a_caller_that_stops_reading_closes_the_stream():
    async def run():
        b = SilentBroker(silent=False)
        gen = api._stream_with_heartbeat(GoneSocket({}, "never"), b.chat_stream(), heartbeat=5)
        first = await gen.__anext__()
        await gen.aclose()
        return first, b.closed, b.reads, others()

    first, closed, reads, pending = run_alone(run(), 10)
    assert first == "late" and closed and reads == 1 and pending == []


@pytest.mark.parametrize("fail_on", ["ping", "token"])
def test_ws_ask_a_client_gone_mid_answer_ends_the_read_and_logs_no_traceback(
        tmp_path, monkeypatch, caplog, fail_on):
    """`ping`: gone during the silence before the first token. `token`: gone after it."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    monkeypatch.setattr(api, "_retrieve", lambda *_a: [])
    b = SilentBroker(silent=fail_on == "ping")
    monkeypatch.setattr(broker, "chat_stream", b.chat_stream)
    if fail_on == "ping":
        monkeypatch.setattr(api, "HEARTBEAT_S", 0.01)
    ws = GoneSocket({"question": "How is GECX priced?"}, fail_on=fail_on)
    endpoint = next(r.endpoint for r in create_api().routes
                    if getattr(r, "path", None) == "/ws/ask")

    async def run():
        with caplog.at_level(logging.INFO):
            await endpoint(ws)
        closed_on_return = b.closed
        b.release.set()
        await asyncio.sleep(0.05)
        return closed_on_return, b.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the broker stream was still open when the socket's handler returned"
    assert reads == (0 if fail_on == "ping" else 1) and pending == []
    assert [r.getMessage() for r in caplog.records if r.exc_info] == [], \
        "a client leaving was logged as a failure"
    assert ws.closed
