"""AI meal planner: propose a day-by-day plan from the user's OWN library, each pick
carrying a short rationale, for the UI to accept / swap / skip slot by slot.

Library-only by design — every pick references a real recipe id, so Optimize-Shopping
and the aggregated shopping list stay exact. All model work goes through the broker.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from recipe_book import broker, db, settings, state

# Which categories feed each slot's candidate pool ("To Try" is always excluded).
_SLOT_CATS: dict[str, set[str]] = {
    "breakfast": {"Breakfast", "Baking", "Smoothies"},
    "lunch": {"Salads & Dressings", "Soups", "Pasta", "Entrees", "Side Dishes", "Ramen"},
    "dinner": {"Entrees", "Pasta", "BBQ", "Crock Pot", "Thai", "Soups", "Date Night",
               "Blue Apron", "Dinnerly", "Marley and Spoon", "Ramen", "Smoker"},
    "snack": {"Deserts", "Baking", "Hummus", "Yonanas"},
    "drink": {"Beverages", "Smoothies"},
}
_MEAL_SLOTS = ("breakfast", "lunch", "dinner", "snack", "drink")
_SLOT_ORDER = {s: i for i, s in enumerate(("breakfast", "lunch", "snack", "dinner", "drink"))}
_POOL_CAP = 55


def _pool(slot: str, exclude: set[str] | None = None) -> list:
    cats = _SLOT_CATS.get(slot, set())
    recs = [r for r in state.catalog().recipes
            if r.category in cats and r.category != "To Try" and (r.ingredients or r.instructions)]
    if exclude:
        fresh = [r for r in recs if r.id not in exclude]
        if len(fresh) >= 12:   # drop recently-served picks, but only while a decent pool remains
            recs = fresh
    random.shuffle(recs)
    return recs[:_POOL_CAP]


def _recently_planned(con, days: int, owner: int) -> set[str]:
    """Recipe ids planned within the last ``days`` (recent past) or already scheduled
    ahead, so the planner won't re-suggest a dinner you just had or already have coming up."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return {r["recipe_id"] for r in con.execute(
        "SELECT DISTINCT recipe_id FROM meal_plan_entries "
        "WHERE owner_id=? AND recipe_id IS NOT NULL AND date>=?",
        (owner, cutoff)) if r["recipe_id"]}


def _cocktail_pool() -> list:
    recs = [r for r in state.catalog().recipes
            if r.kind == "beverage" and r.category == "Beverages"
            and r.primary_spirit != "Non-Alcoholic" and r.ingredients]
    random.shuffle(recs)
    return recs[:_POOL_CAP]


def _line(r) -> str:
    return f'- id={r.id} | {r.title} | {r.category} | {"; ".join(r.ingredients[:6])}'


def _match_cocktail(name: str):
    """Match a cocktail name from the model back to a real Bar recipe (exact title,
    then containment), so a named pairing links to the library when possible."""
    low = (name or "").lower().strip()
    if not low:
        return None
    bevs = [r for r in state.catalog().recipes if r.kind == "beverage"]
    for r in bevs:
        if r.title.lower() == low:
            return r
    for r in bevs:
        t = r.title.lower()
        if low in t or t in low:
            return r
    return None


def _occupied(con, dates: list[str], owner: int) -> set:
    if not dates:
        return set()
    q = ("SELECT date, slot FROM meal_plan_entries WHERE owner_id=? AND date IN (%s)"
         % ",".join("?" * len(dates)))
    return {(r["date"], r["slot"]) for r in con.execute(q, [owner, *dates])}


def _item(r, *, date: str, slot: str, ptype: str, why: str) -> dict:
    return {"date": date, "slot": slot, "ptype": ptype, "recipe_id": r.id,
            "title": r.title, "summary": r.summary(), "why": (why or "").strip()}


def propose(con, dates: list[str], slots: list[str],
            optimize_shopping: bool, drink_pairing: bool, owner: int) -> dict:
    slots = [s for s in slots if s in _MEAL_SLOTS]
    if not dates or not slots:
        return {"items": []}
    occ = _occupied(con, dates, owner)
    targets = [(d, s) for d in dates for s in slots if (d, s) not in occ]
    if not targets:
        return {"items": []}

    recent = _recently_planned(con, settings.recency_days(con), owner)  # keep suggestions varied
    pools = {s: _pool(s, recent) for s in {s for _, s in targets}}
    want_pairing = drink_pairing and any(s == "dinner" for _, s in targets)
    cpool = _cocktail_pool() if want_pairing else []

    lines: list[str] = []
    for s, recs in pools.items():
        lines.append(f"## {s.upper()} candidates")
        lines += [_line(r) for r in recs]
    if want_pairing:
        lines.append("## COCKTAIL candidates (for dinner pairings)")
        lines += [_line(r) for r in cpool]

    rules = [
        "Pick exactly ONE recipe id from the matching slot's candidate list for each target.",
        "Use each recipe at most once; vary cuisines, proteins and styles across the plan.",
    ]
    if optimize_shopping:
        rules.append("OPTIMIZE SHOPPING: strongly prefer recipes that share core ingredients so one "
                     "grocery trip covers many meals; name the shared ingredients in your 'why'.")
    if want_pairing:
        rules.append('For EACH dinner add a pairing {"date","kind","name","why"}: kind="cocktail" with '
                     "name = the EXACT title of a cocktail from the COCKTAIL list, OR kind=\"wine\" with "
                     'name = a wine style (e.g. "Pinot Noir (red)"). Do NOT put recipe ids in pairings. '
                     "One short sentence tied to the dish.")

    sys = ("You are a thoughtful home meal planner. Return STRICT JSON ONLY, no prose. Schema: "
           '{"picks":[{"date":str,"slot":str,"recipe_id":str,"why":str}],'
           '"pairings":[{"date":str,"kind":"cocktail"|"wine","name":str,"why":str}]}. '
           "For picks, only use recipe ids that literally appear in the candidate lists. "
           "Each 'why' is ONE short sentence.")
    user = ("TARGETS (date + slot to fill):\n" + "\n".join(f"- {d} {s}" for d, s in targets)
            + "\n\nRULES:\n" + "\n".join(f"- {x}" for x in rules)
            + "\n\nCANDIDATES:\n" + "\n".join(lines))

    data = broker.chat_json(broker.ASSISTANT_MODEL,
                            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                            options={"temperature": 0.7})
    return _assemble(data, targets, want_pairing)


def _assemble(data: dict, targets: list[tuple[str, str]], want_pairing: bool) -> dict:
    cat = state.catalog()
    target_set = set(targets)
    dates = {d for d, _ in targets}
    used: set[str] = set()
    items: list[dict] = []

    for p in (data.get("picks") or []):
        d, s, rid = p.get("date"), p.get("slot"), p.get("recipe_id")
        if (d, s) not in target_set:
            continue
        r = cat.get(rid)
        if not r or rid in used:
            continue
        used.add(rid)
        items.append(_item(r, date=d, slot=s, ptype="meal", why=p.get("why", "")))

    if want_pairing:
        for pr in (data.get("pairings") or []):
            d, kind = pr.get("date"), pr.get("kind")
            name = (pr.get("name") or pr.get("wine") or "").strip()
            why = (pr.get("why") or "").strip()
            if d not in dates or not name:
                continue
            if kind == "cocktail":
                r = _match_cocktail(name)
                if r:  # links to the real Bar recipe (shows in the plan + shopping list)
                    items.append(_item(r, date=d, slot="drink", ptype="cocktail", why=why))
                else:  # named but not in the library -> a plain titled drink card
                    items.append({"date": d, "slot": "drink", "ptype": "cocktail", "recipe_id": None,
                                  "title": f"🍸 {name}", "summary": None, "why": why})
            elif kind == "wine":
                items.append({"date": d, "slot": "drink", "ptype": "wine", "recipe_id": None,
                              "title": f"🍷 {name}", "summary": None, "why": why})

    items.sort(key=lambda x: (x["date"], _SLOT_ORDER.get(x["slot"], 9)))
    return {"items": items}


def swap(con, date: str, slot: str, ptype: str, exclude_ids: list[str], context: str = "") -> dict:
    """Re-roll a single slot. ``context`` (optional) is the dish being paired with, so a
    drink pairing can actually reference the meal ("to go with the Shoyu Ramen") rather
    than just the date; the 'why' is asked to tie the pick to that dish."""
    cat = state.catalog()
    dish = f" to go with {context}" if context else ""
    if ptype == "wine":
        sys = ('Return STRICT JSON {"wine":str,"why":str}. Suggest ONE wine style different from any excluded. '
               "The 'why' ties the wine to the dish in one short sentence.")
        user = (f"Suggest one wine pairing style (e.g. 'Pinot Noir (red)', 'Albariño (white)'){dish} for the "
                f"dinner on {date}. Avoid these styles: {', '.join(exclude_ids) or '(none)'}.")
        d = broker.chat_json(broker.ASSISTANT_MODEL,
                             [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                             options={"temperature": 0.9})
        wine = (d.get("wine") or "").strip() or "Sparkling rosé"
        return {"item": {"date": date, "slot": "drink", "ptype": "wine", "recipe_id": None,
                         "title": f"🍷 {wine}", "summary": None, "why": (d.get("why") or "").strip()}}

    exclude = set(exclude_ids)
    pool = [r for r in (_cocktail_pool() if ptype == "cocktail" else _pool(slot)) if r.id not in exclude]
    if not pool:
        return {"item": None}
    lst = "\n".join(_line(r) for r in pool[:40])
    label = "cocktail" if ptype == "cocktail" else slot
    sys = ('Return STRICT JSON {"recipe_id":str,"why":str}. Pick ONE id from the list; '
           "one-sentence 'why' tying the pick to the dish.")
    user = f"Pick one {label}{dish} for the dinner on {date}, different from before.\nCANDIDATES:\n{lst}"
    d = broker.chat_json(broker.ASSISTANT_MODEL,
                         [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                         options={"temperature": 0.9})
    r = cat.get(d.get("recipe_id")) or pool[0]
    out_slot = "drink" if ptype == "cocktail" else slot
    return {"item": _item(r, date=date, slot=out_slot, ptype=ptype, why=d.get("why", ""))}
