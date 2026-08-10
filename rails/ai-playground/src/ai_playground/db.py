"""SQLite storage for corpora + their embedded chunks.

One connection per request, ``check_same_thread=False`` + WAL + a busy timeout, so
FastAPI's threadpool can run blocking DB work safely (single writer). Seed corpora are
shared (owner NULL, visible to everyone); uploaded corpora are owner-scoped by the
gateway-verified ``X-Platform-User``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ai_playground import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT NOT NULL,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'user',   -- 'seed' | 'user'
  owner       TEXT,                            -- X-Platform-User (NULL = shared/seed)
  embed_model TEXT,
  created_at  TEXT,
  UNIQUE(slug, owner)
);
CREATE TABLE IF NOT EXISTS chunk (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  corpus_id INTEGER NOT NULL,
  source    TEXT,
  text      TEXT NOT NULL,
  vector    TEXT NOT NULL,                     -- JSON list[float], L2-normalized
  FOREIGN KEY(corpus_id) REFERENCES corpus(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_corpus ON chunk(corpus_id);

-- Embedding Lab (bench) demo ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bench_model (       -- user/admin-added registry entries
  id         TEXT PRIMARY KEY,                 -- model id (shadows a seed of the same id)
  spec       TEXT NOT NULL,                    -- JSON model spec
  owner      TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS bench_queryset (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  slug       TEXT NOT NULL,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'user',     -- 'seed' | 'user'
  owner      TEXT,
  created_at TEXT,
  UNIQUE(slug, owner)
);
CREATE TABLE IF NOT EXISTS bench_query (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  queryset_id INTEGER NOT NULL,
  q           TEXT NOT NULL,
  targets     TEXT NOT NULL,                   -- JSON list[str] of target source names
  FOREIGN KEY(queryset_id) REFERENCES bench_queryset(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bq_set ON bench_query(queryset_id);
CREATE TABLE IF NOT EXISTS bench_run (         -- run history
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  owner       TEXT,
  corpus_name TEXT,
  queryset    TEXT,
  k           INTEGER,
  created_at  TEXT,
  results     TEXT NOT NULL                    -- JSON: [{config, metrics|error}]
);
CREATE INDEX IF NOT EXISTS idx_run_owner ON bench_run(owner, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def corpus_exists(con: sqlite3.Connection, slug: str, owner: str | None) -> bool:
    # `owner IS ?` is null-safe: with a NULL param it means `owner IS NULL`, else `= owner`.
    return con.execute("SELECT 1 FROM corpus WHERE slug=? AND owner IS ?",
                       (slug, owner)).fetchone() is not None


def add_corpus(con: sqlite3.Connection, *, slug: str, name: str, kind: str,
               owner: str | None, embed_model: str) -> int:
    cur = con.execute(
        "INSERT INTO corpus(slug, name, kind, owner, embed_model, created_at) VALUES (?,?,?,?,?,?)",
        (slug, name, kind, owner, embed_model, _now()))
    con.commit()
    return int(cur.lastrowid)


def add_chunks(con: sqlite3.Connection, corpus_id: int, chunks: list[dict],
               vectors: list[list[float]]) -> None:
    con.executemany(
        "INSERT INTO chunk(corpus_id, source, text, vector) VALUES (?,?,?,?)",
        [(corpus_id, c["source"], c["text"], json.dumps(v)) for c, v in zip(chunks, vectors)])
    con.commit()


def list_corpora(con: sqlite3.Connection, owner: str | None) -> list[dict]:
    """Seed corpora (shared) + this owner's uploads; seeds first, then newest uploads."""
    rows = con.execute(
        "SELECT c.id, c.slug, c.name, c.kind, c.owner, "
        "  (SELECT COUNT(*) FROM chunk WHERE corpus_id=c.id) AS chunks "
        "FROM corpus c WHERE c.kind='seed' OR c.owner IS ? "
        "ORDER BY (c.kind='seed') DESC, c.id DESC", (owner,)).fetchall()
    return [dict(r) for r in rows]


def get_corpus(con: sqlite3.Connection, corpus_id: int) -> dict | None:
    row = con.execute("SELECT * FROM corpus WHERE id=?", (corpus_id,)).fetchone()
    return dict(row) if row else None


def get_chunks(con: sqlite3.Connection, corpus_id: int) -> tuple[list[dict], list[list[float]]]:
    rows = con.execute("SELECT source, text, vector FROM chunk WHERE corpus_id=? ORDER BY id",
                       (corpus_id,)).fetchall()
    chunks = [{"source": r["source"], "text": r["text"]} for r in rows]
    vectors = [json.loads(r["vector"]) for r in rows]
    return chunks, vectors


def delete_corpus(con: sqlite3.Connection, corpus_id: int) -> None:
    con.execute("DELETE FROM chunk WHERE corpus_id=?", (corpus_id,))
    con.execute("DELETE FROM corpus WHERE id=?", (corpus_id,))
    con.commit()


# --- Embedding Lab: registry models ---------------------------------------------------------
def list_bench_models(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("SELECT spec FROM bench_model").fetchall()
    return [json.loads(r["spec"]) for r in rows]


def upsert_bench_model(con: sqlite3.Connection, spec: dict, owner: str | None) -> None:
    con.execute(
        "INSERT INTO bench_model(id, spec, owner, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET spec=excluded.spec",
        (spec["id"], json.dumps(spec), owner, _now()))
    con.commit()


def delete_bench_model(con: sqlite3.Connection, model_id: str) -> None:
    con.execute("DELETE FROM bench_model WHERE id=?", (model_id,))
    con.commit()


# --- Embedding Lab: query sets --------------------------------------------------------------
def queryset_exists(con: sqlite3.Connection, slug: str, owner: str | None) -> bool:
    return con.execute("SELECT 1 FROM bench_queryset WHERE slug=? AND owner IS ?",
                       (slug, owner)).fetchone() is not None


def add_queryset(con: sqlite3.Connection, *, slug: str, name: str, kind: str,
                 owner: str | None, queries: list[dict]) -> int:
    cur = con.execute(
        "INSERT INTO bench_queryset(slug, name, kind, owner, created_at) VALUES (?,?,?,?,?)",
        (slug, name, kind, owner, _now()))
    qsid = int(cur.lastrowid)
    con.executemany(
        "INSERT INTO bench_query(queryset_id, q, targets) VALUES (?,?,?)",
        [(qsid, q["q"], json.dumps(q.get("targets") or [])) for q in queries])
    con.commit()
    return qsid


def list_querysets(con: sqlite3.Connection, owner: str | None) -> list[dict]:
    rows = con.execute(
        "SELECT s.id, s.slug, s.name, s.kind, s.owner, "
        "  (SELECT COUNT(*) FROM bench_query WHERE queryset_id=s.id) AS queries "
        "FROM bench_queryset s WHERE s.kind='seed' OR s.owner IS ? "
        "ORDER BY (s.kind='seed') DESC, s.id DESC", (owner,)).fetchall()
    return [dict(r) for r in rows]


def get_queryset(con: sqlite3.Connection, qsid: int) -> dict | None:
    row = con.execute("SELECT * FROM bench_queryset WHERE id=?", (qsid,)).fetchone()
    return dict(row) if row else None


def get_queries(con: sqlite3.Connection, qsid: int) -> list[dict]:
    rows = con.execute("SELECT q, targets FROM bench_query WHERE queryset_id=? ORDER BY id",
                       (qsid,)).fetchall()
    return [{"q": r["q"], "targets": json.loads(r["targets"])} for r in rows]


def delete_queryset(con: sqlite3.Connection, qsid: int) -> None:
    con.execute("DELETE FROM bench_query WHERE queryset_id=?", (qsid,))
    con.execute("DELETE FROM bench_queryset WHERE id=?", (qsid,))
    con.commit()


# --- Embedding Lab: run history -------------------------------------------------------------
def add_run(con: sqlite3.Connection, *, owner: str | None, corpus_name: str, queryset: str,
            k: int, results: list[dict]) -> int:
    cur = con.execute(
        "INSERT INTO bench_run(owner, corpus_name, queryset, k, created_at, results) "
        "VALUES (?,?,?,?,?,?)",
        (owner, corpus_name, queryset, k, _now(), json.dumps(results)))
    con.commit()
    return int(cur.lastrowid)


def list_runs(con: sqlite3.Connection, owner: str | None, limit: int = 25) -> list[dict]:
    rows = con.execute(
        "SELECT id, corpus_name, queryset, k, created_at FROM bench_run "
        "WHERE owner IS ? OR owner IS NULL ORDER BY id DESC LIMIT ?", (owner, limit)).fetchall()
    return [dict(r) for r in rows]


def get_run(con: sqlite3.Connection, run_id: int) -> dict | None:
    row = con.execute("SELECT * FROM bench_run WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["results"] = json.loads(d["results"])
    return d
