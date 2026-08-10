"""Bar inventory (bottles / mixers you have) and "what can I pour right now" —
the beverage analog of pantry-match, ranked by coverage over cocktails. Owner-scoped."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from recipe_book import db, state
from recipe_book.api import deps

router = APIRouter()


class ItemReq(BaseModel):
    name: str
    kind: str = "on_hand"  # on_hand | staple | unavailable (staples: ice, citrus, simple syrup…)


@router.get("/api/bar")
def get_bar(owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT id, name, kind FROM bar_items WHERE owner_id=? ORDER BY kind, name",
            (owner_id,)).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        con.close()


@router.post("/api/bar")
def add_bar(req: ItemReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("INSERT OR IGNORE INTO bar_items (owner_id, name, kind) VALUES (?,?,?)",
                    (owner_id, req.name.strip(), req.kind))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.delete("/api/bar/{item_id}")
def del_bar(item_id: int, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("DELETE FROM bar_items WHERE owner_id=? AND id=?", (owner_id, item_id))
        con.commit()
        return {"deleted": item_id}
    finally:
        con.close()


@router.get("/api/bar/pour")
def what_can_i_pour(limit: int = 40, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        inv = db.inventory_lists(con, "bar_items", owner=owner_id)
    finally:
        con.close()
    results = state.catalog().match_pantry(
        inv["on_hand"], inv["staple"], inv["unavailable"], kind="beverage", limit=limit)
    return {"results": results, "inventory": inv}
