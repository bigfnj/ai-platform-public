"""SQLite-backed job + event store. Single file, thread-safe via a lock so the
web thread and the queue worker can share it."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    workflow   TEXT NOT NULL,
    status     TEXT NOT NULL,        -- queued | running | done | failed
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    dir        TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    error      TEXT,
    owner      TEXT                  -- X-Platform-User who created it; NULL = legacy (admin-only)
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    ts      REAL NOT NULL,
    stage   TEXT,
    model   TEXT,
    status  TEXT,
    message TEXT,
    elapsed REAL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL + busy timeout: the web process and per-job subprocesses share this DB.
            # WAL needs a shared-memory index the filesystem must support; some mounts
            # (Docker Desktop Windows bind mounts) can't back it, and a *second* opener
            # then dies with "unable to open database file" — which crashed every job.
            # Keep the DB on a real volume (see library.db_path); fall back to a rollback
            # journal if WAL can't be enabled so a bad mount degrades instead of crashing.
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                self._conn.execute("PRAGMA journal_mode=DELETE")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(_SCHEMA)
            # Additive migration: older DBs predate the owner column (per-user ownership).
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "owner" not in cols:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN owner TEXT")
            self._conn.commit()

    # --- jobs ---
    def create_job(self, job_id: str, name: str, workflow: str, dir: str,
                   params: dict[str, Any] | None = None, owner: str | None = None) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(id,name,workflow,status,created_at,updated_at,dir,params,owner) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, name, workflow, "queued", now, now, dir, json.dumps(params or {}), owner),
            )
            self._conn.commit()

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, error=? WHERE id=?",
                (status, time.time(), error, job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, workflow: str | None = None, status: str | None = None,
                  query: str | None = None, limit: int = 200,
                  restrict_owner: str | None = None) -> list[dict]:
        # restrict_owner: when set (a non-admin caller), return only that user's jobs — legacy
        # NULL-owner rows never match a real username, so they stay admin-only. Internal callers
        # and admins pass nothing => all jobs.
        sql = "SELECT * FROM jobs"
        clauses, args = [], []
        if workflow:
            clauses.append("workflow=?"); args.append(workflow)
        if status:
            clauses.append("status=?"); args.append(status)
        if query:
            clauses.append("name LIKE ?"); args.append(f"%{query}%")
        if restrict_owner is not None:
            clauses.append("owner=?"); args.append(restrict_owner)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # rowid (insertion order) breaks created_at ties so the order is deterministic.
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def next_queued(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                # rowid tiebreaker => a true FIFO even when created_at values tie.
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC, rowid ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # --- events ---
    def add_event(self, job_id: str, event) -> None:
        d = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(job_id,kind,ts,stage,model,status,message,elapsed) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (job_id, d.get("kind"), d.get("ts"), d.get("stage"), d.get("model"),
                 d.get("status"), d.get("message"), d.get("elapsed")),
            )
            self._conn.commit()

    def get_events(self, job_id: str, after_id: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id ASC",
                (job_id, after_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_job(self, job_id: str, name: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE jobs SET name=?, updated_at=? WHERE id=?",
                               (name, time.time(), job_id))
            self._conn.commit()

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE job_id=?", (job_id,))
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
