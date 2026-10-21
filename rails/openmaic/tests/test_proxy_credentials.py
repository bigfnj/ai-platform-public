"""The platform's credentials stop at this rail; the wrapped app never sees them or sets them.

openmaic-app is third-party code (THU-MAIC/OpenMAIC) served on the platform's own origin, and
the gateway hands this rail every browser header it received. The 2026-10-08 audit put a
recording upstream behind the proxy and found it handed the caller's `platform_session` cookie
(the owner's is a super-admin session), `Authorization`, and the gateway's x-platform-admin /
x-platform-peers; and the app's Set-Cookie came back unread, `platform_session` included, so
the app could replace the caller's session in the browser.

Driven through the real route with an httpx.MockTransport upstream that records what it was
sent, so the assertions are about what crossed the wire, not about a helper in isolation. Every
cookie and token value here is a dummy.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from openmaic_app.api import proxy
from openmaic_app.api.app import app

SESSION = "platform_session=DUMMY-SESSION-TOKEN"
GATEWAY = {
    "x-platform-user": "owner-one",
    "x-platform-admin": "1",
    "x-platform-peers": '["bob"]',
    "x-platform-rooms": "[1]",
}


class _Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        pass


@pytest.fixture
def upstream(monkeypatch, gateway):
    """A recording openmaic-app. `reply` sets the Set-Cookie headers it answers with."""
    seen: dict = {"headers": None}
    reply: dict = {"set_cookie": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        headers = [("content-type", "text/plain")]
        headers += [("set-cookie", c) for c in reply["set_cookie"]]
        return httpx.Response(200, headers=headers, stream=_Body())

    monkeypatch.setattr(proxy, "_client", httpx.AsyncClient(
        base_url="http://openmaic-app:3000", transport=httpx.MockTransport(handler)))
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    # From the gateway's address: the headers below are the gateway's, and from any other peer
    # they would be dropped before the proxy (api/gateway_only.py).
    with TestClient(app, client=(gateway.address, 50000)) as client:
        yield client, seen, reply


def _get(client, cookie: str | None = None, **extra) -> httpx.Response:
    headers = dict(GATEWAY, **extra)
    if cookie is not None:
        headers["cookie"] = cookie
    return client.get("/api/app/some/page", headers=headers)


def test_the_platform_session_cookie_never_reaches_the_app(upstream):
    client, seen, _ = upstream
    assert _get(client, cookie=f"{SESSION}; theme=dark; next-auth.csrf=abc").status_code == 200
    sent = seen["headers"].get("cookie")
    assert "DUMMY-SESSION-TOKEN" not in (sent or "")
    assert sent == "theme=dark; next-auth.csrf=abc", "the app's own cookies must still reach it"


def test_a_request_carrying_only_the_session_sends_no_cookie_header(upstream):
    client, seen, _ = upstream
    _get(client, cookie=SESSION)
    assert "cookie" not in seen["headers"]


def test_case_variants_and_cloudflare_access_cookies_are_stripped_too(upstream):
    client, seen, _ = upstream
    _get(client, cookie="PLATFORM_SESSION=x; CF_Authorization=jwt; keep=1")
    assert seen["headers"].get("cookie") == "keep=1"


def test_no_credential_header_reaches_the_app(upstream):
    client, seen, _ = upstream
    _get(client, authorization="Bearer DUMMY-CLIENT-AUTH",
         **{"cf-access-jwt-assertion": "jwt", "cf-access-client-id": "id",
            "cf-access-client-secret": "secret"})
    sent = {k.lower() for k in seen["headers"].keys()}
    for name in ("authorization", "cf-access-jwt-assertion", "cf-access-client-id",
                 "cf-access-client-secret"):
        assert name not in sent, f"{name} reached openmaic-app"


def test_only_the_resolved_user_crosses_of_the_platforms_headers(upstream):
    """Admin, peers, rooms and anything the gateway adds later stay on this side."""
    client, seen, _ = upstream
    _get(client)
    platform = {k.lower(): v for k, v in seen["headers"].items()
                if k.lower().startswith("x-platform-")}
    assert platform == {"x-platform-user": "owner-one"}


def test_ordinary_headers_are_still_relayed(upstream):
    """Guards the over-correction: stripping credentials must not strip the request."""
    client, seen, _ = upstream
    _get(client, accept="text/html", **{"accept-language": "fr"})
    assert seen["headers"].get("accept") == "text/html"
    assert seen["headers"].get("accept-language") == "fr"
    assert seen["headers"].get("x-forwarded-prefix") == "/openmaic/api/app"


def test_the_app_cannot_set_the_platform_session(upstream):
    """The app is on the platform's origin, so this Set-Cookie would replace the caller's
    session in the browser. Refused; the app's own cookies still go through, each as its own
    header (a joined Set-Cookie is the corruption _relay exists to avoid)."""
    client, _, reply = upstream
    reply["set_cookie"] = ["platform_session=attacker; Path=/; HttpOnly",
                           "Platform_Session=attacker2; Path=/",
                           "app-sid=1; Path=/openmaic/api/app",
                           "csrf=2; Path=/openmaic/api/app"]
    r = _get(client)
    relayed = r.headers.get_list("set-cookie")
    assert not [c for c in relayed if c.lower().startswith("platform_session")], relayed
    assert relayed == ["app-sid=1; Path=/openmaic/api/app", "csrf=2; Path=/openmaic/api/app"]


def test_the_refusal_is_logged(upstream, caplog):
    client, _, reply = upstream
    reply["set_cookie"] = ["platform_session=attacker; Path=/"]
    with caplog.at_level("WARNING", logger="openmaic.proxy"):
        _get(client)
    assert any("platform cookie" in r.getMessage() for r in caplog.records)


def test_app_cookies_joins_split_cookie_headers():
    """An HTTP/2 client may send one Cookie header per crumb; none may be lost on the way."""
    assert proxy.app_cookies(["a=1", f"{SESSION}; b=2", " ; c=3"]) == "a=1; b=2; c=3"
