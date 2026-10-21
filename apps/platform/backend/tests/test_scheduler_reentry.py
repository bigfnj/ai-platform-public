"""A scheduled task fires once at a time, whoever asks.

"Run now" had no re-entrancy guard: a second click while the first pass was still running (the
button gives no feedback) started a second pass beside it, and the tick could do the same to a
task a Run now was still running. Two passes of a GPU task compete for one card.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from platform_gateway_app import scheduler

BACKENDS = {"recipe-book": "http://recipe-book.test"}


@pytest.fixture(autouse=True)
def _clean_flight():
    scheduler._in_flight.clear()
    yield
    scheduler._in_flight.clear()


def _slow_backend(gate: asyncio.Event, seen: list):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        await gate.wait()
        return httpx.Response(200, json={"ok": True})
    return handler


def test_a_second_run_now_while_the_first_runs_is_refused(session):
    scheduler.seed(session, {"recipe-book"})
    seen: list = []

    async def go():
        gate = asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _slow_backend(gate, seen))) as http:
            first = asyncio.create_task(
                scheduler.run_now(session, http, BACKENDS, "recipe-book", "purge"))
            while not seen:
                await asyncio.sleep(0)
            # Bounded: a second run that is NOT refused would wait on the same held backend
            # forever, and a hang is a slower way to fail than this.
            with pytest.raises(scheduler.AlreadyRunning):
                await asyncio.wait_for(
                    scheduler.run_now(session, http, BACKENDS, "recipe-book", "purge"), 2.0)
            gate.set()
            return await first

    out = asyncio.run(go())
    assert len(seen) == 1, f"the task was fired {len(seen)} times"
    assert out["task_id"] == "purge"


def test_the_guard_is_released_after_a_run_even_a_failing_one(session):
    scheduler.seed(session, {"recipe-book"})

    async def go():
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
            await scheduler.run_now(session, http, BACKENDS, "recipe-book", "purge")
            await scheduler.run_now(session, http, BACKENDS, "recipe-book", "purge")

    asyncio.run(go())
    assert scheduler._in_flight == set()


def test_a_tick_skips_a_task_a_run_now_is_still_running(session, db):
    import datetime as dt

    from sqlalchemy import select

    from platform_gateway_app.models import Schedule
    scheduler.seed(session, {"recipe-book"})
    row = session.execute(select(Schedule).where(Schedule.rail == "recipe-book",
                                                 Schedule.task_id == "purge")).scalar_one()
    row.next_run = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    session.commit()
    seen: list = []

    async def go():
        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            scheduler._in_flight.add(("recipe-book", "purge"))
            await scheduler.tick(db.session, http, BACKENDS)

    asyncio.run(go())
    assert seen == [], f"the tick fired a task a Run now holds: {seen}"
