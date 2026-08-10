"""Recipe content model: parse the Markdown cards, normalize ingredients, and
run search / pantry-match / shopping aggregation over an in-memory catalog.

Adapted from the deleted recipe-book app's ``catalog.py`` (recovered from platform
git history), extended with Bar fields (base spirits / glass / technique) and an
icon status. The corpus is small (835 cards), so the catalog is loaded once into
memory from the SQLite ``recipes`` table (which ``ingest.py`` populates from the
markdown) and rebuilt on re-import.

Recipe ids are opaque (sha1 of the relative path) so titles/categories with
spaces and slashes never leak into URLs.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from recipe_book import attributes as _attr

# --- ingredient normalization ------------------------------------------------

_IGNORED_WORDS = {
    "a", "about", "all", "and", "as", "at", "can", "clove", "cup", "dash",
    "divided", "drained", "fresh", "finely", "for", "handful", "large",
    "medium", "minced", "of", "optional", "ounce", "oz", "package", "peeled",
    "pinch", "pound", "quartered", "rinsed", "roughly", "small", "sliced",
    "sprig", "stalk", "tablespoon", "tbsp", "teaspoon", "thinly", "to",
    "taste", "tsp", "with",
}

_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnonstick (?:cooking )?spray\b"), "cooking spray"),
    (re.compile(r"\bcanola oil\b"), "neutral cooking oil"),
    (re.compile(r"\bvegetable oil\b"), "neutral cooking oil"),
    (re.compile(r"\bkosher salt\b"), "salt"),
    (re.compile(r"\bsea salt\b"), "salt"),
    (re.compile(r"\bground black pepper\b"), "black pepper"),
    (re.compile(r"\bwater\b"), "tap water"),
]

_FRACTIONS = re.compile(r"[¼½¾⅓⅔⅛⅜⅝⅞]")


def _singularize(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _normalized_text(value: str) -> str:
    text = value.lower()
    text = _FRACTIONS.sub(" ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[-–—]", " ", text)
    for pattern, replacement in _ALIASES:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\d+(?:[./]\d+)?", " ", text)
    text = re.sub(r"[^a-z&+\s]", " ", text)
    text = re.sub(r"[&+]", " and ", text)
    return re.sub(r"\s+", " ", text).strip()


def ingredient_words(value: str) -> list[str]:
    return [
        w for w in (_singularize(x) for x in _normalized_text(value).split(" "))
        if w and w not in _IGNORED_WORDS
    ]


def ingredient_key(value: str) -> str:
    words = ingredient_words(value)
    return " ".join(words) or _normalized_text(value) or "other ingredient"


def ingredient_is_covered(ingredient: str, inventory: list[str], *, strict: bool = False) -> bool:
    """Does the inventory cover this ingredient?

    Lenient (default): a generic on-hand item covers a more specific call-out — having
    "vodka" covers "grey goose vodka" (head-noun shortcut), and "butter" covers "unsalted
    butter". Good for meals, where brand rarely matters.

    ``strict``: every distinguishing word of the ingredient must be on hand, so a generic
    "vodka" does NOT cover the branded "grey goose vodka" (used for the beverage shopping
    list, so a branded call-out still surfaces for the user to decide on)."""
    required = list(dict.fromkeys(ingredient_words(ingredient)))
    if not required:
        return True
    compound = bool(re.search(r"\b(?:and|or)\b|&|\+", ingredient.lower()))
    covered: set[str] = set()
    for item in inventory:
        available = list(dict.fromkeys(ingredient_words(item)))
        if not available:
            continue
        for word in required:
            if word in available:
                covered.add(word)
        if (
            not strict
            and not compound
            and all(w in required for w in available)
            and (required[-1] if required else "") in available
        ):
            return True
    return all(w in covered for w in required)


def _title_case(value: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), value)


# --- recipe model + Markdown parser -----------------------------------------


@dataclass
class Section:
    heading: str
    ordered: bool
    items: list[str]


@dataclass
class Recipe:
    id: str
    title: str
    category: str
    kind: str  # "meal" | "beverage"
    rel_path: str
    meta: str = ""
    source: str = ""
    ingredients: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    shopping_list: list[str] = field(default_factory=list)
    extra_sections: list[Section] = field(default_factory=list)
    is_collection: bool = False
    # Bar enrichment (beverages only; filled by bar.enrich)
    base_spirits: list[str] = field(default_factory=list)
    glass: str = ""
    technique: str = ""
    primary_spirit: str = ""  # single base spirit for the Bar facet; multi/unclear -> Liqueur
    # Icon (SDXL clipart): "pending" until generated, then "ready"
    icon_status: str = "pending"
    # Dietary/allergen attributes. `auto_attributes` is the classifier output (from
    # ingredients); `attributes` is the effective set after manual overrides.
    auto_attributes: set = field(default_factory=set)
    attributes: set = field(default_factory=set)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "kind": self.kind,
            "ingredient_count": len(self.ingredients),
            "step_count": len(self.instructions),
            "is_collection": self.is_collection,
            "base_spirits": self.base_spirits,
            "primary_spirit": self.primary_spirit,
            "glass": self.glass,
            "technique": self.technique,
            "icon_status": self.icon_status,
        }

    def detail(self) -> dict:
        return {
            **self.summary(),
            "meta": self.meta,
            "source": self.source,
            "ingredients": self.ingredients,
            "instructions": self.instructions,
            "shopping_list": self.shopping_list,
            "extra_sections": [
                {"heading": s.heading, "ordered": s.ordered, "items": s.items}
                for s in self.extra_sections
            ],
            "rel_path": self.rel_path,
            "attributes": sorted(self.attributes),
            "attributes_auto": sorted(self.auto_attributes),
        }


def recipe_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.replace("\\", "/").encode("utf-8")).hexdigest()[:12]


def parse_recipe_markdown(markdown: str, rel_path: str) -> Recipe:
    fallback = Path(rel_path).stem
    body, _, footer = markdown.partition("\n---\n*")
    source = ""
    if footer:
        source = re.sub(r"^Source:\s*", "", footer.strip().rstrip("*").strip("* "), flags=re.I)

    lines = body.splitlines()
    title, meta = fallback, ""
    i = 0
    while i < len(lines) and not lines[i].startswith("# "):
        i += 1
    if i < len(lines):
        title = lines[i][2:].strip() or fallback
        i += 1
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith("#"):
            break
        m = re.match(r"^[_*](.+)[_*]$", s)
        meta = (m.group(1).strip() if m else s)
        i += 1
        break

    sections: list[Section] = []
    cur: Section | None = None
    for line in lines[i:]:
        s = line.rstrip()
        if s.startswith("## "):
            cur = Section(heading=s[3:].strip(), ordered=False, items=[])
            sections.append(cur)
            continue
        if s.startswith("# "):
            continue
        st = s.strip()
        if cur is None or not st or st.startswith("---") or st.startswith("*Source"):
            continue
        # A markdown sub-heading INSIDE a section (### Dough, "### For the Sauce", …) is a
        # component/step group label, not an item. Keep it as a labelled sub-header
        # (**bold**), which the frontend renders as a group header instead of a checkbox.
        m_sub = re.match(r"^#{1,6}\s+(.+?):?\s*$", st)
        if m_sub:
            cur.items.append(f"**{m_sub.group(1).strip()}**")
            continue
        m_ol = re.match(r"^\d+\.\s+(.*)", st)
        m_ul = re.match(r"^[-*]\s+(.*)", st)
        if m_ol:
            cur.ordered = True
            cur.items.append(m_ol.group(1))
        elif m_ul:
            cur.items.append(m_ul.group(1))
        else:
            cur.items.append(st)

    ingredients: list[str] = []
    instructions: list[str] = []
    shopping: list[str] = []
    extras: list[Section] = []
    for sec in sections:
        h = sec.heading.lower()
        if "ingredient" in h:
            ingredients.extend(sec.items)
        elif "instruction" in h or "direction" in h or "method" in h:
            instructions.extend(sec.items)
        elif "shopping" in h:
            shopping.extend(sec.items)
        else:
            extras.append(sec)

    category = rel_path.replace("\\", "/").split("/")[0] or "Uncategorized"
    kind = "beverage" if category.lower() == "beverages" else "meal"
    is_collection = not ingredients and not instructions and bool(extras)

    return Recipe(
        id=recipe_id(rel_path),
        title=title,
        category=category,
        kind=kind,
        rel_path=rel_path.replace("\\", "/"),
        meta=meta,
        source=source,
        ingredients=ingredients,
        instructions=instructions,
        shopping_list=shopping,
        extra_sections=extras,
        is_collection=is_collection,
    )


# --- catalog (loaded into memory from the DB, rebuilt on re-import) ----------

_DOMINANT_SPIRITS = {"Gin", "Vodka", "Rum", "Whiskey", "Tequila", "Mezcal", "Brandy", "Wine & Sparkling"}


def primary_spirit(base_spirits: list[str]) -> str:
    """One base spirit for the Bar facet: the single dominant spirit if there is exactly
    one; multiple dominant spirits (or only liqueurs/modifiers) -> 'Liqueur'; no alcohol
    detected -> 'Non-Alcoholic' (its own facet for mocktails / zero-proof drinks)."""
    dominant = [s for s in base_spirits if s in _DOMINANT_SPIRITS]
    if len(dominant) == 1:
        return dominant[0]
    if base_spirits:
        return "Liqueur"
    return "Non-Alcoholic"


class Catalog:
    def __init__(self, recipes: list[Recipe]):
        self.recipes = sorted(recipes, key=lambda r: r.title.lower())
        self.by_id = {r.id: r for r in self.recipes}
        # Auto-derive dietary/allergen attributes from ingredients (manual overrides are
        # layered on top by db.load_catalog). Cheap; recomputed on every (re)load so a new
        # recipe added through the ingestor is classified with no extra step.
        for r in self.recipes:
            r.auto_attributes = _attr.classify(r.ingredients, r.title, r.category, r.kind)
            r.attributes = set(r.auto_attributes)
        cats: dict[str, dict] = {}
        for r in self.recipes:
            c = cats.setdefault(r.category, {"name": r.category, "count": 0, "kind": r.kind})
            c["count"] += 1
        self.categories = [cats[name] for name in sorted(cats, key=str.lower)]
        # single primary base spirit per drink (the Bar facet). One base spirit -> that
        # spirit; multiple, or only liqueurs/modifiers -> Liqueur; non-alcoholic -> none.
        spirits: dict[str, int] = {}
        for r in self.recipes:
            if r.kind == "beverage":
                r.primary_spirit = primary_spirit(r.base_spirits)
                if r.primary_spirit:
                    spirits[r.primary_spirit] = spirits.get(r.primary_spirit, 0) + 1
        self.spirits = [{"name": s, "count": spirits[s]} for s in sorted(spirits, key=str.lower)]

    def stats(self) -> dict:
        meals = sum(1 for r in self.recipes if r.kind == "meal")
        return {"total": len(self.recipes), "meals": meals,
                "beverages": len(self.recipes) - meals,
                "categories": len(self.categories), "spirits": len(self.spirits)}

    def get(self, recipe_id: str) -> Recipe | None:
        return self.by_id.get(recipe_id)

    def search(self, query: str = "", kind: str = "all", category: str | None = None,
               spirit: str | None = None, ids: set[str] | None = None,
               limit: int = 60, offset: int = 0) -> tuple[list[Recipe], int]:
        needle = query.strip().lower()
        hits: list[Recipe] = []
        for r in self.recipes:
            if kind != "all" and r.kind != kind:
                continue
            if category and r.category != category:
                continue
            if spirit and r.primary_spirit != spirit:
                continue
            if ids is not None and r.id not in ids:
                continue
            if needle and not (
                needle in r.title.lower()
                or needle in r.category.lower()
                or any(needle in ing.lower() for ing in r.ingredients)
            ):
                continue
            hits.append(r)
        total = len(hits)
        return hits[offset: offset + max(1, min(limit, 500))], total

    def match_pantry(self, on_hand: list[str], staples: list[str], unavailable: list[str],
                     kind: str = "all", limit: int = 40) -> list[dict]:
        """What can I make? Coverage ranking. Reused for the Bar ('what can I pour')
        by passing the bar inventory as ``on_hand`` and ``kind='beverage'``.

        With nothing on hand there's nothing to suggest, so return empty — the UI then
        prompts the user to add what they have rather than dumping the whole catalog."""
        if not on_hand:
            return []
        inventory = on_hand + staples
        results: list[dict] = []
        for r in self.recipes:
            if not r.ingredients:
                continue
            if kind != "all" and r.kind != kind:
                continue
            matched, missing = [], []
            for ing in r.ingredients:
                blocked = ingredient_is_covered(ing, unavailable)
                covered = not blocked and ingredient_is_covered(ing, inventory)
                (matched if covered else missing).append(ing)
            coverage = len(matched) / len(r.ingredients)
            if coverage > 0:
                results.append({**r.summary(),
                                "matched_ingredients": matched,
                                "missing_ingredients": missing,
                                "coverage": round(coverage, 3)})
        results.sort(key=lambda x: (len(x["missing_ingredients"]), -x["coverage"], x["title"].lower()))
        return results[: max(1, min(limit, 100))]

    def shopping_list(self, recipe_ids: list[str], on_hand: list[str],
                      staples: list[str], unavailable: list[str],
                      bar_on_hand: list[str] | None = None, bar_staples: list[str] | None = None,
                      bar_unavailable: list[str] | None = None) -> list[dict]:
        """What to buy for these recipes, minus what you already have. MEALS check the
        pantry (lenient — 'butter' covers 'unsalted butter'). BEVERAGES check the bar cart
        (plus pantry, for shared things like citrus) with STRICT matching, so a generic
        spirit on your cart ('vodka') does NOT hide a recipe's branded call-out ('Grey Goose
        vodka'); you decide whether the generic will do."""
        pantry_inv = on_hand + staples
        bev_inv = (bar_on_hand or []) + (bar_staples or []) + pantry_inv
        bev_avoid = (bar_unavailable or []) + unavailable
        grouped: dict[str, dict] = {}
        for rid in recipe_ids:
            recipe = self.by_id.get(rid)
            if not recipe:
                continue
            is_bev = recipe.kind == "beverage"
            inventory = bev_inv if is_bev else pantry_inv
            avoid = bev_avoid if is_bev else unavailable
            source_items = recipe.shopping_list or recipe.ingredients
            for ing in source_items:
                blocked = ingredient_is_covered(ing, avoid)
                if not blocked and ingredient_is_covered(ing, inventory, strict=is_bev):
                    continue
                key = ingredient_key(ing)
                entry = grouped.setdefault(
                    key, {"key": key, "label": _title_case(key), "details": set(), "sources": set()})
                entry["details"].add(ing)
                entry["sources"].add(recipe.title)
        out = [{"key": e["key"], "label": e["label"],
                "detail": " + ".join(sorted(e["details"])),
                "sources": sorted(e["sources"])} for e in grouped.values()]
        out.sort(key=lambda x: x["label"].lower())
        return out
