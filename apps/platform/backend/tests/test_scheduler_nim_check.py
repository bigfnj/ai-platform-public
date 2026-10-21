"""The weekly NVIDIA cloud-model check, as Admin > Schedule will show it.

ai-playground's ``POST /api/maintenance/nim-check`` answers 200 {ok, model, latency_s} when the
configured NVIDIA model works, 200 {ok, skipped} with no key, and 502 with nim.ModelUnavailable's
sentence when NVIDIA has retired the model. That sentence is only worth writing if it survives
into the run record: these tests drive the REAL scheduler.fire over httpx.MockTransport with
the rail's actual answer shapes and check what lands in Schedule.last_status.

The rail side (route, admin gate, shared rate limit, no-key skip) is tested in
rails/ai-playground/tests/test_nim_maintenance_check.py; tests/mutate_nim_unavailable.py there
mutates both.
"""
from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from platform_gateway_app import scheduler
from platform_gateway_app.models import Schedule
from platform_gateway_app.scheduler_tasks import TASKS_BY_KEY

KEY = ("ai-playground", "nim-check")
BACKENDS = {"ai-playground": "http://ap:8850"}
MODEL = "meta/llama-3.2-11b-vision-instruct"
# nim.ModelUnavailable's 410 text, verbatim from a live run on 2026-10-07 (model id swapped).
RETIRED = (f"NVIDIA has retired the cloud model '{MODEL}' (410 Gone). Set "
           "AI_PLAYGROUND_NVIDIA_CHAT_MODEL in deploy/.env to a plain-instruct model this key can "
           "call, proven by a real completion (see ai_playground/nim.py), then recreate the "
           f"ai-playground container. NVIDIA said: The model '{MODEL}' has reached its end of "
           "life on 2026-09-28T08:00:00Z and is no longer available.")


def _fire(code: int, body: dict) -> tuple[str, list]:
    seen: list = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("X-Platform-Admin")))
        return httpx.Response(code, json=body)

    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            return await scheduler.fire(http, BACKENDS, *KEY)
    return asyncio.run(go()), seen


def test_the_task_is_registered_synchronous_and_weekly():
    task = TASKS_BY_KEY.get(KEY)
    assert task is not None, "the weekly NVIDIA check is not registered"
    assert (task["method"], task["path"]) == ("POST", "/api/maintenance/nim-check")
    # A fire-and-forget task records "triggered", never the rail's verdict on the model.
    assert not task.get("async")
    assert task["default"]["freq"] == "weekly"


def test_it_is_seeded_on_a_deployment_with_the_rail(session):
    scheduler.seed(session, {"ai-playground"})
    keys = {(r.rail, r.task_id) for r in session.execute(select(Schedule)).scalars().all()}
    assert KEY in keys


def test_a_retired_model_reads_as_an_error_that_names_it():
    status, seen = _fire(502, {"detail": RETIRED})
    assert seen == [("POST", "http://ap:8850/api/maintenance/nim-check", "1")]
    assert status.startswith(f"error (502) NVIDIA has retired the cloud model '{MODEL}'"), status
    # The record is bounded at 255; the sentence leads with what matters, so the cut keeps it.
    assert len(status) <= scheduler.STATUS_MAX
    assert "AI_PLAYGROUND_NVIDIA_CHAT_MODEL" in status


def test_a_working_model_reads_ok_with_model_and_latency():
    status, _ = _fire(200, {"ok": True, "model": MODEL, "latency_s": 0.42})
    assert status.startswith("ok (200)")
    assert f"model={MODEL}" in status and "latency_s=0.42" in status


def test_no_key_reads_as_ok_not_as_a_failure():
    """AdminPage.tsx colours a badge good only when it starts with ok/triggered."""
    status, _ = _fire(200, {"ok": True, "skipped": "no NVIDIA key configured", "model": MODEL})
    assert status.startswith("ok (200)") and "skipped=no NVIDIA key configured" in status
