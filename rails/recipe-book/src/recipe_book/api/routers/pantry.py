"""Kitchen pantry: items tagged on_hand / staple / unavailable, and the
"what can I make" coverage match over meals. Owner-scoped (per platform user)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from recipe_book import db, state
from recipe_book.api import deps

router = APIRouter()


class ItemReq(BaseModel):
    name: str
    kind: str = "on_hand"  # on_hand | staple | unavailable


@router.get("/api/pantry")
def get_pantry(owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT id, name, kind FROM pantry_items WHERE owner_id=? ORDER BY kind, name",
            (owner_id,)).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        con.close()


@router.post("/api/pantry")
def add_pantry(req: ItemReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("INSERT OR IGNORE INTO pantry_items (owner_id, name, kind) VALUES (?,?,?)",
                    (owner_id, req.name.strip(), req.kind))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.delete("/api/pantry/{item_id}")
def del_pantry(item_id: int, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("DELETE FROM pantry_items WHERE owner_id=? AND id=?", (owner_id, item_id))
        con.commit()
        return {"deleted": item_id}
    finally:
        con.close()


@router.get("/api/pantry/match")
def pantry_match(kind: str = "meal", limit: int = 40,
                 owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        inv = db.inventory_lists(con, "pantry_items", owner=owner_id)
    finally:
        con.close()
    results = state.catalog().match_pantry(
        inv["on_hand"], inv["staple"], inv["unavailable"], kind=kind, limit=limit)
    return {"results": results, "inventory": inv}
