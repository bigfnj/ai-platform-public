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
