"""Semantic search uses the platform @embed role, declares it, and notices when it moves.

Two findings, one mechanism. The coordinator's (2026-10-08): RECIPE_BOOK_EMBED_MODEL defaulted
to the literal "bge-m3" with no manifest slot, so the chips, RC013 and RC027 all walked past it
and the full stack never passed the variable. The 2026-09-11 record's: the index recorded the
model it was built with and nothing compared it, so after a repoint zip(qvec, vec) truncated to
the shorter vector, scores still cleared the floor, and the route said "semantic": true.

The broker is stubbed: embed_resolved answers (vectors, the concrete model Ollama named).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recipe_book import broker, config, semantic

RAIL = Path(__file__).resolve().parents[1]


def _recipe(rid: str, title: str = "Onion soup"):
    return SimpleNamespace(id=rid, title=title, category="Soups", meta="", base_spirits=[],
                           ingredients=["1 onion"], instructions=["Cook."])


@pytest.fixture()
def embedder(tmp_path, monkeypatch):
    """A stub embedder whose model and width the test can change, as a repoint would."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(semantic, "_INDEX", None)
    monkeypatch.setattr(semantic, "_STALE", "")
    state = {"model": "bge-m3:latest", "width": 4, "calls": 0}

    def fake(texts, **_kw):
        texts = [texts] if isinstance(texts, str) else list(texts)
        state["calls"] += 1
        return [[1.0] + [0.5] * (state["width"] - 1) for _ in texts], state["model"]

    monkeypatch.setattr(broker, "embed_resolved", fake)
    return state


def test_the_default_is_the_platform_embed_role(monkeypatch):
    import importlib

    monkeypatch.delenv("RECIPE_BOOK_EMBED_MODEL", raising=False)
    try:
        assert importlib.reload(broker).EMBED_MODEL == "@embed"
    finally:
        monkeypatch.undo()
        importlib.reload(broker)


def test_the_manifest_declares_the_embed_slot_the_code_reads():
    slots = {s["slot"]: s for s in json.loads((RAIL / "rail.json").read_text("utf-8"))["model_slots"]}
    assert slots["embed"]["env"] == "RECIPE_BOOK_EMBED_MODEL"
    assert slots["embed"]["role"] == "embed" and slots["embed"]["kind"] == "embed"
    assert "@" + slots["embed"]["role"] == "@embed"


def test_the_compose_service_passes_it():
    compose = (RAIL.parents[1] / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("\n  recipe-book:", 1)[1].split("\n  bouquet:", 1)[0]
    assert "RECIPE_BOOK_EMBED_MODEL: ${RECIPE_BOOK_EMBED_MODEL:-@embed}" in block


def test_the_header_draws_a_chip_for_the_embedder(tmp_path, monkeypatch):
    """A pruned or repointed embedder must be visible where the other two models are."""
    from fastapi.testclient import TestClient

    from recipe_book import ingest, seed, state
    from recipe_book.api import create_api

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(seed, "hydrate_if_empty", lambda: None)
    monkeypatch.setattr(seed, "hydrate_icons", lambda: False)
    monkeypatch.setattr(ingest, "ingest", lambda con: None)
    monkeypatch.setattr(semantic, "load", lambda: False)
    monkeypatch.setattr(state, "_catalog", None)
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "embed", "pattern": "bge-m3*",
                                                   "resolved": "bge-m3:latest"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "bge-m3:latest"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": []})
    monkeypatch.setattr(broker, "EMBED_MODEL", "@embed")
    c = TestClient(create_api(), raise_server_exceptions=False)
    r = c.get("/api/capabilities", headers={"X-Platform-User": "bob"})
    assert r.status_code == 200, r.text
    chips = {m["slot"]: m for m in r.json()["models"]}
    assert chips["embed"]["role"] == "@embed"
    assert chips["embed"]["model"] == "bge-m3:latest" and chips["embed"]["state"] == "cold"


def test_the_index_records_the_concrete_model_it_was_built_with(embedder):
    out = semantic.build(SimpleNamespace(recipes=[_recipe("a"), _recipe("b")]))
    assert out["model"] == "bge-m3:latest"
    assert json.loads((config.DATA_DIR / "semantic_index.json").read_text("utf-8"))["model"] \
        == "bge-m3:latest"


def test_a_repointed_embedder_of_the_same_width_falls_back_to_lexical(embedder):
    semantic.build(SimpleNamespace(recipes=[_recipe("a"), _recipe("b")]))
    assert semantic.query("onion")                     # same model: semantic answers
    embedder["model"] = "nomic-embed-text:latest"      # @embed repointed, same width
    assert semantic.query("onion") == []
    assert "nomic-embed-text" in semantic.status()["stale"]


def test_a_different_width_falls_back_instead_of_truncating(embedder):
    semantic.build(SimpleNamespace(recipes=[_recipe("a")]))
    embedder["width"] = 8
    assert semantic.query("onion") == []
    assert "8-d" in semantic.status()["stale"]


def test_add_refuses_a_vector_from_another_model(embedder):
    semantic.build(SimpleNamespace(recipes=[_recipe("a")]))
    embedder["model"] = "other-embed:latest"
    assert semantic.add(_recipe("b")) is False
    assert semantic.status()["count"] == 1, "a foreign vector was folded into the index"


def test_a_legacy_index_named_by_its_bare_tag_still_answers(embedder):
    """The deployed index recorded the literal "bge-m3"; @embed resolves to bge-m3:latest.
    The same model, so the switch to @embed must not turn semantic search off."""
    semantic.build(SimpleNamespace(recipes=[_recipe("a")]))
    semantic._INDEX["model"] = "bge-m3"
    assert semantic.query("onion")
    assert "stale" not in semantic.status()


def test_a_rebuild_clears_the_stale_mark(embedder):
    semantic.build(SimpleNamespace(recipes=[_recipe("a")]))
    embedder["model"] = "nomic-embed-text:latest"
    assert semantic.query("onion") == []
    semantic.build(SimpleNamespace(recipes=[_recipe("a")]))
    assert "stale" not in semantic.status()
    assert semantic.query("onion")
