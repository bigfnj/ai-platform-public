"""The rail relay streams a rail's response instead of holding it whole.

`_forward`'s ordinary branch answered with `Response(content=upstream.content)`: every response
that did not ask for text/event-stream (a 100 MB backup download, a job's whole output.zip) was
read into the gateway, the one process every rail shares, before the client saw a byte. Now both
branches relay the rail's raw bytes (`aiter_raw()`) as they arrive, under the rail's own
Content-Length and Content-Encoding, which describe those bytes exactly. Two things follow from
streaming that buffering never had to answer, and each is pinned here:

- a client that goes away stops the relay and closes the rail's response, on a server that says
  so through receive() (uvicorn, ASGI 2.3) and on one whose send() raises (ASGI 2.4);
- a rail that breaks off mid-body leaves the client's copy visibly cut short. The status line has
  gone by then, so the relay cannot turn it into a 502 any more, and the http middlewares would
  otherwise end the body cleanly, which a browser reads as a finished, shorter download.

Through the REAL ASGI app (both http middlewares, the guard, the route, the relay), with a rail
whose answer is made on demand and which refuses to run more than LAG bytes ahead of its client.
So "a chunk at a time" is measured at both ends, and the old relay fails fast at the watchdog
instead of holding 200 MB.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import struct

import httpx
import pytest
from starlette.requests import Request

from platform_gateway_app import main as m
from platform_gateway_app.config import GatewaySettings

CHUNK = 64 * 1024
MIB = 1024 * 1024
LAG = 8 * CHUNK                 # the most the rail lets the gateway run ahead of the client
RAIL = "syn-download-rail"      # synthetic: not a catalog id, so the access gate lets it through


def _chunk(i: int) -> bytes:
    """Chunk i of a download: different for every i, so a dropped, doubled or reordered chunk
    changes the hash."""
    return struct.pack(">Q", i) * (CHUNK // 8)


class _Meter:
    """Bytes the rail has produced and the client has been handed, and the widest gap seen."""

    def __init__(self) -> None:
        self.produced = 0
        self.delivered = 0
        self.max_lag = 0


class _Answer(httpx.AsyncByteStream):
    """A rail's response body, produced a chunk at a time as the relay asks for it.

    `watchdog` makes it refuse (as a broken connection would) to get more than LAG bytes ahead of
    what the client has received, so a relay that buffers fails here at once. `closed` is set by
    aclose() only, which is what httpx calls when the gateway closes the response: a generator
    merely dropped and collected later does not count."""

    def __init__(self, meter: _Meter, chunks: int, *, watchdog: bool = True,
                 fail_after: int | None = None, payload: bytes | None = None):
        self.meter = meter
        self.chunks = chunks
        self.watchdog = watchdog
        self.fail_after = fail_after
        self.payload = payload
        self.closed = False
        self.sha = hashlib.sha256()
        self.made = 0                   # chunks produced

    async def __aiter__(self):
        if self.payload is not None:
            self.meter.produced += len(self.payload)
            yield self.payload
            return
        for i in range(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise httpx.RemoteProtocolError("the rail went away mid-body")
            if self.watchdog and self.meter.produced - self.meter.delivered >= LAG:
                # Counted as the gap it would have become, so the lag assertion is what fails.
                self.meter.max_lag = self.meter.produced - self.meter.delivered + CHUNK
                raise httpx.ReadError(
                    f"the gateway is holding {self.meter.produced - self.meter.delivered} bytes "
                    "its client has not been handed")
            data = _chunk(i)
            self.sha.update(data)
            self.made += 1
            self.meter.produced += len(data)
            self.meter.max_lag = max(self.meter.max_lag, self.meter.produced - self.meter.delivered)
            yield data
            await asyncio.sleep(0)      # a socket read is a checkpoint; so is this

    async def aclose(self) -> None:
        # A real close waits on the socket once, so a close made inside a cancelled scope is
        # cancelled with it unless it is shielded. This makes the fake one the same.
        await asyncio.sleep(0)
        self.closed = True


class _Rail(httpx.AsyncBaseTransport):
    def __init__(self, answer: _Answer, headers: list[tuple[str, str]], status: int = 200):
        self.answer = answer
        self.headers = headers
        self.status = status
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, headers=self.headers, stream=self.answer)


class _Client:
    """The browser end: takes every message the app sends, and can walk away after `leave_after`
    body chunks, the way uvicorn reports it (send() goes quiet, receive() says disconnect) or the
    way an ASGI 2.4 server does (send() raises OSError)."""

    def __init__(self, meter: _Meter, leave_after: int | None = None, server: str = "uvicorn"):
        self.meter = meter
        self.leave_after = leave_after
        self.server = server
        self.sent: list[dict] = []
        self.body_messages = 0
        self.sha = hashlib.sha256()
        self.gone = asyncio.Event()
        self._asked = False

    async def receive(self) -> dict:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, msg: dict) -> None:
        if self.gone.is_set():
            if self.server == "asgi-2.4":
                raise OSError("the client went away")
            return
        self.sent.append(msg)
        if msg["type"] == "http.response.body":
            body = msg.get("body", b"")
            self.sha.update(body)
            self.meter.delivered += len(body)
            if body:
                self.body_messages += 1
            if self.leave_after is not None and self.body_messages >= self.leave_after:
                self.gone.set()

    @property
    def start(self) -> dict:
        return next(x for x in self.sent if x["type"] == "http.response.start")

    def header(self, name: str) -> list[str]:
        return [v.decode() for k, v in self.start["headers"] if k.decode().lower() == name]

    @property
    def completed(self) -> bool:
        return any(x["type"] == "http.response.body" and not x.get("more_body", False)
                   for x in self.sent)


def _scope(method: str, path: str, headers: list[tuple[str, str]], spec: str = "2.3") -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": spec},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}


@pytest.fixture()
def wire(monkeypatch):
    """wire(rail) points the real app at `rail` behind the synthetic id; returns call()."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(m.app.state, "backends", {RAIL: "http://rail:9"}, raising=False)
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)

    def wire_(rail: _Rail):
        monkeypatch.setattr(m.app.state, "http", httpx.AsyncClient(transport=rail), raising=False)

    async def call(client: _Client, accept: str = "application/json", spec: str = "2.3",
                   method: str = "GET"):
        scope = _scope(method, f"/{RAIL}/api/download", [("accept", accept)], spec)
        await asyncio.wait_for(m.app(scope, client.receive, client.send), timeout=60)

    return wire_, call


# --- a download is relayed as it arrives ------------------------------------------------------

async def test_a_200_mb_download_is_relayed_a_chunk_at_a_time_and_arrives_byte_exact(wire):
    """The old relay read all 200 MB before sending a byte; the watchdog stops it at LAG."""
    wire_, call = wire
    meter = _Meter()
    total = 200 * MIB
    answer = _Answer(meter, total // CHUNK)
    wire_(_Rail(answer, [("content-type", "application/zip"), ("content-length", str(total)),
                         ("content-disposition", 'attachment; filename="bundle.zip"')]))
    client = _Client(meter)
    await call(client)
    assert meter.max_lag <= LAG, (
        f"the gateway held {meter.max_lag} bytes of the rail's answer that its client did not "
        "have yet: the response was buffered, not streamed")
    assert client.start["status"] == 200, client.sent[:2]
    assert meter.delivered == total and client.completed
    assert client.sha.hexdigest() == answer.sha.hexdigest(), "the bytes changed on the way"
    # Its own length still goes with it, so the browser can show progress and tell a short
    # transfer from a whole one; and the filename still comes through.
    assert client.header("content-length") == [str(total)]
    assert client.header("content-disposition") == ['attachment; filename="bundle.zip"']
    assert answer.closed, "the rail's response was never closed"


async def test_the_rails_encoding_and_length_describe_the_bytes_relayed(wire):
    """Raw bytes under the rail's own labels. The old relay decoded the body (httpx does, for
    `.content`), dropped Content-Encoding and recomputed the length: harmless when buffered,
    wrong for a stream, where only the raw bytes have a length known up front."""
    wire_, call = wire
    meter = _Meter()
    plain = b'{"rows": [' + b",".join(b'{"n": %d}' % i for i in range(2000)) + b"]}"
    packed = gzip.compress(plain)
    answer = _Answer(meter, 0, payload=packed)
    wire_(_Rail(answer, [("content-type", "application/json"), ("content-encoding", "gzip"),
                         ("content-length", str(len(packed)))]))
    client = _Client(meter)
    await call(client)
    body = b"".join(x.get("body", b"") for x in client.sent if x["type"] == "http.response.body")
    assert body == packed, "the relay changed the rail's bytes"
    assert client.header("content-encoding") == ["gzip"]
    assert client.header("content-length") == [str(len(packed))]
    assert gzip.decompress(body) == plain


async def test_repeated_set_cookie_lines_survive_the_streamed_relay(wire):
    wire_, call = wire
    meter = _Meter()
    cookies = ["a=1; Path=/; HttpOnly", "b=2; Path=/"]
    wire_(_Rail(_Answer(meter, 2), [("content-type", "application/octet-stream")]
                + [("set-cookie", c) for c in cookies]))
    client = _Client(meter)
    await call(client)
    assert client.header("set-cookie") == cookies


async def test_a_head_relays_the_rails_length_and_no_body(monkeypatch):
    """HEAD reaches the relay through a rail's root-asset routes. The rail's Content-Length is
    the length a GET would bring; the old relay answered every HEAD with Content-Length: 0."""
    meter = _Meter()
    rail = _Rail(_Answer(meter, 0, payload=b""),
                 [("content-type", "image/svg+xml"), ("content-length", "1234")])
    monkeypatch.setattr(m.app.state, "http", httpx.AsyncClient(transport=rail), raising=False)
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)
    client = _Client(meter)
    scope = _scope("HEAD", "/logos/x.svg", [("accept", "image/svg+xml")])
    resp = await m._forward("http://rail:9/logos/x.svg", RAIL, Request(scope, client.receive))
    await resp(scope, client.receive, client.send)
    assert client.start["status"] == 200
    assert client.header("content-length") == ["1234"]
    assert meter.delivered == 0 and client.completed


# --- a client that goes away -------------------------------------------------------------------

@pytest.mark.parametrize("server", ["uvicorn", "asgi-2.4"])
@pytest.mark.parametrize("accept", ["application/json", "text/event-stream"])
async def test_a_client_that_goes_away_stops_the_relay_and_closes_the_rails_response(
        wire, server, accept):
    """16 MiB on offer, the client takes 4 chunks and leaves. The relay must stop asking the
    rail for more and close its response, or every abandoned download holds a rail connection
    and keeps the rail reading for nobody. On an ASGI 2.4 server the old event-stream branch
    left the response open: its close was a BackgroundTask, which a disconnect never reaches."""
    wire_, call = wire
    meter = _Meter()
    answer = _Answer(meter, 16 * MIB // CHUNK, watchdog=False)
    wire_(_Rail(answer, [("content-type", "application/octet-stream")]))
    client = _Client(meter, leave_after=4, server=server)
    spec = "2.4" if server == "asgi-2.4" else "2.3"
    try:
        await call(client, accept=accept, spec=spec)
    except OSError:
        assert server == "asgi-2.4"     # that server's way of saying the client left
    assert client.start["status"] == 200
    assert answer.made <= 4 + LAG // CHUNK, (
        f"the rail made {answer.made} chunks for a client that took 4 and left")
    assert answer.closed, "the rail's response was left open after the client went away"


# --- a rail that breaks off mid-body -----------------------------------------------------------

@pytest.mark.parametrize("framing", ["chunked", "content-length"])
async def test_a_rail_that_breaks_off_mid_body_leaves_the_download_cut_short(wire, framing):
    """Three chunks, then the rail's connection dies. The client must never see the response
    finish: a finished chunked body is a whole file as far as the browser knows. The server
    closes the connection instead, so the download fails where it can be retried."""
    wire_, call = wire
    meter = _Meter()
    answer = _Answer(meter, 10, fail_after=3)
    hdrs = [("content-type", "application/zip")]
    if framing == "content-length":
        hdrs.append(("content-length", str(10 * CHUNK)))
    wire_(_Rail(answer, hdrs))
    client = _Client(meter)
    with pytest.raises(Exception) as err:
        await call(client)
    assert client.start["status"] == 200, "the status line should be the rail's, already sent"
    assert meter.delivered == 3 * CHUNK
    assert not client.completed, (
        "the response was ended cleanly, so the client takes a cut-off download for a whole one")
    assert "mid-body" in str(err.value) or "cut short" in str(err.value), err.value
    assert answer.closed
