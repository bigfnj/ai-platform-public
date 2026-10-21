"""The session cookie's Secure flag is decided per request, not once globally.

`PLATFORM_COOKIE_SECURE=true` is correct for the public HTTPS front door, but Caddy also serves
plain `http://localhost`, and a Secure cookie is never returned over plain HTTP. Browsers do not
care (localhost is a secure context), but every scripted client silently loses its session — and
the installer's own e2e smoke stage responds by SKIPPING its authenticated checks, which is worse
than failing them.

The relaxation has to stay narrow, so these cases are pinned: only plain HTTP *at a loopback
host* drops the flag. A request that merely lost its TLS somewhere must not be able to talk the
gateway out of it. `X-Forwarded-Proto` is trustworthy here because Caddy overwrites any
client-supplied copy with the scheme it actually terminated.
"""
from types import SimpleNamespace

import pytest

from platform_gateway_app import main as m


class _Req:
    def __init__(self, headers: dict, scheme: str = "http"):
        self.headers = headers
        self.url = SimpleNamespace(scheme=scheme)


@pytest.fixture(autouse=True)
def _secure_on(monkeypatch):
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(cookie_secure=True),
                        raising=False)


@pytest.mark.parametrize("headers,expected,why", [
    ({"x-forwarded-proto": "http", "host": "localhost:1111"}, False,
     "the case this exists for: local scripted access"),
    ({"x-forwarded-proto": "http", "host": "127.0.0.1:1111"}, False,
     "loopback by address, not just by name"),
    ({"host": "localhost:1111"}, False,
     "no X-Forwarded-Proto at all falls back to the request scheme"),
    ({"x-forwarded-proto": "https", "host": "localhost:1111"}, True,
     "HTTPS keeps Secure even on loopback"),
    ({"x-forwarded-proto": "http", "host": "platform.example.com"}, True,
     "a real hostname keeps Secure even over plain HTTP — the important one"),
    ({"x-forwarded-proto": "https", "host": "platform.example.com"}, True,
     "the public front door, unchanged"),
])
def test_secure_flag_is_scheme_and_host_aware(headers, expected, why):
    assert m._cookie_secure_for(_Req(headers)) is expected, why


def test_setting_off_always_wins(monkeypatch):
    """Nothing here can turn Secure ON when the deployment did not ask for it."""
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(cookie_secure=False),
                        raising=False)
    assert m._cookie_secure_for(_Req({"x-forwarded-proto": "https",
                                      "host": "platform.example.com"})) is False


def test_bracketed_ipv6_loopback_is_recognised():
    """Host arrives as `[::1]:1111`; the brackets must not defeat the comparison."""
    assert m._cookie_secure_for(
        _Req({"x-forwarded-proto": "http", "host": "[::1]:1111"})) is False
