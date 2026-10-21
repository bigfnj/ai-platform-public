"""Tags belong to their owner — the cross-user disclosure.

Two halves of one hole. The WRITER (POST /api/recipes/{id}/tags) never checked that the
posted ``tag_id`` was the caller's: the recipe_tags row carried the caller's owner_id but
pointed at somebody else's tag. The READER (overlays.decorate) then joined
``recipe_tags rt JOIN tags t ON t.id = rt.tag_id`` scoped on ``rt`` alone, so it happily read
the name and colour off that other owner's row. Verified before the fix: user A's private tag
name appeared in user B's recipe list while GET /api/tags correctly showed B owning none.

Both halves are asserted, because either one alone still leaks: the reader is what stops rows
already in the database from rendering, and the writer is what stops new ones being made.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db, overlays
from recipe_book.api.routers import personalization

ALICE = {"X-Platform-User": "alice", "X-Platform-Admin": "0"}
BOB = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}

#: catalog.recipe_id() shape — a 12-char sha1 prefix, so it survives the {recipe_id} path
#: converter (an id with a "/" in it would 404 on routing and every assertion below would
#: pass for the wrong reason).
RECIPE = "a1b2c3d4e5f6"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.close()
    app = FastAPI()
    app.include_router(personalization.router)
    return TestClient(app, raise_server_exceptions=False)


def _owner(username: str) -> int:
    con = db.connect()
    try:
        return db.resolve_owner(con, username)
    finally:
        con.close()


def _decorated_tags(username: str, recipe_id: str = RECIPE) -> list[dict]:
    """What the recipe list/detail would render for this user (recipes.py -> overlays)."""
    con = db.connect()
    try:
        return overlays.decorate(con, [{"id": recipe_id}], owner=_owner(username))[0]["tags"]
    finally:
        con.close()


def test_cannot_attach_another_users_tag(client):
    """The writer half. Alice's tag id is a small integer, so guessing it is not the hard
    part — nothing checked it at all."""
    alice_tag = client.post("/api/tags", json={"name": "secret-diet"}, headers=ALICE).json()
    r = client.post(f"/api/recipes/{RECIPE}/tags",
                    json={"tag_id": alice_tag["id"]}, headers=BOB)
    assert r.status_code == 404
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) c FROM recipe_tags WHERE owner_id=?",
                           (_owner("bob"),)).fetchone()["c"] == 0
    finally:
        con.close()


def test_another_users_tag_name_is_never_rendered(client):
    """The reader half, asserted against a row planted directly in the DB — that is the state
    the old writer produced, and it survives a deployment of the writer fix alone."""
    alice_tag = client.post("/api/tags", json={"name": "secret-diet"}, headers=ALICE).json()
    con = db.connect()
    try:
        con.execute("INSERT INTO recipe_tags (owner_id, recipe_id, tag_id) VALUES (?,?,?)",
                    (_owner("bob"), RECIPE, alice_tag["id"]))
        con.commit()
    finally:
        con.close()
    assert _decorated_tags("bob") == []
    assert [t["name"] for t in _decorated_tags("alice")] == []   # alice never tagged it


def test_tag_list_and_recipe_list_agree(client):
    """The tell that made this findable: /api/tags said bob owned no tags while his recipe
    list displayed one. The two views must not disagree."""
    alice_tag = client.post("/api/tags", json={"name": "secret-diet"}, headers=ALICE).json()
    con = db.connect()
    try:
        con.execute("INSERT INTO recipe_tags (owner_id, recipe_id, tag_id) VALUES (?,?,?)",
                    (_owner("bob"), RECIPE, alice_tag["id"]))
        con.commit()
    finally:
        con.close()
    assert client.get("/api/tags", headers=BOB).json()["tags"] == []
    assert _decorated_tags("bob") == []


def test_own_tag_still_attaches_and_renders(client):
    """The fix must not break the feature: the whole thing could 'pass' by tagging nothing."""
    bob_tag = client.post("/api/tags", json={"name": "weeknight", "color": "lime"},
                          headers=BOB).json()
    assert client.post(f"/api/recipes/{RECIPE}/tags",
                       json={"tag_id": bob_tag["id"]}, headers=BOB).status_code == 200
    rendered = _decorated_tags("bob")
    assert rendered == [{"id": bob_tag["id"], "name": "weeknight", "color": "lime"}]
    assert _decorated_tags("alice") == []          # and it stays bob's


def test_attaching_a_nonexistent_tag_is_404(client):
    assert client.post(f"/api/recipes/{RECIPE}/tags",
                       json={"tag_id": 9999}, headers=BOB).status_code == 404


def test_reattaching_an_own_tag_is_idempotent(client):
    """INSERT OR IGNORE used to swallow the repeat; the EXISTS-conditional insert must not
    turn a harmless double-click into a 404."""
    bob_tag = client.post("/api/tags", json={"name": "weeknight"}, headers=BOB).json()
    for _ in range(2):
        assert client.post(f"/api/recipes/{RECIPE}/tags",
                           json={"tag_id": bob_tag["id"]}, headers=BOB).status_code == 200
    assert len(_decorated_tags("bob")) == 1
