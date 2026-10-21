"""What the rail relay passes through, line for line, and which paths it refuses.

1. Repeated response headers survive. The relay built its headers from httpx's
   ``Headers.items()``, which joins a repeated name into one comma-separated value: two
   Set-Cookie lines became one that browsers read as a single cookie, undoing a rail (openmaic)
   that relays several.
2. The event-stream branch asks the rail for an uncompressed body. A compressed stream waits in
   the rail's compressor until a block fills, so a live log would arrive in lumps. (It was asked
   for first because that branch dropped Content-Encoding; every relay now keeps the rail's.)
3. A dot segment never leaves the /api/ prefix. Starlette hands the route a DECODED path, so
   ``/job-aid/api/..%2fdocs`` arrived as ``../docs`` and the rail served ``/docs``.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from platform_gateway_app import main as m

COOKIES = [("set-cookie", "a=1; Path=/; HttpOnly"), ("set-cookie", "b=2; Path=/")]


class _Upstream:
    """A rail's response as the relay reads it: streamed. An event stream has no length; an
    ordinary answer carries its own, which the relay passes on because it relays these very
    bytes."""

    def __init__(self, ctype: str):
        self.status_code = 200
        self.body = b"data: one\n\n" if ctype == "text/event-stream" else b'{"ok":true}'
        length = [] if ctype == "text/event-stream" else [("content-length", str(len(self.body)))]
        self.headers = httpx.Headers([("content-type", ctype), *COOKIES,
                                      ("content-disposition", 'attachment; filename="x.zip"'),
                                      *length])

    async def aiter_raw(self):
        yield self.body

    async def aclose(self) -> None:
        pass


class _Request:
    def __init__(self, accept: str):
        self.method = "GET"
        self.headers = {"accept": accept, "accept-encoding": "gzip, br"}
        self.url = SimpleNamespace(query="")      # the relay forwards the raw query
        self.state = SimpleNamespace(platform_user="owner-one", platform_is_admin=False)

    async def body(self) -> bytes:
        return b""


@pytest.fixture()
def wired(monkeypatch):
    """A fake client for both kinds of relay. Both stream now (build_request + send); the event
    stream is the one built with its own timeout, so that is how the fake tells them apart."""
    sent: dict = {}

    class _Http:
        def build_request(self, *a, **kw):
            kind = "stream" if kw.get("timeout") is not None else "ordinary"
            sent[f"{kind}_headers"] = dict(kw.get("headers") or {})
            return SimpleNamespace(kind=kind)

        async def send(self, req, stream=False):
            return _Upstream("text/event-stream" if req.kind == "stream" else "application/json")

    monkeypatch.setattr(m.app.state, "backends", {"iep-goals": "http://x:8800"}, raising=False)
    monkeypatch.setattr(m.app.state, "http", _Http(), raising=False)
    return sent


def _set_cookies(resp) -> list[str]:
    return [v.decode() for k, v in resp.raw_headers if k.decode().lower() == "set-cookie"]


async def test_the_ordinary_relay_keeps_each_set_cookie_line(wired):
    resp = await m.proxy("iep-goals", "session", _Request("application/json"))
    assert _set_cookies(resp) == [v for _k, v in COOKIES]
    # The headers that were already relayed still are, and the one the relay sets itself
    # (content-type) is its own: exactly one, not the response's plus a copy of the rail's. The
    # rail's length passes once, unchanged: the relay sends the rail's own bytes.
    assert resp.headers["content-disposition"] == 'attachment; filename="x.zip"'
    names = [k.decode().lower() for k, _v in resp.raw_headers]
    assert names.count("content-type") == 1 and names.count("content-length") == 1, names
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["content-length"] == str(len(b'{"ok":true}'))


async def test_the_streaming_relay_keeps_each_set_cookie_line(wired):
    resp = await m.proxy("iep-goals", "events", _Request("text/event-stream"))
    assert isinstance(resp, StreamingResponse)
    assert _set_cookies(resp) == [v for _k, v in COOKIES]
    names = [k.decode().lower() for k, _v in resp.raw_headers]
    assert names.count("content-type") == 1, names
    assert "content-length" not in resp.headers     # the rail sent none, so none is invented


async def test_the_streaming_relay_asks_for_an_uncompressed_body(wired):
    await m.proxy("iep-goals", "events", _Request("text/event-stream"))
    assert wired["stream_headers"]["accept-encoding"] == "identity"


async def test_the_ordinary_relay_still_passes_the_clients_encoding(wired):
    """The relay sends the rail's bytes under the rail's own Content-Encoding, so the encodings
    the browser accepts are the ones the rail must choose from."""
    await m.proxy("iep-goals", "students", _Request("application/json"))
    assert wired["ordinary_headers"]["accept-encoding"] == "gzip, br"


@pytest.mark.parametrize("path", ["../docs", "../openapi.json", "a/../../docs", "./x", "a/./b",
                                  "a/..", "..", ".."  + "\\" + "docs"])
async def test_a_dot_segment_is_refused_before_anything_is_sent(wired, path):
    with pytest.raises(HTTPException) as exc:
        await m.proxy("iep-goals", path, _Request("application/json"))
    assert exc.value.status_code == 400
    assert "ordinary_headers" not in wired and "stream_headers" not in wired


@pytest.mark.parametrize("path", ["students", "a/b.c/d", "x..y", "..x", "file.tar.gz"])
async def test_names_that_merely_contain_dots_still_pass(wired, path):
    await m.proxy("iep-goals", path, _Request("application/json"))
    assert "ordinary_headers" in wired


# --- 4. the platform's own cookies stay at the gateway -----------------------------------
# Every rail authenticates by the x-platform-* identity the gateway sets, and none reads the
# session cookie (grep: no rail names platform_session or CF_Authorization outside openmaic's
# own strip), yet each was handed the caller's platform_session (a super-admin's, for the owner)
# and Cloudflare Access's CF_Authorization on every request.

class _CookieRequest(_Request):
    def __init__(self, cookie_lines: list[str]):
        super().__init__("application/json")
        from starlette.datastructures import Headers
        self.headers = Headers(raw=[(b"accept", b"application/json")] +
                               [(b"cookie", line.encode()) for line in cookie_lines])


async def _forwarded_cookie(wired, lines):
    await m.proxy("iep-goals", "students", _CookieRequest(lines))
    return wired["ordinary_headers"].get("cookie")


async def test_the_session_and_access_cookies_are_not_forwarded(wired):
    got = await _forwarded_cookie(
        wired, ["platform_session=s3cret; rail_sid=1; CF_Authorization=jwt; theme=dark"])
    assert got == "rail_sid=1; theme=dark"


async def test_a_case_variant_or_a_second_cookie_line_does_not_smuggle_it_through(wired):
    got = await _forwarded_cookie(wired, ["rail_sid=1", "PLATFORM_SESSION=s3cret",
                                          "cf_authorization=jwt"])
    assert got == "rail_sid=1"


async def test_no_cookie_header_at_all_when_only_platform_cookies_were_sent(wired):
    assert await _forwarded_cookie(wired, ["platform_session=s3cret"]) is None


async def test_a_renamed_session_cookie_is_the_one_stripped(wired, monkeypatch):
    monkeypatch.setattr(m.app.state, "settings",
                        SimpleNamespace(session_cookie="gw_sess"), raising=False)
    got = await _forwarded_cookie(wired, ["gw_sess=s3cret; rail_sid=1"])
    assert got == "rail_sid=1"


def test_the_websocket_handshake_carries_identity_and_no_cookie():
    """The WS proxy builds its upstream headers fresh (ws_identity_headers), so no browser
    cookie can reach a rail there. Pinned, so a change that starts copying them is seen."""
    out = m.ws_identity_headers("owner-one", "0", ["owner-two"], [3])
    assert all(k.startswith("x-platform-") for k in out), out
