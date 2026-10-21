"""The "Deserts" category reads "Desserts" on a fresh install and on the live one.

Two corpora reach this code. The committed seed now files its desserts under Desserts/. A live
volume keeps the Deserts/ folder its cards were filed in, because a card's id is a hash of its
path: moving the files would mint new ids and orphan every favourite, rating, plan entry,
override and icon keyed by the old ones. So both spellings must show "Desserts" and both must
keep the id the card was minted under, and the live database's stored rows must be rewritten
on the next start with nothing else touched. Real parser, real ingest, real SQLite.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import authoring, catalog, config, db, reclassify, state
from recipe_book import ingest as ingestmod
from recipe_book.api.routers import recipes as recipesrouter

ADMIN = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}
CARD = "# {title}\n\n## Ingredients\n- 1 cup sugar\n\n## Instructions\n1. Bake it.\n"
# The committed seed and its icon archive, read where the code reads them by default.
SEED_DIR = Path(config.SEED_RECIPES_DIR)
SEED_ICONS = Path(config.SEED_ICONS_ARCHIVE)


def sha_id(rel: str) -> str:
    """The id a card at `rel` was given before the rename: the sha1 of its path, as written."""
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]


def write(root: Path, rel: str, title: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CARD.format(title=title), encoding="utf-8")


def snapshot(con: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Every row of every table, so a test can say exactly which cells changed."""
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in tables}


def test_either_folder_spelling_shows_desserts_under_the_minted_id():
    for folder in ("Deserts", "Desserts"):
        r = catalog.parse_recipe_markdown(CARD.format(title="Lemon Bars"), f"{folder}/Lemon Bars.md")
        assert r.category == "Desserts", folder
        assert r.id == sha_id("Deserts/Lemon Bars.md"), folder
        assert r.kind == "meal"
    # Every other path still hashes to itself, including one that only starts with the name.
    for rel in ("Baking/Lemon Bars.md", "Dessertsx/Lemon Bars.md", "Desserts.md"):
        assert catalog.recipe_id(rel) == sha_id(rel), rel


def test_title_keywords_file_a_cake_under_desserts():
    assert reclassify.category_for("Chocolate Fudge Cake") == "Desserts"


def test_the_seed_folder_is_renamed_and_its_cards_keep_their_seed_icons():
    """The seed's icon archive names each PNG by recipe id, and it was built while the folder was
    still Deserts/. A fresh install unpacks it by name (seed.hydrate_icons), so a dessert whose
    id moved with the folder would ship without its icon.

    Asked of the cards that HAD an icon under their Deserts/ id."""
    assert (SEED_DIR / "Desserts").is_dir()
    assert not (SEED_DIR / "Deserts").exists()
    cards = sorted(p.relative_to(SEED_DIR).as_posix() for p in (SEED_DIR / "Desserts").rglob("*.md"))
    with tarfile.open(SEED_ICONS, "r:gz") as tar:
        icons = {Path(m.name).name for m in tar.getmembers() if m.isfile()}
    had = [rel for rel in cards if f"{sha_id('Deserts/' + rel.split('/', 1)[1])}.png" in icons]
    assert len(had) >= 40, f"only {len(had)} of {len(cards)} seed desserts had a seed icon at all"
    missing = [rel for rel in had if f"{catalog.recipe_id(rel)}.png" not in icons]
    assert missing == [], f"{len(missing)} of {len(had)} seed desserts lost their seed icon: {missing[:3]}"


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """A live install as it stands before this change: the corpus files desserts under
    Deserts/, and the database holds 'Deserts' on their rows and on one manual move into it."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    corpus = tmp_path / "recipes"
    write(corpus, "Deserts/Lemon Bars.md", "Lemon Bars")
    write(corpus, "Baking/Scones.md", "Scones")
    write(corpus, "Entrees/Fish Pie.md", "Fish Pie")
    con = db.connect()
    db.init_db(con)
    ingestmod.ingest(con, recipes_dir=corpus)
    bars, scones, pie = (sha_id(r) for r in ("Deserts/Lemon Bars.md", "Baking/Scones.md",
                                              "Entrees/Fish Pie.md"))
    # What the old parser stored, and what an admin's "Change category" into it wrote.
    con.execute("UPDATE recipes SET category='Deserts' WHERE id=?", (bars,))
    con.execute("INSERT INTO category_overrides VALUES (?, 'Deserts', '2026-01-02T03:04:05+00:00')",
                (scones,))
    con.execute("INSERT INTO category_overrides VALUES (?, 'Entrees', '2026-01-02T03:04:05+00:00')",
                (pie,))
    con.execute("UPDATE recipes SET category='Deserts' WHERE id=?", (scones,))
    # Per-owner state keyed by the dessert's id, which must still point at it afterwards.
    con.execute("INSERT INTO favorites (owner_id, recipe_id, created_at) VALUES (1, ?, 't')", (bars,))
    con.execute("INSERT INTO ratings (owner_id, recipe_id, stars, updated_at) VALUES (1, ?, 5, 't')", (bars,))
    con.execute("INSERT INTO meal_plan_entries (owner_id, date, slot, recipe_id, title, created_at) "
                "VALUES (1, '2026-10-10', 'snack', ?, 'Lemon Bars', 't')", (bars,))
    con.commit()
    yield {"con": con, "corpus": corpus, "bars": bars, "scones": scones, "pie": pie}
    con.close()


def test_the_next_start_rewrites_the_category_and_nothing_else(live):
    con = live["con"]
    before = snapshot(con)
    db.init_db(con)                  # what create_api runs on the next start
    after = snapshot(con)

    expected = {t: list(rows) for t, rows in before.items()}
    cols = [r[1] for r in con.execute("PRAGMA table_info(recipes)")]
    ci = cols.index("category")
    expected["recipes"] = [r[:ci] + ("Desserts",) + r[ci + 1:] if r[ci] == "Deserts" else r
                           for r in before["recipes"]]
    expected["category_overrides"] = [(rid, "Desserts" if cat == "Deserts" else cat, at)
                                      for rid, cat, at in before["category_overrides"]]
    assert after == expected
    assert expected != before        # the fixture really held the old spelling

    # Idempotent: a second start finds nothing to rewrite and changes nothing.
    assert db.rename_categories(con) == 0
    db.init_db(con)
    assert snapshot(con) == after


def test_ingest_after_the_rename_cannot_bring_the_old_name_back(live):
    con, corpus = live["con"], live["corpus"]
    db.init_db(con)
    stats = ingestmod.ingest(con, recipes_dir=corpus)
    assert stats["unchanged"] == 3   # no card re-parsed: the content hashes were left alone
    # A changed card IS re-parsed, from the Deserts/ folder it still lives in.
    (corpus / "Deserts" / "Lemon Bars.md").write_text(
        CARD.format(title="Lemon Bars") + "2. Cool it.\n", encoding="utf-8")
    ingestmod.ingest(con, recipes_dir=corpus)
    ingestmod.rebuild(con, recipes_dir=corpus)
    rows = dict(con.execute("SELECT id, category FROM recipes").fetchall())
    assert rows == {live["bars"]: "Desserts", live["scones"]: "Desserts", live["pie"]: "Entrees"}
    assert {r["recipe_id"] for r in con.execute("SELECT recipe_id FROM favorites")} == {live["bars"]}


def test_a_new_card_cannot_take_an_id_its_old_spelling_already_holds(tmp_path, monkeypatch):
    """Desserts/Lemon Bars.md hashes to the id of Deserts/Lemon Bars.md, so adding a same-named
    recipe to a live corpus must not write it there."""
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path)
    write(tmp_path, "Deserts/Lemon Bars.md", "Lemon Bars")
    rel = authoring.write_card(category="Desserts", title="Lemon Bars", markdown=CARD.format(title="x"))
    assert rel == "Desserts/Lemon Bars (2).md"
    assert catalog.recipe_id(rel) != catalog.recipe_id("Deserts/Lemon Bars.md")


def test_a_move_typed_with_the_old_spelling_lands_in_desserts(tmp_path, monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(state, "_catalog", None)
    write(tmp_path / "recipes", "Baking/Scones.md", "Scones")
    con = db.connect()
    db.init_db(con)
    ingestmod.ingest(con, recipes_dir=tmp_path / "recipes")
    con.commit()
    con.close()
    state.reload()
    app = FastAPI()
    app.include_router(recipesrouter.router)
    rid = catalog.recipe_id("Baking/Scones.md")
    r = TestClient(app).put(f"/api/recipes/{rid}/category", json={"category": "Deserts"},
                            headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "Desserts"
    monkeypatch.setattr(state, "_catalog", None)
