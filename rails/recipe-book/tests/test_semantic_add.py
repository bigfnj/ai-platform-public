"""Adding a recipe keeps the semantic index in step with the corpus.

The gap this guards: ``POST /api/recipes`` wrote a real card and ingested it, but never
touched ``semantic``, so a contributed recipe stayed invisible to semantic search until
someone re-embedded the entire catalog. The content sweep's ``index_covers_catalog`` is
the deployed observable; these are the offline equivalents.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import recipe_book.authoring as authmod
from recipe_book import broker, config, db, semantic
from recipe_book import ingest as ingestmod
from recipe_book import state as statemod
from recipe_book.api.routers import authoring as authrouter

ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


def _recipe(rid: str, title: str = "Jerk Seasoning"):
    """Enough of a Recipe for both `_recipe_text` and the fields the edit routes write."""
    return SimpleNamespace(id=rid, title=title, category="Sauces, Rubs, Marinades",
                           kind="meal", meta="", base_spirits=[],
                           ingredients=["1 tablespoon onion powder"], instructions=["Mix."],
                           shopping_list=[], glass="", technique="")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the index at a scratch dir and stub the embedder; nothing here touches a GPU."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)   # a Path: ensure_dirs() mkdirs it
    monkeypatch.setattr(semantic, "_INDEX", None)
    monkeypatch.setattr(broker, "EMBED_MODEL", "test-embed")
    calls: list[list[str]] = []

    def fake_embed(texts):
        texts = [texts] if isinstance(texts, str) else list(texts)
        calls.append(texts)
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    monkeypatch.setattr(broker, "embed", fake_embed)
    return calls


def test_add_is_a_noop_without_an_index(_isolate):
    """A missing index is ``build``'s job. ``add`` must not silently start a full re-embed,
    which is admin-gated precisely because it occupies the shared GPU."""
    assert semantic.add(_recipe("aaa")) is False
    assert _isolate == []                       # and it did not reach the broker
    assert semantic.status()["count"] == 0


def test_add_appends_and_persists(_isolate):
    semantic.build(SimpleNamespace(recipes=[_recipe("aaa"), _recipe("bbb")]))
    assert semantic.status()["count"] == 2

    assert semantic.add(_recipe("ccc", "New Card")) is True
    assert semantic.status()["count"] == 3

    # survives a reload from disk, not just an in-memory append
    path = semantic._path()
    assert semantic.load() is True
    assert semantic.status()["count"] == 3
    assert "ccc" in json.loads(path.read_text())["ids"]
    assert path.with_suffix(".json.tmp").exists() is False   # the atomic write cleaned up


def test_add_replaces_rather_than_duplicating(_isolate):
    semantic.build(SimpleNamespace(recipes=[_recipe("aaa")]))
    semantic.add(_recipe("aaa", "Renamed Card"))

    assert semantic.status()["count"] == 1
    ids = json.loads(semantic._path().read_text())["ids"]
    assert ids == ["aaa"]


def test_add_ranks_the_new_recipe(_isolate):
    """The point of the fix: a just-added recipe is reachable by query, not merely counted."""
    semantic.build(SimpleNamespace(recipes=[_recipe("aaa", "Pancakes")]))
    semantic.add(_recipe("ccc", "Jerk Seasoning"))
    assert {rid for rid, _ in semantic.query("jerk")} == {"aaa", "ccc"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The commit route with its corpus write, ingest and icon render stubbed out, so the
    only live side effect left is the one under test."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.close()

    monkeypatch.setattr(authmod, "write_card",
                        lambda *, category, title, markdown: f"{category}/{title}.md")
    monkeypatch.setattr(ingestmod, "ingest", lambda con: None)
    monkeypatch.setattr(statemod, "reload", lambda: None)
    monkeypatch.setattr(authrouter, "_gen_icon", lambda rid: None)

    indexed: list[str] = []
    monkeypatch.setattr(semantic, "add", lambda r: indexed.append(r.id) or True)
    # the route resolves the freshly-ingested card through the catalog
    monkeypatch.setattr(statemod, "catalog",
                        lambda: SimpleNamespace(get=lambda rid: _recipe(rid)))

    app = FastAPI()
    app.include_router(authrouter.router)
    c = TestClient(app)
    c.indexed = indexed   # type: ignore[attr-defined]
    return c


def test_commit_indexes_the_new_recipe(client):
    """Regression guard for the gap itself: committing must reach ``semantic.add``.
    TestClient drains background tasks before returning, so this covers the wiring."""
    r = client.post("/api/recipes", headers=ADMIN, json={
        "title": "Homemade Jerk Seasoning", "kind": "meal",
        "category": "Sauces, Rubs, Marinades",
        "ingredients": ["1 tablespoon onion powder"], "instructions": ["Mix."]})
    assert r.status_code == 200, r.text
    assert client.indexed == [r.json()["id"]]


def test_title_edit_reindexes(client):
    """A rename changes the embedded text (``_recipe_text`` leads with the title), so the
    cached vector goes stale unless the edit reindexes too."""
    r = client.put("/api/recipes/abc123/title", headers=ADMIN, json={"title": "Renamed"})
    assert r.status_code == 200, r.text
    assert client.indexed == ["abc123"]


def test_content_edit_reindexes(client):
    """Same for ingredients, which are the bulk of the embedded text."""
    r = client.put("/api/recipes/abc123/content", headers=ADMIN,
                   json={"ingredients": ["2 teaspoons allspice"], "instructions": ["Mix."]})
    assert r.status_code == 200, r.text
    assert client.indexed == ["abc123"]
