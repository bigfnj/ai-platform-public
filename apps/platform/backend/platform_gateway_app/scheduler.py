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
            "next_run": r.next_run.isoformat() if r and r.next_run else None,
            "last_run": r.last_run.isoformat() if r and r.last_run else None,
            "last_status": r.last_status if r else None,
        }
        g = by_rail.setdefault(task["rail"], {"rail": task["rail"], "icon": task["icon"], "tasks": []})
        g["tasks"].append(entry)
    return list(by_rail.values())


def set_schedule(db: OrmSession, rail: str, task_id: str,
                 recurrence: dict, enabled: bool) -> dict[str, Any]:
    """Validate + persist a task's recurrence/enabled and recompute next_run. Returns the row view."""
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
    return {"rail": rail, "task_id": task_id, "enabled": enabled,
            "next_run": nxt.isoformat() if nxt else None}


def _fire_platform(db: OrmSession, task_id: str) -> str:
    """Run a gateway-owned ('platform') task in-process against the gateway DB."""
    handler = platform_maintenance.HANDLERS.get(task_id)
    if handler is None:
        return "error: unknown platform task"
    try:
        n = handler(db)
        return f"ok (pruned {n})" if isinstance(n, int) else "ok"
    except Exception as exc:  # noqa: BLE001 — record and keep the loop alive
        return f"error: {str(exc)[:180]}"


def _summarize_result(resp) -> str:
    """Render a synchronous task's JSON response into a short ``k=v`` string for last_status,
    so the Schedule tab shows what a run actually did (e.g. sweep/purge counts). Best-effort:
    any non-flat / unparseable body yields '' (falls back to a plain 'ok'). Capped to keep
    last_status compact."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a non-JSON body just means no summary
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [f"{k}={v}" for k, v in body.items() if isinstance(v, (int, float, str, bool))]
    return " ".join(parts)[:150]


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
        return f"ok ({resp.status_code}){f' {summary}' if summary else ''}"
    except httpx.HTTPError as exc:
        return f"error: {str(exc)[:180]}"


async def run_now(db: OrmSession, http: httpx.AsyncClient, backends: dict[str, str],
                  rail: str, task_id: str) -> dict[str, Any]:
    """Manual 'Run now' from the console. Fires immediately, records the outcome, keeps next_run."""
    status = (_fire_platform(db, task_id) if rail == PLATFORM_RAIL
              else await fire(http, backends, rail, task_id))
    row = db.execute(
        select(Schedule).where(Schedule.rail == rail, Schedule.task_id == task_id)
    ).scalar_one_or_none()
    if row is not None:
        row.last_run = _now()
        row.last_status = status
        db.commit()
    return {"rail": rail, "task_id": task_id, "status": status}


async def tick(db_factory, http: httpx.AsyncClient, backends: dict[str, str]) -> None:
    """One scheduler pass: fire every enabled task whose next_run is due, then reschedule it."""
    db = db_factory()
    try:
        now = _now()
        due = db.execute(
            select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run.isnot(None),
                                   Schedule.next_run <= now)
        ).scalars().all()
        for row in due:
            recurrence = json.loads(row.recurrence)
            status = (_fire_platform(db, row.task_id) if row.rail == PLATFORM_RAIL
                      else await fire(http, backends, row.rail, row.task_id))
            row.last_run = now
            row.last_status = status
            anchor = row.anchor.date() if row.anchor else None
            row.next_run = rec.next_run(now, recurrence, anchor)  # next occurrence, anchor-aware
            db.commit()
    finally:
        db.close()


async def loop(app, interval_seconds: int = 60) -> None:
    """Background scheduler loop. Ticks every interval; a bad tick never kills the loop."""
    while True:
        try:
            await tick(app.state.db.session, app.state.http, app.state.backends)
        except Exception:  # noqa: BLE001 — resilience: keep ticking
            pass
        await asyncio.sleep(interval_seconds)
