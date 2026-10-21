"""Slow uploads and long streams cannot starve the relay's page loads.

Since the relay streams request bodies, an upload holds an upstream connection until its last
byte arrives; since it streams responses, so does a download, for as long as its client takes to
read it, and an event stream for its whole life. All of them drew on ONE httpx client with
httpx's default pool: 100 connections and, at the client's 600 s timeout, a 600 s wait for one.
A hundred slow uploads (or open job logs) and every relay waited, plain page loads included.

Now the relay has two clients (`_relay_clients`, built by the lifespan and closed by it):
- every request without an upload, and every event stream, goes out on a client with NO cap on
  connections, so nothing queues behind a long transfer;
- an upload (a body over 1 MiB, or of unknown length) goes out on its own client, capped, so a
  flood of uploads queues behind itself, and one that waits too long for a slot is a 503 to
  retry, not a 502 that blames the rail.

These run against a REAL socket: a rail on 127.0.0.1 that never reads an upload's body and
holds an event stream open, behind the clients the shipped lifespan builds. httpx's pool lives in
its real transport, so a mock transport would test nothing here.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools

import httpx
import pytest

from platform_gateway_app import main as m
from platform_gateway_app import scheduler

RAIL = "syn-pool-rail"          # synthetic: not a catalog id, so the access gate lets it through
CHUNK = 64 * 1024
MIB = 1024 * 1024
HELD = 101                      # one more than httpx's default pool holds
# Seconds. A page load behind no queue answers in milliseconds (3-4 ms measured behind 101
# uploads), and one queued behind a full pool waits that pool's own 600 s (30 s for the
# uploads'), so 10 s is far from both. It was 3 s.
PROMPT = 10.0
WHOLE = 90.0                    # seconds: the most one test may take, teardown included


def _bounded(fn):
    """Run the whole test, teardown of the gateway and rail included, under WHOLE seconds, so
    a hang anywhere in it fails this test with its traceback instead of stalling the suite."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        await asyncio.wait_for(fn(*args, **kwargs), WHOLE)
    return run


class _SlowRail:
    """A rail on a real socket. /upload reads the head and never the body; /events answers one
    event and stays open; /page and /save answer at once (reading /save's small body first)."""

    def __init__(self) -> None:
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.uploads = 0
        self.events = 0
        self._tasks: set[asyncio.Task] = set()
        self._writers: list[asyncio.StreamWriter] = []

    async def __aenter__(self) -> "_SlowRail":
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        for t in list(self._tasks):
            t.cancel()
        for w in self._writers:
            with contextlib.suppress(Exception):
                w.close()
        assert self.server is not None
        self.server.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._tasks.add(asyncio.current_task())
        self._writers.append(writer)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                line, *rest = head.decode("latin-1").split("\r\n")
                path = line.split(" ")[1]
                hdrs = dict(h.lower().split(": ", 1) for h in rest if ": " in h)
                if "/upload" in path:
                    self.uploads += 1
                    await asyncio.Event().wait()            # never read the body
                if "/events" in path:
                    self.events += 1
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                                 b"connection: close\r\n\r\ndata: one\n\n")
                    await writer.drain()
                    await asyncio.Event().wait()            # an open job log
                n = int(hdrs.get("content-length", "0"))
                if n:
                    await reader.readexactly(n)
                writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\n"
                             b"content-length: 2\r\n\r\nok")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._tasks.discard(asyncio.current_task())


class _Client:
    """One browser request: hands over `chunks`, then (if `stall`) never sends the rest."""

    def __init__(self, chunks: list[bytes], stall: bool = False):
        self.chunks = list(chunks)
        self.stall = stall
        self.calls = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        if self.chunks:
            self.calls += 1
            chunk = self.chunks.pop(0)
            return {"type": "http.request", "body": chunk,
                    "more_body": bool(self.chunks) or self.stall}
        if self.stall and self.calls:
            await asyncio.Event().wait()                    # the rest of the upload never comes
        await asyncio.Event().wait()                        # waiting for a disconnect
        return {"type": "http.disconnect"}

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    @property
    def status(self) -> int | None:
        return next((x["status"] for x in self.sent if x["type"] == "http.response.start"), None)

    def header(self, name: str) -> str | None:
        start = next(x for x in self.sent if x["type"] == "http.response.start")
        return next((v.decode() for k, v in start["headers"] if k.decode().lower() == name), None)

    @property
    def body(self) -> bytes:
        return b"".join(x.get("body", b"") for x in self.sent if x["type"] == "http.response.body")


def _scope(method: str, path: str, headers: list[tuple[str, str]]) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}


def _upload(framing: str) -> tuple[_Client, dict]:
    """A 10 MiB upload whose client sends one chunk and then goes quiet."""
    hdrs = ([("content-length", str(10 * MIB))] if framing == "content-length"
            else [("transfer-encoding", "chunked")])
    return _Client([b"u" * CHUNK], stall=True), _scope("POST", f"/{RAIL}/api/upload", hdrs)


async def _call(client: _Client, scope: dict) -> None:
    await m.app(scope, client.receive, client.send)


async def _answers_promptly(scope: dict, client: _Client, what: str) -> _Client:
    try:
        await asyncio.wait_for(_call(client, scope), timeout=PROMPT)
    except TimeoutError:
        pytest.fail(f"{what} waited more than {PROMPT} s for a relay connection")
    return client


async def _until(cond, what: str, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            pytest.fail(f"never got there: {what}")
        await asyncio.sleep(0.02)


def _count_sends(monkeypatch) -> dict[str, int]:
    """How many relayed requests have reached `send` on either of the relay's clients, by the
    last segment of their path ("upload", "events").

    httpx files a request in its pool's queue before its first await, so once N requests are
    counted here, all N hold a connection or wait in line for one, and a request sent after
    them waits behind them in the same pool. That is the moment the tests below need; a 0.3 s
    sleep stood in for it, and a host slow to start the uploads let a page load in ahead of
    them, so a pool too small for the uploads could pass."""
    sent: dict[str, int] = {}
    for client in {id(c): c for c in (m.app.state.http, m.app.state.http_body)}.values():
        async def send(request, *a, _real=client.send, **kw):
            key = request.url.path.rsplit("/", 1)[-1]
            sent[key] = sent.get(key, 0) + 1
            return await _real(request, *a, **kw)
        monkeypatch.setattr(client, "send", send)
    return sent


@contextlib.asynccontextmanager
async def _gateway(monkeypatch, tmp_path, rail_port: int):
    """The shipped lifespan (it builds and closes the relay's clients), on a throwaway database,
    with the scheduler's loop idle and the synthetic rail as the only backend."""
    monkeypatch.setenv("PLATFORM_DB_URL", f"sqlite:///{tmp_path / 'gw.db'}")
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    for attr in ("settings", "backends", "broker", "http", "http_body", "db", "sched_task"):
        monkeypatch.setattr(m.app.state, attr, None, raising=False)
    monkeypatch.setattr(m, "_seed_admin", lambda *a, **k: None)

    async def idle(_app, _tick):
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, "loop", idle)
    async with m.lifespan(m.app):
        m.app.state.backends = {RAIL: f"http://127.0.0.1:{rail_port}"}
        yield


async def _cancel(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 20)


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
@_bounded
async def test_with_101_slow_uploads_in_flight_a_page_load_and_a_save_still_answer(
        monkeypatch, tmp_path, framing):
    async with _SlowRail() as rail, _gateway(monkeypatch, tmp_path, rail.port):
        sent = _count_sends(monkeypatch)
        uploads = [asyncio.create_task(_call(*_upload(framing))) for _ in range(HELD)]
        try:
            await _until(lambda: rail.uploads >= 32, "32 uploads holding a rail connection")
            await _until(lambda: sent.get("upload", 0) >= HELD,
                         f"all {HELD} uploads holding or queued for a relay connection")
            page = await _answers_promptly(_scope("GET", f"/{RAIL}/api/page", []),
                                           _Client([]), f"a page load behind {HELD} uploads")
            assert page.status == 200 and page.body == b"ok"
            # A small JSON write is not an upload: it never waits on the uploads' pool.
            save = await _answers_promptly(
                _scope("POST", f"/{RAIL}/api/save", [("content-length", "20")]),
                _Client([b'{"name": "a recipe"}']), f"a 20-byte save behind {HELD} uploads")
            assert save.status == 200 and save.body == b"ok"
        finally:
            await _cancel(uploads)


@_bounded
async def test_with_101_event_streams_open_a_page_load_still_answers(monkeypatch, tmp_path):
    """Open job logs, and since the relay streams responses, slow downloads too, hold their
    connection for as long as their client reads. They share the client page loads use, so that
    client has no cap to run out of."""
    async with _SlowRail() as rail, _gateway(monkeypatch, tmp_path, rail.port):
        sent = _count_sends(monkeypatch)
        streams = [asyncio.create_task(_call(
            _Client([]), _scope("GET", f"/{RAIL}/api/events", [("accept", "text/event-stream")])))
            for _ in range(HELD)]
        try:
            await _until(lambda: rail.events >= HELD - 1, f"{HELD - 1} event streams open")
            await _until(lambda: sent.get("events", 0) >= HELD,
                         f"all {HELD} event streams holding or queued for a relay connection")
            page = await _answers_promptly(_scope("GET", f"/{RAIL}/api/page", []),
                                           _Client([]), f"a page load beside {HELD} streams")
            assert page.status == 200 and page.body == b"ok"
        finally:
            await _cancel(streams)


@_bounded
async def test_the_lifespan_builds_both_clients_and_closes_both(monkeypatch, tmp_path):
    async with _SlowRail() as rail, _gateway(monkeypatch, tmp_path, rail.port):
        plain, uploads = m.app.state.http, m.app.state.http_body
        assert isinstance(plain, httpx.AsyncClient) and isinstance(uploads, httpx.AsyncClient)
        assert plain is not uploads
    assert plain.is_closed and uploads.is_closed, "the lifespan left a relay client open"




@pytest.fixture()
async def one_slot(monkeypatch):
    """The relay's real clients with the uploads' pool shrunk to one connection and a 0.3 s
    wait, and a slow rail behind them. Yields the rail."""
    monkeypatch.setattr(m, "_UPLOAD_LIMITS", httpx.Limits(max_connections=1))
    monkeypatch.setattr(m, "_UPLOAD_POOL_WAIT", 0.3)
    plain, uploads = m._relay_clients()
    monkeypatch.setattr(m.app.state, "http", plain, raising=False)
    monkeypatch.setattr(m.app.state, "http_body", uploads, raising=False)
    monkeypatch.setattr(m.app.state, "settings", m.GatewaySettings(), raising=False)
    try:
        async with _SlowRail() as rail:
            monkeypatch.setattr(m.app.state, "backends",
                                {RAIL: f"http://127.0.0.1:{rail.port}"}, raising=False)
            yield rail
    finally:
        await plain.aclose()
        await uploads.aclose()


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
@_bounded
async def test_an_upload_that_cannot_get_a_slot_is_a_503_to_retry_and_reads_nothing(
        one_slot, framing):
    """With the one slot held, the second upload waits 0.3 s and is told to retry. Not a 502,
    which would blame a rail that is fine; and nothing of its body is read while it waits,
    since httpx takes a connection before it sends a byte."""
    rail = one_slot
    first = asyncio.create_task(_call(*_upload(framing)))
    try:
        await _until(lambda: rail.uploads >= 1, "the first upload holding the slot")
        second, scope = _upload(framing)
        await asyncio.wait_for(_call(second, scope), timeout=PROMPT)
        assert second.status == 503, second.sent[:2]
        assert second.header("retry-after")
        assert b"upload" in second.body
        assert second.calls == 0, "the gateway read a body it had no slot to send"
        assert rail.uploads == 1
    finally:
        await _cancel([first])


@_bounded
async def test_an_event_stream_that_carries_a_body_is_not_an_upload(one_slot):
    """A fetch() that POSTs a prompt and reads the answer as text/event-stream holds its
    connection for the whole answer. It goes out on the uncapped client, so open token streams
    never fill the uploads' slots, and a full upload pool never holds one back."""
    rail = one_slot
    first = asyncio.create_task(_call(*_upload("chunked")))
    try:
        await _until(lambda: rail.uploads >= 1, "an upload holding the only slot")
        stream = _Client([b'{"prompt": "hello"}'])
        scope = _scope("POST", f"/{RAIL}/api/events", [("accept", "text/event-stream"),
                                                       ("transfer-encoding", "chunked")])
        task = asyncio.create_task(_call(stream, scope))
        try:
            await _until(lambda: stream.status is not None, "the event stream's answer", PROMPT)
            assert stream.status == 200
            await _until(lambda: b"data: one" in stream.body, "the first event", PROMPT)
        finally:
            await _cancel([task])
    finally:
        await _cancel([first])
