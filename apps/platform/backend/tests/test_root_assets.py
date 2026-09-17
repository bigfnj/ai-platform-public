"""A rail may claim ORIGIN-ROOT paths, and the gateway must route and gate them.

Why the feature exists: a rail written for this platform lives entirely under /<id>/ and never
needs this. A rail that WRAPS a third-party app does — openmaic fronts an upstream Next.js app
carrying absolute `<img src="/logos/...">` literals, and Next's basePath only rewrites URLs Next
itself generates, so a JSX string literal is passed through untouched and the browser resolves it
against the origin root.

Why it is checked here rather than trusted: the root is an exhaustible shared resource whose
failure mode is silent. Two rails claiming /avatars/ would not error; one would simply serve the
other's images. And before this existed, an unrouted /logos/openai.svg did not even 404 — the
shell's client-side-routing catch-all answered index.html with status 200, so the browser showed
a broken image while the network tab showed success.
"""
from __future__ import annotations

import pytest

from platform_gateway_app.config import ROOT_ASSETS, GatewaySettings


@pytest.fixture()
def settings(monkeypatch) -> GatewaySettings:
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    return GatewaySettings()


def test_declared_prefixes_route_to_their_owner(settings):
    for path in ("/logos/openai.svg", "/avatars/teacher.png", "/logo-horizontal.png",
                 "/openmaic-mark.png"):
        assert settings.root_asset_owner(path) == "openmaic", path


def test_undeclared_paths_are_not_claimed(settings):
    """Anything not declared must fall through to the shell exactly as before — the feature adds
    a route, it does not widen the gateway's reach."""
    for path in ("/", "/favicon.ico", "/unrelated.png", "/logosX/other.svg", "/some/deep/link"):
        assert settings.root_asset_owner(path) is None, path


def test_directory_prefix_respects_the_boundary(settings):
    """`/logos/` must not match `/logosX/...`. A plain startswith on the prefix without its
    trailing slash would, and would quietly steal another owner's namespace."""
    assert settings.root_asset_owner("/logos/a/b/c.svg") == "openmaic"
    assert settings.root_asset_owner("/logosX/a.svg") is None


@pytest.mark.parametrize("reserved", ["/api/platform/status", "/assets/index-abc.js",
                                      "/ws/anything"])
def test_platform_reserved_paths_can_never_be_claimed(settings, monkeypatch, reserved):
    """Even a mirror that wrongly claims a reserved prefix must not shadow the shell's own
    bundle or the API surface. RC028 fails the build on it; this is the runtime backstop."""
    monkeypatch.setitem(ROOT_ASSETS, "openmaic", ("/api/", "/assets/", "/ws/"))
    assert GatewaySettings().root_asset_owner(reserved) is None


def test_disabling_a_rail_releases_its_root_claim(settings, monkeypatch):
    """Turning a rail off must free the paths it held, not leave a dead reservation that 404s
    for everyone while the rail is gone."""
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "terminal-fun,recipe-book")
    assert GatewaySettings().root_asset_owner("/logos/openai.svg") is None


def test_exact_path_beats_a_containing_directory_prefix(monkeypatch):
    """Longest-prefix-first. Declared out of order, the more specific claim must still win, or
    which rail serves a file would depend on dict insertion order."""
    monkeypatch.setitem(ROOT_ASSETS, "openmaic", ("/shared/",))
    monkeypatch.setitem(ROOT_ASSETS, "terminal-fun", ("/shared/one.png",))
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "openmaic,terminal-fun")
    s = GatewaySettings()
    assert s.root_asset_owner("/shared/one.png") == "terminal-fun"
    assert s.root_asset_owner("/shared/two.png") == "openmaic"


def test_the_shipped_mirror_only_claims_paths_it_should():
    """A guard on the mirror itself: every declared prefix is absolute, and none reaches into
    another rail's /<id>/ namespace, which the gateway already routes."""
    for app_id, prefixes in ROOT_ASSETS.items():
        for p in prefixes:
            assert p.startswith("/"), (app_id, p)
            assert not p.startswith(f"/{app_id}/"), (app_id, p)


def test_root_asset_routes_expose_no_query_parameters():
    """The owning rail is bound in a closure, and that is load-bearing.

    Capturing the loop variable as a default argument -- `async def handler(request, _owner: str
    = app_id)` -- is the obvious way to write this, and FastAPI then reads the signature and
    treats the defaulted scalar as a QUERY PARAMETER. `/logo-horizontal.png?_owner=recipe-book`
    would have re-pointed the request at a different rail, AFTER the entitlement gate had already
    decided the path belonged to openmaic.
    """
    from fastapi.routing import APIRoute

    from platform_gateway_app.main import app

    routes = [r for r in app.routes
              if isinstance(r, APIRoute) and (r.name or "").startswith("root-assets:")]
    assert routes, "no root-asset routes registered"
    for r in routes:
        assert not r.dependant.query_params, (
            f"{r.path} exposes query params {[q.name for q in r.dependant.query_params]} — "
            "a caller could re-point the owning rail")


def test_root_asset_routes_are_registered_for_every_declared_prefix():
    from fastapi.routing import APIRoute

    from platform_gateway_app.main import app

    names = {r.name for r in app.routes if isinstance(r, APIRoute) and (r.name or "").startswith("root-assets:")}
    for prefix in ROOT_ASSETS["openmaic"]:
        assert f"root-assets:openmaic:{prefix}" in names, prefix


def test_root_asset_routes_are_read_only():
    from fastapi.routing import APIRoute

    from platform_gateway_app.main import app

    for r in app.routes:
        if isinstance(r, APIRoute) and (r.name or "").startswith("root-assets:"):
            assert set(r.methods) <= {"GET", "HEAD"}, (r.path, r.methods)
