"""Editable recipe title: a rename is stored as an override that re-applies on a full
rebuild, while the path-derived recipe id stays stable (so favorites / ratings / planner
links survive the rename)."""
from __future__ import annotations

from recipe_book import catalog, config, db
from recipe_book import ingest as ingestmod


def test_title_override_survives_rebuild_and_keeps_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    recipes_dir = tmp_path / "recipes"
    card = recipes_dir / "Entrees" / "My Dish.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# My Dish\n\n- 1 cup flour\n- *Method:* mix\n", encoding="utf-8")

    con = db.connect()
    try:
        db.init_db(con)
        ingestmod.ingest(con, recipes_dir=recipes_dir)
        con.commit()
        rid = catalog.recipe_id("Entrees/My Dish.md")
        assert con.execute("SELECT title FROM recipes WHERE id=?", (rid,)).fetchone()["title"] == "My Dish"

        db.set_title_override(con, rid, "Renamed Dish")
        con.commit()
        # A full rebuild re-parses every card from disk; the override must re-apply.
        ingestmod.ingest(con, recipes_dir=recipes_dir, full=True)
        con.commit()

        row = con.execute("SELECT id, title FROM recipes WHERE id=?", (rid,)).fetchone()
        assert row["title"] == "Renamed Dish"   # rename survived the rebuild
        assert row["id"] == rid                  # stable, path-derived id preserved
    finally:
        con.close()
