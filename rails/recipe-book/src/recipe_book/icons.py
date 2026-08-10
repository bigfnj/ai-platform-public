"""Per-recipe clipart icons rendered by the platform's FLUX image worker (through the broker).

A small glyph is cached to ``ICONS_DIR/<id>.png`` and shown beside the recipe title. The
render subject is the distinctive LLM-authored one from ``recipe_book.icon_prompts`` when
present (see ``subject_for``), else a deterministic heuristic (``subject``): cocktails use
their parsed glassware (a martini glass, a coupe, …); meals use a title-keyword / category
mapping. Generation is batched so the image model loads once per batch.
"""
from __future__ import annotations

import base64
import sqlite3
import threading
import time

from recipe_book import broker, config, db, state

# --- background-run state: single-flight guard for the admin (re)generation endpoints ---
# Rendering hits the shared GPU broker (one heavy model at a time, used by every rail), so we
# never want two icon runs stacked on it. The API layer calls try_begin()/set_phase()/finish()
# around a background job; run_state() feeds the status poll. In-process + single worker, so a
# lock-guarded module global is sufficient.
_run_lock = threading.Lock()
_run: dict = {"running": False, "phase": None, "started_at": None,
              "finished_at": None, "last": None}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def try_begin(phase: str) -> bool:
    """Claim the run slot. Returns False if one is already in flight — the caller should
    refuse rather than queue a second broker-heavy run."""
    with _run_lock:
        if _run["running"]:
            return False
        _run.update(running=True, phase=phase, started_at=_now(), finished_at=None)
        return True


def set_phase(phase: str) -> None:
    with _run_lock:
        if _run["running"]:
            _run["phase"] = phase


def finish(result: dict) -> None:
    with _run_lock:
        _run.update(running=False, phase=None, last=result, finished_at=_now())


def run_state() -> dict:
    with _run_lock:
        return dict(_run)

# recipe-app clipart look — one clean, isolated object on white (not a busy plated scene)
_ICON_PROMPT = (
    "a single {subject}, flat vector clipart sticker, bold clean outlines, bright flat "
    "colors, centered, isolated on a plain solid white background, no text"
)
_ICON_NEGATIVE = (
    "text, words, letters, watermark, signature, photo, photorealistic, 3d, realistic, blurry, "
    "cluttered, busy background, multiple plates, stacked plates, table, scenery, frame, border, shadow"
)

# parsed glassware -> a concrete SDXL subject
_GLASS_SUBJECT = {
    "Martini": "a martini glass with a cocktail",
    "Nick & Nora": "a nick and nora cocktail glass",
    "Coupe": "a coupe cocktail glass",
    "Old Fashioned": "an old fashioned rocks glass with a cocktail",
    "Double Old Fashioned": "a rocks glass with a cocktail and a large ice cube",
    "Collins": "a tall collins cocktail glass",
    "Highball": "a highball cocktail glass",
    "Flute": "a champagne flute",
    "Hurricane": "a hurricane cocktail glass",
    "Mug": "a copper mug cocktail",
    "Tiki": "a tiki mug cocktail",
    "Julep": "a silver julep cup cocktail",
    "Wine": "a glass of wine",
    "Shot": "a shot glass",
    "Snifter": "a brandy snifter glass",
}


# Frontier-authored food subjects: map a distinctive title keyword to ONE clean,
# renderable object. Ordered — first match wins (more specific before generic), so
# e.g. "meatball" beats "beef", "cheesecake" beats "cake".
_FOOD_KEYWORDS: list[tuple[str, str]] = [
    ("pizza", "slice of pizza"), ("cheeseburger", "cheeseburger"), ("burger", "hamburger"),
    ("taco", "taco"), ("burrito", "burrito"), ("quesadilla", "quesadilla"), ("nacho", "plate of nachos"),
    ("enchilada", "enchilada"), ("ramen", "bowl of ramen"), ("pho", "bowl of pho noodle soup"),
    ("udon", "bowl of udon noodles"), ("pad thai", "bowl of pad thai noodles"),
    ("lasagna", "slice of lasagna"), ("spaghetti", "bowl of spaghetti"),
    ("mac and cheese", "bowl of macaroni and cheese"), ("macaroni", "bowl of macaroni"),
    ("noodle", "bowl of noodles"), ("pasta", "bowl of pasta"), ("gnocchi", "bowl of gnocchi"),
    ("dumpling", "dumplings"), ("potsticker", "dumplings"), ("gyoza", "dumplings"), ("wonton", "wontons"),
    ("sushi", "sushi rolls"), ("sashimi", "sashimi"), ("chowder", "bowl of chowder"),
    ("bisque", "bowl of bisque"), ("chili", "bowl of chili"), ("stew", "bowl of stew"),
    ("soup", "bowl of soup"), ("salad", "salad in a bowl"), ("slaw", "bowl of coleslaw"),
    ("sandwich", "sandwich"), ("panini", "panini sandwich"), ("wrap", "wrap sandwich"),
    ("curry", "bowl of curry"), ("risotto", "bowl of risotto"), ("fried rice", "bowl of fried rice"),
    ("rice", "bowl of rice"), ("meatball", "meatballs"), ("rib", "rack of bbq ribs"),
    ("brisket", "smoked brisket"), ("steak", "grilled steak"), ("beef", "grilled beef"),
    ("pulled pork", "pulled pork"), ("bacon", "strips of bacon"), ("sausage", "sausage"),
    ("ham", "glazed ham"), ("pork", "pork chop"), ("wing", "chicken wings"),
    ("chicken", "roast chicken"), ("turkey", "roast turkey"), ("duck", "roast duck"),
    ("salmon", "salmon fillet"), ("tuna", "tuna steak"), ("shrimp", "shrimp"), ("prawn", "shrimp"),
    ("scampi", "shrimp scampi"), ("crab", "crab"), ("lobster", "lobster"), ("fish", "fish fillet"),
    ("omelet", "omelette"), ("frittata", "frittata"), ("quiche", "quiche"), ("egg", "fried egg"),
    ("pancake", "stack of pancakes"), ("waffle", "waffle"), ("french toast", "french toast"),
    ("smoothie", "fruit smoothie in a glass"), ("cheesecake", "slice of cheesecake"),
    ("cupcake", "cupcake"), ("cake", "slice of cake"), ("brownie", "brownie"),
    ("cookie", "cookie"), ("pie", "slice of pie"), ("tart", "fruit tart"), ("cobbler", "fruit cobbler"),
    ("donut", "donut"), ("doughnut", "donut"), ("ice cream", "ice cream cone"),
    ("gelato", "scoop of gelato"), ("pudding", "cup of pudding"), ("muffin", "muffin"),
    ("scone", "scone"), ("biscuit", "biscuit"), ("bagel", "bagel"), ("pretzel", "pretzel"),
    ("bread", "loaf of bread"), ("hummus", "bowl of hummus"), ("guacamole", "bowl of guacamole"),
    ("salsa", "bowl of salsa"), ("dip", "bowl of dip"), ("dressing", "bottle of salad dressing"),
    ("marinade", "bottle of marinade"), ("sauce", "bottle of sauce"), ("rub", "jar of spice rub"),
    ("jam", "jar of jam"), ("jelly", "jar of jelly"), ("preserve", "jar of preserves"),
    ("pickle", "jar of pickles"), ("oatmeal", "bowl of oatmeal"), ("granola", "bowl of granola"),
    ("potato", "roasted potatoes"), ("fries", "french fries"), ("casserole", "casserole dish"),
    ("stir fry", "stir fry in a wok"), ("stir-fry", "stir fry in a wok"), ("kebab", "skewered kebab"),
    ("skewer", "food skewer"), ("taco", "taco"),
    # --- coverage fills: recipes whose titles missed every keyword above ---
    ("tri-tip", "sliced grilled beef roast"), ("tri tip", "sliced grilled beef roast"),
    ("carnitas", "carnitas tacos"), ("tilapia", "fish fillet"), ("cod", "fish fillet"),
    ("chimichanga", "chimichanga"), ("tostada", "tostada"), ("torta", "torta sandwich"),
    ("calzone", "calzone"), ("empanada", "empanada"), ("moussaka", "moussaka casserole"),
    ("ratatouille", "ratatouille"), ("succotash", "bowl of succotash"),
    ("polenta", "bowl of polenta"), ("fregola", "bowl of pasta"),
    ("ditalini", "bowl of pasta"), ("alfredo", "bowl of pasta"), ("dal", "bowl of dal"),
    ("collard", "bowl of collard greens"), ("greens", "bowl of greens"),
    ("curried", "bowl of curry"), ("cauliflower", "roasted cauliflower"),
    ("bell pepper", "roasted bell peppers"), ("iced tea", "glass of iced tea"),
    ("beer cheese", "bowl of cheese dip"), ("seasoning", "jar of spice seasoning"),
    ("platter", "food platter"),
    # round 2: drinks / condiments / specific desserts that hit the mixed-category fallbacks
    ("almond milk", "glass of almond milk"), ("coconut milk", "glass of coconut milk"),
    ("cooler", "tall glass of iced fruit drink"), ("tonic", "glass of herbal tonic"),
    ("popsicle", "fruit popsicle"), ("trail mix", "bowl of trail mix"),
    ("seed mix", "bowl of seed mix"), ("quinoa", "bowl of quinoa"),
    ("pesto", "jar of pesto"), ("chimichurri", "bowl of chimichurri"),
    ("vinaigrette", "bottle of vinaigrette"),
    ("tiramisu", "tiramisu"), ("trifle", "trifle dessert in a glass"),
    ("toffee", "toffee candy"), ("bark", "chocolate bark"), ("fudge", "fudge"),
    ("lemon bar", "lemon bars"), ("caramel", "caramel candy"),
    ("baked pear", "baked pears"), ("cherries", "bowl of cherries"),
    ("bananas foster", "bananas foster"), ("honeyed banana", "glazed bananas"),
    ("flambe", "flambe dessert"), ("sabayon", "custard in a glass"),
    ("anglaise", "custard cream"), ("smores", "s'mores bars"),
    ("veggie", "plate of vegetables"),
    ("vegetable", "plate of vegetables"), ("tofu", "tofu"), ("smoothie", "smoothie"),
]

# category fallback when no keyword matches (program/method categories -> a sensible default)
_CATEGORY_SUBJECT: dict[str, str] = {
    "Ramen": "bowl of ramen", "Soups": "bowl of soup", "Pasta": "bowl of pasta",
    "Salads & Dressings": "salad in a bowl", "Deserts": "slice of cake", "Baking": "fresh baked bread",
    "Breakfast": "breakfast plate with eggs", "Side Dishes": "side dish of vegetables",
    "BBQ": "barbecue grilled meat", "Smoker": "smoked barbecue meat", "Crock Pot": "pot of stew",
    "Hummus": "bowl of hummus", "Preserves": "jar of preserves",
    "Yonanas": "bowl of frozen fruit dessert", "Sauces, Rubs, Marinades": "bottle of sauce",
    "Date Night": "romantic plated dinner", "21 Day Cleanse": "healthy green bowl",
    # meal-kit + catch-all categories that previously fell through to "plated dish"
    "Smoothies": "fruit smoothie in a glass", "Entrees": "plated dinner",
    "Blue Apron": "plated dinner", "Marley and Spoon": "plated dinner",
    "Dinnerly": "plated dinner", "Thai": "bowl of thai food", "To Try": "plated dinner",
}


# Exact-title overrides for idiosyncratic recipes whose title matches no keyword and whose
# category fallback would mislabel them (user-reviewed). Checked first, so they always win.
_TITLE_SUBJECT: dict[str, str] = {
    "Almonds & Berries": "bowl of almonds and mixed berries",
    "Almonds and Blueberries": "bowl of almonds and blueberries",
    "Apple & Almond Butter": "apple slices with almond butter",
    "Apple Almond Butter Bites": "apple slices topped with almond butter",
    "Avocado & Sunflower Seed Snack": "sliced avocado with sunflower seeds",
    "Avocado Mash": "bowl of mashed avocado",
    "Avocado Snack": "a slice of toast next to a whole avocado",
    "Cashew Cream": "small bowl of cashew cream",
    "Chia Seed Bars": "a chia seed energy bar",
    "Fruit Cream": "bowl of fruit with cream",
    "Hot Water with Lemon, Ginger, Cayenne": "mug of hot lemon-ginger water",
    "Kale Croutons": "bowl of crispy kale chips",
    "Strawberry Almond Snack": "strawberries with almonds",
    "Cranachan": "raspberry-and-cream parfait in a glass",
    "Pate Sucree": "ball of pastry dough",
    "Soft Pumpkin Chocolate Chip Bars": "pumpkin chocolate-chip dessert bars",
    "Bombshell Bags": "skillet of Spanish rice with sausage and peppers",
}


def subject(recipe) -> str:
    """Deterministic heuristic subject (fallback). See ``subject_for`` for the
    LLM-authored, per-recipe subject that supersedes this when present."""
    override = _TITLE_SUBJECT.get(recipe.title)
    if override:
        return override
    if recipe.kind == "beverage":
        if recipe.glass and recipe.glass in _GLASS_SUBJECT:
            return _GLASS_SUBJECT[recipe.glass]
        return "cocktail in a glass"
    low = recipe.title.lower()
    for kw, subj in _FOOD_KEYWORDS:
        if kw in low:
            return subj
    return _CATEGORY_SUBJECT.get(recipe.category, "plated dish")


def subject_for(recipe, llm_subjects: dict[str, str]) -> str:
    """The subject to render: the distinctive LLM-authored one if we have it
    (recipe_book.icon_prompts), else the deterministic heuristic."""
    return (llm_subjects.get(recipe.id) or "").strip() or subject(recipe)


def _targets(force: bool, ids: set[str] | None, limit: int):
    out = []
    for r in state.catalog().recipes:
        if ids is not None and r.id not in ids:
            continue
        if not force and (config.ICONS_DIR / f"{r.id}.png").exists():
            continue
        out.append(r)
        if limit and len(out) >= limit:
            break
    return out


def generate(con: sqlite3.Connection, *, ids: set[str] | None = None, limit: int = 0,
             force: bool = False, batch: int = 16, steps: int = 4, size: int = 768) -> dict:
    """Generate + cache icons for recipes that don't have one yet (or all, if
    ``force``). Batched broker calls; marks ``icon_status='ready'`` per success.

    ``size`` defaults to 768 (FLUX.1-schnell renders a cleaner isolated glyph at
    higher res, then displayed small). Each broker call spawns a worker that loads the
    image model once for the whole ``batch`` and exits, so a larger ``batch`` amortizes
    the (heavier) FLUX load across more icons — tune ``batch`` up for the full run."""
    config.ensure_dirs()
    targets = _targets(force, ids, limit)
    llm_subjects = db.get_icon_subjects(con)   # distinctive per-recipe subjects (if built)
    made = failed = 0
    for i in range(0, len(targets), batch):
        chunk = targets[i:i + batch]
        prompts = [_ICON_PROMPT.format(subject=subject_for(r, llm_subjects)) for r in chunk]
        try:
            imgs = broker.generate_images(prompts, negative_prompt=_ICON_NEGATIVE,
                                          steps=steps, size=size)
        except broker.BrokerError:
            failed += len(chunk)
            continue
        for j, r in enumerate(chunk):
            b64 = imgs[j] if j < len(imgs) else None
            if not b64:
                failed += 1
                continue
            (config.ICONS_DIR / f"{r.id}.png").write_bytes(base64.b64decode(b64))
            con.execute("UPDATE recipes SET icon_status='ready' WHERE id=?", (r.id,))
            made += 1
        con.commit()
    state.reload()
    return {"made": made, "failed": failed, "targets": len(targets)}


def status(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    ready = con.execute("SELECT COUNT(*) FROM recipes WHERE icon_status='ready'").fetchone()[0]
    return {"ready": ready, "pending": total - ready, "total": total}


def reconcile(con: sqlite3.Connection) -> int:
    """Mark icon_status='ready' for any recipe whose PNG already exists on disk
    (e.g. after a rebuild reset the column but the cached icons survive)."""
    n = 0
    for r in con.execute("SELECT id FROM recipes WHERE icon_status!='ready'"):
        if (config.ICONS_DIR / f"{r['id']}.png").exists():
            con.execute("UPDATE recipes SET icon_status='ready' WHERE id=?", (r["id"],))
            n += 1
    con.commit()
    return n
