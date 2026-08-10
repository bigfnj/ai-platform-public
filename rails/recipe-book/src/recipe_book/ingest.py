"""Ingest the markdown corpus into the ``recipes`` table.

The corpus is a tree of ``<Category>/<Title>.md`` cards (dirs starting with ``_``
are ignored, matching the source pipeline). Incremental by content hash: only
new/changed cards are re-parsed. ``rebuild`` wipes and re-parses everything.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from recipe_book import bar, collections, config, db, reclassify
from recipe_book.catalog import parse_recipe_markdown


def _iter_md(root: Path):
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if any(part.startswith("_") for part in rel.parts):
            continue
        yield path, rel.as_posix()


def _apply_content(recipe, content_ov: dict) -> None:
    """Overlay a manual content edit (ingredients / method / shopping) onto a parsed
    recipe. A NULL column means that part was left unchanged."""
    ov = content_ov.get(recipe.id)
    if not ov:
        return
    if ov["ingredients"] is not None:
        recipe.ingredients = ov["ingredients"]
    if ov["instructions"] is not None:
        recipe.instructions = ov["instructions"]
    if ov["shopping_list"] is not None:
        recipe.shopping_list = ov["shopping_list"]


def ingest(con, recipes_dir: Path | str | None = None, full: bool = False) -> dict:
    root = Path(recipes_dir or config.RECIPES_DIR)
    existing = {} if full else db.content_hashes(con)
    overrides = db.category_overrides(con)   # manual "Change category" moves win over auto-classify
    content_ov = db.content_overrides(con)   # manual ingredient / method / shopping edits
    title_ov = db.title_overrides(con)       # manual renames (display title only; id/path unchanged)
    added = updated = unchanged = 0
    seen: set[str] = set()

    for path, rel in _iter_md(root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        h = hashlib.sha256(raw).hexdigest()
        recipe = parse_recipe_markdown(raw.decode("utf-8", "replace"), rel)

        # Collection card (several recipes bundled under sub-headings) -> explode into
        # individual cards; the parent card is not stored. Content-based sub-ids dedupe
        # the same recipe recurring across files.
        if collections.is_collection(recipe):
            for sub in collections.split(recipe):
                if sub.id in overrides:
                    sub.category = overrides[sub.id]
                    sub.kind = "beverage" if sub.category.lower() == "beverages" else "meal"
                _apply_content(sub, content_ov)
                if sub.id in title_ov:
                    sub.title = title_ov[sub.id]
                bar.enrich(sub)
                added += 0 if sub.id in seen else 1
                seen.add(sub.id)
                db.upsert_recipe(con, sub, h)
            continue

        seen.add(recipe.id)
        if not full and existing.get(recipe.id) == h:
            unchanged += 1
            continue
        if recipe.category in reclassify.RECLASSIFY_CATEGORIES:
            recipe.category = reclassify.category_for(recipe.title)
        # Thai pass: pull Thai dishes into "Thai" from any category EXCEPT the To-Try bucket
        if recipe.category != "To Try" and reclassify.is_thai(recipe):
            recipe.category = "Thai"
        # a manual "Change category" move wins over all auto-classification
        if recipe.id in overrides:
            recipe.category = overrides[recipe.id]
        _apply_content(recipe, content_ov)
        # Rename last (display only): the original title already drove classification above.
        if recipe.id in title_ov:
            recipe.title = title_ov[recipe.id]
        recipe.kind = "beverage" if recipe.category.lower() == "beverages" else "meal"
        bar.enrich(recipe)
        db.upsert_recipe(con, recipe, h)
        if recipe.id in existing:
            updated += 1
        else:
            added += 1

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    bev = con.execute("SELECT COUNT(*) FROM recipes WHERE kind='beverage'").fetchone()[0]
    return {"added": added, "updated": updated, "unchanged": unchanged,
            "seen": len(seen), "total": total, "meals": total - bev, "beverages": bev,
            "recipes_dir": str(root)}


def rebuild(con, recipes_dir: Path | str | None = None) -> dict:
    """Full re-parse of the whole corpus (disaster recovery / big source refresh)."""
    con.execute("DELETE FROM recipes")
    con.commit()
    return ingest(con, recipes_dir, full=True)
