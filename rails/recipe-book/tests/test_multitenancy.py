"""Per-user (owner) scoping — the multi-tenant flip. Offline, no broker/catalog.

Mounts the owner-scoped routers on a bare app over a temp DB and drives them with the
gateway identity headers the platform injects."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db
from recipe_book.api.routers import bar, pantry, planner

ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}
ALICE = {"X-Platform-User": "alice"}
BOB = {"X-Platform-User": "bob"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "admin")   # admin inherits the legacy data
    con = db.connect()
    db.init_db(con)
    con.close()
    app = FastAPI()
    for m in (pantry, bar, planner):
        app.include_router(m.router)
    return TestClient(app)


def _pantry(client, headers, **params):
    return client.get("/api/pantry", headers=headers, params=params).json()["items"]


def test_pantry_isolation(client):
    client.post("/api/pantry", json={"name": "eggs", "kind": "on_hand"}, headers=ALICE)
    assert any(i["name"] == "eggs" for i in _pantry(client, ALICE))
    assert _pantry(client, BOB) == []          # bob can't see alice's pantry


def test_bar_and_plan_isolation(client):
    client.post("/api/bar", json={"name": "gin"}, headers=ALICE)
    client.post("/api/planner", json={"date": "2026-08-10", "slot": "dinner", "title": "Tacos"}, headers=ALICE)
    assert any(i["name"] == "gin" for i in client.get("/api/bar", headers=ALICE).json()["items"])
    assert client.get("/api/bar", headers=BOB).json()["items"] == []
    assert len(client.get("/api/planner", headers=ALICE).json()["entries"]) == 1
    assert client.get("/api/planner", headers=BOB).json()["entries"] == []


def test_admin_can_view_another_user(client):
    client.post("/api/pantry", json={"name": "gin"}, headers=ALICE)
    # admin acting as alice (?owner=alice) sees her pantry; the admin's own is separate/empty
    assert any(i["name"] == "gin" for i in _pantry(client, ADMIN, owner="alice"))
    assert _pantry(client, ADMIN) == []


def test_nonadmin_owner_param_is_ignored(client):
    client.post("/api/pantry", json={"name": "gin"}, headers=ALICE)
    # bob is not admin: ?owner=alice must be ignored -> he sees only his own (empty)
    assert _pantry(client, BOB, owner="alice") == []


def test_ungated_uses_default_owner_which_admin_inherits(client):
    # no identity header (standalone/dev) -> the default owner (1)
    client.post("/api/pantry", json={"name": "salt"})
    # admin is PRIMARY_USER, so their owner_id IS the default owner -> they see it
    assert any(i["name"] == "salt" for i in _pantry(client, ADMIN))


def test_resolve_owner_list_users_and_legacy_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "admin")
    con = db.connect()
    try:
        db.init_db(con)
        # legacy single-tenant data (owner 1) is claimed by the primary user
        assert db.resolve_owner(con, "admin") == db.OWNER_ID
        aid = db.resolve_owner(con, "alice")
        bid = db.resolve_owner(con, "bob")
        assert aid != bid and aid != db.OWNER_ID          # distinct, non-default ids
        assert db.resolve_owner(con, "alice") == aid       # stable across calls
        assert db.resolve_owner(con, "") == db.OWNER_ID    # empty/un-gated -> default
        assert {"admin", "alice", "bob"} <= set(db.list_users(con))
    finally:
        con.close()
