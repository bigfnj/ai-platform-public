"""LLM-authored, distinctive per-recipe icon subjects.

The old heuristic mapped a title keyword (or the category) to ONE generic subject, so
898 recipes collapsed to ~155 prompts — 70 different chicken dishes all became "roast
chicken" and rendered identical icons. This asks the culinary LLM for a specific,
drawable subject per recipe (main dish + form/vessel + a distinguishing element), keyed
by a short batch index (never the opaque sha1 id, which a model would mangle), and caches
it to ``icon_prompts``. ``icons.generate`` then renders those instead of the heuristic.

All model work goes through the broker (a chat round-trip per batch). GPU-light vs the
image render, but still GPU — this is the first step of the icon re-pass.
"""
from __future__ import annotations

import re
import sqlite3

from recipe_book import broker, db, state

SUBJECT_SYS = (
    "You write ONE short, vivid visual subject for a food/drink ICON, one per recipe. Each "
    "subject names the single most iconic thing to draw for THAT recipe so it reads as visually "
    "DISTINCT from similar recipes: the main dish or object, its form and vessel, and ONE "
    "distinguishing element (a key ingredient, colour, or garnish). 3 to 9 words. A bare noun "
    "phrase — no leading 'a/an', no cooking verbs, no brand names, no camera or art-style words, "
    "no text or letters. Prefer concrete, drawable nouns.\n"
    "Distinctiveness matters: when many recipes are similar, differentiate them — e.g. instead of "
    "'roast chicken' for every chicken dish, use 'lemon-thyme roast chicken', 'chicken tikka masala "
    "in a bowl', 'crispy chicken katsu cutlet', 'chicken caesar salad in a bowl'.\n"
    'Return STRICT JSON only: {"subjects": {"1": "...", "2": "..."}} keyed by the recipe number.'
)

_LEAD_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)


def _clean(s) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip()
    s = _LEAD_ARTICLE.sub("", s)          # the render wrapper already says "a single {subject}"
    return s[:90]


def _recipe_line(n: int, r) -> str:
    ings = ", ".join(r.ingredients[:6]) if r.ingredients else "—"
    kind = "drink" if r.kind == "beverage" else "dish"
    return f"{n}. [{kind}] {r.title} | {r.category} | {ings}"


def build(con: sqlite3.Connection, *, recipes=None, force: bool = False,
          model: str | None = None, batch: int = 24, on_progress=None) -> dict:
    """Generate + cache a distinctive icon subject per recipe. Skips recipes that already
    have one unless ``force``. A recipe the model omits keeps no cached subject, so
    ``icons.subject_for`` falls back to the heuristic for it — never blocks the render."""
    recs = list(recipes if recipes is not None else state.catalog().recipes)
    if not force:
        have = set(db.get_icon_subjects(con))
        recs = [r for r in recs if r.id not in have]
    made = failed = 0
    for i in range(0, len(recs), batch):
        chunk = recs[i:i + batch]
        user = "RECIPES:\n" + "\n".join(_recipe_line(n + 1, r) for n, r in enumerate(chunk))
        try:
            data = broker.chat_json(
                model or broker.ASSISTANT_MODEL,
                [{"role": "system", "content": SUBJECT_SYS}, {"role": "user", "content": user}],
                options={"temperature": 0.7})
        except broker.BrokerError:
            failed += len(chunk)
            continue
        subs = data.get("subjects") if isinstance(data.get("subjects"), dict) else data
        subs = subs if isinstance(subs, dict) else {}
        for n, r in enumerate(chunk):
            s = _clean(subs.get(str(n + 1)) or subs.get(n + 1) or "")
            if s:
                db.set_icon_subject(con, r.id, s)
                made += 1
            else:
                failed += 1
        con.commit()
        if on_progress:
            on_progress(min(i + batch, len(recs)), len(recs))
    return {"made": made, "failed": failed, "targets": len(recs)}


def repass(con: sqlite3.Connection, *, force: bool = True, llm_batch: int = 24,
           img_batch: int = 200) -> dict:
    """Full re-pass: (1) write distinctive subjects with the LLM, then (2) render icons
    with the local image model. Both hit the GPU. ``force`` redoes every recipe."""
    from recipe_book import icons
    prompts = build(con, force=force, batch=llm_batch)
    imgs = icons.generate(con, force=force, batch=img_batch)
    return {"prompts": prompts, "icons": imgs}
