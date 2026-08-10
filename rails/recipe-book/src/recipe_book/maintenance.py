"""Scheduled housekeeping for the meal plan.

The plan is date-aware: every entry sits on an absolute date and simply falls into the
past as the calendar advances (no dates are ever rewritten — that once corrupted plans).
This module trims genuinely old entries so the table doesn't grow forever, while keeping
a long window of recent history browsable (via the planner's ‹ Prev) and available to the
AI planner for variety. It runs on a nightly schedule, never as a side effect of a read.
"""
from __future__ import annotations

from datetime import date, timedelta

from recipe_book import db, settings


def purge_old_plan_entries(con, override_days: int | None = None) -> int:
    """Delete dated plan entries older than the retention window (the admin-editable
    setting, else the config default). Never touches tray entries (empty/NULL date) or
    anything within the window. Returns rows removed."""
    days = settings.retention_days(con) if override_days is None else override_days
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    # Housekeeping across ALL owners — a global sweep of genuinely old dated entries.
    cur = con.execute(
        "DELETE FROM meal_plan_entries "
        "WHERE date IS NOT NULL AND date != '' AND date < ?",
        (cutoff,))
    con.commit()
    return cur.rowcount


def run_purge() -> int:
    """Open a connection, purge, close. Safe to call repeatedly (idempotent)."""
    con = db.connect()
    try:
        return purge_old_plan_entries(con)
    finally:
        con.close()
