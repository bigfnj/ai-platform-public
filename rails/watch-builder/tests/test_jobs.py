"""The AI features as background jobs (jobs.py): lifecycle, ownership, cancel, bounds, restart.

Real worker threads throughout, because "the request does not wait on the job" is a claim about
threads. The broker is a fake `broker.chat`; `Held` makes each call wait until the test lets it
go, so a test can look at a job while a call is in flight. Neutral account names; nothing here
runs a prompt.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

os.environ.pop("PLATFORM_STANDALONE", None)

from watch_builder import broker, jobs  # noqa: E402
from watch_builder.api.app import app  # noqa: E402

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER}
THEIRS = {"X-Platform-User": OTHER}

GOOD = {
    "schema": 1, "name": "Stub", "background": "#000000", "platforms": ["emery"],
    "elements": [
        {"type": "time", "x": 0, "y": "30%", "w": "100%", "color": "#FFFFFF"},
        {"type": "date", "x": 0, "y": "60%", "w": "100%", "color": "#AAAAAA",
         "font": "auto-small"},
    ],
}
REPLY = json.dumps(GOOD)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    jobs._JOBS.clear()
    jobs._OWNER_LOCKS.clear()
    yield
    for job in list(jobs._JOBS.values()):
        job.cancel.set()
        assert job.exited.wait(10), f"a job's worker never returned: {job.id}"
    jobs._JOBS.clear()
    jobs._OWNER_LOCKS.clear()


class Held:
    """broker.chat stand-in: each call is recorded, then waits until the test lets it go."""

    def __init__(self, reply=REPLY):
        self.reply = reply
        self.calls: list[dict] = []
        self._entered = threading.Semaphore(0)
        self._go = threading.Semaphore(0)

    def __call__(self, messages, **kw):
        self.calls.append(kw)
        self._entered.release()
        if not self._go.acquire(timeout=8):
            raise broker.BrokerError("the test never let this call go")
        return self.reply

    def in_call(self) -> None:
        assert self._entered.acquire(timeout=5), "no call reached the broker"

    def let_go(self, n: int = 1) -> None:
        for _ in range(n):
            self._go.release()


@pytest.fixture()
def held(monkeypatch):
    h = Held()
    monkeypatch.setattr(broker, "chat", h)
    return h


@pytest.fixture()
def instant(monkeypatch):
    calls: list[dict] = []

    def chat(messages, **kw):
        calls.append(kw)
        return REPLY
    monkeypatch.setattr(broker, "chat", chat)
    return calls


def submit(path="/api/ai/variations", body=None, headers=MINE):
    body = body if body is not None else {"design": GOOD, "count": 3}
    r = client.post(path, json=body, headers=headers)
    return r


def poll(jid, headers=MINE):
    return client.get(f"/api/ai/jobs/{jid}", headers=headers)


def finished(jid, headers=MINE) -> dict:
    assert jobs._wait(jid, 10), "the job's worker never returned"
    r = poll(jid, headers)
    assert r.status_code == 200, r.text
    return r.json()


def owner_looks() -> None:
    """The owner's open page reads each of their jobs, renewing its lease (jobs.LEASE_S). A fake
    call that moves the job's clock by more than the lease calls this, as the page's 1.5 s poll
    would have done meanwhile."""
    for jid in list(jobs._JOBS):
        poll(jid)


# --- lifecycle ---------------------------------------------------------------------------


def test_the_post_answers_before_the_first_call_returns(held):
    """202 with the job id while the model is still thinking: no request waits on a model."""
    r = submit("/api/ai/design", {"prompt": "a face"})
    assert r.status_code == 202, r.text
    view = r.json()
    assert view["state"] in ("queued", "running") and view["job_id"]
    held.in_call()
    assert poll(view["job_id"]).json()["state"] == "running"
    held.let_go()
    done = finished(view["job_id"])
    assert done["state"] == "done", done
    assert done["result"]["design"]["name"] == "Stub"
    assert done["progress"] == {"done": 1, "total": 1, "unit": "design"}
    assert done["calls"] == 1


def test_variants_appear_as_they_finish(held):
    """The page shows each variant the moment it is done, with progress counted in variants."""
    jid = submit().json()["job_id"]
    held.in_call()
    first = poll(jid).json()
    assert (first["state"], first["progress"]["done"], first["partial"]) == ("running", 0, [])
    held.let_go()
    held.in_call()
    second = poll(jid).json()
    assert second["progress"] == {"done": 1, "total": 3, "unit": "variant"}
    assert [v["ok"] for v in second["partial"]] == [True]
    assert second["result"] is None
    held.let_go(2)
    done = finished(jid)
    assert done["state"] == "done"
    assert len(done["result"]["variants"]) == 3 and len(done["partial"]) == 3
    assert done["progress"]["done"] == 3


def test_a_failed_variant_is_part_of_a_done_job(monkeypatch):
    """The broker failing one call (its CLI and its local fallback both) fails that variant, not
    the pass."""
    replies = iter([REPLY, broker.BrokerError("CLI timed out and the local model is down"),
                    REPLY])

    def chat(messages, **kw):
        got = next(replies)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(broker, "chat", chat)
    done = finished(submit().json()["job_id"])
    assert done["state"] == "done"
    assert [v["ok"] for v in done["result"]["variants"]] == [True, False, True]
    assert "unavailable" in done["result"]["variants"][1]["error"]


def test_a_design_the_broker_cannot_answer_is_a_failed_job(monkeypatch):
    def down(messages, **kw):
        raise broker.BrokerError("connection refused")
    monkeypatch.setattr(broker, "chat", down)
    done = finished(submit("/api/ai/design", {"prompt": "a face"}).json()["job_id"])
    assert done["state"] == "failed" and "unavailable" in done["error"], done
    assert done["result"] is None


def test_a_slow_fallback_answer_is_waited_for(monkeypatch):
    """The fallback path. A call the broker answers locally after the CLI gave up takes the
    CLI's timeout and then the local answer; the job waits for it like any other call, and the
    calls after it still run, every one named. The 470 s each call takes moves time.monotonic
    as well as the job's clock, so a request budget read from either would show here. The
    owner's page is open, reading the job meanwhile, so the lease never lapses."""
    real = time.monotonic
    clock = {"offset": 0.0}

    def mono():
        return real() + clock["offset"]
    monkeypatch.setattr(time, "monotonic", mono)
    monkeypatch.setattr(jobs, "_mono", mono)
    seen = []

    def chat(messages, **kw):
        seen.append((kw.get("requester"), kw.get("timeout")))
        clock["offset"] += 300.0 + 170.0
        owner_looks()
        return REPLY
    monkeypatch.setattr(broker, "chat", chat)
    done = finished(submit(body={"design": GOOD, "count": 4}).json()["job_id"])
    assert [v["ok"] for v in done["result"]["variants"]] == [True] * 4
    assert seen == [(OWNER, None)] * 4


# --- ownership -----------------------------------------------------------------------------


def test_another_user_cannot_see_or_cancel_a_job(held):
    jid = submit().json()["job_id"]
    held.in_call()
    assert poll(jid, THEIRS).status_code == 404
    assert client.delete(f"/api/ai/jobs/{jid}", headers=THEIRS).status_code == 404
    assert client.get("/api/ai/jobs", headers=THEIRS).json() == {"jobs": []}
    assert poll(jid).json()["state"] == "running", "their cancel must not have touched it"
    held.let_go(3)
    assert finished(jid)["state"] == "done"


def test_the_listing_is_the_viewers_own(instant):
    mine = finished(submit().json()["job_id"])
    theirs = finished(submit(headers=THEIRS).json()["job_id"], THEIRS)
    listed = client.get("/api/ai/jobs", headers=MINE).json()["jobs"]
    assert [j["job_id"] for j in listed] == [mine["job_id"]]
    assert theirs["job_id"] not in json.dumps(listed)


def test_a_finished_job_read_by_its_owner_is_delivered(instant):
    jid = submit().json()["job_id"]
    assert jobs._wait(jid, 10)
    assert client.get("/api/ai/jobs", headers=MINE).json()["jobs"][0]["delivered"] is False
    poll(jid)
    assert client.get("/api/ai/jobs", headers=MINE).json()["jobs"][0]["delivered"] is True


def test_a_keepalive_read_leaves_a_finished_job_on_offer(instant):
    """The page's keep-alive reads its job with ?keepalive=1 when no page of Watch Builder is
    mounted to show the answer (the viewer is on another rail). A pass that finished meanwhile
    stays undelivered, so the listing still offers it to the next page (a reload, a
    new tab, a return from another rail), and that page's own read delivers it. Someone else's
    keep-alive read is the 404 an unknown id gets."""
    jid = submit().json()["job_id"]
    assert jobs._wait(jid, 10)
    r = client.get(f"/api/ai/jobs/{jid}?keepalive=1", headers=MINE)
    assert r.status_code == 200 and r.json()["state"] == "done", r.text
    assert client.get(f"/api/ai/jobs/{jid}?keepalive=1", headers=THEIRS).status_code == 404
    listed = client.get("/api/ai/jobs", headers=MINE).json()["jobs"]
    assert [(j["job_id"], j["state"], j["delivered"]) for j in listed] == [(jid, "done", False)]
    poll(jid)
    assert client.get("/api/ai/jobs", headers=MINE).json()["jobs"][0]["delivered"] is True


def test_a_direct_call_of_the_route_reads_as_the_page_does(instant):
    """tools/rail_smoke.py's probe calls app.ai_job() as a function, with no query string, and
    its read is the page's plain read, which delivers a finished job. A Query() default would
    hand the function a truthy FieldInfo for `keepalive`, and make it a keep-alive read."""
    routes = importlib.import_module("watch_builder.api.app")
    from watch_builder.api.identity import Identity
    jid = submit().json()["job_id"]
    assert jobs._wait(jid, 10)
    assert routes.ai_job(jid, ident=Identity(OWNER, False))["state"] == "done"
    assert client.get("/api/ai/jobs", headers=MINE).json()["jobs"][0]["delivered"] is True


# --- one job per person --------------------------------------------------------------------


def test_a_second_job_while_one_runs_is_refused_and_names_it(held):
    first = submit().json()["job_id"]
    held.in_call()
    r = submit("/api/ai/design", {"prompt": "another"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["job_id"] == first
    # Someone else is not blocked by it.
    other = submit(headers=THEIRS)
    assert other.status_code == 202, other.text
    held.let_go(6)


def test_a_job_after_a_cancel_waits_for_the_last_call(held):
    """Cancel, then submit again at once: accepted, but queued until the cancelled job's call
    in flight has returned, so one person never has two jobs making calls at once."""
    first = submit().json()["job_id"]
    held.in_call()
    assert client.delete(f"/api/ai/jobs/{first}", headers=MINE).json()["state"] == "cancelled"
    second = submit("/api/ai/design", {"prompt": "again"})
    assert second.status_code == 202, second.text
    sid = second.json()["job_id"]
    assert poll(sid).json()["state"] == "queued"
    assert len(held.calls) == 1, "the second job started a call while the first one's ran"
    held.let_go()                 # the cancelled job's call returns; it starts no other
    held.in_call()                # and only now the second job's first call
    assert poll(sid).json()["state"] == "running"
    held.let_go()
    assert finished(sid)["state"] == "done"
    assert len(held.calls) == 2


def test_submit_and_cancel_cannot_pile_up_jobs(held):
    """A cancelled job whose call is in flight is never dropped, and nor is one cancelled while
    it waited behind it. So a person holds at most two jobs whose worker has not returned; past
    that a submission is refused with the 409 that names the cancelled job still in a call,
    instead of filling the store for everyone until that call returns."""
    first = submit().json()["job_id"]
    held.in_call()
    client.delete(f"/api/ai/jobs/{first}", headers=MINE)
    second = submit("/api/ai/design", {"prompt": "again"})
    assert second.status_code == 202, second.text
    sid = second.json()["job_id"]
    assert client.delete(f"/api/ai/jobs/{sid}", headers=MINE).json()["state"] == "cancelled"
    for _ in range(3):
        r = submit("/api/ai/design", {"prompt": "and again"})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert (detail["error"], detail["job_id"]) == ("finishing", first), detail
        assert "still waiting on a model call" in detail["message"], detail
    assert set(jobs._JOBS) == {first, sid}
    held.let_go()                 # the cancelled call returns, and both workers with it
    assert jobs._wait(first, 10) and jobs._wait(sid, 10)
    again = submit("/api/ai/design", {"prompt": "now"})
    assert again.status_code == 202, again.text
    held.in_call()
    held.let_go()
    assert finished(again.json()["job_id"])["state"] == "done"
    assert len(held.calls) == 2


def test_a_worker_that_cannot_start_fails_its_job(instant, monkeypatch):
    """Thread.start can raise (no thread left to start). The POST is then a 500, and the job it
    created is failed with the reason and marked returned, so it neither blocks its owner's next
    submission (a 409 naming a job that never runs) nor holds a place in the store for good."""
    spawn = jobs._spawn

    def no_thread(target, name):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(jobs, "_spawn", no_thread)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/ai/design", json={"prompt": "a face"}, headers=MINE)
    assert r.status_code == 500, r.text
    [job] = jobs._JOBS.values()
    assert jobs._wait(job.id, 0), "the job is not marked returned, so the store never drops it"
    view = poll(job.id).json()
    assert view["state"] == "failed" and "can't start new thread" in view["error"], view
    assert instant == []
    monkeypatch.setattr(jobs, "_spawn", spawn)
    again = submit("/api/ai/design", {"prompt": "again"})
    assert again.status_code == 202, again.text
    assert finished(again.json()["job_id"])["state"] == "done"


# --- cancel --------------------------------------------------------------------------------


def test_cancel_starts_no_new_call(held):
    jid = submit(body={"design": GOOD, "count": 4}).json()["job_id"]
    held.in_call()
    held.let_go()
    held.in_call()                # variant two's call is in flight
    r = client.delete(f"/api/ai/jobs/{jid}", headers=MINE)
    assert r.status_code == 200 and r.json()["state"] == "cancelled", r.text
    assert r.json()["progress"]["done"] == 1
    held.let_go()                 # its answer arrives after the cancel, for nobody
    done = finished(jid)
    assert len(held.calls) == 2, f"a call started after the cancel: {len(held.calls)}"
    assert done["state"] == "cancelled" and done["result"] is None, done
    # The job stays as the cancel answered it: the late variant is dropped, not added.
    assert [v["ok"] for v in done["partial"]] == [True] and done["progress"]["done"] == 1


def test_a_queued_job_cancelled_makes_no_call(held):
    first = submit().json()["job_id"]
    held.in_call()
    client.delete(f"/api/ai/jobs/{first}", headers=MINE)
    sid = submit("/api/ai/design", {"prompt": "again"}).json()["job_id"]
    assert client.delete(f"/api/ai/jobs/{sid}", headers=MINE).json()["state"] == "cancelled"
    held.let_go()
    assert finished(sid)["state"] == "cancelled"
    assert finished(first)["state"] == "cancelled"
    assert len(held.calls) == 1


def test_cancelling_a_finished_job_changes_nothing(instant):
    jid = submit().json()["job_id"]
    done = finished(jid)
    again = client.delete(f"/api/ai/jobs/{jid}", headers=MINE).json()
    assert again["state"] == "done" and again["result"] == done["result"]


# --- the ceiling ---------------------------------------------------------------------------


def test_a_job_past_its_ceiling_starts_no_call(monkeypatch):
    """1000 s a call against the 2700 s ceiling: three calls start (at 0, 1000 and 2000 s), the
    rest are reported as stopped, and the job is still a done pass with three cells, whose
    `stopped` names the ceiling. The owner's page reads it meanwhile, so it is the ceiling and
    not the lease that stops it."""
    clock = {"now": 0.0}
    monkeypatch.setattr(jobs, "_mono", lambda: clock["now"])
    calls = []

    def chat(messages, **kw):
        calls.append(kw)
        clock["now"] += 1000.0
        owner_looks()
        return REPLY
    monkeypatch.setattr(broker, "chat", chat)
    done = finished(submit(body={"design": GOOD, "count": 6}).json()["job_id"])
    assert len(calls) == 3, calls
    variants = done["result"]["variants"]
    assert [v["ok"] for v in variants] == [True] * 3 + [False] * 3
    assert all("ceiling" in v["error"] for v in variants[3:]), variants
    assert done["state"] == "done" and done["progress"]["done"] == 3
    assert done["stopped"] == "ceiling", done


@pytest.mark.parametrize("claude", [True, False])
def test_the_ceiling_outlasts_the_longest_pass_at_the_estimate(monkeypatch, claude):
    """variation_max variants at up to three calls each, at the estimate's own per-call figure,
    must fit under the ceiling, or the ceiling cuts short a pass that is going as estimated."""
    from watch_builder import ai
    from watch_builder.config import settings
    monkeypatch.setattr(ai, "on_claude", lambda requester: claude)
    per_call = ai.estimate("variations", 1, requester=OWNER)["approx_seconds"]
    assert settings.variation_max * 3 * per_call < jobs.JOB_CEILING_S


# --- the lease (the moving-clock cases are in test_cli_requester.py) -------------------------


def test_an_owner_back_while_a_call_is_in_flight_keeps_the_job_going(held, monkeypatch):
    """The tab was closed and the lease lapsed during the first call. The owner reloads before
    that call returns: their read renews the lease, so the call's answer is kept and the next
    call starts as if they had never left."""
    clock = {"now": 0.0}
    monkeypatch.setattr(jobs, "_mono", lambda: clock["now"])
    jid = submit().json()["job_id"]
    held.in_call()
    clock["now"] = jobs.LEASE_S + 60.0
    assert poll(jid).json()["state"] == "running"
    held.let_go()
    held.in_call()
    held.let_go(2)
    done = finished(jid)
    assert (done["state"], done["stopped"], len(held.calls)) == ("done", None, 3), done


def test_a_lapsed_lease_leaves_the_cancel_working(held, monkeypatch):
    """A job past its lease is still its owner's to cancel, and is cancelled, not stopped."""
    clock = {"now": 0.0}
    monkeypatch.setattr(jobs, "_mono", lambda: clock["now"])
    jid = submit().json()["job_id"]
    held.in_call()
    clock["now"] = jobs.LEASE_S + 60.0
    r = client.delete(f"/api/ai/jobs/{jid}", headers=MINE)
    assert r.json()["state"] == "cancelled", r.text
    held.let_go()
    done = finished(jid)
    assert (done["state"], done["stopped"], len(held.calls)) == ("cancelled", None, 1), done


# --- the store's bounds --------------------------------------------------------------------


def test_a_finished_job_expires_after_the_keep_window(instant, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(jobs, "_mono", lambda: clock["now"])
    jid = submit().json()["job_id"]
    assert jobs._wait(jid, 10)
    clock["now"] = jobs.KEEP_FINISHED_S - 1
    assert poll(jid).status_code == 200
    clock["now"] = jobs.KEEP_FINISHED_S + 1
    r = poll(jid)
    assert r.status_code == 404 and "restart" in r.json()["detail"], r.text
    assert client.get("/api/ai/jobs", headers=MINE).json() == {"jobs": []}


def test_a_running_job_never_expires(held, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(jobs, "_mono", lambda: clock["now"])
    jid = submit().json()["job_id"]
    held.in_call()
    clock["now"] = jobs.KEEP_FINISHED_S * 10
    assert poll(jid).json()["state"] == "running"
    held.let_go(3)


def test_at_the_cap_the_oldest_finished_job_makes_room(instant, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_JOBS", 3)
    ids = []
    for _ in range(3):
        jid = submit().json()["job_id"]
        assert jobs._wait(jid, 10)
        ids.append(jid)
    newest = submit()
    assert newest.status_code == 202, newest.text
    assert len(jobs._JOBS) == 3
    assert ids[0] not in jobs._JOBS and set(ids[1:]) <= set(jobs._JOBS)


def test_a_store_full_of_running_jobs_refuses_a_new_one(held, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_JOBS", 2)
    for user in ("u1", "u2"):
        assert submit(headers={"X-Platform-User": user}).status_code == 202
        held.in_call()
    r = submit(headers={"X-Platform-User": "u3"})
    assert r.status_code == 503, r.text
    assert len(jobs._JOBS) == 2
    held.let_go(6)


def test_a_cancelled_job_still_in_a_call_holds_its_place(held, monkeypatch):
    """Its worker thread is still alive, so dropping it would leave that thread unbounded."""
    monkeypatch.setattr(jobs, "MAX_JOBS", 1)
    jid = submit().json()["job_id"]
    held.in_call()
    client.delete(f"/api/ai/jobs/{jid}", headers=MINE)
    assert submit(headers=THEIRS).status_code == 503
    held.let_go()
    assert jobs._wait(jid, 10)
    assert submit(headers=THEIRS).status_code == 202
    held.let_go(3)


# --- a restart -----------------------------------------------------------------------------


def test_a_restart_loses_the_job_and_says_so(instant):
    """A new process has an empty store: the id the page is polling is a 404 whose detail says
    a restart loses jobs in flight, which is what the page shows instead of polling on."""
    jid = submit().json()["job_id"]
    assert jobs._wait(jid, 10)
    importlib.reload(jobs)
    try:
        r = poll(jid)
        assert r.status_code == 404
        assert "restart" in r.json()["detail"] and "nothing is running" in r.json()["detail"]
        assert client.get("/api/ai/jobs", headers=MINE).json() == {"jobs": []}
    finally:
        importlib.reload(jobs)
