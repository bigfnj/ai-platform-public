"""Per-user (owner) scoping — the multi-tenant flip. Offline, no broker/catalog.

Mounts the owner-scoped routers on a bare app over a temp DB and drives them with the
gateway identity headers the platform injects."""
from __future__ import annotations

import sqlite3
import threading

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


def test_ungated_caller_cannot_reach_the_default_owner(client):
    # Inverted deliberately. This used to assert that a header-less write lands on the DEFAULT
    # OWNER — and since users.id=1 is claimed by RECIPE_BOOK_PRIMARY_USER (admin), that
    # meant any sibling container could write into a real person's pantry. Now 401.
    assert client.post("/api/pantry", json={"name": "salt"}).status_code == 401


def test_standalone_still_uses_the_default_owner(client, monkeypatch):
    # The behaviour the old test described is correct once it is an explicit dev mode.
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    client.post("/api/pantry", json={"name": "salt"})
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


def test_resolve_owner_survives_a_concurrent_first_sight(tmp_path, monkeypatch):
    """The first request from a brand-new user is not one request.

    resolve_owner was a SELECT followed by an INSERT against the UNIQUE index on
    users.platform_user, and api.deps.owner_id calls it for nearly every route — so the first
    page load of a new user runs it from every request that page makes, at once, on separate
    connections. Every caller that lost the race raised sqlite3.IntegrityError, which reaches
    the browser as a 500. Measured before the fix: 4 of 8 barrier-synchronised calls raised.

    Barrier-synchronised on purpose: without it the threads simply queue and the bug hides.
    """
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "race.db"))
    con = db.connect()
    db.init_db(con)
    con.close()

    def storm(username: str, n: int) -> tuple[list[int], list[BaseException]]:
        barrier = threading.Barrier(n)
        ids: list[int] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def call() -> None:
            c = db.connect()          # one connection per request, as api.deps.owner_id does
            try:
                barrier.wait(timeout=20)
                oid = db.resolve_owner(c, username)
            except BaseException as exc:          # noqa: BLE001 - the point is to record it
                with lock:
                    errors.append(exc)
            else:
                with lock:
                    ids.append(oid)
            finally:
                c.close()

        threads = [threading.Thread(target=call) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return ids, errors

    con = db.connect()
    try:
        for username in ("newcomer", "second-newcomer", "third-newcomer"):
            ids, errors = storm(username, 12)
            assert [type(e).__name__ for e in errors] == [], f"{username}: {errors[:1]}"
            assert not any(isinstance(e, sqlite3.IntegrityError) for e in errors)
            # One row, and every caller agrees which id it is.
            assert len(ids) == 12 and len(set(ids)) == 1, f"{username}: {sorted(set(ids))}"
            rows = con.execute("SELECT id FROM users WHERE platform_user=?",
                               (username,)).fetchall()
            assert len(rows) == 1 and rows[0]["id"] == ids[0]
    finally:
        con.close()
