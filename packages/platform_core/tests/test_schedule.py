"""Unit tests for the recurrence engine (platform_core.schedule).

Pure date math, no I/O — these lock in the "next occurrence strictly after now" contract, the
day/week/month interval counting, the month-length clamping, and the per-schedule anchor that makes
"every N weeks/months" relative to when a schedule was configured.
"""
import datetime as dt

from platform_core.schedule import next_run, validate

U = dt.timezone.utc


def at(y, m, d, hh=0, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=U)


# 2026-08-05 is a Wednesday (weekday 2).
NOW = at(2026, 8, 5, 14, 0)


def test_daily_rolls_to_tomorrow_when_time_passed():
    assert next_run(NOW, {"freq": "daily", "interval": 1, "at": "03:00", "tz": "UTC"}) == at(2026, 8, 6, 3, 0)


def test_daily_same_day_when_time_ahead():
    assert next_run(NOW, {"freq": "daily", "interval": 1, "at": "18:00", "tz": "UTC"}) == at(2026, 8, 5, 18, 0)


def test_weekly_next_sunday():
    r = next_run(NOW, {"freq": "weekly", "interval": 1, "byweekday": [6], "at": "04:00", "tz": "UTC"})
    assert r == at(2026, 8, 9, 4, 0)


def test_weekly_multiple_days_picks_next():
    # Mon/Wed/Fri 09:00; today is Wed 14:00 (passed) -> Fri 2026-08-07 09:00.
    r = next_run(NOW, {"freq": "weekly", "interval": 1, "byweekday": [0, 2, 4], "at": "09:00", "tz": "UTC"})
    assert r == at(2026, 8, 7, 9, 0)


def test_monthly_day_15():
    r = next_run(NOW, {"freq": "monthly", "interval": 1, "bymonthday": 15, "at": "03:00", "tz": "UTC"})
    assert r == at(2026, 8, 15, 3, 0)


def test_monthly_last_day():
    r = next_run(NOW, {"freq": "monthly", "interval": 1, "bymonthday": -1, "at": "03:00", "tz": "UTC"})
    assert r == at(2026, 8, 31, 3, 0)


def test_monthly_day_31_clamps_to_short_month():
    # From 2026-09-05, "day 31" clamps to Sep 30.
    r = next_run(at(2026, 9, 5, 0, 0), {"freq": "monthly", "interval": 1, "bymonthday": 31, "at": "03:00", "tz": "UTC"})
    assert r == at(2026, 9, 30, 3, 0)


def test_validate_weekly_needs_days():
    assert validate({"freq": "weekly", "at": "03:00"}) is not None


def test_validate_ok_daily():
    assert validate({"freq": "daily", "at": "03:00"}) is None


def test_validate_rejects_bad_freq():
    assert validate({"freq": "hourly", "at": "03:00"}) is not None


def test_validate_rejects_zero_interval():
    assert validate({"freq": "daily", "interval": 0, "at": "03:00"}) is not None


# --- anchor: "every N weeks/months" counted from the schedule's own anchor date ---

WK2 = {"freq": "weekly", "interval": 2, "byweekday": [0], "at": "09:00", "tz": "UTC"}  # every 2 wks, Mon


def test_biweekly_anchor_skips_off_week():
    # Anchored Mon 2026-08-03; from Wed 08-05 the next ACTIVE Monday is 08-17 (08-10 is the off week).
    assert next_run(at(2026, 8, 5, 9, 0), WK2, dt.date(2026, 8, 3)) == at(2026, 8, 17, 9, 0)


def test_biweekly_anchor_shift_flips_parity():
    # Anchor shifted a week (Mon 08-10) flips parity -> next active Monday is 08-10 itself.
    assert next_run(at(2026, 8, 5, 9, 0), WK2, dt.date(2026, 8, 10)) == at(2026, 8, 10, 9, 0)


def test_no_anchor_falls_back_to_fixed_reference():
    # From 08:00 the next 09:00 is today; a None anchor still works (fixed reference Monday).
    assert next_run(at(2026, 8, 5, 8, 0), {"freq": "daily", "interval": 1, "at": "09:00", "tz": "UTC"}) == at(2026, 8, 5, 9, 0)


def test_invalid_recurrence_returns_none():
    assert next_run(NOW, {"freq": "nope"}) is None
