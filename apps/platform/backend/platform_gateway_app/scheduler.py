"""The central platform scheduler.

The gateway is the single source of truth for every rail's maintenance schedule: it stores one
``Schedule`` row per registered task, computes each task's next fire time from its recurrence
(``platform_core.schedule``), and a background loop fires due tasks by calling the owning rail's
internal endpoint with trusted system-admin headers. This replaces the per-rail env-driven loops
with one editable console.

Read/seed/edit helpers are sync (they own a short-lived Session); the loop + firing are async.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from platform_core import schedule as rec
from platform_gateway_app import platform_maintenance
from platform_gateway_app.models import Schedule
from platform_gateway_app.scheduler_tasks import TASKS_BY_KEY, tasks_for

# The reserved rail id for gateway-owned tasks that run in-process (no rail HTTP call).
PLATFORM_RAIL = "platform"

# Trusted headers for a system-initiated trigger. The rail backends sit on the internal network and
# trust X-Platform-* from the gateway; a scheduled run is "system admin".
SYSTEM_HEADERS = {"X-Platform-User": "system", "X-Platform-Admin": "1"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_utc(when: dt.datetime | None) -> str | None:
    """An ISO time that SAYS it is UTC. Every stored time is UTC, but SQLite hands DateTime
    columns back naive, and a naive ISO string is read by the browser as LOCAL time: the
    Schedule tab showed every next/last run shifted by the viewer's UTC offset."""
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.isoformat()


# Tasks firing right now, keyed (rail, task_id). One event loop runs every fire, so a plain set
# is enough: the check and the add have no await between them. A second "Run now" on a task
# still running, or a tick reaching a task a "Run now" is still running, used to start a second
# pass that competed with the first for the GPU.
_in_flight: set[tuple[str, str]] = set()


class AlreadyRunning(RuntimeError):
    """This task is already firing; the caller should say so rather than start another."""


def seed(db: OrmSession, enabled: set[str]) -> None:
    """Create a Schedule row (default recurrence, computed next_run) for any registered task in an
    enabled rail that doesn't have one yet. Idempotent."""
    for task in tasks_for(enabled):
        row = db.execute(
            select(Schedule).where(Schedule.rail == task["rail"], Schedule.task_id == task["task_id"])
        ).scalar_one_or_none()
        if row is not None:
            continue
        recurrence = task["default"]
        now = _now()
        db.add(Schedule(rail=task["rail"], task_id=task["task_id"],
                        recurrence=json.dumps(recurrence), enabled=True, anchor=now,
                        next_run=rec.next_run(now, recurrence, now.date())))
    db.commit()


def list_view(db: OrmSession, enabled: set[str]) -> list[dict[str, Any]]:
    """Registry tasks joined with their stored schedule, grouped by rail (only installed rails)."""
    rows = {(r.rail, r.task_id): r for r in db.execute(select(Schedule)).scalars().all()}
    by_rail: dict[str, dict[str, Any]] = {}
    for task in tasks_for(enabled):
        r = rows.get((task["rail"], task["task_id"]))
        recurrence = json.loads(r.recurrence) if r else task["default"]
        entry = {
            "task_id": task["task_id"], "label": task["label"],
            "description": task["description"],
            "recurrence": recurrence,
            "enabled": bool(r.enabled) if r else True,
            "next_run": _iso_utc(r.next_run) if r else None,
            "last_run": _iso_utc(r.last_run) if r else None,
            "last_status": r.last_status if r else None,
        }
        g = by_rail.setdefault(task["rail"], {"rail": task["rail"], "icon": task["icon"], "tasks": []})
        g["tasks"].append(entry)
    return list(by_rail.values())


def set_schedule(db: OrmSession, rail: str, task_id: str,
                 recurrence: dict, enabled: bool) -> None:
    """Validate + persist a task's recurrence/enabled and recompute next_run.

    Returns nothing. It used to return a row view that its only production caller discarded
    (the route answers with list_view), so the test guarding next_run checked a value no
    endpoint ever served. What the admin sees is list_view's, and that is what is tested."""
    if (rail, task_id) not in TASKS_BY_KEY:
        raise ValueError(f"unknown task {rail}/{task_id}")
    err = rec.validate(recurrence)
    if err:
        raise ValueError(err)
    row = db.execute(
        select(Schedule).where(Schedule.rail == rail, Schedule.task_id == task_id)
    ).scalar_one_or_none()
    now = _now()
    if row is None:
        row = Schedule(rail=rail, task_id=task_id)
        db.add(row)
    row.recurrence = json.dumps(recurrence)
    row.enabled = enabled
    row.anchor = now                       # re-anchor interval counting to when it was saved
    nxt = rec.next_run(now, recurrence, now.date()) if enabled else None
    row.next_run = nxt
    db.commit()


def _fire_platform(db: OrmSession, task_id: str) -> str:
    """Run a SYNC gateway-owned ('platform') task in-process against the gateway DB."""
    handler = platform_maintenance.HANDLERS.get(task_id)
    if handler is None:
        return "error: unknown platform task"
    try:
        n = handler(db)
        if isinstance(n, str):
            return f"ok · {n}"[:150]
        return f"ok (pruned {n})" if isinstance(n, int) else "ok"
    except Exception as exc:  # noqa: BLE001 — record and keep the loop alive
        return f"error: {str(exc)[:180]}"


async def _run_platform(db: OrmSession, broker: Any, task_id: str) -> str:
    """Dispatch a 'platform' task. The model-pool scan is async (it awaits the broker to describe
    new models); everything else is a sync HANDLER. ``broker`` may be None if unavailable."""
    if task_id == "model-scan":
        if broker is None:
            return "error: broker unavailable"
        try:
            return f"ok · {await platform_maintenance.run_model_scan(broker, db)}"[:150]
        except Exception as exc:  # noqa: BLE001 — record and keep the loop alive
            return f"error: {str(exc)[:180]}"
    return _fire_platform(db, task_id)


# The run record IS Schedule.last_status, a String(255) column, so that is the bound on what a
# run may record. Tied to the column rather than restated, so the two cannot drift apart.
STATUS_MAX = Schedule.__table__.c.last_status.type.length or 255

# A rail's response body is recorded, so anything credential-shaped in it is not. Keys whose
# NAME looks secret are dropped from a JSON summary, and an Authorization-style value in free
# text is masked. Request and response HEADERS are never read into the record at all.
_SECRET_KEY = re.compile(r"token|secret|passw|authori[sz]ation|cookie|api[_-]?key|credential",
                         re.I)
# "Bearer <x>" / "Basic <x>", and NAME=<x> or NAME: <x> for a credential-shaped name. The name
# may be the tail of a longer one (BROKER_AUTH_TOKEN=...), hence a lookbehind rather than \b,
# which an underscore defeats. "token file unreadable" has no = or :, and is left alone.
_AUTH_VALUE = re.compile(
    r"\b(bearer|basic)\s+[^\s,;\"']+"
    r"|(?<![a-z])(token|api[_-]?key|passw(?:or)?d|secret)(\s*[:=]\s*)[^\s,;\"']+", re.I)


def _mask(m: re.Match) -> str:
    return f"{m.group(1)} [redacted]" if m.group(1) else f"{m.group(2)}{m.group(3)}[redacted]"


def _clean(text: str) -> str:
    """One line, credentials masked."""
    return _AUTH_VALUE.sub(_mask, " ".join(text.split()))


def _flat(d: dict) -> str:
    """``k=v`` for a dict's scalar values, secret-named keys left out."""
    return " ".join(f"{k}={v}" for k, v in d.items()
                    if isinstance(v, (int, float, str, bool)) and not _SECRET_KEY.search(str(k)))


def _summarize_result(resp) -> str:
    """Render a synchronous task's JSON response into a short ``k=v`` string for last_status,
    so the Schedule tab shows what a run actually did (e.g. sweep/purge counts). Best-effort:
    any non-flat / unparseable body yields '' (falls back to a plain 'ok')."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a non-JSON body just means no summary
        return ""
    if not isinstance(body, dict):
        return ""
    return _clean(_flat(body))


def _summarize_error(resp) -> str:
    """What a FAILED task's rail said, for last_status.

    Without this a rail that answered 500 with its counts ("removed=3 failed=1") or a reason
    ("admin only") was recorded as httpx's own text, "Server error '500 ...' for url ...",
    which names the URL and drops everything the rail actually reported. FastAPI wraps a
    raised detail as ``{"detail": ...}``, so that is unwrapped first: a dict becomes ``k=v``,
    a string is kept, a validation list becomes its messages. A body that is not JSON (a proxy's
    HTML 502) is kept as collapsed text; the caller bounds the length.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — not JSON: keep the text itself
        try:
            return _clean(resp.text or "")
        except Exception:  # noqa: BLE001 — an undecodable body says nothing usable
            return ""
    if isinstance(body, dict) and "detail" in body:
        body = body["detail"]
    if isinstance(body, dict):
        return _clean(_flat(body))
    if isinstance(body, str):
        return _clean(body)
    if isinstance(body, list):
        return _clean("; ".join(str(e["msg"]) for e in body
                                if isinstance(e, dict) and isinstance(e.get("msg"), str)))
    return ""


def _bounded(status: str) -> str:
    return status[:STATUS_MAX]


async def fire(http: httpx.AsyncClient, backends: dict[str, str], rail: str, task_id: str) -> str:
    """Call a task's rail endpoint now. Returns a short status string (stored as last_status)."""
    task = TASKS_BY_KEY.get((rail, task_id))
    if task is None:
        return "error: unknown task"
    base = backends.get(rail)
    if not base:
        return "error: rail not installed"
    try:
        resp = await http.request(task["method"], base + task["path"], headers=SYSTEM_HEADERS,
                                  timeout=120)
        resp.raise_for_status()
        # A fire-and-forget task only STARTED; a synchronous one actually finished, so surface
        # a compact summary of its result (counts) alongside the ok.
        if task.get("async"):
            return f"triggered ({resp.status_code})"
        summary = _summarize_result(resp)
        return _bounded(f"ok ({resp.status_code}){f' {summary}' if summary else ''}")
    except httpx.HTTPStatusError as exc:
        # The rail answered, and said why. Keep that, not httpx's sentence about it.
        detail = _summarize_error(exc.response)
        return _bounded(f"error ({exc.response.status_code}){f' {detail}' if detail else ''}")
    except httpx.HTTPError as exc:
        # No answer at all (refused, timed out): httpx's text is the only account there is.
        return _bounded(f"error: {_clean(str(exc))}")


async def run_now(db: OrmSession, http: httpx.AsyncClient, backends: dict[str, str],
                  rail: str, task_id: str, broker: Any = None) -> dict[str, Any]:
    """Manual 'Run now' from the console. Fires immediately, records the outcome, keeps next_run.

    Raises AlreadyRunning while the same task is still firing (from an earlier Run now or from
    the tick), rather than starting a second pass beside it."""
    key = (rail, task_id)
    if key in _in_flight:
        raise AlreadyRunning(f"{rail}/{task_id} is already running")
    _in_flight.add(key)
    try:
        status = (await _run_platform(db, broker, task_id) if rail == PLATFORM_RAIL
                  else await fire(http, backends, rail, task_id))
    finally:
        _in_flight.discard(key)
    row = db.execute(
        select(Schedule).where(Schedule.rail == rail, Schedule.task_id == task_id)
    ).scalar_one_or_none()
    if row is not None:
        row.last_run = _now()
        row.last_status = status
        db.commit()
    return {"rail": rail, "task_id": task_id, "status": status}


async def tick(db_factory, http: httpx.AsyncClient, backends: dict[str, str],
               broker: Any = None) -> None:
    """One scheduler pass: fire every enabled task whose next_run is due, then reschedule it."""
    db = db_factory()
    try:
        now = _now()
        due = db.execute(
            select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run.isnot(None),
                                   Schedule.next_run <= now)
        ).scalars().all()
        for row in due:
            key = (row.rail, row.task_id)
            if key in _in_flight:
                continue        # a Run now has it; the next tick picks it up if still due
            recurrence = json.loads(row.recurrence)
            _in_flight.add(key)
            try:
                status = (await _run_platform(db, broker, row.task_id)
                          if row.rail == PLATFORM_RAIL
                          else await fire(http, backends, row.rail, row.task_id))
            finally:
                _in_flight.discard(key)
            row.last_run = now
            row.last_status = status
            anchor = row.anchor.date() if row.anchor else None
            row.next_run = rec.next_run(now, recurrence, anchor)  # next occurrence, anchor-aware
            db.commit()
    finally:
        db.close()


log = logging.getLogger(__name__)


async def loop(app, interval_seconds: int = 60) -> None:
    """Background scheduler loop. Ticks every interval; a bad tick never kills the loop."""
    while True:
        try:
            await tick(app.state.db.session, app.state.http, app.state.backends,
                       getattr(app.state, "broker", None))
        except Exception:  # noqa: BLE001 — resilience: keep ticking
            # LOG it. "A bad tick never kills the loop" was true, and is not the same thing
            # as observable. Per-task failures already land in last_status; what this
            # swallowed was the INFRASTRUCTURE case (the DB session factory, app.state.http),
            # where the only symptom was a Schedule tab whose next_run sat in the past and
            # nothing anywhere saying why.
            log.exception("scheduler tick failed; continuing")
        await asyncio.sleep(interval_seconds)
