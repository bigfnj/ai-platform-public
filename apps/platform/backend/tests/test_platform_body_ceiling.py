"""The platform's own routes read a bounded body, login included.

Every /api/platform/* route that takes a pydantic body is parsed by FastAPI, which reads the
request body whole before it validates anything or resolves a dependency, and nothing bounded
it: uvicorn sets no limit and neither Caddyfile sets `request_body`. POST /api/platform/login
needs no session, so any client could make the gateway hold a body of any size. Even a route
behind require_user read the body first, since FastAPI reads the body before it runs the
dependency that would have refused the caller.

Now `_PlatformBodyCeiling`, outside the app, holds every /api/platform/ request to 1 MiB: a
Content-Length over it is a 413 before a byte is read, and any other body is counted as it
arrives and refused one chunk past the ceiling, before the route runs. The one route that needs
more, transcribe (15 MB of base64 audio), gets its room only for a signed-in caller. The rail
relay is not held to it: it has its own per-rail ceiling.

Through the REAL ASGI app (both middlewares, the routes, a real database), with a client that
hands the body over one chunk at a time and counts what was taken.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from platform_gateway_app import main as m
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.config import GatewaySettings
from platform_gateway_app.models import User

CHUNK = 64 * 1024
MIB = 1024 * 1024
CEILING = 1 * MIB


class _Client:
    """Hands the app its body a chunk at a time, on demand, and counts what was taken."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks) or [b""]
        self._i = 0
        self.calls = 0
        self.read = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        if self._i < len(self._chunks):
            chunk = self._chunks[self._i]
            self._i += 1
            self.calls += 1
            self.read += len(chunk)
            return {"type": "http.request", "body": chunk,
                    "more_body": self._i < len(self._chunks)}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    @property
    def status(self) -> int:
        return next(x["status"] for x in self.sent if x["type"] == "http.response.start")

    def header(self, name: str) -> list[str]:
        start = next(x for x in self.sent if x["type"] == "http.response.start")
        return [v.decode() for k, v in start["headers"] if k.decode().lower() == name]

    @property
    def body(self) -> bytes:
        return b"".join(x.get("body", b"") for x in self.sent if x["type"] == "http.response.body")


def _chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def _scope(method: str, path: str, headers: list[tuple[str, str]]) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}


def _padded_login(size: int, username: str = "nobody", password: str = "wrong") -> bytes:
    """A valid login body of exactly `size` bytes: JSON allows any amount of whitespace."""
    core = json.dumps({"username": username, "password": password}).encode()
    assert size >= len(core)
    return core[:-1] + b" " * (size - len(core)) + b"}"


class _Broker:
    def __init__(self) -> None:
        self.transcribed: list[int] = []

    async def transcribe(self, audio_b64: str, suffix=None, language=None):
        self.transcribed.append(len(audio_b64))
        return {"text": "hello"}

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def gw(db, monkeypatch):
    """The real app on a throwaway database, a known account and a session for it, and a record
    of whether login() itself ever ran (it calls _client_ip first)."""
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)
    monkeypatch.setattr(m.app.state, "broker", _Broker(), raising=False)
    monkeypatch.setattr(m, "_login_fails", {})
    ran: list[str] = []
    real_ip = m._client_ip
    monkeypatch.setattr(m, "_client_ip", lambda request: ran.append("login") or real_ip(request))
    with db.session_ctx() as s:
        u = User(username="teacher", password_hash=hash_password("pw-1"), is_admin=False)
        s.add(u)
        s.commit()
        token = create_session(s, u, 168).token

    async def call(method: str, path: str, chunks: list[bytes], headers: list[tuple[str, str]],
                   signed_in: bool = False) -> _Client:
        if signed_in:
            headers = [*headers, ("cookie", f"platform_session={token}")]
        client = _Client(chunks)
        await asyncio.wait_for(m.app(_scope(method, path, headers), client.receive, client.send),
                               timeout=30)
        return client

    call.ran = ran                      # type: ignore[attr-defined]
    return call


# --- login, which needs no session --------------------------------------------------------------

async def test_a_declared_50_mb_login_is_a_413_before_a_byte_is_read(gw):
    client = await gw("POST", "/api/platform/login", [b"x" * CHUNK] * 8,
                      [("content-type", "application/json"), ("content-length", str(50 * MIB))])
    assert client.status == 413, client.body
    assert b"1 MB" in client.body
    assert client.calls == 0 and client.read == 0, "the gateway read the body it was refusing"
    assert gw.ran == [], "login() ran for a body that was refused"


async def test_a_declared_2_mb_login_is_a_413_too(gw):
    client = await gw("POST", "/api/platform/login", _chunks(_padded_login(2 * MIB)),
                      [("content-type", "application/json"), ("content-length", str(2 * MIB))])
    assert client.status == 413 and client.calls == 0 and gw.ran == []


async def test_a_chunked_login_past_the_ceiling_is_cut_off_one_chunk_later(gw):
    """A chunked body declares nothing, so it is counted as it arrives."""
    data = _padded_login(2 * MIB)
    client = await gw("POST", "/api/platform/login", _chunks(data),
                      [("content-type", "application/json"), ("transfer-encoding", "chunked")])
    assert client.status == 413, client.body
    assert client.read <= CEILING + CHUNK, f"read {client.read} bytes past a {CEILING}-byte ceiling"
    assert client.calls < len(_chunks(data)), "the whole body was read before it was refused"
    assert gw.ran == [], "login() ran for a body that was refused"


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
async def test_a_login_exactly_at_the_ceiling_reaches_login_and_one_byte_more_does_not(
        gw, framing):
    def hdrs(n: int) -> list[tuple[str, str]]:
        return [("content-type", "application/json"),
                ("content-length", str(n)) if framing == "content-length"
                else ("transfer-encoding", "chunked")]

    at = await gw("POST", "/api/platform/login", _chunks(_padded_login(CEILING)), hdrs(CEILING))
    assert at.status == 401, at.body             # login() ran and refused the password
    assert gw.ran == ["login"]
    over = await gw("POST", "/api/platform/login", _chunks(_padded_login(CEILING + 1)),
                    hdrs(CEILING + 1))
    assert over.status == 413, over.body
    assert gw.ran == ["login"], "login() ran for the body one byte over"


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
async def test_an_ordinary_login_still_signs_in(gw, framing):
    body = json.dumps({"username": "teacher", "password": "pw-1"}).encode()
    hdrs = [("content-type", "application/json"),
            ("content-length", str(len(body))) if framing == "content-length"
            else ("transfer-encoding", "chunked")]
    client = await gw("POST", "/api/platform/login", [body], hdrs)
    assert client.status == 200, client.body
    assert json.loads(client.body)["username"] == "teacher"
    assert any(c.startswith("platform_session=") for c in client.header("set-cookie"))


# --- the other routes ---------------------------------------------------------------------------

async def test_a_signed_in_users_own_small_bodies_still_pass(gw):
    body = json.dumps({"palette": "ocean", "mode": "dark"}).encode()
    client = await gw("PUT", "/api/platform/theme", [body],
                      [("content-type", "application/json"), ("content-length", str(len(body)))],
                      signed_in=True)
    assert client.status in (200, 400), client.body      # 400 = a palette this tree lacks
    assert client.status != 413


async def test_a_route_behind_a_session_is_bounded_too(gw):
    """FastAPI reads the body before it resolves require_user, so a signed-out client was read
    in full and only then told 401."""
    client = await gw("PUT", "/api/platform/theme", [b"{" + b" " * CHUNK] * 40,
                      [("content-type", "application/json"),
                       ("content-length", str(40 * (CHUNK + 1)))])
    assert client.status == 413 and client.calls == 0


# --- transcribe, the one route that needs more ---------------------------------------------------

def _audio(n: int) -> bytes:
    return json.dumps({"audio_b64": "A" * n, "suffix": ".webm"}).encode()


async def test_a_signed_in_transcribe_takes_its_15_mb_room(gw):
    body = _audio(4 * MIB)
    client = await gw("POST", "/api/platform/transcribe", _chunks(body),
                      [("content-type", "application/json"), ("content-length", str(len(body)))],
                      signed_in=True)
    assert client.status == 200, client.body[:300]
    assert m.app.state.broker.transcribed == [4 * MIB]


async def test_an_anonymous_transcribe_gets_the_ordinary_ceiling(gw):
    """Its room is a signed-in caller's. Otherwise anyone could make the gateway hold 15 MB."""
    body = _audio(4 * MIB)
    client = await gw("POST", "/api/platform/transcribe", _chunks(body),
                      [("content-type", "application/json"), ("content-length", str(len(body)))])
    assert client.status == 413, client.body[:300]
    assert client.calls == 0
    assert m.app.state.broker.transcribed == []


async def test_transcribes_room_holds_the_largest_audio_the_route_accepts_and_no_more(gw):
    """The room is the audio bound plus the ordinary allowance for the rest of the body, so the
    largest audio the route's own validator takes still arrives, and a body past the room is
    refused before it is read."""
    room = m._PLATFORM_BODY_ROOM["/api/platform/transcribe"]
    assert room >= len(_audio(m._TRANSCRIBE_MAX_B64))
    client = await gw("POST", "/api/platform/transcribe", [b"{" + b" " * CHUNK] * 4,
                      [("content-type", "application/json"), ("content-length", str(room + 1))],
                      signed_in=True)
    assert client.status == 413 and client.calls == 0


# --- the relay has its own ceiling ---------------------------------------------------------------

async def test_the_rail_relay_is_not_held_to_the_platform_ceiling(gw, monkeypatch):
    got = bytearray()

    class _Rail(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            async for chunk in request.stream:
                got.extend(chunk)
            # A stream, as a real transport returns: Response(json=...) arrives already read.
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  stream=httpx.ByteStream(b'{"ok": true}'))

    rail = "syn-ceiling-rail"
    monkeypatch.setattr(m.app.state, "backends", {rail: "http://rail:9"}, raising=False)
    for attr in ("http", "http_body"):
        monkeypatch.setattr(m.app.state, attr, httpx.AsyncClient(transport=_Rail()), raising=False)
    data = b"r" * (3 * MIB)
    client = await gw("POST", f"/{rail}/api/upload", _chunks(data),
                      [("content-length", str(len(data)))])
    assert client.status == 200, client.body
    assert bytes(got) == data


# --- every route that reads a body -------------------------------------------------------------

def _body_routes() -> list[tuple[str, str]]:
    """(method, path) for every route FastAPI reads a body for, path parameters filled in."""
    import re
    from fastapi.routing import APIRoute
    out = []
    for r in m.app.routes:
        if isinstance(r, APIRoute) and r.body_field is not None:
            for method in sorted(r.methods):
                out.append((method, re.sub(r"\{[^}]+\}", "x", r.path)))
    return out


def test_every_route_that_reads_a_body_is_under_the_ceiling():
    """The ceiling covers /api/platform/ by path. A body route added anywhere else would be read
    whole again, so that is pinned here."""
    routes = _body_routes()
    assert len(routes) >= 10, routes          # login, theme, users, rails, schedules, speech...
    assert [p for _m, p in routes if not p.startswith("/api/platform/")] == []


@pytest.mark.parametrize("method,path", _body_routes(), ids=lambda v: str(v))
async def test_every_body_route_refuses_a_declared_2_mb_body_unread(gw, method, path):
    client = await gw(method, path, [b"{" + b" " * CHUNK] * 4,
                      [("content-type", "application/json"), ("content-length", str(2 * MIB))])
    assert client.status == 413, (path, client.body[:200])
    assert client.calls == 0
