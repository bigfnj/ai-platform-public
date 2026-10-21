"""The Base spirit picked in Add recipe is the drink's spirit (record 2026-09-11 third pass).

The modal's footer said "Saving to Beverages · Tequila", but the field was in neither the
frontend's RecipeDraft nor the backend's CommitReq, so the pick was dropped and the spirit
re-derived from the ingredient text. POST /api/recipes now takes `spirit`, persists it as an
override (db.spirit_overrides) and returns it; the catalog's Bar facet and the card read it,
and a full rebuild keeps it. Offline: a scratch corpus and DB, the icon and index jobs stubbed.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db
from recipe_book import ingest as ingestmod
from recipe_book import state as statemod
from recipe_book.api.routers import authoring as authrouter
from recipe_book.api.routers import recipes as recipesrouter

# A tequila sour filed under Mezcal: the ingredient text alone says Tequila, so only a pick
# that is honoured can make it Mezcal.
DRINK = {"title": "Smoked Sour", "kind": "beverage", "category": "Beverages",
         "ingredients": ["2 oz tequila", "1 oz lime juice", "3/4 oz agave syrup"],
         "instructions": ["Shake with ice and strain."]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")   # the dev run: chosen categories stand
    con = db.connect()
    db.init_db(con)
    con.close()
    statemod.reload()
    monkeypatch.setattr(authrouter, "_index_recipe", lambda rid: None)
    monkeypatch.setattr(authrouter, "_gen_icon", lambda rid: None)
    app = FastAPI()
    app.include_router(authrouter.router)
    app.include_router(recipesrouter.router)
    yield TestClient(app)
    statemod._catalog = None   # the next file loads its own catalog


def _spirit_of(client, rid: str) -> tuple[str, list[str]]:
    d = client.get(f"/api/recipes/{rid}").json()
    return d["primary_spirit"], d["base_spirits"]


def test_the_picked_spirit_is_persisted_and_returned(client):
    r = client.post("/api/recipes", json=dict(DRINK, spirit="Mezcal"))
    assert r.status_code == 200, r.text
    assert r.json()["spirit"] == "Mezcal"
    rid = r.json()["id"]
    primary, base = _spirit_of(client, rid)
    assert primary == "Mezcal"
    assert base[0] == "Mezcal", base        # the card's chips show the pick
    facet = {s["name"]: s["count"] for s in client.get("/api/spirits").json()["spirits"]}
    assert facet == {"Mezcal": 1}           # and the Bar facet files it there
    hits = client.get("/api/recipes", params={"kind": "beverage", "spirit": "Mezcal"}).json()
    assert [i["id"] for i in hits["items"]] == [rid]


def test_the_pick_survives_a_full_rebuild(client):
    rid = client.post("/api/recipes", json=dict(DRINK, spirit="Mezcal")).json()["id"]
    con = db.connect()
    try:
        ingestmod.rebuild(con)              # deletes every row and re-parses the corpus
    finally:
        con.close()
    statemod.reload()
    assert _spirit_of(client, rid)[0] == "Mezcal"


def test_without_a_pick_the_ingredients_decide(client):
    """The control: with no pick the derived spirit stands, so the tests above see the pick."""
    r = client.post("/api/recipes", json=DRINK)
    assert r.status_code == 200, r.text
    assert r.json()["spirit"] == ""
    assert _spirit_of(client, r.json()["id"]) == ("Tequila", ["Tequila"])


def test_an_unknown_spirit_is_refused_and_writes_nothing(client):
    r = client.post("/api/recipes", json=dict(DRINK, spirit="Moonshine"))
    assert r.status_code == 400, r.text
    assert not (config.RECIPES_DIR / "Beverages").exists()


def test_a_meal_ignores_a_spirit(client):
    meal = {"title": "Plain Rice", "kind": "meal", "category": "Sides",
            "ingredients": ["1 cup rice"], "instructions": ["Simmer."], "spirit": "Gin"}
    r = client.post("/api/recipes", json=meal)
    assert r.status_code == 200, r.text
    assert r.json()["spirit"] == ""
    con = db.connect()
    try:
        assert db.spirit_overrides(con) == {}
    finally:
        con.close()
