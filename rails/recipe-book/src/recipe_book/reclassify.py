"""Reclassify recipes filed under workflow buckets (e.g. "New") into proper
dish-type categories by title keyword — ordered, first match wins. Applied at
ingest, so the change is permanent and survives a rebuild.
"""
from __future__ import annotations

import re

# source categories that are workflow buckets, not dish types
RECLASSIFY_CATEGORIES = {"New"}
_DEFAULT = "Entrees"

_RULES: list[tuple[str, str]] = [
    (r"mocha|hot chocolate|\bcocoa\b|\blatte\b|\bcider\b|milkshake|\bsmoothie\b|eggnog", "Beverages"),
    (r"cinnamon roll|biscotti|\bscone\b|\bmuffin\b|banana bread|\bfocaccia\b|\bbagel\b", "Baking"),
    (r"cheesecake|cupcake|\bcake\b|cookie|doughnut|donut|\btart\b|truffle|bananas foster|"
     r"brownie|s'?mores|\bcordial\b|\bfudge\b|\bpie\b|pudding|\bbars?\b", "Deserts"),
    (r"\bsauce\b|\bpaste\b|seasoning|\bketchup\b|dipping|\brub\b|\bmarinade\b|\bglaze\b|vinaigrette", "Sauces, Rubs, Marinades"),
    (r"\bsoup\b|chowder|bisque|\bstew\b", "Soups"),
    (r"casserole|scalloped potato|potato salad|green bean|\bmashed\b|stuffing", "Side Dishes"),
    (r"crock.?pot|slow.?cooker", "Crock Pot"),
    (r"barbecue|\bbbq\b|\bwings?\b|moo dang|brisket", "BBQ"),
    (r"gnocchi|\bpasta\b|lasagna|spaghetti|linguine|fettuccine|ravioli|mac and cheese|carbonara", "Pasta"),
    (r"shakshuka|omelet|frittata|pancake|waffle|\bquiche\b|granola|\boatmeal\b|french toast", "Breakfast"),
    (r"\bsalad\b", "Salads & Dressings"),
]


def category_for(title: str) -> str:
    t = title.lower()
    for pat, cat in _RULES:
        if re.search(pat, t):
            return cat
    return _DEFAULT


# --- Thai detection: a cross-category pass that pulls Thai dishes into "Thai" ----
# (peanut sauces, pad thai, the curries/pastes, etc.). Applied to every category
# EXCEPT the "To Try" staging bucket.
_THAI_TITLE = re.compile(
    r"\bthai\b|pad ?thai|pad see ?ew|pad (?:ga ?pow|krapow|kra ?pao)|panang|penang|massaman|"
    r"tom (?:yum|kha)|(?:green|red|yellow) curry|curry paste|\blarb\b|som tum|drunken noodle|"
    r"khao soi|mango sticky rice|\bsatay\b|moo dang|nam prik|\bgalangal\b|bamboo curry", re.I)
_NOT_THAI = re.compile(r"\bindian\b|butter chicken|tikka|masala|vindaloo|korma|biryani|"
                       r"\bjapanese\b|\bkorean\b|vietnam", re.I)
_THAI_PASTE = re.compile(r"(?:green|red|panang|massaman|yellow) curry paste", re.I)
_THAI_AROMA = re.compile(r"lemongrass|galangal|kaffir|thai basil|thai chil", re.I)


def is_thai(recipe) -> bool:
    if _NOT_THAI.search(recipe.title):
        return False
    if _THAI_TITLE.search(recipe.title):
        return True
    low = " ".join(recipe.ingredients).lower()
    if _THAI_PASTE.search(low):
        return True
    return "fish sauce" in low and "coconut milk" in low and bool(_THAI_AROMA.search(low))
