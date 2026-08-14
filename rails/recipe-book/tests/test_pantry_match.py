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


def test_on_hand_returns_only_fully_covered_recipes():
    cat = _cat()
    # holding only bread is NOT enough for Buttered Toast (it still needs butter): it may appear
    # as a one-away suggestion, but never as makeable.
    res = cat.match_pantry(["bread"], [], [])
    makeable = {r["title"] for r in res if r["makeable"]}
    assert "Buttered Toast" not in makeable
    # bread + butter -> Buttered Toast is makeable; Cheese Omelet (eggs/cheese short) is not
    res2 = cat.match_pantry(["bread", "butter"], [], [])
    makeable2 = {r["title"] for r in res2 if r["makeable"]}
    assert "Buttered Toast" in makeable2
    assert "Cheese Omelet" not in makeable2


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


# --- "what you can pour": only fully-makeable drinks, brand-blind but flavor-aware ---

def _pour() -> Catalog:
    return Catalog([
        # brands (Malibu) + a flavor qualifier (Vanilla) — the user's real example
        Recipe(id="dole", title="Dole Whip", category="Beverages", kind="beverage",
               rel_path="Beverages/Dole Whip.md",
               ingredients=["1.5 oz Malibu Coconut Rum", "1.5 oz Vanilla Vodka", "1.5 oz Pineapple Juice"]),
        # a garnish line + a leaked numbered instruction step masquerading as ingredients
        Recipe(id="fizz", title="Vodka Fizz", category="Beverages", kind="beverage",
               rel_path="Beverages/Vodka Fizz.md",
               ingredients=["2 oz vodka", "Lime wedge garnish", "3. Garnish with a lime wedge."]),
    ])


def test_pour_needs_full_coverage_not_just_one_shared_ingredient():
    cat = _pour()
    # holding only "vodka": Dole Whip is short by >1 required ingredient -> not surfaced at all
    res = cat.match_pantry(["vodka"], [], [], kind="beverage")
    assert [r for r in res if r["id"] == "dole"] == []


def test_generic_covers_branded_call_out():
    cat = _pour()
    res = cat.match_pantry(["coconut rum", "vanilla vodka", "pineapple juice"], [], [], kind="beverage")
    dole = next(r for r in res if r["id"] == "dole")
    assert dole["makeable"] is True          # generic "coconut rum" covered branded "Malibu Coconut Rum"


def test_plain_vodka_does_not_cover_vanilla_vodka():
    cat = _pour()
    # plain vodka (not vanilla) leaves Vanilla Vodka uncovered -> one away, not makeable
    res = cat.match_pantry(["coconut rum", "vodka", "pineapple juice"], [], [], kind="beverage")
    dole = next(r for r in res if r["id"] == "dole")
    assert dole["makeable"] is False
    assert "vanilla vodka" in dole["need"].lower()


def test_one_ingredient_away_is_flagged_with_the_missing_bottle():
    cat = _pour()
    res = cat.match_pantry(["coconut rum", "vanilla vodka"], [], [], kind="beverage")
    dole = next(r for r in res if r["id"] == "dole")
    assert dole["makeable"] is False
    assert "pineapple" in dole["need"].lower()


def test_two_ingredients_away_is_dropped():
    cat = _pour()
    res = cat.match_pantry(["coconut rum"], [], [], kind="beverage")
    assert [r for r in res if r["id"] == "dole"] == []


def test_garnish_and_leaked_steps_do_not_block_makeability():
    cat = _pour()
    res = cat.match_pantry(["vodka"], [], [], kind="beverage")
    fizz = next(r for r in res if r["id"] == "fizz")
    assert fizz["makeable"] is True          # only "2 oz vodka" is a real (required) ingredient


def test_marking_vodka_unavailable_does_not_block_vanilla_vodka():
    cat = _pour()
    res = cat.match_pantry(["coconut rum", "vanilla vodka", "pineapple juice"], [], ["vodka"],
                           kind="beverage")
    dole = next(r for r in res if r["id"] == "dole")
    assert dole["makeable"] is True          # "vodka" avoid must not block the vanilla-vodka call-out
