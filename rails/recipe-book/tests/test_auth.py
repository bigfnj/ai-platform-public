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

from recipe_book import config, db
from recipe_book.api.routers import bar, pantry, planner

USER = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "admin")
    con = db.connect()
    db.init_db(con)
    con.close()
    app = FastAPI()
    for m in (pantry, bar, planner):
        app.include_router(m.router)
    return TestClient(app, raise_server_exceptions=False)


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


def test_standalone_allows_headerless_access(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/pantry").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from recipe_book.api import deps
    assert deps.standalone() is False


# --- a PRESENT but blank header ----------------------------------------------------------
#
# The second half of the same defect, found 2026-09-11 and worse than the header-less case it
# hid behind. Starlette hands `X-Platform-User:` through as "" rather than None, so the
# `is None` gate admitted it, `owner_id()` computed `target = ""`, and `resolve_owner(con, "")`
# returned OWNER_ID — the row init_db claims for RECIPE_BOOK_PRIMARY_USER. Measured against the
# running container before the fix: a blank header returned the primary user's real pantry,
# byte-identical to the authenticated response, while no header at all correctly 401'd.
#
# Asserted over HTTP, because the empty string only exists once Starlette has parsed the
# request: a unit call passing user="" proves the resolver's behaviour, not the gate's.


@pytest.mark.parametrize("blank", ["", " ", "\t", "   "])
def test_a_blank_identity_header_is_refused(client, blank):
    assert client.get("/api/pantry", headers={"X-Platform-User": blank}).status_code == 401


def test_a_blank_header_cannot_reach_the_primary_owner(client):
    """The escalation itself, not just the status code. A 401 assertion alone would still pass
    if a later refactor made the gate 401 for an unrelated reason while the resolver kept
    mapping "" onto row 1, so this pins the DATA: what the admin wrote must not come back."""
    client.post("/api/pantry", json={"name": "saffron"}, headers=ADMIN)
    mine = client.get("/api/pantry", headers=ADMIN).json()["items"]
    assert any(i["name"] == "saffron" for i in mine), "fixture precondition: the write landed"

    r = client.get("/api/pantry", headers={"X-Platform-User": ""})
    assert r.status_code == 401
    assert "saffron" not in r.text


def test_a_blank_header_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. A caller asserting an
    empty identity is making a claim, not omitting one, so the dev hatch does not cover it."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/pantry").status_code == 200
    assert client.get("/api/pantry", headers={"X-Platform-User": ""}).status_code == 401


def test_a_surrounded_username_is_accepted_and_trimmed(api):
    """The flip side of stripping: a header the gateway padded must still identify the user,
    and must not become a second distinct owner. Uses the full app, because /api/whoami is
    declared in app.py rather than in the three routers the `client` fixture assembles."""
    assert api.get("/api/whoami", headers={"X-Platform-User": " bob "}).json()["user"] == "bob"


def test_no_route_admits_a_blank_identity(api):
    """App-level, over app.routes rather than a list, for the same reason the header-less
    version of this test is: it has to hold for routes added tomorrow."""
    from fastapi.routing import APIRoute
    open_routes = [f"{sorted(r.methods)[0]} {r.path}" for r in api.app.routes
                   if isinstance(r, APIRoute) and r.path.startswith("/api/")
                   and api.request(sorted(r.methods)[0], r.path,
                                   headers={"X-Platform-User": ""}).status_code != 401]
    assert open_routes == []


# --- the gate is APP-level, not per-route -----------------------------------------------
#
# The routers above were gated one route at a time and sixteen were missed, including
# POST /api/assistant — which had no identity parameter at all, so with PLATFORM_STANDALONE
# unset it returned 502 (it had reached the broker) instead of 401. These tests drive the real
# create_api() app rather than a hand-assembled router set, because the fix is a property of
# the app object and a per-route test cannot see it.


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """The app the container actually runs, made hermetic.

    Seeding/ingest/index load are stubbed (the bundled corpus is ~900 markdown cards) and
    every broker entry point is poisoned, so a route that answers without identity cannot
    quietly reach the GPU on the way to failing the assertion.
    """
    from recipe_book import broker, ingest, seed, semantic, state
    from recipe_book.api import create_api

    def _no_broker(*a, **kw):
        raise AssertionError("a test reached the broker")

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(seed, "hydrate_if_empty", lambda: None)
    monkeypatch.setattr(seed, "hydrate_icons", lambda: False)
    monkeypatch.setattr(ingest, "ingest", lambda con: None)
    monkeypatch.setattr(semantic, "load", lambda: False)
    monkeypatch.setattr(state, "_catalog", None)   # monkeypatch restores the process catalog
    for name in ("chat", "chat_json", "embed", "picker_models", "roles", "status", "up"):
        monkeypatch.setattr(broker, name, _no_broker)
    return TestClient(create_api(), raise_server_exceptions=False)


#: Every route that had NO identity dependency of its own, with a body where one is required.
#: Six of them spend broker/GPU time; the rest disclose the catalog.
UNGATED = [
    ("POST", "/api/assistant", {"json": {"mode": "ask", "prompt": "hi"}}),
    ("GET", "/api/models", {}),
    ("POST", "/api/recipes/draft", {"json": {"mode": "manual", "title": "x"}}),
    ("POST", "/api/recipes/draft/refine", {"json": {"draft": {}, "message": "more salt"}}),
    ("POST", "/api/recipes/extract/url", {"json": {"url": "http://example.invalid/r"}}),
    ("POST", "/api/recipes/extract/files", {"files": {"files": ("r.txt", b"hi")}}),
    ("POST", "/api/recipes/duplicate_check", {"json": {"title": "Tacos"}}),
    ("POST", "/api/planner/propose/swap", {"json": {"date": "2026-09-10", "slot": "dinner"}}),
    ("GET", "/api/stats", {}),
    ("GET", "/api/categories", {}),
    ("GET", "/api/spirits", {}),
    ("GET", "/api/icon/abc", {}),
    ("GET", "/api/icons/status", {}),
    # app.py's own routes, which were gated the same way (or not at all)
    ("GET", "/api/health", {}),
    ("GET", "/api/capabilities", {}),
    ("GET", "/api/search/status", {}),
]


@pytest.mark.parametrize("method,path,kw", UNGATED, ids=[f"{m} {p}" for m, p, _ in UNGATED])
def test_previously_ungated_routes_are_401(api, method, path, kw):
    assert api.request(method, path, **kw).status_code == 401


def test_no_route_is_reachable_without_identity(api):
    """The point of an app-level dependency: this holds for routes added tomorrow too, so it
    is asserted over app.routes rather than over a list someone has to remember to extend."""
    from fastapi.routing import APIRoute
    open_routes = [f"{sorted(r.methods)[0]} {r.path}" for r in api.app.routes
                   if isinstance(r, APIRoute) and r.path.startswith("/api/")
                   and api.request(sorted(r.methods)[0], r.path).status_code != 401]
    assert open_routes == []


def test_identity_header_still_reaches_the_route(api):
    """Guards against the gate passing for the wrong reason — a 401 everywhere is also what a
    broken app looks like."""
    assert api.get("/api/whoami", headers=USER).json() == {"user": "bob", "is_admin": False}
    assert api.get("/api/stats", headers=USER).status_code == 200


def test_docs_and_openapi_are_disabled(api):
    """FastAPI serves these outside the router, so they bypass an app-level dependency: with
    them on, a caller who can invoke nothing can still read the entire route list."""
    for path in ("/api/docs", "/api/redoc", "/api/openapi.json", "/docs", "/openapi.json"):
        assert api.get(path, headers=ADMIN).status_code == 404, path


def test_standalone_still_opens_the_whole_app(api, monkeypatch):
    """The dev escape hatch has to survive the move from per-route to app-level."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert api.get("/api/stats").status_code == 200
