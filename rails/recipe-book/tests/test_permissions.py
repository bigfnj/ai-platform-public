"""Contributor permissions: non-admins may add a recipe (forced into "To Try") but
may NOT edit recipes. Admins keep full control. Offline (authoring side-effects mocked)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import recipe_book.authoring as authmod
from recipe_book import config, db
from recipe_book import ingest as ingestmod
from recipe_book import state as statemod
from recipe_book.api.routers import authoring as authrouter
from recipe_book.api.routers import recipes as recipesrouter

ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}
ALICE = {"X-Platform-User": "alice"}

_COMMIT = {"title": "Test Dish", "kind": "meal", "category": "Entrees",
           "ingredients": ["1 cup flour"], "instructions": ["mix"]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.close()
    # Isolate the corpus write + reindex; we only assert which category was chosen.
    captured: dict = {}

    def fake_write_card(*, category, title, markdown):
        captured["category"] = category
        return f"{category}/{title}.md"

    monkeypatch.setattr(authmod, "write_card", fake_write_card)
    monkeypatch.setattr(ingestmod, "ingest", lambda con: None)
    monkeypatch.setattr(statemod, "reload", lambda: None)
    monkeypatch.setattr(authrouter, "_gen_icon", lambda rid: None)

    app = FastAPI()
    app.include_router(authrouter.router)
    app.include_router(recipesrouter.router)
    client = TestClient(app)
    client.captured = captured   # type: ignore[attr-defined]
    return client


def test_nonadmin_upload_is_forced_to_try(client):
    r = client.post("/api/recipes", json=_COMMIT, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "To Try"           # not "Entrees"
    assert client.captured["category"] == "To Try"    # the card was filed into the bucket


def test_admin_upload_keeps_chosen_category(client):
    r = client.post("/api/recipes", json=_COMMIT, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "Entrees"
    assert client.captured["category"] == "Entrees"


def test_ungated_upload_keeps_category(client):
    # standalone/dev (no identity header) is not a contributor -> keeps the chosen category
    r = client.post("/api/recipes", json=_COMMIT)
    assert r.json()["category"] == "Entrees"


@pytest.mark.parametrize("path,body", [
    ("/api/recipes/x/category", {"category": "Entrees"}),
    ("/api/recipes/x/title", {"title": "Renamed"}),
    ("/api/recipes/x/content", {"ingredients": ["a"], "instructions": ["b"]}),
    ("/api/recipes/x/attributes", {"attributes": ["vegetarian"]}),
])
def test_nonadmin_cannot_edit(client, path, body):
    assert client.put(path, json=body, headers=ALICE).status_code == 403


def test_admin_may_edit(client):
    # admin passes the gate (category-move on a missing recipe still succeeds via override)
    assert client.put("/api/recipes/x/category", json={"category": "Soups"},
                      headers=ADMIN).status_code == 200
