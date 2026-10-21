"""Background work that fails says so, and the pairing 404 names the real reason.

Found 2026-10-08: the semantic reindex job and the per-recipe index/icon tasks ended in
`except Exception: pass`, so a nightly rebuild that failed left no trace (the scheduler only
sees "queued") and a recipe silently missed the index or its icon. And the cocktail-pairing
404 told people to "add bottles" when the pool is the BOOK's cocktails, never their bar.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from recipe_book import broker, config, icons, planner_ai, semantic, state
from recipe_book.api.routers import authoring as authrouter


@pytest.fixture(autouse=True)
def _clean_semantic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(semantic, "_INDEX", None)
    monkeypatch.setattr(semantic, "_STALE", "")
    monkeypatch.setattr(semantic, "_LAST_ERROR", "")


def test_a_failed_build_is_recorded_and_a_good_one_clears_it(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="recipe_book.semantic")
    semantic.record_failure(broker.BrokerError("embed failed: 502"))
    assert "BrokerError: embed failed: 502" in semantic.status()["last_error"]
    assert "semantic index build failed" in caplog.text

    monkeypatch.setattr(broker, "embed_resolved",
                        lambda texts, **_kw: ([[1.0, 0.0] for _ in texts], "bge-m3:latest"))
    semantic.build(SimpleNamespace(recipes=[SimpleNamespace(
        id="a", title="Soup", category="Soups", meta="", base_spirits=[], ingredients=["onion"])]))
    assert "last_error" not in semantic.status()


def test_the_reindex_route_records_a_failure(tmp_path, monkeypatch):
    """Through the real route and its background task, not record_failure called by hand."""
    from fastapi.testclient import TestClient

    from recipe_book import ingest, seed
    from recipe_book.api import create_api

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(seed, "hydrate_if_empty", lambda: None)
    monkeypatch.setattr(seed, "hydrate_icons", lambda: False)
    monkeypatch.setattr(ingest, "ingest", lambda con: None)
    monkeypatch.setattr(semantic, "load", lambda: False)
    monkeypatch.setattr(state, "_catalog", None)

    def boom(_catalog, **_kw):
        raise broker.BrokerError("embed failed: broker unreachable")

    monkeypatch.setattr(semantic, "build", boom)
    c = TestClient(create_api(), raise_server_exceptions=False)
    admin = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}
    assert c.post("/api/search/reindex", headers=admin).status_code == 200
    status = c.get("/api/search/status", headers=admin).json()
    assert "broker unreachable" in status.get("last_error", ""), status


def test_a_failed_index_add_and_icon_are_logged(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="recipe_book.authoring")
    monkeypatch.setattr(state, "_catalog", SimpleNamespace(get=lambda rid: SimpleNamespace(id=rid)))

    def boom(*_a, **_kw):
        raise broker.BrokerError("down")

    monkeypatch.setattr(semantic, "add", boom)
    monkeypatch.setattr(icons, "generate", boom)
    monkeypatch.setattr(config, "DB_PATH", str(config.DATA_DIR / "rb.db"))
    authrouter._index_recipe("r1")
    authrouter._gen_icon("r1")
    assert "semantic index did not take r1" in caplog.text
    assert "icon for r1 was not generated" in caplog.text


def test_an_exhausted_cocktail_pool_does_not_blame_the_bar(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from recipe_book import db
    from recipe_book.api.routers import planner

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(config.DATA_DIR / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.close()
    monkeypatch.setattr(state, "_catalog", SimpleNamespace(get=lambda rid: None, recipes=[]))
    monkeypatch.setattr(planner_ai, "_cocktail_pool", lambda: [])
    app = FastAPI()
    app.include_router(planner.router)
    r = TestClient(app).post("/api/planner/pair", json={"date": "2026-10-08", "ptype": "cocktail"},
                             headers={"X-Platform-User": "bob"})
    assert r.status_code == 404
    assert "bottle" not in r.json()["detail"].lower()
    assert "book" in r.json()["detail"]
