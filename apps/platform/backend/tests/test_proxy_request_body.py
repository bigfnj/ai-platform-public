"""The rail relay streams a request body and stops it at the rail's ceiling.

`_forward` used to `await request.body()`: every proxied body was held whole in the gateway, the
process every rail shares, before one byte was relayed, and nothing capped it. A rail's own
limit bounds what that rail receives, never what the gateway holds, so one large POST from any
signed-in user to any rail's API sat in the gateway's memory in full.

Now each rail has a ceiling (its manifest's `max_request_bytes`, mirrored in
config.MAX_REQUEST_BYTES, else PLATFORM_MAX_REQUEST_BYTES). A declared length over it is refused
before the body is read; any other body is relayed as it arrives and cut off with a 413 once it
passes the ceiling.

Most of these go through the REAL ASGI app (both http middlewares, the route, the relay), with a
client that hands the body over one chunk at a time and counts what was taken, and a rail that
reads the body the way httpx's own transport does, as a stream. So "read no more than the
ceiling plus one chunk" is measured at the socket's edge, not inferred.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from platform_gateway_app import main as m
from platform_gateway_app.catalog import APP_IDS
from platform_gateway_app.config import (
    DEFAULT_MAX_REQUEST_BYTES,
    MAX_REQUEST_BYTES,
    GatewaySettings,
)

MIB = 1024 * 1024
CHUNK = 64 * 1024
RAIL = "syn-body-rail"          # synthetic: not a catalog id, so the access gate lets it through


class _Client:
    """The client side of one request: hands the app its body a chunk at a time, on demand."""

    def __init__(self, chunks: list[bytes]):
        # A server always delivers at least one http.request message, an empty one for a
        # request with no body, so asking for the body of a GET is still a read.
        self._chunks = list(chunks) or [b""]
        self._i = 0
        self.calls = 0          # receive() calls that handed over a body message
        self.read = 0           # body bytes handed over

    async def receive(self) -> dict:
        if self._i < len(self._chunks):
            chunk = self._chunks[self._i]
            self._i += 1
            self.calls += 1
            self.read += len(chunk)
            return {"type": "http.request", "body": chunk,
                    "more_body": self._i < len(self._chunks)}
        await asyncio.Event().wait()        # body done: wait (cancellably) for the disconnect
        return {"type": "http.disconnect"}


class _Rail(httpx.AsyncBaseTransport):
    """A rail at the far end: reads the body as httpx's own transport does, as a stream.

    httpx.MockTransport is no use here: it calls `request.aread()` before the handler runs, which
    would read a streamed body whole and hide the very thing under test."""

    def __init__(self, client: _Client | None = None, stop_after: int | None = None):
        self.client = client
        self.stop_after = stop_after
        self.calls = 0
        self.headers: httpx.Headers | None = None
        self.got = bytearray()
        self.client_calls_at_first_chunk: int | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.headers = request.headers
        n = 0
        async for chunk in request.stream:
            if self.client_calls_at_first_chunk is None and self.client is not None:
                self.client_calls_at_first_chunk = self.client.calls
            self.got += chunk
            n += 1
            if self.stop_after is not None and n >= self.stop_after:
                break
        ctype = ("text/event-stream" if "text/event-stream" in request.headers.get("accept", "")
                 else "application/json")
        return httpx.Response(200, headers={"content-type": ctype}, stream=_Answer())


class _Answer(httpx.AsyncByteStream):
    """A response body the relay can read once, buffered or streamed, like a real one."""

    async def __aiter__(self):
        yield b'{"ok":true}'


def _wire(monkeypatch, rail: httpx.AsyncBaseTransport) -> None:
    """Both of the relay's clients (an upload goes out on its own) lead to this one rail."""
    client = httpx.AsyncClient(transport=rail)
    monkeypatch.setattr(m.app.state, "http", client, raising=False)
    monkeypatch.setattr(m.app.state, "http_body", client, raising=False)


def _scope(method: str, path: str, headers: list[tuple[str, str]]) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}


@pytest.fixture()
def relay(monkeypatch):
    """call(method, chunks, headers, ceiling=None) -> (status, body, client, rail) through the
    real app, to a synthetic rail whose ceiling is the platform default (set per call)."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(m.app.state, "backends", {RAIL: "http://rail:9"}, raising=False)

    async def call(method: str, chunks: list[bytes], headers: list[tuple[str, str]],
                   ceiling: int = 25 * MIB, stop_after: int | None = None):
        monkeypatch.setattr(m.app.state, "settings",
                            GatewaySettings(max_request_bytes=ceiling), raising=False)
        client = _Client(chunks)
        rail = _Rail(client, stop_after)
        _wire(monkeypatch, rail)
        sent: list[dict] = []

        async def send(msg: dict) -> None:
            sent.append(msg)

        await m.app(_scope(method, f"/{RAIL}/api/upload", headers), client.receive, send)
        start = next(x for x in sent if x["type"] == "http.response.start")
        body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
        return start["status"], body, client, rail

    return call


def _chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


# --- refused before a byte is read ---------------------------------------------------------

async def test_a_declared_300_mb_body_to_a_25_mb_rail_is_refused_before_a_byte_is_read(relay):
    # The client would send 300 MiB if asked; it is never asked.
    status, body, client, rail = await relay(
        "POST", [b"x" * CHUNK] * 8, [("content-length", str(300 * MIB))], ceiling=25 * MIB)
    assert status == 413, body
    assert b"25 MB" in body and RAIL.encode() in body, body
    assert client.calls == 0 and client.read == 0, "the gateway read the body it was refusing"
    assert rail.calls == 0, "the rail was contacted for a body the gateway had already refused"


# --- cut off as it streams ------------------------------------------------------------------

async def test_a_chunked_body_past_the_ceiling_is_cut_off_one_chunk_later(relay):
    status, body, client, rail = await relay(
        "POST", [b"y" * CHUNK] * 64, [("transfer-encoding", "chunked")], ceiling=1 * MIB)
    assert status == 413, body
    assert client.read <= 1 * MIB + CHUNK, f"read {client.read} bytes past a {MIB}-byte ceiling"
    assert client.calls < 64, "the whole body was read before it was refused"
    assert len(rail.got) <= 1 * MIB, "more than the ceiling reached the rail"


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
async def test_a_body_exactly_at_the_ceiling_reaches_the_rail_byte_for_byte(relay, framing):
    data = os.urandom(1 * MIB)
    hdrs = ([("content-length", str(len(data)))] if framing == "content-length"
            else [("transfer-encoding", "chunked")])
    status, body, _client, rail = await relay("POST", _chunks(data), hdrs, ceiling=1 * MIB)
    assert status == 200, body
    assert bytes(rail.got) == data
    if framing == "content-length":
        # A known length is passed on, so a rail's own Content-Length check still works...
        assert rail.headers["content-length"] == str(len(data))
        assert "transfer-encoding" not in rail.headers
    else:
        # ...and a body that declared none goes on chunked.
        assert rail.headers["transfer-encoding"] == "chunked"
        assert "content-length" not in rail.headers


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
async def test_one_byte_over_the_ceiling_is_refused(relay, framing):
    data = b"z" * (1 * MIB + 1)
    hdrs = ([("content-length", str(len(data)))] if framing == "content-length"
            else [("transfer-encoding", "chunked")])
    status, body, _client, _rail = await relay("POST", _chunks(data), hdrs, ceiling=1 * MIB)
    assert status == 413, body


async def test_a_streamed_body_reaches_the_rail_as_it_arrives_and_intact(relay):
    """Byte for byte, and AS IT ARRIVES: when the rail saw its first chunk the gateway had taken
    one or two from the client, not all 48. A relay that read the body whole first would hand
    over the same bytes, and only this second half tells the two apart."""
    data = os.urandom(3 * MIB)
    status, body, client, rail = await relay(
        "PUT", _chunks(data), [("content-length", str(len(data)))], ceiling=25 * MIB)
    assert status == 200, body
    assert hashlib.sha256(bytes(rail.got)).hexdigest() == hashlib.sha256(data).hexdigest()
    assert client.calls == len(_chunks(data))
    assert rail.client_calls_at_first_chunk is not None
    assert rail.client_calls_at_first_chunk <= 2, (
        f"the rail's first chunk arrived after {rail.client_calls_at_first_chunk} of "
        f"{client.calls} reads: the body was buffered, not streamed")


# --- requests with no body ------------------------------------------------------------------

@pytest.mark.parametrize("method", ["GET", "DELETE", "OPTIONS"])
async def test_a_request_with_no_body_sends_none_and_reads_none(relay, method):
    """No Content-Length and no Transfer-Encoding means no body. An empty STREAM would have made
    httpx label every GET `Transfer-Encoding: chunked`, which no rail asked for."""
    status, _body, client, rail = await relay(method, [], [("accept", "application/json")])
    assert status == 200
    assert rail.calls == 1 and bytes(rail.got) == b""
    assert "transfer-encoding" not in rail.headers and "content-length" not in rail.headers
    assert client.calls == 0


async def test_a_head_for_a_root_asset_sends_no_body_and_reads_none(monkeypatch):
    """HEAD reaches the relay only through a rail's root-origin asset routes (GET/HEAD), which
    call _forward directly."""
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(session_cookie="s"),
                        raising=False)
    client = _Client([])
    rail = _Rail(client)
    monkeypatch.setattr(m.app.state, "http", httpx.AsyncClient(transport=rail), raising=False)
    scope = _scope("HEAD", "/logos/x.svg", [("accept", "image/svg+xml")])
    resp = await m._forward("http://rail:9/logos/x.svg", RAIL, Request(scope, client.receive))
    assert resp.status_code == 200 and rail.calls == 1 and bytes(rail.got) == b""
    assert "transfer-encoding" not in rail.headers and "content-length" not in rail.headers
    assert client.calls == 0


async def test_identity_and_the_anti_spoof_strip_still_apply_to_a_streamed_body(relay):
    data = b"q" * (2 * CHUNK)
    status, _body, _client, rail = await relay(
        "POST", _chunks(data), [("content-length", str(len(data))),
                                ("x-platform-user", "someone-else"), ("x-platform-admin", "1")])
    assert status == 200
    assert "x-platform-user" not in rail.headers and "x-platform-admin" not in rail.headers
    assert bytes(rail.got) == data


# --- the event-stream branch ----------------------------------------------------------------

async def test_an_event_stream_request_body_is_capped_too(relay):
    """fetch() can POST with `Accept: text/event-stream` (a token stream), which takes the
    streaming branch. Its body goes through the same ceiling."""
    status, body, client, _rail = await relay(
        "POST", [b"e" * CHUNK] * 64,
        [("accept", "text/event-stream"), ("transfer-encoding", "chunked")], ceiling=1 * MIB)
    assert status == 413, body
    assert client.read <= 1 * MIB + CHUNK


async def test_an_event_stream_request_body_streams_through_intact(relay):
    data = os.urandom(5 * CHUNK)
    status, body, client, rail = await relay(
        "POST", _chunks(data),
        [("accept", "text/event-stream"), ("content-length", str(len(data)))])
    assert status == 200, body
    assert bytes(rail.got) == data
    assert rail.client_calls_at_first_chunk is not None and rail.client_calls_at_first_chunk <= 2


# --- each rail's own figure ------------------------------------------------------------------
# Called through proxy() itself, since the real rail ids sit behind the access gate. A declared
# length is decided before anything is read, so a 250 MB DECISION needs no 250 MB of bytes: the
# rail reads the first chunk and answers, as a rail that refuses early would.

async def _decide(monkeypatch, app_id: str, declared: int):
    monkeypatch.setattr(m.app.state, "backends", {app_id: "http://rail:9"}, raising=False)
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(session_cookie="s"),
                        raising=False)
    client = _Client([b"d" * CHUNK] * 4)
    rail = _Rail(client, stop_after=1)
    _wire(monkeypatch, rail)
    scope = _scope("POST", f"/{app_id}/api/upload", [("content-length", str(declared))])
    try:
        resp = await m.proxy(app_id, "upload", Request(scope, client.receive))
    except HTTPException as exc:
        return exc.status_code, client, rail
    return resp.status_code, client, rail


async def test_a_25_mb_file_and_its_multipart_framing_fit_the_default(monkeypatch):
    """Most rails accept one file of up to 25 MiB. Its form adds boundaries and part headers, so
    a default of exactly 25 MiB would refuse a file the rail itself takes."""
    status, _c, rail = await _decide(monkeypatch, RAIL, 25 * MIB + 4096)
    assert status == 200 and rail.calls == 1
    assert DEFAULT_MAX_REQUEST_BYTES >= 25 * MIB + 4096


async def test_openmaic_admits_the_200_mb_its_upstream_accepts(monkeypatch):
    """OpenMAIC's own Next.js config sets proxyClientMaxBodySize to 200mb."""
    status, _c, rail = await _decide(monkeypatch, "openmaic", 200 * MIB)
    assert status == 200 and rail.headers["content-length"] == str(200 * MIB)


async def test_recipe_book_admits_ten_25_mb_files_in_one_import(monkeypatch):
    """Its import route takes up to 10 files of 25 MiB each in one request."""
    status, _c, _rail = await _decide(monkeypatch, "recipe-book", 250 * MIB + 64 * 1024)
    assert status == 200


async def test_watch_builder_refuses_13_mb_that_the_default_would_admit(monkeypatch):
    """Its own ceiling is a 12 MiB image plus 64 KiB of framing. Relaying more only for the rail
    to refuse it would spend the bandwidth the ceiling exists to save."""
    status, client, rail = await _decide(monkeypatch, "watch-builder", 13 * MIB)
    assert status == 413 and client.calls == 0 and rail.calls == 0
    assert 13 * MIB <= DEFAULT_MAX_REQUEST_BYTES     # so it is the rail's figure that refused
    status, _c, _rail = await _decide(monkeypatch, "watch-builder", 12 * MIB + 64 * 1024)
    assert status == 200


@pytest.mark.skipif("edu-suite" not in MAX_REQUEST_BYTES,
                    reason="edu-suite is not part of this snapshot")
async def test_a_folder_upload_of_250_mb_and_its_framing_still_lands(monkeypatch):
    """edu-suite takes a whole folder in one request, up to 250 MiB of files, each with its own
    part header. This is the largest upload any rail takes."""
    status, _c, rail = await _decide(monkeypatch, "edu-suite", 250 * MIB + 2 * MIB)
    assert status == 200 and rail.calls == 1


async def test_the_iep_tile_has_the_26_mb_default_not_the_folder_ceiling(monkeypatch):
    """The iep tile is edu-suite's image run IEP_ONLY=1, and its one upload is a SEIS PDF of up
    to 25 MiB (/api/iep/parse); it creates no job from an upload. It inherited edu-suite's 256 MiB
    through also_serves until 2026-10-14; rails/edu-suite/rail.json's also_serves_default_ceiling
    holds it to the platform default now, so the gateway has no mirror entry for it."""
    assert "iep" not in MAX_REQUEST_BYTES
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(), raising=False)
    assert m._request_ceiling("iep") == 26 * MIB == DEFAULT_MAX_REQUEST_BYTES
    status, _c, rail = await _decide(monkeypatch, "iep", 25 * MIB + 4096)
    assert status == 200 and rail.calls == 1
    status, client, rail = await _decide(monkeypatch, "iep", 250 * MIB + 2 * MIB)
    assert status == 413 and client.calls == 0 and rail.calls == 0


# --- the default and its setting -------------------------------------------------------------

def test_the_default_follows_platform_max_request_bytes(monkeypatch):
    monkeypatch.setenv("PLATFORM_MAX_REQUEST_BYTES", str(5 * MIB))
    assert GatewaySettings().max_request_bytes == 5 * MIB


@pytest.mark.parametrize("raw", ["", "   ", "50MB", "0", "-3"])
def test_a_blank_or_unusable_setting_is_the_default_not_a_dead_gateway(monkeypatch, raw):
    """Settings are built at import, so a value that raised would take every rail down."""
    monkeypatch.setenv("PLATFORM_MAX_REQUEST_BYTES", raw)
    assert GatewaySettings().max_request_bytes == DEFAULT_MAX_REQUEST_BYTES


def test_every_mirrored_ceiling_names_a_catalog_tile():
    """A mirror entry for an id no tile has is a ceiling nothing applies (a renamed rail)."""
    assert set(MAX_REQUEST_BYTES) <= set(APP_IDS), set(MAX_REQUEST_BYTES) - set(APP_IDS)
