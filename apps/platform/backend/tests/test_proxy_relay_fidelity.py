"""The rail relay carries the client's request as sent, less the platform's own, and the rail's
answer less the platform's cookies. Five defects the 2026-10-14 audit verified in-process:

1. A client that named no Accept-Encoding was sent gzip. httpx adds its own Accept-Encoding to a
   request that carries none, and the relay hands back the rail's raw bytes under the rail's own
   Content-Encoding.
2. Repeated query keys collapsed to the last one (`params=request.query_params`: Starlette's
   multidict gives httpx one value per key), undoing openmaic's raw-query fix one hop earlier.
3. The front door's credentials reached every rail in headers: Cloudflare Access's JWT
   (Cf-Access-Jwt-Assertion) while its cookie twin was stripped, a service token
   (Cf-Access-Client-*), and any Authorization the browser sent.
4. A rail's `Set-Cookie: platform_session=...` reached the browser: session fixation, or a forced
   logout by clearing it.
5. HEAD on /<rail>/api/* was a 405 from the proxy route (or a 404 from the rail's static mount)
   and never reached the rail; HEAD on the shell and on healthz were 405s too.

Through the REAL ASGI app (both http middlewares, the guard, the route, the relay), with a rail
that records every request it is sent and answers a streamed response, as a rail does.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_gateway_app import main as m
from platform_gateway_app.config import GatewaySettings

RAIL = "syn-fidelity-rail"      # synthetic: not a catalog id, so the access gate lets it through
BODY = b'{"ok":true}'


class _Stream(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        if self.data:
            yield self.data

    async def aclose(self) -> None:
        pass


class _Rail(httpx.AsyncBaseTransport):
    """Records each request it is sent; answers 200 with `headers`, streamed. A HEAD gets the
    length a GET would bring and no body, as a rail's own server answers one."""

    def __init__(self, headers: list[tuple[str, str]] | None = None):
        self.headers = headers or []
        self.seen: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        hdrs = [("content-type", "application/json"), ("content-length", str(len(BODY))),
                *self.headers]
        return httpx.Response(200, headers=hdrs,
                              stream=_Stream(b"" if request.method == "HEAD" else BODY))


class _Answer:
    def __init__(self, sent: list[dict]):
        self.sent = sent
        self.start = next(x for x in sent if x["type"] == "http.response.start")
        self.status = self.start["status"]
        self.body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")

    def header(self, name: str) -> list[str]:
        return [v.decode() for k, v in self.start["headers"] if k.decode().lower() == name]


async def _call(method: str, path: str, headers: list[tuple[str, str]] = (),
                query: str = "") -> _Answer:
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
             "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}
    sent: list[dict] = []
    asked = False

    async def receive() -> dict:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()        # the client stays; the response's end cancels this
        return {"type": "http.disconnect"}

    async def send(msg: dict) -> None:
        sent.append(msg)

    await asyncio.wait_for(m.app(scope, receive, send), timeout=30)
    return _Answer(sent)


@pytest.fixture()
def wire(monkeypatch):
    """wire(rail, **settings) points the real app at `rail` behind the synthetic id."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(m.app.state, "backends", {RAIL: "http://rail:9"}, raising=False)

    def wire_(rail: _Rail, **settings) -> _Rail:
        monkeypatch.setattr(m.app.state, "settings", GatewaySettings(**settings), raising=False)
        client = httpx.AsyncClient(transport=rail)
        monkeypatch.setattr(m.app.state, "http", client, raising=False)
        monkeypatch.setattr(m.app.state, "http_body", client, raising=False)
        return rail

    return wire_


API = f"/{RAIL}/api/rows"


# --- 1. Accept-Encoding -------------------------------------------------------------------------

async def test_a_client_that_names_no_encoding_is_sent_none(wire):
    rail = wire(_Rail())
    await _call("GET", API, [("accept", "application/json")])
    assert rail.seen[-1].headers["accept-encoding"] == "identity", (
        "httpx's own gzip, deflate went to the rail, and its gzip back to a client that never "
        "said it could read it")


async def test_a_client_that_names_encodings_keeps_its_list(wire):
    rail = wire(_Rail())
    await _call("GET", API, [("accept-encoding", "gzip, br")])
    assert rail.seen[-1].headers["accept-encoding"] == "gzip, br"


# --- 2. the query string ------------------------------------------------------------------------

async def test_the_query_string_reaches_the_rail_byte_for_byte(wire):
    """Repeated keys in order, an encoded plus, a form-encoded space, a blank value, a bare key."""
    rail = wire(_Rail())
    raw = "tag=math&x=%2B1&tag=physics&y=a+b&tag=&bare"
    assert (await _call("GET", API, query=raw)).status == 200
    assert rail.seen[-1].url.query == raw.encode()
    assert rail.seen[-1].url.path == "/api/rows"


async def test_no_query_sends_no_question_mark(wire):
    rail = wire(_Rail())
    await _call("GET", API)
    assert rail.seen[-1].url.query == b"" and str(rail.seen[-1].url) == "http://rail:9/api/rows"


# --- 3. the front door's credentials ------------------------------------------------------------

CREDENTIALS = [("Authorization", "Bearer browser-token"),
               ("Proxy-Authorization", "Basic cHJveHk6cHc="),
               ("Cf-Access-Jwt-Assertion", "eyJhbGciOiJSUzI1NiJ9.e30.sig"),
               ("Cf-Access-Client-Id", "client.access"),
               ("Cf-Access-Client-Secret", "s3cret")]


async def test_cloudflare_and_bearer_credentials_stay_at_the_gateway(wire):
    rail = wire(_Rail())
    await _call("GET", API, [*CREDENTIALS, ("x-rail-own", "kept"), ("accept", "text/plain")])
    sent = rail.seen[-1].headers
    leaked = [k for k, _v in CREDENTIALS if k in sent]
    assert not leaked, f"reached the rail: {leaked}"
    assert sent["x-rail-own"] == "kept" and sent["accept"] == "text/plain", (
        "the strip took the rail's own headers with it")


# --- 4. the platform's cookies in the rail's answer ---------------------------------------------

async def test_a_rail_cannot_set_or_clear_the_platform_session_or_access_cookie(wire):
    rail = wire(_Rail([("set-cookie", "platform_session=chosen; Path=/; HttpOnly"),
                       ("set-cookie", "PLATFORM_SESSION=x; Path=/"),
                       ("set-cookie", " CF_Authorization=; Max-Age=0; Path=/"),
                       ("set-cookie", "rail_sid=1; Path=/"),
                       ("set-cookie", "theme=dark; Path=/")]))
    out = await _call("GET", API)
    assert out.status == 200 and rail.seen
    assert out.header("set-cookie") == ["rail_sid=1; Path=/", "theme=dark; Path=/"]


async def test_a_renamed_session_cookie_cannot_be_set_by_a_rail_either(wire):
    wire(_Rail([("set-cookie", "gw_sess=chosen; Path=/"),
                ("set-cookie", "platform_session=chosen; Path=/"),
                ("set-cookie", "rail_sid=1; Path=/")]), session_cookie="gw_sess")
    out = await _call("GET", API)
    assert out.header("set-cookie") == ["rail_sid=1; Path=/"]


# --- 5. HEAD ------------------------------------------------------------------------------------

async def test_head_on_a_rail_api_path_reaches_the_rail(wire):
    rail = wire(_Rail())
    out = await _call("HEAD", API)
    assert out.status == 200, out.status
    assert [r.method for r in rail.seen] == ["HEAD"]
    assert out.header("content-length") == [str(len(BODY))], "the length a GET would bring"
    assert out.body == b""


async def test_head_on_healthz_answers(wire):
    wire(_Rail())
    out = await _call("HEAD", "/api/platform/healthz")
    assert out.status == 200, out.status


@pytest.fixture()
def shell(monkeypatch, tmp_path):
    """The SPA catch-all, mounted on a fresh app beside one GET and one POST platform route, so
    a HEAD the catch-all takes can be told from one for a route that takes other methods."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>shell</title>", encoding="utf-8")
    fresh = FastAPI()

    @fresh.get("/api/platform/me")
    def me() -> dict:
        return {}

    @fresh.post("/api/platform/login")
    def login() -> dict:
        return {}

    monkeypatch.setattr(m, "app", fresh)
    monkeypatch.setattr(m, "GatewaySettings", lambda: SimpleNamespace(
        resolved_frontend_dist=lambda: dist))
    m._mount_spa()
    return TestClient(fresh)


def test_head_on_the_shell_answers_like_its_get(shell):
    for path in ("/", "/some/deep/link"):
        r = shell.head(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.headers["content-type"].startswith("text/html")
        assert r.content == b""


def test_head_on_a_platform_route_is_still_a_405_naming_its_methods(shell):
    """Not "no such endpoint": FastAPI adds no HEAD to a GET route, so the catch-all now meets
    every HEAD on /api/platform/*, and these routes exist."""
    me = shell.head("/api/platform/me")
    assert me.status_code == 405 and me.headers["allow"] == "GET"
    login = shell.head("/api/platform/login")
    assert login.status_code == 405 and login.headers["allow"] == "POST"


def test_an_unknown_api_path_is_still_a_404_by_head_or_get(shell):
    assert shell.head("/api/no-such-thing").status_code == 404
    r = shell.get("/api/no-such-thing")
    assert r.status_code == 404 and "no such endpoint" in r.json()["detail"]
