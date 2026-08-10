"""Bar enrichment: derive base spirit(s), glassware, and technique from a
beverage recipe's ingredients + instructions, so cocktails are browsable by
spirit and the "what can I pour" match has structure to lean on.

Heuristic and deterministic (runs at ingest, no LLM). Base spirits are collapsed
to canonical categories (Gin, Whiskey, Rum, …) which is what the Bar's
"browse by base spirit" facet wants.
"""
from __future__ import annotations

import re

# keyword -> canonical spirit category. Order matters: earlier, more specific
# patterns win (bourbon before the generic whiskey fallback is unnecessary since
# both map to Whiskey, but e.g. mezcal must be checked before agave/tequila-ish).
# Each canonical spirit matches its own name, common brands, and (for the base
# audit) the classic cocktails built on it — so a "Cuervo Bloody Maria" or a
# "Captain Morgan Planter's Punch" is recognized even when the bare word isn't
# spelled out. Scanned against title + ingredients.
_SPIRIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmezcal\b|\bmescal\b"), "Mezcal"),
    (re.compile(r"\btequila\b|cuervo|patr[oó]n|don julio|espol[oó]n|herradura|casamigos|"
                r"\b1800\b|reposado|a[ñn]ejo|\bpaloma\b|margarita|bloody maria|\bdiablo\b"), "Tequila"),
    (re.compile(r"\bgin\b|tanqueray|beefeater|bombay|hendrick|plymouth|genever|"
                r"negroni|gimlet|tom collins|dutch collins|last word|aviation"), "Gin"),
    (re.compile(r"\bvodka\b|tito|absolut|smirnoff|ketel one|grey goose|belvedere|"
                r"stolichnaya|\bstoli\b|svedka|cosmopolitan|moscow mule|white russian|bloody mary"), "Vodka"),
    (re.compile(r"\b(?:rum|rhum|cacha[cç]a)\b|bacardi|captain morgan|\bmalibu\b|myer'?s|"
                r"appleton|mount gay|gosling|kraken|daiquiri|mojito|colada|mai tai|"
                r"painkiller|dark and stormy|planter'?s punch"), "Rum"),
    (re.compile(r"\b(?:bourbon|rye|scotch|whisk(?:e)?y)\b|jack daniel|jameson|maker'?s|"
                r"bulleit|wild turkey|jim beam|crown royal|woodford|knob creek|buffalo trace|"
                r"old forester|manhattan|sazerac|mint julep|boulevardier"), "Whiskey"),
    (re.compile(r"\b(?:cognac|armagnac|brandy|pisco|calvados|applejack)\b|sidecar|hennessy|courvoisier"), "Brandy"),
    (re.compile(r"\b(?:sweet |dry )?vermouth\b|lillet|dubonnet|cocchi"), "Vermouth"),
    (re.compile(r"\b(?:sherry|port|madeira|marsala)\b"), "Fortified Wine"),
    (re.compile(r"\b(?:champagne|prosecco|cava|sparkling wine|wine|sake|sak[eé])\b|mimosa|bellini"), "Wine & Sparkling"),
    (re.compile(r"\babsinthe\b|\bpastis\b"), "Absinthe"),
    (re.compile(r"\b(?:amaretto|campari|aperol|amaro|chartreuse|cointreau|triple sec|"
                r"cur[aà]?[cç]ao|liqueur|schnapps|kahl[uú]a|baileys|grand marnier|"
                r"st[\.\- ]?germain|elderflower|maraschino|b[eé]n[eé]dictine|drambuie|"
                r"frangelico|midori|chambord|limoncello|falernum|orgeat|cr[eè]me de|pimm)\b"), "Liqueur"),
]

_GLASSES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"nick and nora"), "Nick & Nora"),
    (re.compile(r"double old.?fashioned|dof\b"), "Double Old Fashioned"),
    (re.compile(r"old.?fashioned|rocks glass|\brocks\b"), "Old Fashioned"),
    (re.compile(r"coupe"), "Coupe"),
    (re.compile(r"martini glass|cocktail glass|\bmartini\b"), "Martini"),
    (re.compile(r"collins glass|\bcollins\b"), "Collins"),
    (re.compile(r"highball"), "Highball"),
    (re.compile(r"champagne flute|\bflute\b"), "Flute"),
    (re.compile(r"hurricane"), "Hurricane"),
    (re.compile(r"copper mug|moscow mule mug|\bmug\b"), "Mug"),
    (re.compile(r"tiki|mai tai glass"), "Tiki"),
    (re.compile(r"julep|julep tin"), "Julep"),
    (re.compile(r"wine glass|\bgoblet\b"), "Wine"),
    (re.compile(r"shot glass|\bshot\b"), "Shot"),
    (re.compile(r"snifter"), "Snifter"),
]

# technique priority: the strongest signal wins
_TECHNIQUES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"blend(?:er|ed|)\b"), "Blended"),
    (re.compile(r"muddl"), "Muddled"),
    (re.compile(r"dry.?shake|shake|shaken"), "Shaken"),
    (re.compile(r"\bstir(?:red)?\b"), "Stirred"),
    (re.compile(r"\bbuild|built\b"), "Built"),
]


def base_spirits(ingredients: list[str]) -> list[str]:
    found: list[str] = []
    for line in ingredients:
        low = line.lower()
        for pat, name in _SPIRIT_PATTERNS:
            if name not in found and pat.search(low):
                found.append(name)
    return found


def glass_of(text: str) -> str:
    low = text.lower()
    for pat, name in _GLASSES:
        if pat.search(low):
            return name
    return ""


def technique_of(text: str) -> str:
    low = text.lower()
    for pat, name in _TECHNIQUES:
        if pat.search(low):
            return name
    return ""


def enrich(recipe) -> None:
    """Fill recipe.base_spirits / glass / technique in place (beverages only)."""
    if recipe.kind != "beverage":
        return
    # Title carries strong base-spirit signal (brand / classic-cocktail name) that the
    # ingredient list sometimes omits, so scan both.
    recipe.base_spirits = base_spirits([recipe.title, *recipe.ingredients])
    blob = " ".join([recipe.meta, *recipe.instructions, *recipe.ingredients])
    recipe.glass = glass_of(blob)
    recipe.technique = technique_of(" ".join(recipe.instructions) or blob)
