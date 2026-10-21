"""A client that leaves ends the broker read: the heartbeat wrappers and the sockets behind them.

Both wrappers (api._stream_with_heartbeat for /ws/ask, generate._with_heartbeat for
/ws/scenario's passes) keep the next broker read in a task of its own while they ping. Until
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

from smb_partner import api, broker, generate
from smb_partner.api import create_api
# What the loop still runs, sampled inside it, and a run that is the loop's one task on every
# Python (tools/tests/loop_tasks.py; conftest puts tools/tests on sys.path).
from loop_tasks import others, run_alone

PRACTICE = "grow-your-practice"


class SilentBroker:
    """One /v1/chat/stream answer that says nothing until `release` is set, then its tokens.
    Records each token read, and whether its `finally` (the HTTP request's unwinding) ran."""

    def __init__(self, tokens: tuple[str, ...] = ("late", "later"), silent: bool = True):
        self.tokens = tokens
        self.silent = silent
        self.release = asyncio.Event()
        self.reads = 0
        self.opened = 0
        self.closed = False

    def chat_stream(self, *_args, **_kwargs):
        self.opened += 1
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


def _endpoint(path: str):
    return next(r.endpoint for r in create_api().routes if getattr(r, "path", None) == path)


def _tracebacks(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.exc_info]


# --- api._stream_with_heartbeat ------------------------------------------------------------

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
        ws = GoneSocket({}, fail_on="never")
        gen = api._stream_with_heartbeat(ws, b.chat_stream(), heartbeat=5)
        first = await gen.__anext__()
        await gen.aclose()
        return first, b.closed, b.reads, others()

    first, closed, reads, pending = run_alone(run(), 10)
    assert first == "late" and closed and reads == 1 and pending == []


@pytest.mark.parametrize("fail_on", ["ping", "token"])
def test_ws_ask_a_client_gone_mid_answer_ends_the_read_and_logs_no_traceback(
        fake_broker, monkeypatch, caplog, fail_on):
    """`ping`: gone during the silence before the first token. `token`: gone after it."""
    b = SilentBroker(silent=fail_on == "ping")
    monkeypatch.setattr(broker, "chat_stream", b.chat_stream)
    if fail_on == "ping":
        monkeypatch.setattr(api, "HEARTBEAT_S", 0.01)
    ws = GoneSocket({"question": "What does a partner do first?"}, fail_on=fail_on)

    async def run():
        with caplog.at_level(logging.INFO):
            await _endpoint("/ws/ask")(ws)
        closed_on_return = b.closed
        b.release.set()
        await asyncio.sleep(0.05)
        return closed_on_return, b.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the broker stream was still open when the socket's handler returned"
    assert reads == (0 if fail_on == "ping" else 1) and pending == []
    assert _tracebacks(caplog) == [], "a client leaving was logged as a failure"
    assert ws.closed


# --- generate._with_heartbeat and the scenario socket --------------------------------------

def test_a_scenario_ping_to_a_client_that_left_ends_the_broker_read(monkeypatch):
    monkeypatch.setattr(generate, "HEARTBEAT_S", 0.01)

    async def emit(event: str, payload: dict) -> None:
        if event == "ping":
            raise WebSocketDisconnect(1006)

    async def run():
        b = SilentBroker()
        with pytest.raises(WebSocketDisconnect):
            async for _ in generate._with_heartbeat(b.chat_stream(), emit):
                pass
        closed_on_return = b.closed
        b.release.set()
        await asyncio.sleep(0.05)
        return closed_on_return, b.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed and reads == 0 and pending == []


@pytest.mark.parametrize("fail_on", ["ping", "token"])
def test_ws_scenario_a_client_gone_mid_pass_ends_the_read_and_the_build(
        fake_broker, monkeypatch, caplog, fail_on):
    """The pass's stream is closed when the handler returns, no later pass opens one, and the
    log carries no traceback."""
    b = SilentBroker(silent=fail_on == "ping")
    monkeypatch.setattr(broker, "chat_stream", b.chat_stream)
    monkeypatch.setattr(generate, "HEARTBEAT_S", 0.01)
    ws = GoneSocket({"scenario_id": PRACTICE, "answers": {}}, fail_on=fail_on)

    async def run():
        with caplog.at_level(logging.INFO):
            await _endpoint("/ws/scenario")(ws)
        closed_on_return = b.closed
        b.release.set()
        await asyncio.sleep(0.05)
        return closed_on_return, b.reads, others()

    closed, reads, pending = run_alone(run(), 10)
    assert closed, "the pass's broker stream was still open when the socket's handler returned"
    assert b.opened == 1, "a pass ran after the client had left"
    assert reads == (0 if fail_on == "ping" else 1) and pending == []
    assert _tracebacks(caplog) == [], "a client leaving was logged as a failure"
