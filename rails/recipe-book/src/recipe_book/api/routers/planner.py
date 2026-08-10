"""Meal planner (day + slot, incl. a drinks slot) and the aggregated,
pantry-aware shopping list with persistent checkboxes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recipe_book import broker, config, db, gtasks, planner_ai, state
from recipe_book.api import deps

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/api/planner")
def get_plan(start: str | None = None, end: str | None = None, tray: bool = False,
             owner_id: int = Depends(deps.owner_id)) -> dict:
    """Planned entries in a date range, or (tray=true) the unassigned "tray" —
    entries with an empty date, waiting to be dragged onto a day.

    NB: reads must never mutate stored dates. An earlier "rolling meal plan" feature
    rewrote every entry's date on each GET, which corrupted plans mid-drag (a reload
    that momentarily saw a future-only plan shifted the whole thing back a week, and
    re-fired on every subsequent reload). If a roll-into-this-week affordance is wanted
    again, make it an explicit, idempotent user action — not a side effect of reading."""
    con = db.connect()
    try:
        if tray:
            sql = ("SELECT * FROM meal_plan_entries WHERE owner_id=? "
                   "AND (date='' OR date IS NULL) ORDER BY created_at")
            params: list = [owner_id]
        else:
            sql = "SELECT * FROM meal_plan_entries WHERE owner_id=? AND date!=''"
            params = [owner_id]
            if start:
                sql += " AND date>=?"; params.append(start)
            if end:
                sql += " AND date<=?"; params.append(end)
            sql += " ORDER BY date, slot"
        rows = [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
    cat = state.catalog()
    for r in rows:
        rec = cat.get(r["recipe_id"]) if r["recipe_id"] else None
        r["recipe"] = rec.summary() if rec else None
    return {"entries": rows}


class PlanReq(BaseModel):
    date: str
    slot: str
    recipe_id: str | None = None
    title: str = ""
    servings: int = 2


@router.post("/api/planner")
def add_plan(req: PlanReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        cur = con.execute(
            "INSERT INTO meal_plan_entries "
            "(owner_id, date, slot, recipe_id, title, servings, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (owner_id, req.date, req.slot, req.recipe_id, req.title, req.servings, _now()))
        con.commit()
        return {"id": cur.lastrowid}
    finally:
        con.close()


class PlanPatch(BaseModel):
    date: str | None = None   # "" moves the entry back to the unassigned tray
    slot: str | None = None


@router.patch("/api/planner/{entry_id}")
def update_plan(entry_id: int, req: PlanPatch, owner_id: int = Depends(deps.owner_id)) -> dict:
    """Reassign an entry's day (drag-and-drop) and/or slot."""
    sets: list[str] = []
    params: list = []
    if req.date is not None:
        sets.append("date=?"); params.append(req.date)
    if req.slot is not None:
        sets.append("slot=?"); params.append(req.slot)
    if not sets:
        return {"updated": entry_id}
    params += [owner_id, entry_id]
    con = db.connect()
    try:
        con.execute(f"UPDATE meal_plan_entries SET {', '.join(sets)} WHERE owner_id=? AND id=?", params)
        con.commit()
        return {"updated": entry_id}
    finally:
        con.close()


@router.delete("/api/planner/{entry_id}")
def del_plan(entry_id: int, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("DELETE FROM meal_plan_entries WHERE owner_id=? AND id=?",
                    (owner_id, entry_id))
        con.commit()
        return {"deleted": entry_id}
    finally:
        con.close()


class ProposeReq(BaseModel):
    dates: list[str]
    slots: list[str]
    optimize_shopping: bool = False
    drink_pairing: bool = False


@router.post("/api/planner/propose")
def propose_plan(req: ProposeReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    """AI builds a day-by-day proposal from the library (uncommitted; the UI accepts
    /swaps/skips each slot). Skips slots already filled in the given dates."""
    con = db.connect()
    try:
        return planner_ai.propose(con, req.dates, req.slots, req.optimize_shopping,
                                  req.drink_pairing, owner_id)
    except broker.BrokerError as exc:
        raise HTTPException(status_code=503, detail=f"assistant unavailable: {exc}")
    finally:
        con.close()


class SwapReq(BaseModel):
    date: str
    slot: str = "dinner"
    ptype: str = "meal"          # meal | cocktail | wine
    exclude_ids: list[str] = []


@router.post("/api/planner/propose/swap")
def swap_plan(req: SwapReq) -> dict:
    """Re-roll a single proposed slot with a different pick."""
    con = db.connect()
    try:
        return planner_ai.swap(con, req.date, req.slot, req.ptype, req.exclude_ids)
    except broker.BrokerError as exc:
        raise HTTPException(status_code=503, detail=f"assistant unavailable: {exc}")
    finally:
        con.close()


class PairReq(BaseModel):
    date: str
    ptype: str = "cocktail"       # cocktail | wine
    exclude_ids: list[str] = []   # drinks already tried on this day, so a re-roll varies


@router.post("/api/planner/pair")
def pair_drink(req: PairReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    """Manual beverage pairing: suggest a drink for the dinner on a given day and add it to
    that day's drink slot. Reuses the AI single-slot swap — a real Bar cocktail when the model
    names one, otherwise a titled wine/cocktail card — then commits the pick."""
    ptype = req.ptype if req.ptype in ("cocktail", "wine") else "cocktail"
    con = db.connect()
    try:
        # what dinner(s) are on that day? — so the pairing is chosen for the dish, not just the date.
        cat = state.catalog()
        dishes = []
        for r in con.execute("SELECT recipe_id, title FROM meal_plan_entries "
                             "WHERE owner_id=? AND date=? AND slot='dinner'", (owner_id, req.date)):
            rec = cat.get(r["recipe_id"]) if r["recipe_id"] else None
            name = (rec.title if rec else None) or r["title"]
            if name:
                dishes.append(name)
        item = planner_ai.swap(con, req.date, "dinner", ptype, req.exclude_ids,
                               context=" and ".join(dishes)).get("item")
        if not item:
            raise HTTPException(status_code=404,
                                detail="No cocktail in your Bar to pair — add bottles, or try a wine.")
        title = "" if item.get("recipe_id") else item.get("title", "")
        cur = con.execute(
            "INSERT INTO meal_plan_entries "
            "(owner_id, date, slot, recipe_id, title, servings, created_at) VALUES (?,?,?,?,?,?,?)",
            (owner_id, item["date"], "drink", item.get("recipe_id"), title, 2, _now()))
        con.commit()
        eid = cur.lastrowid
    except broker.BrokerError as exc:
        raise HTTPException(status_code=503, detail=f"assistant unavailable: {exc}")
    finally:
        con.close()
    rec = cat.get(item["recipe_id"]) if item.get("recipe_id") else None
    entry = {"id": eid, "date": item["date"], "slot": "drink", "recipe_id": item.get("recipe_id"),
             "title": title, "servings": 2, "recipe": rec.summary() if rec else None,
             "why": item.get("why", "")}
    return {"entry": entry}


def _build_shopping(owner_id: int, ids: str, start: str | None, end: str | None):
    """Aggregate the shopping needs of either an explicit recipe-id list or the planned
    recipes in a date range, drop what the pantry/bar covers. Returns (items, checks, n).

    Beverages also subtract the bar cart (with strict matching, so a branded call-out survives
    a generic spirit on hand); meals just use the pantry.
    """
    con = db.connect()
    try:
        rec_ids = [x for x in ids.split(",") if x]
        if not rec_ids:
            sql = ("SELECT recipe_id FROM meal_plan_entries "
                   "WHERE owner_id=? AND recipe_id IS NOT NULL")
            params: list = [owner_id]
            if start:
                sql += " AND date>=?"; params.append(start)
            if end:
                sql += " AND date<=?"; params.append(end)
            rec_ids = [r["recipe_id"] for r in con.execute(sql, params)]
        pantry = db.inventory_lists(con, "pantry_items", owner=owner_id)
        bar = db.inventory_lists(con, "bar_items", owner=owner_id)
        checks = {r["item_key"]: bool(r["checked"]) for r in con.execute(
            "SELECT item_key, checked FROM shopping_checks WHERE owner_id=?", (owner_id,))}
    finally:
        con.close()
    items = state.catalog().shopping_list(
        rec_ids, pantry["on_hand"], pantry["staple"], pantry["unavailable"],
        bar_on_hand=bar["on_hand"], bar_staples=bar["staple"], bar_unavailable=bar["unavailable"])
    return items, checks, len(rec_ids)


@router.get("/api/planner/shopping")
def planner_shopping(start: str | None = None, end: str | None = None, ids: str = "",
                     owner_id: int = Depends(deps.owner_id)) -> dict:
    items, checks, n = _build_shopping(owner_id, ids, start, end)
    for it in items:
        it["checked"] = checks.get(it["key"], False)
    return {"items": items, "recipe_count": n}


class SendReq(BaseModel):
    ids: str = ""
    start: str | None = None
    end: str | None = None
    include_checked: bool = False  # by default only send what's still UNchecked (yet to buy)


@router.post("/api/planner/shopping/send")
def shopping_send(req: SendReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    """'Send to Phone': push the current shopping list into THIS user's Google Tasks. Sends
    the still-unchecked items (what you actually need to buy) unless ``include_checked``."""
    if not gtasks.app_configured():
        raise HTTPException(status_code=501, detail="Google Tasks isn't configured on this server.")
    con = db.connect()
    try:
        tok = db.gtasks_get(con, owner_id)
    finally:
        con.close()
    if not tok:
        raise HTTPException(status_code=409,
                            detail="Connect your Google account first (Connect Google Tasks).")
    items, checks, _ = _build_shopping(owner_id, req.ids, req.start, req.end)
    labels = [it["label"] for it in items
              if req.include_checked or not checks.get(it["key"], False)]
    list_title = tok["list_title"] or config.GTASKS_LIST_TITLE
    if not labels:
        return {"sent": 0, "list": list_title,
                "detail": "Nothing to send — everything is checked off."}
    try:
        res = gtasks.push(tok["refresh_token"], labels, list_title)
    except gtasks.GTasksError as exc:
        msg = str(exc)
        if "invalid_grant" in msg:  # revoked/expired → forget it so the UI re-prompts connect
            con = db.connect()
            try:
                db.gtasks_delete(con, owner_id)
            finally:
                con.close()
            raise HTTPException(status_code=409,
                                detail="Your Google connection expired — please reconnect.")
        raise HTTPException(status_code=502, detail=f"Google Tasks: {msg}")
    return {"sent": res["sent"], "list": res["list_title"]}


class CheckReq(BaseModel):
    item_key: str
    checked: bool


@router.post("/api/planner/shopping/check")
def shopping_check(req: CheckReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO shopping_checks (owner_id, item_key, checked) VALUES (?,?,?) "
            "ON CONFLICT(owner_id, item_key) DO UPDATE SET checked=excluded.checked",
            (owner_id, req.item_key, 1 if req.checked else 0))
        con.commit()
        return {"item_key": req.item_key, "checked": req.checked}
    finally:
        con.close()
