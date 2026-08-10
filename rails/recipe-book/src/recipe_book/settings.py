"""Effective, runtime-editable app settings.

A value saved in the ``app_settings`` table (via the admin Settings panel) overrides the
``config`` default (which itself comes from an env var). Absent/invalid stored value falls
back to the default, so the app always has a sane number.
"""
from __future__ import annotations

from recipe_book import config, db

RETENTION_KEY = "plan_retention_days"
RECENCY_KEY = "plan_recency_days"

# Accepted ranges for the admin editor / API validation.
RETENTION_RANGE = (7, 3650)   # 1 week .. ~10 years
RECENCY_RANGE = (0, 3650)     # 0 disables the "avoid repeats" filter entirely


def _get_int(con, key: str, default: int) -> int:
    v = db.get_setting(con, key)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def retention_days(con) -> int:
    return _get_int(con, RETENTION_KEY, config.PLAN_RETENTION_DAYS)


def recency_days(con) -> int:
    return _get_int(con, RECENCY_KEY, config.PLAN_RECENCY_DAYS)


def set_plan(con, *, retention: int | None = None, recency: int | None = None) -> None:
    if retention is not None:
        db.set_setting(con, RETENTION_KEY, str(retention))
    if recency is not None:
        db.set_setting(con, RECENCY_KEY, str(recency))
