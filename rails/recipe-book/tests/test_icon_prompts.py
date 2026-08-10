"""Distinctive icon subjects — LLM batch parse/cache + heuristic fallback. Offline."""
from __future__ import annotations

import pytest

from recipe_book import broker, config, db, icon_prompts, icons


class R:
    """Minimal recipe stub with just the fields subject()/subject_for()/build() read."""
    def __init__(self, id, title, category="Entrees", ingredients=None, kind="meal", glass=""):
        self.id = id
        self.title = title
        self.category = category
        self.ingredients = ingredients or []
        self.kind = kind
        self.glass = glass


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    c = db.connect()
    db.init_db(c)
    yield c
    c.close()


def test_build_parses_indexed_json_and_caches(con, monkeypatch):
    recs = [R("aaa", "Grilled Lemon Chicken"), R("bbb", "Chicken Tikka Masala")]

    def fake_json(model, messages, **kw):
        # keyed by the batch index (1-based), with a stray leading article to clean
        return {"subjects": {"1": "lemon-thyme roast chicken", "2": "a chicken tikka masala in a bowl"}}

    monkeypatch.setattr(broker, "chat_json", fake_json)
    out = icon_prompts.build(con, recipes=recs, force=True)
    assert out == {"made": 2, "failed": 0, "targets": 2}
    subs = db.get_icon_subjects(con)
    assert subs["aaa"] == "lemon-thyme roast chicken"
    assert subs["bbb"] == "chicken tikka masala in a bowl"      # leading "a " stripped


def test_build_skips_already_cached_unless_forced(con, monkeypatch):
    db.set_icon_subject(con, "aaa", "existing subject")
    con.commit()
    recs = [R("aaa", "X"), R("bbb", "Y")]
    monkeypatch.setattr(broker, "chat_json", lambda *a, **k: {"subjects": {"1": "new one"}})
    out = icon_prompts.build(con, recipes=recs, force=False)   # only bbb is a target
    assert out["targets"] == 1
    assert db.get_icon_subjects(con)["aaa"] == "existing subject"   # untouched


def test_omitted_recipe_falls_back_to_heuristic(con, monkeypatch):
    recs = [R("aaa", "Mystery Bowl", category="Blue Apron")]
    monkeypatch.setattr(broker, "chat_json", lambda *a, **k: {"subjects": {}})   # model omits it
    icon_prompts.build(con, recipes=recs, force=True)
    subs = db.get_icon_subjects(con)
    # no cached subject -> subject_for uses the heuristic (Blue Apron -> "plated dinner")
    assert "aaa" not in subs
    assert icons.subject_for(recs[0], subs) == icons.subject(recs[0]) == "plated dinner"


def test_subject_for_prefers_cached():
    r = R("zzz", "Roast Chicken")
    assert icons.subject_for(r, {"zzz": "chicken katsu cutlet"}) == "chicken katsu cutlet"
    assert icons.subject_for(r, {}) == "roast chicken"          # heuristic keyword hit


def test_generate_renders_using_cached_subjects(con, tmp_path, monkeypatch):
    """Exercises icons.generate end-to-end with a fake broker — catches wiring bugs
    (e.g. a missing db import) that a subject-only test would miss."""
    import base64
    r = R("aaa", "Grilled Lemon Chicken")
    db.set_icon_subject(con, "aaa", "lemon-thyme roast chicken")
    con.execute("INSERT INTO recipes (id, title, category, kind) VALUES (?,?,?,?)",
                ("aaa", r.title, "Entrees", "meal"))
    con.commit()
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path)
    monkeypatch.setattr(icons.state, "catalog", lambda: type("C", (), {"recipes": [r]})())
    seen = {}

    def fake_imgs(prompts, **kw):
        seen["prompts"] = prompts
        return [base64.b64encode(b"png").decode()]

    monkeypatch.setattr(icons.broker, "generate_images", fake_imgs)
    out = icons.generate(con, force=True)
    assert out["made"] == 1 and out["failed"] == 0
    assert "lemon-thyme roast chicken" in seen["prompts"][0]     # cached subject used
    assert (tmp_path / "aaa.png").exists()
