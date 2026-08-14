"""Tests for the central scheduler: seeding, editing/validation, list view, in-process platform
tasks, the HTTP fire path (mocked), and one tick that fires a due task and reschedules it."""
import asyncio
import datetime as dt
import json

import pytest
from sqlalchemy import select

from platform_gateway_app import scheduler
from platform_gateway_app.models import Schedule, SessionRow, User

ENABLED = {"recipe-book", "ai-playground"}


# --- seeding ---------------------------------------------------------------

def test_seed_creates_rows_including_platform(session):
    scheduler.seed(session, ENABLED)
    rows = session.execute(select(Schedule)).scalars().all()
    keys = {(r.rail, r.task_id) for r in rows}
    assert ("platform", "prune-sessions") in keys        # gateway task always installs
    assert ("recipe-book", "reindex") in keys
    assert ("recipe-book", "icons-repass") in keys
    assert ("recipe-book", "purge") in keys
    assert all(r.rail != "edu-suite" for r in rows)       # not-enabled rail not seeded
    for r in rows:
        assert r.next_run is not None and r.anchor is not None


def test_seed_is_idempotent(session):
    scheduler.seed(session, ENABLED)
    n1 = len(session.execute(select(Schedule)).scalars().all())
    scheduler.seed(session, ENABLED)
    n2 = len(session.execute(select(Schedule)).scalars().all())
    assert n1 == n2


# --- edit + validation -----------------------------------------------------

def test_set_schedule_persists_and_recomputes(session):
    scheduler.seed(session, ENABLED)
    rec = {"freq": "weekly", "interval": 2, "byweekday": [0], "at": "09:00", "tz": "UTC"}
    out = scheduler.set_schedule(session, "recipe-book", "reindex", rec, True)
    assert out["next_run"]
    row = session.execute(
        select(Schedule).where(Schedule.rail == "recipe-book", Schedule.task_id == "reindex")
    ).scalar_one()
    assert json.loads(row.recurrence) == rec
    assert row.anchor is not None


def test_set_schedule_rejects_unknown_task(session):
    with pytest.raises(ValueError):
        scheduler.set_schedule(session, "recipe-book", "nope", {"freq": "daily", "at": "03:00"}, True)


def test_set_schedule_rejects_bad_recurrence(session):
    # weekly with no byweekday is invalid
    with pytest.raises(ValueError):
        scheduler.set_schedule(session, "recipe-book", "reindex", {"freq": "weekly", "at": "03:00"}, True)


def test_disabled_schedule_has_no_next_run(session):
    scheduler.seed(session, ENABLED)
    out = scheduler.set_schedule(session, "recipe-book", "reindex",
                                 {"freq": "daily", "interval": 1, "at": "03:00", "tz": "UTC"}, False)
    assert out["next_run"] is None


def test_list_view_groups_by_rail(session):
    scheduler.seed(session, ENABLED)
    view = scheduler.list_view(session, ENABLED)
    rails = {g["rail"] for g in view}
    assert "platform" in rails and "recipe-book" in rails
    for g in view:
        assert g["icon"] and g["tasks"]


# --- in-process platform task ----------------------------------------------

def test_fire_platform_prunes_sessions(session):
    u = User(username="t", password_hash="x")
    session.add(u)
    session.commit()
    now = dt.datetime.now(dt.timezone.utc)
    session.add(SessionRow(token="dead", user_id=u.id, expires_at=now - dt.timedelta(hours=1)))
    session.commit()
    status = scheduler._fire_platform(session, "prune-sessions")
    assert status.startswith("ok (pruned 1")


def test_fire_platform_unknown_task(session):
    assert scheduler._fire_platform(session, "nope").startswith("error")


# --- HTTP fire path (mocked http) ------------------------------------------

class _FakeResp:
    def __init__(self, code=200):
        self.status_code = code

    def raise_for_status(self):
        return None


class _FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, headers=None, timeout=None):
        self.calls.append((method, url))
        return _FakeResp(200)


def test_fire_calls_rail_endpoint():
    http = _FakeHttp()
    status = asyncio.run(scheduler.fire(http, {"recipe-book": "http://rb:8000"}, "recipe-book", "reindex"))
    assert status.startswith("triggered")  # async (fire-and-forget) task
    assert http.calls == [("POST", "http://rb:8000/api/search/reindex")]


class _FakeJsonResp(_FakeResp):
    def __init__(self, code, payload):
        super().__init__(code)
        self._payload = payload

    def json(self):
        return self._payload


class _FakeJsonHttp:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def request(self, method, url, headers=None, timeout=None):
        self.calls.append((method, url))
        return _FakeJsonResp(200, self._payload)


def test_fire_sync_task_surfaces_result_counts():
    # A synchronous task (recipe-book purge) should carry its JSON counts into last_status.
    http = _FakeJsonHttp({"purged": 8})
    status = asyncio.run(scheduler.fire(http, {"recipe-book": "http://rb:8000"}, "recipe-book", "purge"))
    assert status.startswith("ok (200)")
    assert "purged=8" in status
    assert http.calls == [("POST", "http://rb:8000/api/maintenance/purge")]


def test_fire_unknown_task_errors():
    assert asyncio.run(scheduler.fire(_FakeHttp(), {}, "recipe-book", "nope")).startswith("error")


def test_fire_rail_not_installed():
    status = asyncio.run(scheduler.fire(_FakeHttp(), {}, "recipe-book", "reindex"))
    assert "not installed" in status


# --- one full tick ---------------------------------------------------------

def test_tick_fires_due_task_and_reschedules(session, db):
    scheduler.seed(session, ENABLED)
    row = session.execute(
        select(Schedule).where(Schedule.rail == "recipe-book", Schedule.task_id == "reindex")
    ).scalar_one()
    row.next_run = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)  # force overdue
    session.commit()

    http = _FakeHttp()
    backends = {"recipe-book": "http://rb:8000",
                "ai-playground": "http://ap:8000"}
    asyncio.run(scheduler.tick(db.session, http, backends))

    with db.session_ctx() as s2:
        row2 = s2.execute(
            select(Schedule).where(Schedule.rail == "recipe-book", Schedule.task_id == "reindex")
        ).scalar_one()
        assert row2.last_status.startswith("triggered")
        assert row2.last_run is not None
        # SQLite hands datetimes back naive (stored as UTC wall time) — normalize before comparing.
        nxt = row2.next_run.replace(tzinfo=dt.timezone.utc)
        assert nxt > dt.datetime.now(dt.timezone.utc)  # rescheduled forward
    assert http.calls == [("POST", "http://rb:8000/api/search/reindex")]
