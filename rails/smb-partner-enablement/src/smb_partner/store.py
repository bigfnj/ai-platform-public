"""SQLite-backed chunk index with in-memory vectors.

Vectors live in the row as a float32 blob and are loaded into one normalized matrix at
startup. That keeps the whole retrieval path dependency-free (no vector DB), and at SME-corpus
scale the matrix is a few megabytes.

Concurrency note: one uvicorn worker, so SQLite's single-writer model is fine. The in-memory
matrix is rebuilt wholesale after any ingest rather than patched, because a partial rebuild
that silently desynchronises the matrix from the rows is far worse than a second of work.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

import numpy as np

from smb_partner import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    name        TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    origin      TEXT NOT NULL DEFAULT 'seed',   -- 'seed' | 'upload'
    ingested_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL,
    vec        BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_collection ON chunks(collection);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_lock = threading.Lock()
_chunks: list[dict] = []
_matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(_SCHEMA)
    reload_matrix()


def get_meta(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def replace_collection(name: str, label: str, origin: str, rows: list[dict],
                       vectors: np.ndarray) -> int:
    """Atomically swap a collection's chunks for a freshly embedded set."""
    if len(rows) != len(vectors):
        raise ValueError("rows and vectors must be the same length")
    with connect() as conn:
        conn.execute("DELETE FROM chunks WHERE collection = ?", (name,))
        conn.executemany(
            "INSERT INTO chunks(collection, source, title, text, vec) VALUES(?, ?, ?, ?, ?)",
            [(name, r["source"], r.get("title", ""), r["text"],
              vectors[i].astype(np.float32).tobytes()) for i, r in enumerate(rows)],
        )
        conn.execute(
            "INSERT INTO collections(name, label, origin, ingested_at) "
            "VALUES(?, ?, ?, datetime('now')) ON CONFLICT(name) DO UPDATE SET "
            "label = excluded.label, origin = excluded.origin, ingested_at = excluded.ingested_at",
            (name, label, origin),
        )
    reload_matrix()
    return len(rows)


def delete_collection(name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM chunks WHERE collection = ?", (name,))
        conn.execute("DELETE FROM collections WHERE name = ?", (name,))
    reload_matrix()


def reload_matrix() -> None:
    """Rebuild the in-memory chunk list + normalized vector matrix from SQLite."""
    global _chunks, _matrix
    with connect() as conn:
        rows = conn.execute(
            "SELECT collection, source, title, text, vec FROM chunks "
            "WHERE vec IS NOT NULL ORDER BY id"
        ).fetchall()
    chunks = [{"collection": r["collection"], "source": r["source"],
               "title": r["title"], "text": r["text"]} for r in rows]
    if rows:
        stacked = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    else:
        stacked = np.zeros((0, 0), dtype=np.float32)
    with _lock:
        _chunks, _matrix = chunks, stacked


def snapshot() -> tuple[list[dict], np.ndarray]:
    """The current corpus for a retrieval pass. Returned under the lock so a concurrent
    ingest can never hand back a chunk list and matrix of different lengths."""
    with _lock:
        return _chunks, _matrix


def collections() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.name, c.label, c.origin, c.ingested_at, "
            "  (SELECT COUNT(*) FROM chunks k WHERE k.collection = c.name) AS chunks "
            "FROM collections c ORDER BY c.name"
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    chunks, matrix = snapshot()
    return {
        "chunks": len(chunks),
        "dims": int(matrix.shape[1]) if matrix.size else 0,
        "collections": len(collections()),
    }
