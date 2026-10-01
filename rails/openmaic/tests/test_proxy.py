"""Reverse-proxy header and path handling.

Both cases below are real bugs this rail shipped and then found by actually loading the app
through the proxy rather than by reading the code. Neither raised anything: one returned HTTP 200
full of binary, the other returned a clean 404 from Next.js that looked like a missing
application. They are regression tests, not hypotheticals.
"""
from openmaic_app.api import proxy
from openmaic_app.config import settings


# --- path -------------------------------------------------------------------------------------

def test_upstream_path_restores_the_public_prefix():
    """The gateway strips /openmaic and the router is mounted at /api/app, so the handler only
    sees the tail. Next.js built with a basePath serves AT the prefix, so it has to go back on."""
    assert proxy.upstream_path("_next/static/chunk.js") == "/openmaic/api/app/_next/static/chunk.js"


def test_root_has_no_trailing_slash():
    """The redirect-loop case. Next serves the root AT the prefix and 308s `/prefix/` to
    `/prefix`; FastAPI would then 307 back, with `/openmaic` already stripped from its Location.
    Forwarding the root without the slash is what breaks the cycle."""
    assert proxy.upstream_path("") == "/openmaic/api/app"
    assert proxy.upstream_path("/") == "/openmaic/api/app"


def test_upstream_path_does_not_double_the_separator():
    assert "//" not in proxy.upstream_path("/api/health").replace("://", "")


def test_upstream_path_follows_the_configured_prefix(monkeypatch):
    """The prefix is config, not a literal, because it has to equal the NEXT_BASE_PATH the image
    was built with — and that is a build argument set somewhere else entirely."""
    monkeypatch.setattr(settings, "public_prefix", "/elsewhere/app")
    assert proxy.upstream_path("x") == "/elsewhere/app/x"


# --- headers ----------------------------------------------------------------------------------

def test_content_encoding_is_relayed_not_stripped():
    """The body is relayed with aiter_raw(), i.e. still gzipped. Dropping the header hands the
    browser compressed bytes labelled as text — a 200 rendering as binary garbage."""
    assert "content-encoding" not in proxy._HOP_BY_HOP


def test_content_length_is_stripped():
    """StreamingResponse re-frames the body, so a relayed length can disagree with what is sent."""
    assert "content-length" in proxy._HOP_BY_HOP


def test_genuine_hop_by_hop_headers_are_stripped():
    for h in ("connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"):
        assert h in proxy._HOP_BY_HOP


def test_hop_by_hop_set_is_lowercase():
    """Lookups are done on `k.lower()`; a capitalised entry here would never match and the header
    would be relayed while the test suite still looked green."""
    assert all(h == h.lower() for h in proxy._HOP_BY_HOP)


# --- root-origin assets (rail.json `root_assets`) ----------------------------------------------

import pytest
from fastapi.testclient import TestClient

from openmaic_app.api.app import app

HDR = {"X-Platform-User": "bigfnj"}


@pytest.fixture()
def client(monkeypatch):
    from openmaic_app import broker

    monkeypatch.setattr(broker, "roles", lambda: [])
    monkeypatch.setattr(broker, "models", lambda: [])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": []})
    monkeypatch.setattr(broker, "up", lambda: False)
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/logos/openai.svg", "/avatars/teacher.png",
                                  "/logo-horizontal.png"])
def test_root_assets_are_gated_like_any_other_rail_content(client, path):
    """They are the rail's content and carry the rail's entitlement. The gateway gates them too,
    but a rail that relies on that alone is one routing change from serving them to anyone."""
    assert client.get(path).status_code == 401


def test_root_assets_reach_the_proxy(client):
    """502 is the PASS here: the app container is unreachable from a unit test, so a 502 proves
    the request was routed to the upstream proxy rather than 404'd or swallowed by a catch-all."""
    assert client.get("/logos/openai.svg", headers=HDR).status_code == 502


def test_the_catch_all_does_not_shadow_the_rails_own_api(client):
    """The root-asset route is a /{path:path} catch-all registered last. Registered any earlier
    it would swallow the status route, and the rail would serve its own chips payload as a 404
    from the app it fronts."""
    assert client.get("/api/capabilities", headers=HDR).status_code == 200
    assert client.get("/api/healthz", headers=HDR).status_code == 200


@pytest.mark.parametrize("path", ["/api/no-such-route", "/openapi.json", "/docs",
                                  "/not-declared.png", "/logosX/x.svg"])
def test_undeclared_paths_404_rather_than_being_proxied(client, path):
    """The catch-all serves ONLY what rail.json declares.

    Trusting the gateway to filter would behave identically in production and wrongly in
    standalone, where nothing is in front: the rail would forward its own /openapi.json and /docs
    to the app it fronts. `/logosX/` is here because a prefix match must respect the boundary —
    `/logos/` must not match a longer directory name that merely starts with it.
    """
    r = client.get(path, headers=HDR)
    assert r.status_code == 404
    assert "no such endpoint" in r.json().get("detail", "")


def test_declared_prefixes_come_from_config(monkeypatch):
    """One env override moves the whole set, so a rail whose wrapped app changes its asset roots
    does not need a code change."""
    from openmaic_app.config import settings

    monkeypatch.setattr(settings, "root_assets", "/brand/,/x.png")
    assert settings.root_asset_prefixes() == ("/brand/", "/x.png")
    assert settings.is_root_asset("brand/a/b.svg")
    assert settings.is_root_asset("/x.png")
    assert not settings.is_root_asset("/logos/openai.svg")


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_root_assets_are_read_only(client, method):
    """Claiming the origin root must not hand this rail a writable surface outside its own
    namespace. Only GET/HEAD are registered, so anything else is 405."""
    assert getattr(client, method)("/logos/openai.svg", headers=HDR).status_code == 405
