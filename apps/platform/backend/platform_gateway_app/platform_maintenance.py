"""Gateway-owned maintenance the central scheduler runs in-process.

Most scheduled tasks are HTTP calls to a rail's backend, but a few belong to the gateway itself
(its own DB). Those can't be fired over HTTP against a rail, so the scheduler special-cases
``rail == "platform"`` and calls one of these handlers directly with a DB session.

Today: pruning expired session rows. Deletion is otherwise only lazy (``auth.user_for_token``
drops a row only when that exact expired token is presented again), so sessions from closed
browsers / rotated cookies would linger forever without a periodic sweep.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session as OrmSession

from platform_gateway_app.models import SessionRow


def prune_expired_sessions(db: OrmSession) -> int:
    """Delete every session whose ``expires_at`` is in the past. Returns the row count.

    SQLite stores these datetimes naive (see ``auth.user_for_token``, which treats a naive value
    as UTC), so we compare against a naive-UTC ``now`` to match the stored format exactly."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    res = db.execute(delete(SessionRow).where(SessionRow.expires_at < now))
    db.commit()
    return int(res.rowcount or 0)


# task_id -> handler(db) -> row count / detail. The scheduler dispatches "platform" tasks here.
HANDLERS = {
    "prune-sessions": prune_expired_sessions,
}
