"""Favorites, star ratings, and tags — all owner-scoped (per platform user)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from recipe_book import db
from recipe_book.api import deps

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.post("/api/favorites/{recipe_id}")
def toggle_favorite(recipe_id: str, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        row = con.execute(
            "SELECT id FROM favorites WHERE owner_id=? AND recipe_id=?",
            (owner_id, recipe_id)).fetchone()
        if row:
            con.execute("DELETE FROM favorites WHERE id=?", (row["id"],))
            fav = False
        else:
            con.execute(
                "INSERT INTO favorites (owner_id, recipe_id, created_at) VALUES (?,?,?)",
                (owner_id, recipe_id, _now()))
            fav = True
        con.commit()
        return {"recipe_id": recipe_id, "favorite": fav}
    finally:
        con.close()


class RatingReq(BaseModel):
    stars: int
    note: str = ""


@router.put("/api/ratings/{recipe_id}")
def set_rating(recipe_id: str, req: RatingReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    stars = max(0, min(5, req.stars))
    con = db.connect()
    try:
        if stars == 0:  # 0 clears the rating
            con.execute("DELETE FROM ratings WHERE owner_id=? AND recipe_id=?",
                        (owner_id, recipe_id))
        else:
            con.execute(
                "INSERT INTO ratings (owner_id, recipe_id, stars, note, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(owner_id, recipe_id) DO UPDATE SET "
                "stars=excluded.stars, note=excluded.note, updated_at=excluded.updated_at",
                (owner_id, recipe_id, stars, req.note, _now()))
        con.commit()
        return {"recipe_id": recipe_id, "stars": stars, "note": req.note}
    finally:
        con.close()


class TagReq(BaseModel):
    name: str
    color: str = "accent"


@router.get("/api/tags")
def list_tags(owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT id, name, color FROM tags WHERE owner_id=? ORDER BY name",
            (owner_id,)).fetchall()
        return {"tags": [dict(r) for r in rows]}
    finally:
        con.close()


@router.post("/api/tags")
def create_tag(req: TagReq, owner_id: int = Depends(deps.owner_id)) -> dict:
    name = req.name.strip()
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO tags (owner_id, name, color) VALUES (?,?,?) "
            "ON CONFLICT(owner_id, name) DO UPDATE SET color=excluded.color",
            (owner_id, name, req.color))
        con.commit()
        row = con.execute("SELECT id, name, color FROM tags WHERE owner_id=? AND name=?",
                          (owner_id, name)).fetchone()
        return dict(row)
    finally:
        con.close()


@router.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute("DELETE FROM recipe_tags WHERE owner_id=? AND tag_id=?", (owner_id, tag_id))
        con.execute("DELETE FROM tags WHERE owner_id=? AND id=?", (owner_id, tag_id))
        con.commit()
        return {"deleted": tag_id}
    finally:
        con.close()


class RecipeTagReq(BaseModel):
    tag_id: int


@router.post("/api/recipes/{recipe_id}/tags")
def add_recipe_tag(recipe_id: str, req: RecipeTagReq,
                   owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute(
            "INSERT OR IGNORE INTO recipe_tags (owner_id, recipe_id, tag_id) VALUES (?,?,?)",
            (owner_id, recipe_id, req.tag_id))
        con.commit()
        return {"recipe_id": recipe_id, "tag_id": req.tag_id}
    finally:
        con.close()


@router.delete("/api/recipes/{recipe_id}/tags/{tag_id}")
def remove_recipe_tag(recipe_id: str, tag_id: int,
                      owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        con.execute(
            "DELETE FROM recipe_tags WHERE owner_id=? AND recipe_id=? AND tag_id=?",
            (owner_id, recipe_id, tag_id))
        con.commit()
        return {"removed": True}
    finally:
        con.close()
