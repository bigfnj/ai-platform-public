"""Unit tests for the recurrence engine (platform_core.schedule).

Pure date math, no I/O — these lock in the "next occurrence strictly after now" contract, the
day/week/month interval counting, the month-length clamping, and the per-schedule anchor that makes
"every N weeks/months" relative to when a schedule was configured.
"""
import datetime as dt

import pytest

from platform_core import schedule
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


# --- malformed admin-edited JSON: validate RETURNS the problem, it never raises ---
#
# Every value below is something the console's recurrence editor can store. The numeric
# coercions used to run unguarded, so int("weekly") / int(None) / int("mon") raised a
# ValueError straight out of validate() and next_run() — against a contract that documents
# an error string and None. From next_run() that also aborts a whole scheduler tick, so one
# bad row would stop every OTHER due task from firing.


def test_validate_returns_error_for_non_int_interval():
    err = validate({"freq": "daily", "interval": "weekly", "at": "03:00"})
    assert err is not None and "interval" in err


def test_validate_returns_error_for_null_interval():
    assert validate({"freq": "daily", "interval": None, "at": "03:00"}) is not None


def test_validate_returns_error_for_non_int_bymonthday():
    err = validate({"freq": "monthly", "bymonthday": "last", "at": "03:00"})
    assert err is not None and "bymonthday" in err


def test_validate_returns_error_for_non_int_byweekday():
    assert validate({"freq": "weekly", "byweekday": ["mon", "wed"], "at": "03:00"}) is not None


def test_next_run_returns_none_for_non_int_interval():
    assert next_run(NOW, {"freq": "daily", "interval": "weekly", "at": "03:00", "tz": "UTC"}) is None


def test_next_run_returns_none_for_non_int_byweekday():
    # validate() used to wave this through (a non-empty list) and _matches() then raised.
    assert next_run(NOW, {"freq": "weekly", "byweekday": ["mon"], "at": "09:00", "tz": "UTC"}) is None


def test_next_run_returns_none_for_non_int_bymonthday():
    assert next_run(NOW, {"freq": "monthly", "bymonthday": "last", "at": "03:00", "tz": "UTC"}) is None


def test_one_junk_weekday_does_not_take_out_the_whole_schedule():
    # The unusable entry is dropped and the real days still fire. validate() and _matches()
    # read byweekday through the same helper, so they cannot disagree about which count.
    r = {"freq": "weekly", "interval": 1, "byweekday": [0, "x", 4], "at": "09:00", "tz": "UTC"}
    assert validate(r) is None
    assert next_run(NOW, r) == at(2026, 8, 7, 9, 0)  # Fri


# --- a schedule validate() accepts always has a next run (audit 2026-10-14) ---
#
# validate() accepted {"freq": "monthly", "interval": 36} while next_run() searches only
# _HORIZON_DAYS (800) ahead, so the task saved as enabled with next_run NULL and the scheduler,
# which selects rows by next_run, never ran it. The far case is the instant just after a run,
# which is exactly when the scheduler asks for the next one, so each limit is proved there:
# reachable at MAX_INTERVAL, unreachable one past it even with validate's limit lifted.

_AT = {"at": "03:00", "tz": "UTC"}
_SHAPES = {"daily": {}, "weekly": {"byweekday": [0]}, "monthly": {"bymonthday": 3}}
RUN = at(2026, 8, 3, 3, 0)               # Monday the 3rd, 03:00: an occurrence of each shape
JUST_AFTER = RUN + dt.timedelta(minutes=1)
ANCHOR = RUN.date()                      # anchored there, so that day is an active one


def _rec(freq, interval, **extra):
    return {"freq": freq, "interval": interval, **_SHAPES[freq], **_AT, **extra}


def test_every_36_months_is_refused_and_says_why():
    err = validate(_rec("monthly", 36))
    assert err is not None, "accepted a schedule next_run cannot reach"
    assert "interval" in err and "at most 26" in err and "800 days" in err, err


@pytest.mark.parametrize("freq", schedule.FREQS)
def test_one_past_the_limit_is_refused(freq):
    assert validate(_rec(freq, schedule.MAX_INTERVAL[freq] + 1)) is not None
    assert validate(_rec(freq, schedule.MAX_INTERVAL[freq])) is None


@pytest.mark.parametrize(("freq", "expected"), [
    ("daily", RUN + dt.timedelta(days=800)),      # the next active day
    ("weekly", RUN + dt.timedelta(weeks=114)),    # Monday of the next active week
    ("monthly", at(2028, 10, 3, 3, 0)),           # the 3rd, 26 months on
])
def test_the_longest_interval_is_found_just_after_a_run(freq, expected):
    r = _rec(freq, schedule.MAX_INTERVAL[freq])
    assert validate(r) is None
    assert next_run(JUST_AFTER, r, ANCHOR) == expected


@pytest.mark.parametrize("freq", schedule.FREQS)
def test_one_past_the_limit_could_not_be_found_anyway(monkeypatch, freq):
    """The limit is exactly next_run's reach, not a policy: lift validate's limit and the next
    interval up finds nothing from the same instant."""
    limit = schedule.MAX_INTERVAL[freq]
    monkeypatch.setitem(schedule.MAX_INTERVAL, freq, 10 ** 6)
    assert next_run(JUST_AFTER, _rec(freq, limit + 1), ANCHOR) is None


@pytest.mark.parametrize("dom", [1, 28, 29, 30, 31, -1])
def test_every_26_months_is_found_after_a_run_in_any_month(dom):
    """Month lengths vary, so the monthly limit is held after a run in every month of 2026-2029
    (2028 a leap year), for each kind of month day, the clamped ones included."""
    r = _rec("monthly", schedule.MAX_INTERVAL["monthly"], bymonthday=dom)
    for year in range(2026, 2030):
        for month in range(1, 13):
            anchor = dt.date(year, month, 1)
            occ = next_run(at(year, month, 1), r, anchor)     # this month's run
            assert occ is not None and (occ.year, occ.month) == (year, month), occ
            assert next_run(occ + dt.timedelta(minutes=1), r, anchor) is not None, (
                f"no run after {occ} for day {dom}")


def test_a_weekday_outside_0_to_6_is_no_weekday():
    """[7] used to pass validate and match no date: saved enabled, never run."""
    for bad in ([7], [-1], [9, 12]):
        r = {"freq": "weekly", "interval": 1, "byweekday": bad, **_AT}
        assert validate(r) is not None, bad
        assert next_run(NOW, r) is None
    # One out-of-range entry among real ones is dropped, like a junk entry.
    r = {"freq": "weekly", "interval": 1, "byweekday": [9, 4], "at": "09:00", "tz": "UTC"}
    assert validate(r) is None
    assert next_run(NOW, r) == at(2026, 8, 7, 9, 0)  # Fri
