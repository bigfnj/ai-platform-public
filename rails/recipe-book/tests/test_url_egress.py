"""The egress guard on ``POST /api/recipes/extract/url`` -- present in the code since the
contained-audit fix, unpinned until now.

A caller-supplied URL that recipe-book fetches and reports the result of is a request-forgery
primitive aimed at the compose network: every sibling rail, the broker on 11500 and the cloud
metadata endpoint are reachable from inside the container, and the gate in front of the route
only establishes that the caller has a name.

The guard refuses by ADDRESS, never by domain. A domain allowlist is the obvious reading of
"egress allowlist" and it is the wrong one here -- importing an arbitrary recipe blog IS the
feature, so a whitelist would break the route for every legitimate use while a crafted
`http://recipes.example/` CNAME'd at 127.0.0.1 walked straight through it. Refusing
loopback/private/link-local/reserved/multicast/unspecified resolutions blocks the reachable
targets and leaves the open web alone.

Offline by construction: ``getaddrinfo`` and the HTTP client are both stubbed, so nothing here
depends on DNS or on a network.
"""
from __future__ import annotations

import socket

import pytest

from recipe_book import extraction


def _resolves_to(monkeypatch, *ips: str) -> None:
    """Pin name resolution, so the guard is tested rather than this box's DNS."""
    def fake_getaddrinfo(host, _port, *a, **kw):
        if not ips:
            raise OSError(f"no such host {host!r}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    monkeypatch.setattr(extraction.socket, "getaddrinfo", fake_getaddrinfo)


class TestPublicHostRefusals:
    """Each address here is reachable from the rail container, which is why it is listed."""

    @pytest.mark.parametrize("ip,what", [
        ("127.0.0.1", "loopback -- the rail's own uvicorn"),
        ("::1", "loopback over IPv6"),
        ("169.254.169.254", "the cloud metadata endpoint"),
        ("169.254.1.1", "link-local"),
        ("10.0.0.5", "private /8"),
        ("172.16.4.9", "private /12 -- the default docker bridge range"),
        ("192.168.1.10", "private /16"),
        ("0.0.0.0", "unspecified"),
        ("224.0.0.1", "multicast"),
        ("240.0.0.1", "reserved"),
    ])
    def test_a_literal_address_is_refused(self, monkeypatch, ip, what):
        _resolves_to(monkeypatch, ip)
        assert extraction._public_host(ip) is False, f"{what} was allowed"

    def test_a_public_NAME_resolving_to_loopback_is_refused(self, monkeypatch):
        """The check is on the resolved address, not the string, so a DNS record pointed at
        loopback cannot launder a request through it."""
        _resolves_to(monkeypatch, "127.0.0.1")
        assert extraction._public_host("recipes.example") is False

    def test_one_private_answer_among_public_ones_is_enough_to_refuse(self, monkeypatch):
        """Multi-A records: httpx may connect to any of them, so ALL must be public."""
        _resolves_to(monkeypatch, "93.184.216.34", "10.1.2.3")
        assert extraction._public_host("recipes.example") is False

    def test_an_unresolvable_host_fails_closed(self, monkeypatch):
        _resolves_to(monkeypatch)                       # getaddrinfo raises
        assert extraction._public_host("nope.invalid") is False

    def test_an_empty_host_fails_closed(self):
        assert extraction._public_host("") is False


class TestLegitimateImportsStillWork:
    """The allowlist must not be tight enough to break the feature."""

    @pytest.mark.parametrize("ip", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946",
                                   "8.8.8.8", "1.1.1.1"])
    def test_a_public_address_is_allowed(self, monkeypatch, ip):
        _resolves_to(monkeypatch, ip)
        assert extraction._public_host("recipes.example") is True


class _Resp:
    def __init__(self, *, text: str = "", location: str | None = None):
        self.text = text
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Stands in for httpx.Client: records every URL actually requested."""

    def __init__(self, script: dict[str, _Resp], seen: list[str]):
        self._script = script
        self._seen = seen

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url: str):
        self._seen.append(url)
        return self._script[url]


class TestGuardedFetch:
    @pytest.fixture()
    def wired(self, monkeypatch):
        seen: list[str] = []

        def install(script: dict[str, _Resp]) -> list[str]:
            monkeypatch.setattr(extraction.httpx, "Client", _FakeClient(script, seen))
            return seen
        return install

    def test_a_non_http_scheme_is_refused(self, wired, monkeypatch):
        wired({})
        _resolves_to(monkeypatch, "93.184.216.34")
        with pytest.raises(ValueError, match="http"):
            extraction._guarded_fetch("file:///etc/passwd")

    def test_a_private_target_is_refused_before_any_request(self, wired, monkeypatch):
        seen = wired({})
        _resolves_to(monkeypatch, "127.0.0.1")
        with pytest.raises(ValueError, match="non-public"):
            extraction._guarded_fetch("http://localhost:8830/api/recipes")
        assert seen == [], "the request was sent before the address was checked"

    def test_a_public_fetch_returns_the_body(self, wired, monkeypatch):
        wired({"https://recipes.example/x": _Resp(text="<h1>Soup</h1>")})
        _resolves_to(monkeypatch, "93.184.216.34")
        assert extraction._guarded_fetch("https://recipes.example/x") == "<h1>Soup</h1>"

    def test_a_redirect_into_a_private_address_is_refused(self, wired, monkeypatch):
        """THE case a single up-front check misses: the first hop is a real recipe site and the
        30x is where the forgery lives. Every hop is re-checked, so the second never goes out."""
        seen = wired({
            "https://recipes.example/x": _Resp(location="http://169.254.169.254/latest/meta-data/"),
            "http://169.254.169.254/latest/meta-data/": _Resp(text="LEAKED"),
        })
        hosts = {"recipes.example": "93.184.216.34", "169.254.169.254": "169.254.169.254"}

        def fake_getaddrinfo(host, _port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], 0))]
        monkeypatch.setattr(extraction.socket, "getaddrinfo", fake_getaddrinfo)

        with pytest.raises(ValueError, match="non-public"):
            extraction._guarded_fetch("https://recipes.example/x")
        assert seen == ["https://recipes.example/x"], "the redirect target was fetched anyway"

    def test_a_relative_redirect_is_followed_on_the_same_host(self, wired, monkeypatch):
        wired({"https://recipes.example/x": _Resp(location="/recipe/42"),
               "https://recipes.example/recipe/42": _Resp(text="<h1>Stew</h1>")})
        _resolves_to(monkeypatch, "93.184.216.34")
        assert extraction._guarded_fetch("https://recipes.example/x") == "<h1>Stew</h1>"

    def test_a_redirect_loop_terminates(self, wired, monkeypatch):
        seen = wired({"https://recipes.example/x": _Resp(location="https://recipes.example/x")})
        _resolves_to(monkeypatch, "93.184.216.34")
        with pytest.raises(ValueError, match="redirects"):
            extraction._guarded_fetch("https://recipes.example/x")
        assert len(seen) == 5, "max_redirects is not bounding the loop"


class TestExtractUrl:
    """The route calls extract_url(), so the guard has to be on that path and not beside it."""

    def test_a_bare_host_is_refused_when_it_resolves_privately(self, monkeypatch):
        monkeypatch.setattr(extraction.httpx, "Client", _FakeClient({}, []))
        _resolves_to(monkeypatch, "192.168.1.10")
        with pytest.raises(ValueError, match="non-public"):
            extraction.extract_url("nas.local/recipes")     # no scheme -> https:// prepended
