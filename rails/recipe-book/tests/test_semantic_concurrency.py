"""Index writes are serialised; scoring and incremental ingest stay correct after the speedups.

Three recorded items share this file:
* the .json.tmp race (audit 2026-10-08, "unconfirmed", confirmed by reading): add() runs as a
  BackgroundTask per edit, so two edits were two threads appending to the same lists and
  writing the one fixed temp name. The race window is microseconds, so the invariant is
  asserted directly: every write to disk happens under semantic._LOCK.
* the pure-Python cosine (2026-09-10, 97 ms a query): now sum(map(mul)); the ranking must be
  exactly the cosine ranking.
* "incremental" ingest re-parsed every card (2026-09-10): unchanged cards now skip before the
  parse, and an edited card must still be picked up.
"""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from recipe_book import broker, catalog, config, db, ingest, semantic


def _recipe(rid: str, title: str):
    return SimpleNamespace(id=rid, title=title, category="Soups", meta="", base_spirits=[],
                           ingredients=[title.lower()], instructions=["Cook."])


@pytest.fixture()
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(semantic, "_INDEX", None)
    monkeypatch.setattr(semantic, "_STALE", "")
    vec = {"Onion soup": [1.0, 0.0, 0.0], "Leek soup": [0.8, 0.6, 0.0],
           "Fish stew": [0.0, 0.2, 0.98], "onion": [1.0, 0.1, 0.0]}

    def fake(texts, **_kw):
        texts = [texts] if isinstance(texts, str) else list(texts)
        return [next((v for k, v in vec.items() if t.startswith(k)), [0.1, 0.1, 0.1])
                for t in texts], "bge-m3:latest"

    monkeypatch.setattr(broker, "embed_resolved", fake)
    return vec


def test_every_write_to_disk_holds_the_lock(index, monkeypatch):
    unlocked: list[str] = []
    real = semantic._persist

    def checked(ids, vectors, model):
        if not semantic._LOCK.locked():
            unlocked.append(f"{len(ids)} ids")
        return real(ids, vectors, model)

    monkeypatch.setattr(semantic, "_persist", checked)
    semantic.build(SimpleNamespace(recipes=[_recipe("a", "Onion soup")]))
    assert semantic.add(_recipe("b", "Leek soup")) is True
    assert unlocked == [], f"written without the lock: {unlocked}"


def test_the_ranking_is_exactly_cosine(index):
    semantic.build(SimpleNamespace(recipes=[_recipe("a", "Onion soup"), _recipe("b", "Leek soup"),
                                            _recipe("c", "Fish stew")]))
    got = semantic.query("onion")
    q = index["onion"]

    def cos(v):
        return sum(x * y for x, y in zip(q, v)) / (math.hypot(*q) * math.hypot(*v))

    want = sorted([("a", cos(index["Onion soup"])), ("b", cos(index["Leek soup"])),
                   ("c", cos(index["Fish stew"]))], key=lambda x: x[1], reverse=True)
    assert [r for r, _ in got] == [r for r, _ in want]
    for (_, g), (_, w) in zip(got, want):
        assert g == pytest.approx(w)


def test_incremental_ingest_skips_unchanged_cards_and_takes_an_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    root = tmp_path / "recipes"
    a = root / "Soups" / "Onion Soup.md"
    b = root / "Soups" / "Leek Soup.md"
    a.parent.mkdir(parents=True)
    a.write_text("# Onion Soup\n\n- 2 onions\n- *Method:* simmer\n", encoding="utf-8")
    b.write_text("# Leek Soup\n\n- 2 leeks\n- *Method:* simmer\n", encoding="utf-8")
    con = db.connect()
    try:
        db.init_db(con)
        ingest.ingest(con, recipes_dir=root)
        parsed: list[str] = []
        real = ingest.parse_recipe_markdown

        def counting(text, rel):
            parsed.append(rel)
            return real(text, rel)

        monkeypatch.setattr(ingest, "parse_recipe_markdown", counting)
        out = ingest.ingest(con, recipes_dir=root)
        assert parsed == [] and out["unchanged"] == 2, (parsed, out)

        b.write_text("# Leek and Potato Soup\n\n- 2 leeks\n- *Method:* simmer\n",
                     encoding="utf-8")
        out = ingest.ingest(con, recipes_dir=root)
        assert parsed == ["Soups/Leek Soup.md"] and out["updated"] == 1, (parsed, out)
        rid = catalog.recipe_id("Soups/Leek Soup.md")
        row = con.execute("SELECT title FROM recipes WHERE id=?", (rid,)).fetchone()
        assert row["title"] == "Leek and Potato Soup"
    finally:
        con.close()
