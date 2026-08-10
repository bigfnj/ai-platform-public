"""Cleanup sweep for abandoned uploads (fired by the platform's central scheduler).

Normal use self-cleans: a successful ``generate`` deletes its own pending upload
and only the 720px derivative persists. This sweep mops up the leftovers — pending
uploads from sessions that were identified but never generated (the florist closed
the tab), and, defensively, any stray ``uploads/<file>`` with no DB row.

``sweep()`` is invoked by the gateway's central scheduler via
``POST /api/maintenance/sweep`` (weekly, Sunday 03:00 local by default). The old
in-process ``cleanup_loop`` was retired in favour of that single console — the same
central-scheduler migration every rail follows. ``seconds_until_next_run`` is kept for reference /
tests. The compose service sets ``TZ=America/Los_Angeles`` so the process clock is local.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from bouquet import config, db

log = logging.getLogger("bouquet.maintenance")


def _older_than(path: Path, max_age_seconds: float, now: float) -> bool:
    try:
        return (now - path.stat().st_mtime) > max_age_seconds
    except OSError:
        return False


def sweep(now: float | None = None) -> dict:
    """Delete abandoned pending uploads and unreferenced stray upload files, both
    only once older than ``ORPHAN_MAX_AGE_HOURS`` (the age guard keeps the sweep from
    racing an in-flight generate). Returns the counts deleted."""
    now = time.time() if now is None else now
    max_age = config.ORPHAN_MAX_AGE_HOURS * 3600
    pending_removed = 0
    orphan_removed = 0

    pending = config.pending_dir()
    if pending.is_dir():
        for f in pending.iterdir():
            if f.is_file() and _older_than(f, max_age, now):
                try:
                    f.unlink()
                    pending_removed += 1
                except OSError:
                    log.warning("could not delete pending %s", f)

    keep = db.all_image_names()
    uploads = config.UPLOADS_DIR
    if uploads.is_dir():
        for f in uploads.iterdir():
            if not f.is_file():
                continue  # skip the pending/ subdir
            if f.name not in keep and _older_than(f, max_age, now):
                try:
                    f.unlink()
                    orphan_removed += 1
                except OSError:
                    log.warning("could not delete orphan %s", f)

    return {"pending_removed": pending_removed, "orphan_removed": orphan_removed}


def seconds_until_next_run(now: datetime) -> float:
    """Seconds from ``now`` (local) until the next ``CLEANUP_DOW`` at ``CLEANUP_HOUR``."""
    days_ahead = (config.CLEANUP_DOW - now.weekday()) % 7
    base = now.replace(hour=config.CLEANUP_HOUR, minute=0, second=0, microsecond=0)
    candidate = base + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return (candidate - now).total_seconds()


