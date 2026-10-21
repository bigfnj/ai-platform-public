"""Tests for the central scheduler: seeding, editing/validation, list view, in-process platform
tasks, the HTTP fire path (mocked), and one tick that fires a due task and reschedules it."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from platform_gateway_app import scheduler
from platform_gateway_app.models import Schedule, SessionRow, User

ENABLED = {"recipe-book", "job-aid", "ai-playground"}

# tests -> backend -> platform -> apps -> the repo root.
_RAILS = Path(__file__).resolve().parents[4] / "rails"


def _needs_rail(rail: str):
    """Skip where `rail` is not in this tree: the public snapshot withholds it AND de-wires its
    scheduled tasks, so an assertion about its row cannot hold there.

    Guarded on the rail's MANIFEST, the way tools/tests/test_rail_smoke_roles.py guards on its
    tool, rather than on the task registry. In this repo the file exists and the assertion runs,
    so a task dropped from SCHEDULED_TASKS by mistake still fails instead of skipping.
    """
    return pytest.mark.skipif(
        not (_RAILS / rail / "rail.json").is_file(),
        reason=f"rails/{rail} is withheld from this snapshot (see publish.json "
               f"rails_excluded_why), and publishing de-wired its scheduled task with it. "
               f"This assertion runs in the private repo.",
    )


# --- seeding ---------------------------------------------------------------

def test_seed_creates_rows_including_platform(session):
    scheduler.seed(session, ENABLED)
    rows = session.execute(select(Schedule)).scalars().all()
    keys = {(r.rail, r.task_id) for r in rows}
    assert ("platform", "prune-sessions") in keys        # gateway task always installs
    assert ("recipe-book", "reindex") in keys
    assert ("recipe-book", "icons-repass") in keys
    assert ("ai-playground", "model-repull") in keys     # a second rail, present in every tree
    assert all(r.rail != "finance" for r in rows)         # disabled rail not seeded
    for r in rows:
        assert r.next_run is not None and r.anchor is not None


@_needs_rail("job-aid")
def test_seed_includes_job_aid_refresh(session):
    """Split out of the test above so the public snapshot skips ONE fact, not the whole seed
    check: job-aid is withheld there and its task is de-wired, which failed the combined test."""
    scheduler.seed(session, ENABLED)
    keys = {(r.rail, r.task_id) for r in session.execute(select(Schedule)).scalars().all()}
    assert ("job-aid", "refresh") in keys


def test_seed_is_idempotent(session):
    scheduler.seed(session, ENABLED)
    n1 = len(session.execute(select(Schedule)).scalars().all())
    scheduler.seed(session, ENABLED)
    n2 = len(session.execute(select(Schedule)).scalars().all())
    assert n1 == n2


# --- edit + validation -----------------------------------------------------

def _served(session, rail: str, task_id: str) -> dict:
    """The task as the Schedule tab is served it: list_view, which is what the PUT route
    answers with. set_schedule's own return value used to be asserted here, and no endpoint
    ever served it."""
    view = scheduler.list_view(session, ENABLED)
    return next(t for g in view if g["rail"] == rail for t in g["tasks"]
                if t["task_id"] == task_id)


def test_set_schedule_persists_and_recomputes(session):
    scheduler.seed(session, ENABLED)
    rec = {"freq": "weekly", "interval": 2, "byweekday": [0], "at": "09:00", "tz": "UTC"}
    assert scheduler.set_schedule(session, "recipe-book", "reindex", rec, True) is None
    assert _served(session, "recipe-book", "reindex")["next_run"]
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


# Every 36 months: next_run searches 800 days ahead, so this used to save as enabled with
# next_run NULL, which tick() never selects (audit 2026-10-14).
NEVER = {"freq": "monthly", "interval": 36, "bymonthday": 1, "at": "03:00", "tz": "UTC"}


def test_a_schedule_that_would_never_fire_is_refused_and_the_row_is_kept(session):
    scheduler.seed(session, ENABLED)
    before = _served(session, "recipe-book", "reindex")
    assert before["next_run"]
    with pytest.raises(ValueError, match="at most 26"):
        scheduler.set_schedule(session, "recipe-book", "reindex", NEVER, True)
    session.expire_all()
    assert _served(session, "recipe-book", "reindex") == before


def test_the_schedule_tab_is_told_why_in_the_gateways_words(session, monkeypatch):
    """The Schedule tab shows a refused Apply as the 400's `detail` (platformApi's error, then
    the tab's error line), so the sentence validate() wrote is what the admin reads."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from platform_core import schedule as rec
    from platform_gateway_app import main as m

    scheduler.seed(session, ENABLED)
    monkeypatch.setattr(m.app.state, "settings",
                        SimpleNamespace(root_asset_owner=lambda _path: None,
                                        enabled_apps=sorted(ENABLED)), raising=False)
    monkeypatch.setitem(m.app.dependency_overrides, m.require_admin, lambda: object())
    monkeypatch.setitem(m.app.dependency_overrides, m.get_db, lambda: session)
    r = TestClient(m.app).put("/api/platform/admin/schedules/recipe-book/reindex",
                              json={"recurrence": NEVER, "enabled": True})
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": rec.validate(NEVER)}
    assert "would never run again" in r.json()["detail"]


def test_the_schedule_tabs_interval_box_stops_where_the_gateway_does():
    """AdminPage.tsx's MAX_INTERVAL is the recurrence editor's spinner cap. It used to be 52 for
    every frequency, past the 26 months the gateway now accepts."""
    import re

    from platform_core import schedule as rec

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "AdminPage.tsx").read_text(
        encoding="utf-8")
    m = re.search(r"const MAX_INTERVAL = \{ daily: (\d+), weekly: (\d+), monthly: (\d+) \}", src)
    assert m, "AdminPage.tsx no longer declares MAX_INTERVAL in the form this test reads"
    assert dict(zip(("daily", "weekly", "monthly"), map(int, m.groups()))) == rec.MAX_INTERVAL
    assert "max={MAX_INTERVAL[rec.freq]}" in src


def test_disabled_schedule_has_no_next_run(session):
    scheduler.seed(session, ENABLED)
    scheduler.set_schedule(session, "recipe-book", "reindex",
                           {"freq": "daily", "interval": 1, "at": "03:00", "tz": "UTC"}, False)
    assert _served(session, "recipe-book", "reindex")["next_run"] is None


def test_served_times_say_they_are_utc(session):
    """SQLite hands DateTime columns back NAIVE. Served that way, the browser parses the ISO
    string as LOCAL time and the Schedule tab shifts every run by the viewer's UTC offset."""
    import datetime as dt
    scheduler.seed(session, ENABLED)
    session.expire_all()                       # read back from the database, as a request does
    row = session.execute(select(Schedule).where(Schedule.rail == "recipe-book",
                                                 Schedule.task_id == "reindex")).scalar_one()
    row.last_run = dt.datetime(2026, 1, 2, 3, 4, 5)        # naive, as SQLite returns it
    session.commit()
    session.expire_all()
    served = _served(session, "recipe-book", "reindex")
    for key in ("next_run", "last_run"):
        parsed = dt.datetime.fromisoformat(served[key])
        assert parsed.utcoffset() == dt.timedelta(0), f"{key} served as {served[key]!r}"
    assert served["last_run"] == "2026-01-02T03:04:05+00:00"


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


@pytest.mark.parametrize(("rail", "task_id", "path", "payload", "expected"), [
    # recipe-book's purge is the same synchronous path and it ships, so the property is still
    # tested in the public snapshot, where the bouquet case below skips.
    pytest.param("recipe-book", "purge", "/api/maintenance/purge", {"purged": 3}, ["purged=3"],
                 id="recipe-book-purge"),
    # Two counts, so the join of several k=v pairs is covered too.
    pytest.param("bouquet", "sweep", "/api/maintenance/sweep",
                 {"pending_removed": 3, "orphan_removed": 5},
                 ["pending_removed=3", "orphan_removed=5"],
                 id="bouquet-sweep", marks=_needs_rail("bouquet")),
])
def test_fire_sync_task_surfaces_result_counts(rail, task_id, path, payload, expected):
    # A synchronous task should carry its JSON counts into last_status.
    http = _FakeJsonHttp(payload)
    status = asyncio.run(scheduler.fire(http, {rail: "http://r:8000"}, rail, task_id))
    assert status.startswith("ok (200)")
    for part in expected:
        assert part in status
    assert http.calls == [("POST", f"http://r:8000{path}")]


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
    backends = {"recipe-book": "http://rb:8000", "job-aid": "http://ja:8000",
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
