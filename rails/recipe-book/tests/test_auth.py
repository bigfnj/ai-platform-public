"""Fail-closed identity.

recipe-book was the worst of the inverted gates, because it is genuinely multi-tenant. Its
``identity()`` never raised, ``owner_id()`` resolved a header-less caller to the DEFAULT OWNER,
and ``require_admin`` only rejected a NAMED non-admin. Since ``users.id=1`` is claimed by
``RECIPE_BOOK_PRIMARY_USER`` (admin on this box, verified against the live DB), that meant a
sibling container with no headers read and wrote a real person's pantry, bar, meal plan and
ratings — and could fire rebuild, reindex, purge and GPU icon generation.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import broker, config, db, ingest, modelstate, seed, semantic
from recipe_book.api import app as appmod
from recipe_book.api.routers import assistant, authoring, bar, icons, pantry, planner, recipes

USER = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def _tmp_data(tmp_path, monkeypatch):
    """Point every configured path at tmp. ``db.connect`` calls ``config.ensure_dirs`` on each
    connection, so leaving these at their defaults makes a test create the container's data
    directories on a dev box."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "admin")
    con = db.connect()
    db.init_db(con)
    con.close()


@pytest.fixture()
def client(_tmp_data):
    app = FastAPI()
    for m in (pantry, bar, planner, recipes, assistant, authoring, icons):
        app.include_router(m.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def api(_tmp_data, monkeypatch):
    """The REAL application object, not a bag of routers.

    /api/capabilities and /api/search/status are declared inside ``create_api()``, so a test
    built from routers alone cannot see them — which is part of why they stayed un-gated. The
    expensive first-run work is stubbed out (a fresh DB would otherwise hydrate and ingest the
    whole ~900-card seed corpus) and so is every broker round-trip, so this stays hermetic.
    """
    monkeypatch.setattr(seed, "hydrate_if_empty", lambda: False)
    monkeypatch.setattr(seed, "hydrate_icons", lambda: False)
    monkeypatch.setattr(ingest, "ingest", lambda con: {})
    monkeypatch.setattr(semantic, "load", lambda: False)
    monkeypatch.setattr(modelstate, "resolve", lambda specs: {"broker": "ok", "models": []})
    monkeypatch.setattr(broker, "up", lambda: False)
    return TestClient(appmod.create_api(), raise_server_exceptions=False)


def test_read_without_identity_is_401(client):
    assert client.get("/api/pantry").status_code == 401


def test_write_without_identity_is_401(client):
    """The one that mattered: this used to land on admin's real pantry."""
    assert client.post("/api/pantry", json={"name": "salt"}).status_code == 401


def test_identity_header_is_allowed(client):
    assert client.get("/api/pantry", headers=USER).status_code == 200


def test_owner_impersonation_still_requires_admin(client):
    """?owner=<user> is an admin affordance; a non-admin passing it must be ignored, not
    honoured — the flag comes from the trusted header so it cannot be self-asserted."""
    client.post("/api/pantry", json={"name": "salt"}, headers=ADMIN)
    got = client.get("/api/pantry", params={"owner": "admin"}, headers=USER).json()["items"]
    assert not any(i["name"] == "salt" for i in got)


# --- the routes that were not multi-tenant, and so were never gated ----------------------
#
# The gate was put on the routes that touch an OWNER and stopped there, which left every
# route that reads shared state answering a caller with no identity at all. A probe from a
# sibling container confirmed /api/capabilities served 200; the rest were found with it.

def test_status_route_without_identity_is_401(api):
    """The rail's declared status_route (rail.json). It reports which models this deployment
    is running and whether they are resident — not something an un-authenticated caller on the
    compose network gets to enumerate. RC021 now proves this one from the AST."""
    assert api.get("/api/capabilities").status_code == 401


def test_status_route_with_identity_is_200(api):
    assert api.get("/api/capabilities", headers=USER).status_code == 200


def test_search_status_without_identity_is_401(api):
    assert api.get("/api/search/status").status_code == 401


def test_health_stays_open_as_a_liveness_probe(api):
    """The ONE deliberate exception. The contract allows a per-route rail to leave /api/health
    open; pinning it here means closing it becomes a visible decision rather than a silent one,
    and that a future app-wide gate does not break the container's health check by accident."""
    assert api.get("/api/health").status_code == 200


@pytest.mark.parametrize("path", [
    "/api/stats",            # corpus size
    "/api/categories",       # the whole category list
    "/api/spirits",
    "/api/models",           # what the broker has installed
    "/api/icons/status",
    "/api/icon/anything",    # generated artwork, served as files
])
def test_shared_read_routes_without_identity_are_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path,body", [
    ("/api/assistant", {"mode": "ask", "prompt": "hi"}),
    ("/api/recipes/draft", {"mode": "ai", "text": "1 egg"}),
    ("/api/recipes/draft/refine", {"draft": {}, "message": "more salt"}),
    ("/api/recipes/extract/url", {"url": "http://example.invalid/r"}),
    ("/api/recipes/duplicate_check", {"title": "soup"}),
    ("/api/planner/propose/swap", {"date": "2026-01-01"}),
])
def test_broker_spending_routes_without_identity_are_401(client, path, body):
    """Each of these costs GPU time on the broker every other rail shares, and none of them
    needed an owner — which is exactly why they were missed. /api/recipes/extract/url is the
    sharp one: it makes this container fetch a caller-supplied URL and report what came back."""
    assert client.post(path, json=body).status_code == 401


def test_extract_url_refuses_before_it_fetches_anything(client, monkeypatch):
    """401 must beat the fetch. If the gate ran after, an un-gated caller would still get this
    rail to dial an arbitrary address on the compose network and could time the difference."""
    def boom(url):
        raise AssertionError(f"extraction must not run for an un-gated caller (url={url!r})")
    monkeypatch.setattr(authoring.extraction, "extract_url", boom)
    assert client.post("/api/recipes/extract/url",
                       json={"url": "http://broker:8000/v1/status"}).status_code == 401


def test_standalone_allows_headerless_access(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/pantry").status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from recipe_book.api import deps
    assert deps.standalone() is False
