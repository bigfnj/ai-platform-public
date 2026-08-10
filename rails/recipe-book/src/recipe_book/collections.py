"""Split "collection" cards into individual recipes.

Some source cards bundle several distinct recipes into one card, with each recipe
under its own ``## Heading`` (so the standard Ingredients/Instructions sections are
empty and the card shows 0/0). Those are detected here and exploded into one card
per sub-recipe, so each is independently searchable / plannable / iconable.

Guardrail: only cards with **no standard ingredients** AND **>=2 sub-recipe-looking
sections** are split, so normal recipes (which have real ingredients) are never touched.
"""
from __future__ import annotations

import re

from recipe_book.catalog import Recipe, recipe_id

# a line that opens like an ingredient: a quantity/measure or "a/one/two…"
_QTY = re.compile(r"^\s*(?:[\d¼½¾⅓⅔⅛⅜⅝⅞]|a |an |one |two |three |four |pinch|dash|handful|splash|part\b)", re.I)
# a line that reads like a step/method, not an ingredient
_METHOD = re.compile(r"^\*?\s*method\b[:*]?|^\s*(?:blend|combine|shake|stir|strain|mix|muddle|pour|add|whisk|steep|serve|garnish|shake|top)\b", re.I)
# section labels / markdown-heading artifacts that leak into item lists (incl. the
# **bold** sub-header markers the parser now emits for ### component headings)
_ARTIFACT = re.compile(r"^#+\s|^\*\*.+\*\*$|^\s*(?:ingredients|instructions|method|directions|shopping list)\s*$", re.I)
# generic sub-headings that don't stand alone as a title -> prefix the parent
_GENERIC = {"classic", "iced", "hot", "cold", "original", "basic", "virgin", "regular",
            "alcoholic", "non-alcoholic", "variation", "option 1", "option 2"}

# parent categories whose split-out recipes should be retagged (green smoothies)
_RETAG = {"21 Day Cleanse": "Smoothies"}


def _looks_ingredient(line: str) -> bool:
    return bool(_QTY.match(line)) and not _METHOD.match(line)


def _is_subrecipe(section) -> bool:
    items = [i for i in section.items if i.strip() and not _ARTIFACT.match(i.strip())]
    if len(items) < 2:
        return False
    ing = sum(1 for i in items if _looks_ingredient(i))
    return ing >= 2 and ing >= len(items) * 0.4


def is_collection(recipe: Recipe) -> bool:
    if recipe.ingredients:            # has real standard ingredients -> a normal recipe
        return False
    return sum(1 for s in recipe.extra_sections if _is_subrecipe(s)) >= 2


def _title_for(parent_title: str, heading: str) -> str:
    h = heading.strip()
    if h.lower() in _GENERIC or len(h) <= 3:
        base = re.split(r"[–—-]", parent_title)[0].strip()
        return f"{base} — {h}"
    return h


def split(recipe: Recipe) -> list[Recipe]:
    """Explode a collection card into its sub-recipes."""
    category = _RETAG.get(recipe.category, recipe.category)
    kind = "beverage" if category.lower() == "beverages" else "meal"
    shared_method = [s for s in recipe.instructions if s.strip()]  # e.g. a card-level METHOD

    out: list[Recipe] = []
    for sec in recipe.extra_sections:
        if not _is_subrecipe(sec):
            continue
        ingredients: list[str] = []
        method = list(shared_method)
        for raw in sec.items:
            s = raw.strip()
            if not s or _ARTIFACT.match(s):
                continue
            if _METHOD.match(s):
                method.append(re.sub(r"^\*?\s*method:?\*?\s*", "", s, flags=re.I).strip())
                continue
            ingredients.append(s)
        if not ingredients:
            continue
        # Content-based id so the SAME sub-recipe repeated across files (e.g. a smoothie
        # that recurs across challenge weeks) collapses to one card on upsert.
        sub = Recipe(
            id=recipe_id(f"{category}|{sec.heading.strip().lower()}|"
                         + "|".join(x.strip().lower() for x in ingredients)),
            title=_title_for(recipe.title, sec.heading),
            category=category, kind=kind, rel_path=recipe.rel_path,
            meta=f"from {recipe.title}", source=recipe.source,
            ingredients=ingredients, instructions=method, shopping_list=[],
            extra_sections=[], is_collection=False,
        )
        out.append(sub)
    return out
