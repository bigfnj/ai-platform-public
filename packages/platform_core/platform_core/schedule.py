"""Recurrence engine for the platform scheduler (pure, dependency-light, unit-tested).

A recurrence is a small JSON dict the admin edits (Outlook-style, minus Duration):

    {
      "freq": "daily" | "weekly" | "monthly",
      "interval": 1,               # every N days / weeks / months
      "byweekday": [0, 2, 4],      # weekly only, Mon=0 .. Sun=6
      "bymonthday": 15,            # monthly only, 1..31 or -1 for the last day of the month
      "at": "03:00",              # wall-clock time in `tz`
      "tz": "America/Los_Angeles"
    }

``next_run`` returns the next occurrence strictly after a given instant, as a UTC-aware datetime.
Interval counting uses a fixed reference Monday (2020-01-06) so "every N weeks/months" is stable
and pure — no per-schedule anchor needed. Good enough for the platform's handful of maintenance
tasks; the point is a central, editable schedule, not a general calendar.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

_REF = _dt.date(2020, 1, 6)  # a Monday; fixed anchor for interval math
_HORIZON_DAYS = 800
FREQS = ("daily", "weekly", "monthly")


def _tz(name: str | None) -> _dt.tzinfo:
    try:
        return ZoneInfo(name) if name else _dt.timezone.utc
    except Exception:  # noqa: BLE001 — unknown zone falls back to UTC
        return _dt.timezone.utc


def _int(value, default: int | None = None) -> int | None:
    """``int(value)`` or ``default`` when the value is not coercible. Every recurrence field
    is admin-edited JSON, so "weekly", null and lists all reach the numeric coercions."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_at(at: str | None) -> tuple[int, int]:
    try:
        hh, mm = (at or "03:00").split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except (ValueError, AttributeError):
        return 3, 0


def _last_dom(year: int, month: int) -> int:
    if month == 12:
        nxt = _dt.date(year + 1, 1, 1)
    else:
        nxt = _dt.date(year, month + 1, 1)
    return (nxt - _dt.timedelta(days=1)).day


def _months_between(a: _dt.date, b: _dt.date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _weekdays(rec: dict) -> set[int]:
    """``byweekday`` as a set of ints, dropping entries that are not int-coercible and
    yielding the empty set for a non-sequence. Shared by ``validate`` and ``_matches`` so the
    two cannot disagree about which values count as weekdays."""
    raw = rec.get("byweekday") or []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {n for n in (_int(x) for x in raw) if n is not None}


def validate(rec: dict) -> str | None:
    """Return an error string if the recurrence is malformed, else None.

    The coercions are guarded because this function is contracted to RETURN the problem
    rather than raise it: the recurrence is admin-edited JSON and arrives with arbitrary
    types. A ValueError escaping here breaks the console save, and escaping from
    ``next_run`` it aborts a whole scheduler tick — one bad row would stop every other
    due task from firing."""
    if not isinstance(rec, dict):
        return "recurrence must be an object"
    if rec.get("freq") not in FREQS:
        return f"freq must be one of {FREQS}"
    interval = _int(rec.get("interval", 1))
    if interval is None or interval < 1:
        return "interval must be an integer >= 1"
    if rec["freq"] == "weekly" and not _weekdays(rec):
        return "weekly recurrence needs at least one weekday"
    if rec["freq"] == "monthly":
        d = _int(rec.get("bymonthday", 1))
        if d is None or (d != -1 and not (1 <= d <= 31)):
            return "bymonthday must be 1..31 or -1 (last day)"
    return None


def _matches(rec: dict, d: _dt.date, ref: _dt.date = _REF) -> bool:
    """Whether date ``d`` is an occurrence, counting the ``interval`` from ``ref`` (the schedule's
    anchor date, or the fixed reference Monday when none is stored)."""
    interval = max(1, _int(rec.get("interval", 1), 1))
    freq = rec["freq"]
    if freq == "daily":
        return (d - ref).days % interval == 0
    if freq == "weekly":
        if d.weekday() not in _weekdays(rec):
            return False
        ref_monday = ref - _dt.timedelta(days=ref.weekday())
        d_monday = d - _dt.timedelta(days=d.weekday())
        return ((d_monday - ref_monday).days // 7) % interval == 0
    if freq == "monthly":
        dom = _int(rec.get("bymonthday", 1), 1)
        target = _last_dom(d.year, d.month) if dom == -1 else min(dom, _last_dom(d.year, d.month))
        if d.day != target:
            return False
        return _months_between(ref, d) % interval == 0
    return False


def next_run(now: _dt.datetime, rec: dict, anchor: _dt.date | None = None) -> _dt.datetime | None:
    """Next occurrence strictly after ``now`` (tz-aware), returned as a UTC-aware datetime.
    ``anchor`` (a date, typically the schedule's creation date) is the reference for "every N
    weeks/months" so the cycle is relative to when the schedule was set; None uses the fixed
    reference. Returns None for an invalid recurrence."""
    if validate(rec) is not None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    tz = _tz(rec.get("tz"))
    hh, mm = _parse_at(rec.get("at"))
    ref = anchor or _REF
    now_local = now.astimezone(tz)
    for offset in range(0, _HORIZON_DAYS + 1):
        d = (now_local + _dt.timedelta(days=offset)).date()
        if not _matches(rec, d, ref):
            continue
        cand = _dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
        if cand > now_local:
            return cand.astimezone(_dt.timezone.utc)
    return None
