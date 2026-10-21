"""A category move writes an override only for a recipe that exists.

The audit's probe (2026-10-08): PUT /api/recipes/<nonexistent>/category as admin answered 200
and wrote a category_overrides row for a recipe that does not exist, so a junk id (a 'zzz' ->
Entrees row, say) left an override that re-applies to nothing. The attributes route beside it
already 404'd. Real ingest over a scratch corpus, real catalog.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import catalog, config, db, state
from recipe_book import ingest as ingestmod
from recipe_book.api.routers import recipes as recipesrouter

ADMIN = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(state, "_catalog", None)
    card = tmp_path / "recipes" / "Entrees" / "My Dish.md"
    card.parent.mkdir(parents=True)
    card.write_text("# My Dish\n\n- 1 cup flour\n- *Method:* mix\n", encoding="utf-8")
    con = db.connect()
    db.init_db(con)
    ingestmod.ingest(con, recipes_dir=tmp_path / "recipes")
    con.commit()
    con.close()
    state.reload()
    app = FastAPI()
    app.include_router(recipesrouter.router)
    yield TestClient(app)
    monkeypatch.setattr(state, "_catalog", None)


def _overrides() -> list[tuple[str, str]]:
    con = db.connect()
    try:
        return [(r["recipe_id"], r["category"]) for r in
                con.execute("SELECT recipe_id, category FROM category_overrides")]
    finally:
        con.close()


def test_a_missing_recipe_is_404_and_writes_no_override(client):
    r = client.put("/api/recipes/zzz-does-not-exist/category", json={"category": "Entrees"},
                   headers=ADMIN)
    assert r.status_code == 404
    assert _overrides() == []


def test_an_existing_recipe_moves_and_the_override_is_kept(client):
    rid = catalog.recipe_id("Entrees/My Dish.md")
    r = client.put(f"/api/recipes/{rid}/category", json={"category": "Soups"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert _overrides() == [(rid, "Soups")]
