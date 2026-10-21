"""A line with no significant words is never "unavailable".

Recorded 2026-09-11: ingredient_is_covered answers True for a line that yields no significant
words ("4 cloves"), right for coverage and inverted when the same call asked "is this on the
can't-get list?". With nothing marked unavailable, such a line read as blocked, so it was never
covered: its recipe sat "one ingredient away" (need: Cloves) for good, and the line was put on
the shopping list.
"""
from __future__ import annotations

from recipe_book.catalog import Catalog, Recipe, ingredient_is_blocked


def _cat() -> Catalog:
    return Catalog([Recipe(id="g", title="Garlic Toast", category="Breakfast", kind="meal",
                           rel_path="b/g.md", ingredients=["2 slices bread", "4 cloves"])])


def test_a_wordless_line_does_not_hold_a_recipe_one_away():
    res = _cat().match_pantry(["bread"], [], [])
    assert [(r["title"], r["makeable"]) for r in res] == [("Garlic Toast", True)]


def test_a_wordless_line_is_not_put_on_the_shopping_list():
    assert _cat().shopping_list(["g"], on_hand=["bread"], staples=[], unavailable=[]) == []


def test_a_real_unavailable_ingredient_still_blocks():
    assert ingredient_is_blocked("2 slices bread", ["bread"]) is True
    res = _cat().match_pantry(["bread"], [], ["bread"])
    assert not any(r["makeable"] for r in res)


def test_nothing_unavailable_blocks_nothing():
    assert ingredient_is_blocked("2 slices bread", []) is False
    assert ingredient_is_blocked("4 cloves", ["garlic"]) is False
