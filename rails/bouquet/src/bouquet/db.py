"""SQLite storage for saved analyses (single-tenant, owner-only).

One shared library of reports — access is gated by the platform
entitlement, so there is no per-row owner column. The uploaded photo for
each analysis is filed under ``uploads/`` and referenced by name.

``check_same_thread=False`` because FastAPI runs sync endpoints in a threadpool; a
single worker + WAL + a busy timeout keeps the single-writer model safe.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from bouquet import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    mode          TEXT NOT NULL,              -- 'analysis' | 'florist'
    title         TEXT NOT NULL,
    image_file    TEXT,                       -- filename under uploads/
    model         TEXT,                       -- vision+chat models actually used
    inventory     TEXT NOT NULL,              -- JSON: the edited flower inventory
    matched       TEXT NOT NULL,              -- JSON: [slug, ...] profiled hits
    unprofiled    TEXT NOT NULL,              -- JSON: [name, ...] no-profile
    report_md     TEXT NOT NULL,
    guidance      TEXT NOT NULL DEFAULT '',   -- the florist's free-text direction
    vision_draft  TEXT                        -- JSON: the raw vision inventory before edits
);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Additive migrations for a pre-existing analyses table (the live data volume
    predates the two-step redesign). Adding a column is safe + idempotent."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(analyses)")}
    if "guidance" not in cols:
        con.execute("ALTER TABLE analyses ADD COLUMN guidance TEXT NOT NULL DEFAULT ''")
    if "vision_draft" not in cols:
        con.execute("ALTER TABLE analyses ADD COLUMN vision_draft TEXT")


def init() -> None:
    con = connect()
    try:
        con.executescript(_SCHEMA)
        _migrate(con)
        con.commit()
    finally:
        con.close()


def insert(*, mode: str, title: str, image_file: str | None, model: str,
           inventory: dict, matched: list[str], unprofiled: list[str],
           report_md: str, guidance: str = "", vision_draft: dict | None = None) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO analyses (created_at, mode, title, image_file, model, "
            "inventory, matched, unprofiled, report_md, guidance, vision_draft) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), mode, title,
             image_file, model, json.dumps(inventory), json.dumps(matched),
             json.dumps(unprofiled), report_md, guidance or "",
             json.dumps(vision_draft) if vision_draft is not None else None),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _row_to_dict(row: sqlite3.Row, *, full: bool) -> dict:
    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "mode": row["mode"],
        "title": row["title"],
        "image_file": row["image_file"],
        "image_url": f"/bouquet/api/analyses/{row['id']}/image" if row["image_file"] else None,
        "model": row["model"],
        "matched": json.loads(row["matched"]),
        "unprofiled": json.loads(row["unprofiled"]),
    }
    if full:
        keys = row.keys()
        out["inventory"] = json.loads(row["inventory"])
        out["report_md"] = row["report_md"]
        out["guidance"] = row["guidance"] if "guidance" in keys else ""
        raw_draft = row["vision_draft"] if "vision_draft" in keys else None
        out["vision_draft"] = json.loads(raw_draft) if raw_draft else None
    return out


def list_() -> list[dict]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT * FROM analyses ORDER BY id DESC").fetchall()
        return [_row_to_dict(r, full=False) for r in rows]
    finally:
        con.close()


def get(analysis_id: int) -> dict | None:
    con = connect()
    try:
        row = con.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        return _row_to_dict(row, full=True) if row else None
    finally:
        con.close()


def iter_labeled() -> list[dict]:
    """Every analysis that captured a vision draft, as {inventory, vision_draft} — the
    labeled (draft -> corrected) pairs the vision eval harness scores offline."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT inventory, vision_draft FROM analyses "
            "WHERE vision_draft IS NOT NULL").fetchall()
        return [{"inventory": json.loads(r["inventory"]),
                 "vision_draft": json.loads(r["vision_draft"])} for r in rows]
    finally:
        con.close()


def all_image_names() -> set[str]:
    """Every filename referenced by a saved analysis — the keep-set the cleanup
    sweep checks a stray ``uploads/<file>`` against."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT image_file FROM analyses WHERE image_file IS NOT NULL").fetchall()
        return {r["image_file"] for r in rows if r["image_file"]}
    finally:
        con.close()


def image_name(analysis_id: int) -> str | None:
    con = connect()
    try:
        row = con.execute("SELECT image_file FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        return row["image_file"] if row else None
    finally:
        con.close()


def delete(analysis_id: int) -> bool:
    con = connect()
    try:
        cur = con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()
