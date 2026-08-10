"""Merge the owner's personalization (favorites / ratings / tags) onto the recipe
summaries and details the catalog returns. Small single-user tables, so a full
read per request is fine.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from recipe_book.db import OWNER_ID


def decorate(con: sqlite3.Connection, items: list[dict], owner: int = OWNER_ID) -> list[dict]:
    favs = {r["recipe_id"] for r in con.execute(
        "SELECT recipe_id FROM favorites WHERE owner_id=?", (owner,))}
    ratings = {r["recipe_id"]: {"stars": r["stars"], "note": r["note"]}
               for r in con.execute(
                   "SELECT recipe_id, stars, note FROM ratings WHERE owner_id=?", (owner,))}
    tags: dict[str, list] = defaultdict(list)
    for r in con.execute(
        "SELECT rt.recipe_id AS rid, t.id AS id, t.name AS name, t.color AS color "
        "FROM recipe_tags rt JOIN tags t ON t.id=rt.tag_id WHERE rt.owner_id=?", (owner,)):
        tags[r["rid"]].append({"id": r["id"], "name": r["name"], "color": r["color"]})

    for it in items:
        rid = it["id"]
        it["favorite"] = rid in favs
        it["rating"] = ratings.get(rid)
        it["tags"] = tags.get(rid, [])
    return items
