"""Recipe dietary + allergen attribute classifier.

Derives a flat set of tags from a recipe's ingredients (deterministic, no LLM), so the
same rules apply to the 835-recipe backfill AND to every new recipe added through the
ingestor. Tags are used for search filtering (vegetarian / gluten-free / "no shellfish" …)
and are manually overridable per recipe (see attribute_overrides).

The care is in the edge cases a keyword scan would miss:
  * hidden animal products make a dish NON-vegetarian even with no obvious meat —
    gelatin, chicken/beef broth, lard, fish sauce, oyster sauce, anchovy, and
    Worcestershire (contains anchovy);
  * plant analogues must NOT trip the animal tags — coconut/almond/soy/oat "milk",
    peanut/almond/cocoa "butter", "cream of tartar", non-dairy creamer;
  * "flour"/"noodles"/"soy sauce" mean gluten, but rice/almond/coconut/corn flour,
    rice/glass noodles, tamari and buckwheat do NOT;
  * eggplant is not egg, water chestnut is not a tree nut, nutmeg is not a nut,
    bell/black pepper are not "spicy".

Tag vocabulary (all lowercase):
  diet:        vegetarian, vegan, pescatarian
  free-from:   gluten-free, dairy-free, nut-free, egg-free, soy-free
  contains:    contains-dairy, contains-egg, contains-gluten, contains-peanut,
               contains-tree-nut, contains-soy, contains-fish, contains-shellfish,
               contains-sesame, contains-coconut, contains-pork, contains-beef,
               contains-poultry
  other:       spicy
"""
from __future__ import annotations

import re

# --- keyword sets (whole-word, plural-tolerant match via _rx) ----------------

_PORK = {
    "pork", "bacon", "ham", "prosciutto", "pancetta", "guanciale", "chorizo", "salami",
    "pepperoni", "capicola", "capocollo", "soppressata", "mortadella", "bratwurst",
    "kielbasa", "andouille", "lardon", "lard", "pork belly", "pulled pork", "spare rib",
    "baby back", "carnitas", "gammon", "spam", "chicharron", "sausage",
}
_BEEF = {
    "beef", "steak", "sirloin", "ribeye", "rib eye", "brisket", "chuck", "ground beef",
    "veal", "oxtail", "corned beef", "pastrami", "short rib", "flank", "skirt steak",
    "ground chuck", "hamburger meat", "meatloaf", "tri tip", "tri-tip", "prime rib",
    "beef rib", "roast beef", "pot roast", "carne asada", "barbacoa", "cube steak",
    "filet mignon", "t bone", "porterhouse",
}
_OTHER_MEAT = {"lamb", "mutton", "goat", "venison", "bison", "buffalo", "rabbit", "boar",
               "elk", "foie gras", "hot dog", "frankfurter", "bologna", "suet", "tallow",
               "gyro", "shawarma", "kebab", "kabob", "kofta", "meatball", "tenderloin"}
_POULTRY = {
    "chicken", "chicken breast", "chicken thigh", "chicken broth", "chicken stock",
    "rotisserie chicken", "chicken bouillon", "turkey", "ground turkey", "duck", "goose",
    "quail", "cornish hen", "poussin", "poultry", "schmaltz",
}
_FISH = {
    "fish", "salmon", "tuna", "cod", "tilapia", "halibut", "trout", "sea bass", "snapper",
    "mackerel", "sardine", "anchovy", "anchovie", "herring", "catfish", "flounder", "sole",
    "haddock", "mahi", "swordfish", "grouper", "pollock", "barramundi", "branzino",
    "fish sauce", "fish stock", "worcestershire", "caviar", "roe", "smoked salmon", "lox",
    "dashi", "bonito", "katsuobushi", "nam pla", "colatura",
}
_SHELLFISH = {
    "shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop", "squid",
    "calamari", "octopus", "crawfish", "crayfish", "langoustine", "imitation crab",
    "surimi", "oyster sauce", "shrimp paste", "krill", "abalone", "cockle", "whelk",
}
# Hidden animal products -> non-vegetarian even with no headline meat.
_HIDDEN_ANIMAL = {
    "gelatin", "gelatine", "beef broth", "beef stock", "beef bouillon", "bone broth",
    "chicken broth", "chicken stock", "chicken bouillon", "fish sauce", "oyster sauce",
    "worcestershire", "anchovy", "anchovie", "lard", "suet", "tallow", "duck fat",
    "bacon fat", "aspic", "isinglass", "rennet", "carmine", "cochineal",
}

_DAIRY = {
    "milk", "cream", "heavy cream", "whipping cream", "half and half", "butter",
    "buttermilk", "cheese", "cheddar", "mozzarella", "parmesan", "parmigiano", "romano",
    "provolone", "gouda", "brie", "feta", "ricotta", "mascarpone", "cream cheese",
    "cottage cheese", "gruyere", "swiss cheese", "monterey jack", "pepper jack", "cotija",
    "queso", "yogurt", "yoghurt", "sour cream", "ghee", "whey", "casein", "condensed milk",
    "evaporated milk", "creme fraiche", "custard", "ice cream", "gelato", "milk powder",
    "dulce de leche", "clotted cream", "paneer", "halloumi", "burrata", "quark", "kefir",
    "nacho cheese", "boursin", "havarti", "colby", "pecorino", "asiago", "manchego",
    "grana padano", "gorgonzola", "blue cheese", "fontina", "camembert", "taleggio",
    "emmental", "emmenthal", "stilton", "roquefort", "comte", "raclette", "gruyère",
    "creme anglaise", "bechamel",
}
_EGG = {"egg", "egg white", "egg yolk", "egg wash", "mayonnaise", "mayo", "aioli",
        "meringue", "albumin", "eggnog", "hollandaise"}
_GLUTEN = {
    "flour", "wheat", "all purpose flour", "bread flour", "cake flour", "pastry flour",
    "semolina", "durum", "barley", "rye", "malt", "malted", "breadcrumb", "bread crumb",
    "panko", "bread", "baguette", "brioche", "croissant", "pasta", "spaghetti", "macaroni",
    "penne", "fettuccine", "linguine", "lasagna", "lasagne", "couscous", "bulgur", "farro",
    "spelt", "seitan", "soy sauce", "beer", "ale", "cracker", "pretzel", "pita", "naan",
    "phyllo", "filo", "puff pastry", "pie crust", "graham", "orzo", "gnocchi", "wonton",
    "ramen", "udon", "matzo", "wheat germ", "roux", "biscuit", "crouton", "flour tortilla",
    "hoisin", "teriyaki",
}
_PEANUT = {"peanut", "peanut butter", "peanut oil", "groundnut", "satay"}
_TREENUT = {
    "almond", "cashew", "walnut", "pecan", "pistachio", "hazelnut", "macadamia",
    "brazil nut", "pine nut", "pinenut", "chestnut", "praline", "marzipan", "nutella",
    "frangipane", "almond flour", "almond butter", "cashew butter", "nut butter", "amaretto",
}
_SOY = {"soy", "soybean", "soy sauce", "soya", "tofu", "tempeh", "edamame", "miso",
        "tamari", "soy milk", "soy lecithin", "textured vegetable protein", "natto",
        "soy protein"}
_SESAME = {"sesame", "sesame oil", "sesame seed", "tahini", "hummus", "halva", "za'atar",
           "zaatar", "benne", "gomashio"}
_COCONUT = {"coconut", "coconut milk", "coconut cream", "coconut oil", "coconut flour",
            "coconut water", "creamed coconut", "coconut sugar"}
_HONEY = {"honey", "beeswax", "royal jelly", "bee pollen"}
_SPICE = {
    "chili", "chile", "chilli", "chili powder", "chili flake", "red pepper flake",
    "crushed red pepper", "cayenne", "jalapeno", "jalapeño", "serrano", "habanero",
    "chipotle", "ancho", "arbol", "thai chili", "bird's eye", "scotch bonnet",
    "ghost pepper", "sriracha", "sambal", "gochujang", "gochugaru", "harissa", "hot sauce",
    "tabasco", "cholula", "hot pepper", "spicy", "curry paste", "pepper flake", "wasabi",
    "fra diavolo", "buffalo sauce", "cajun", "blackened", "szechuan", "sichuan", "kimchi",
    "diablo", "pepperoncini", "calabrian",
}

# Phrases scrubbed BEFORE matching a set, so plant analogues / false friends don't trip it.
_PLANT_MILK = re.compile(r"\b(?:coconut|almond|soy|oat|rice|cashew|hemp|pea|flax|macadamia|hazelnut) ?milk\b")
_PLANT_BUTTER = re.compile(r"\b(?:almond|peanut|cashew|sun|seed|cocoa|apple|shea|nut|coconut|pumpkin) ?butter\b")
_NON_GLUTEN = re.compile(
    r"\b(?:rice|almond|coconut|corn|chickpea|cassava|tapioca|oat|buckwheat|potato|gluten[- ]?free)"
    r" ?(?:flour|noodle|pasta|bread|tortilla)\b|\bcornstarch\b|\bcorn tortilla\b|\brice noodle\b"
    r"|\bglass noodle\b|\bshirataki\b|\bzucchini noodle\b|\brice paper\b|\btamari\b|\bbuckwheat\b"
    r"|\bcream of tartar\b")
_NON_NUT = re.compile(r"\bwater chestnut\b|\bnutmeg\b|\bchestnut puree\b")
# "cream of tartar" is not dairy; coconut/cashew cream are plant creams.
_NON_DAIRY = re.compile(r"\bcream of tartar\b|\bcoconut cream\b|\bcashew cream\b|\bcream of coconut\b|\bnon[- ]?dairy\b")
# "ribs" as a cut of meat — but scrub the celery/pepper kind first ("celery ribs",
# "2 ribs celery", "seeds and ribs removed", "stems, ribs, and seeds removed") so a
# recipe whose only "meat" is literally "ribs" (BBQ ribs) still reads as non-vegetarian.
_VEG_RIB = re.compile(
    r"\bcelery ribs?\b"
    r"|\bribs?\s+celery\b"
    r"|\bribs?\s+(?:and\s+)?(?:removed|discarded|trimmed|seeds|stems)\b"
    r"|\b(?:seeds|stems)\s+(?:and\s+)?ribs?\b")
_MEAT_RIB = re.compile(r"\bribs?\b")


def _rx(words: set[str]) -> re.Pattern[str]:
    # longest first so multi-word keywords win; trailing s? tolerates plurals; \b keeps
    # "ham" out of "graham"/"hamburger" and "egg" out of "eggplant".
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"\b(?:" + alts + r")s?\b")


_RX = {name: _rx(words) for name, words in {
    "pork": _PORK, "beef": _BEEF, "other_meat": _OTHER_MEAT, "poultry": _POULTRY,
    "fish": _FISH, "shellfish": _SHELLFISH, "hidden": _HIDDEN_ANIMAL, "dairy": _DAIRY,
    "egg": _EGG, "gluten": _GLUTEN, "peanut": _PEANUT, "treenut": _TREENUT, "soy": _SOY,
    "sesame": _SESAME, "coconut": _COCONUT, "honey": _HONEY, "spice": _SPICE,
}.items()}


def classify(ingredients: list[str], title: str = "", category: str = "", kind: str = "meal") -> set[str]:
    """Return the attribute tag set for a recipe from its ingredients (+ title/category)."""
    raw = " ".join(ingredients + [title, category]).lower()
    raw = re.sub(r"[^a-z' ]+", " ", raw)  # keep letters + apostrophes; digits/punct -> space

    # Scrubbed variants so plant analogues don't trip the animal/gluten/nut tags.
    dairy_txt = _NON_DAIRY.sub("  ", _PLANT_BUTTER.sub("  ", _PLANT_MILK.sub("  ", raw)))
    gluten_txt = _NON_GLUTEN.sub("  ", raw)
    nut_txt = _NON_NUT.sub("  ", raw)

    def hit(name: str, text: str | None = None) -> bool:
        return _RX[name].search(text if text is not None else raw) is not None

    pork = hit("pork")
    beef = hit("beef")
    poultry = hit("poultry")
    fish = hit("fish")
    shellfish = hit("shellfish")
    meat = pork or beef or hit("other_meat")
    if not meat and _MEAT_RIB.search(_VEG_RIB.sub("  ", raw)):
        meat = True  # bare "ribs" (BBQ ribs) — non-veg without asserting pork vs beef
    hidden = hit("hidden")  # broth/gelatin/fish sauce/worcestershire/lard/…
    dairy = hit("dairy", dairy_txt)
    egg = hit("egg")
    gluten = hit("gluten", gluten_txt)
    peanut = hit("peanut")
    treenut = hit("treenut", nut_txt)
    soy = hit("soy")
    sesame = hit("sesame")
    coconut = hit("coconut")
    honey = hit("honey")

    tags: set[str] = set()

    # --- contains (allergens + splittable meats) ---
    if dairy: tags.add("contains-dairy")
    if egg: tags.add("contains-egg")
    if gluten: tags.add("contains-gluten")
    if peanut: tags.add("contains-peanut")
    if treenut: tags.add("contains-tree-nut")
    if soy: tags.add("contains-soy")
    if fish: tags.add("contains-fish")
    if shellfish: tags.add("contains-shellfish")
    if sesame: tags.add("contains-sesame")
    if coconut: tags.add("contains-coconut")
    if pork: tags.add("contains-pork")
    if beef: tags.add("contains-beef")
    if poultry: tags.add("contains-poultry")

    # --- diet ---
    non_veg = meat or poultry or fish or shellfish or hidden
    if not non_veg:
        tags.add("vegetarian")
        if not (dairy or egg or honey):
            tags.add("vegan")
    if (fish or shellfish) and not (meat or poultry):
        tags.add("pescatarian")

    # --- free-from (best-effort: no detected source of that allergen) ---
    if not gluten: tags.add("gluten-free")
    if not dairy: tags.add("dairy-free")
    if not (peanut or treenut): tags.add("nut-free")
    if not egg: tags.add("egg-free")
    if not soy: tags.add("soy-free")

    # --- other ---
    if hit("spice"):
        tags.add("spicy")

    return tags


# All tags the classifier can emit (for the edit UI's "add" picker + validation).
ALL_TAGS: tuple[str, ...] = (
    "vegetarian", "vegan", "pescatarian",
    "gluten-free", "dairy-free", "nut-free", "egg-free", "soy-free", "spicy",
    "contains-dairy", "contains-egg", "contains-gluten", "contains-peanut",
    "contains-tree-nut", "contains-soy", "contains-fish", "contains-shellfish",
    "contains-sesame", "contains-coconut", "contains-pork", "contains-beef",
    "contains-poultry",
)
