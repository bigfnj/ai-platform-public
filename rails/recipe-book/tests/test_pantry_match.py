"""Pantry/Bar coverage matching + shopping-list staple handling (Catalog, offline).

Two behaviours the user relies on:
  * nothing on hand -> no "what you can make" suggestions (the UI shows a reminder), and
  * a Staple (butter, oil, …) is treated as already-owned, so it never lands on the
    shopping list even when a chosen recipe calls for it.
"""
from __future__ import annotations

from recipe_book.catalog import Catalog, Recipe


def _cat() -> Catalog:
    return Catalog([
        Recipe(id="toast", title="Buttered Toast", category="Breakfast", kind="meal",
               rel_path="b/toast.md", ingredients=["2 slices bread", "1 tbsp butter"]),
        Recipe(id="omelet", title="Cheese Omelet", category="Breakfast", kind="meal",
               rel_path="b/omelet.md", ingredients=["3 eggs", "cheddar cheese", "1 tbsp butter"]),
    ])


def test_empty_on_hand_yields_no_matches():
    cat = _cat()
    # even with staples present, nothing ON HAND -> no suggestions
    assert cat.match_pantry([], ["butter"], []) == []
    assert cat.match_pantry([], [], []) == []


def test_on_hand_returns_only_covered_recipes():
    cat = _cat()
    res = cat.match_pantry(["bread"], [], [])
    titles = {r["title"] for r in res}
    assert "Buttered Toast" in titles        # bread matches
    assert "Cheese Omelet" not in titles     # no shared ingredient -> coverage 0, excluded


def test_staple_is_kept_off_the_shopping_list():
    cat = _cat()
    # buy for the omelet with butter as a staple: butter must NOT appear
    items = cat.shopping_list(["omelet"], on_hand=[], staples=["butter"], unavailable=[])
    labels = " ".join(i["label"].lower() for i in items)
    assert "butter" not in labels
    assert "egg" in labels                   # non-staple ingredients still listed


def test_on_hand_also_kept_off_the_shopping_list():
    cat = _cat()
    items = cat.shopping_list(["omelet"], on_hand=["eggs"], staples=[], unavailable=[])
    labels = " ".join(i["label"].lower() for i in items)
    assert "egg" not in labels               # owned -> not purchased


# --- beverages: a generic spirit must not hide a branded call-out (strict matching) ---

def _bar() -> Catalog:
    return Catalog([
        Recipe(id="branded", title="Top-Shelf Martini", category="Cocktails", kind="beverage",
               rel_path="c/branded.md", ingredients=["2 oz Grey Goose vodka", "0.5 oz dry vermouth"]),
        Recipe(id="plain", title="House Martini", category="Cocktails", kind="beverage",
               rel_path="c/plain.md", ingredients=["2 oz vodka", "0.5 oz dry vermouth"]),
    ])


def test_generic_spirit_keeps_branded_call_out_on_the_list():
    cat = _bar()
    items = cat.shopping_list(["branded"], on_hand=[], staples=[], unavailable=[],
                              bar_on_hand=["vodka"], bar_staples=["dry vermouth"])
    labels = " ".join(i["label"].lower() for i in items)
    assert "goose" in labels                 # branded vodka still listed — user decides
    assert "vermouth" not in labels          # generic vermouth on hand -> covered


def test_plain_spirit_is_covered_by_generic_on_hand():
    cat = _bar()
    items = cat.shopping_list(["plain"], on_hand=[], staples=[], unavailable=[],
                              bar_on_hand=["vodka", "dry vermouth"])
    assert items == []                       # plain vodka + vermouth on hand -> nothing to buy


def test_owning_the_brand_covers_the_branded_call_out():
    cat = _bar()
    items = cat.shopping_list(["branded"], on_hand=[], staples=[], unavailable=[],
                              bar_on_hand=["grey goose vodka", "dry vermouth"])
    assert items == []                       # you have the exact brand -> nothing to buy
