"""Whose Claude subscription may answer the designer, and how long each call may wait.

The broker answers a buffered chat call from a platform owner's own Claude CLI subscription
when the call's `requester` names an owner and its @role is switched on there. Both AI features
run as jobs the page polls (jobs.py): the person who submitted the job is waiting on it, and the
prompt holds their own description or the design they are editing, so the job's owner is named
on every call. What these pin:

* every call of a job names its owner, the reformat and the repair included, however long the
  calls before it took: no request budget applies to a job, so no late call goes nameless (the
  request-budget trimming this replaced named only calls started in a request's first ~60 s).
  "However long" is `Clocks`: each scripted call moves every clock a budget could read, not
  only the job's own, because the old request budget read `time.monotonic`. Those jobs are
  watched: the owner's page reads each one during every call (`owners_look`), as it does while
  it is open;
* a job nobody is watching stops at its lease (jobs.LEASE_S) on the same moving clocks: the
  call in flight finishes, a variant it completes is kept, no call starts after it, and the job
  says why; a job its owner reads, or lists, runs past the lease, and anyone else's read renews
  nothing; a job read for a while and then abandoned stops a lease after the LAST read, and many
  reads buy one lease, not one each (a renewal sets the lease, it never adds to it);
* every call waits the whole `broker.CHAT_TIMEOUT`, the ceiling that outlasts the broker's CLI
  timeout plus its local fallback (checked live by tools/rail_smoke.py cli_client_timeouts
  against the broker's reported cli.timeout_s, from a registry tools/tests/test_rail_smoke.py
  holds equal to it);
* the facade keeps a name only on the designer's own role (`broker._cli_requester`);
* a job with no identity behind it names nobody, and a viewer who is not a subscriber is named
  as themselves only (the broker answers them locally), never as anyone else;
* the cost estimate reads Claude for exactly the viewers whose calls would go there, and then
  claims no GPU; the designer chip reads the same while every broker read answers, and when
  only /v1/roles or /v1/models fails it reads local for everyone, which under-claims and never
  over-claims.

Offline: the broker is a fake `urlopen` or a fake `broker.chat`. Neutral account names.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

os.environ.pop("PLATFORM_STANDALONE", None)

from watch_builder import ai, broker, jobs  # noqa: E402
from watch_builder.api.app import app  # noqa: E402
from watch_builder.config import settings  # noqa: E402

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER, "X-Platform-Admin": "0"}
THEIRS = {"X-Platform-User": OTHER, "X-Platform-Admin": "0"}

GOOD = {
    "schema": 1, "name": "Stub", "background": "#000000", "platforms": ["emery"],
    "elements": [
        {"type": "time", "x": 0, "y": "30%", "w": "100%", "color": "#FFFFFF"},
        {"type": "date", "x": 0, "y": "60%", "w": "100%", "color": "#AAAAAA",
         "font": "auto-small"},
    ],
}


# Schema-valid, rejected by the layout audit (two text elements 4% apart): forces a repair.
OVERLAPPING = {
    "schema": 1, "name": "Bad", "background": "#000000", "platforms": ["emery"],
    "elements": [
        {"type": "time", "x": 0, "y": "30%", "w": "100%"},
        {"type": "date", "x": 0, "y": "34%", "w": "100%"},
    ],
}


class _Resp:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _designer(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(settings, "designer_model", "@watch-builder-designer")


@pytest.fixture(autouse=True)
def _no_jobs_left():
    """Every test starts with an empty store and leaves no worker behind."""
    jobs._JOBS.clear()
    jobs._OWNER_LOCKS.clear()
    yield
    for job in list(jobs._JOBS.values()):
        job.cancel.set()
        assert job.exited.wait(10), f"a job's worker never returned: {job.id}"
    jobs._JOBS.clear()
    jobs._OWNER_LOCKS.clear()


_CLOCKS = ("monotonic", "perf_counter", "time", "monotonic_ns", "perf_counter_ns", "time_ns")
_REAL = {name: getattr(time, name) for name in _CLOCKS}


class Clocks:
    """The clocks a request budget in this rail would plausibly read, moved forward together.

    That is `time.monotonic`, `time.perf_counter` and `time.time` (and their _ns forms) on the
    time module, which code reaches as `time.monotonic()`, and every attribute of a loaded
    watch_builder module bound to one of them: jobs._mono, or a `from time import monotonic`.
    Not every clock: `datetime.now()` reads the system clock in C, and a clock captured as a
    default argument, a closure or a class attribute is not a module attribute, so a budget
    built on one of those would not be moved here.
    Each reads the real clock plus `offset`, so real time still passes and no wait (a test's,
    or the TestClient's event loop) can stall on a frozen clock. `bound` names the module
    attributes it rebound, so a test can show the job's own clock was among them."""

    def __init__(self, monkeypatch) -> None:
        self.offset = 0.0
        fakes = {}
        for name, real in _REAL.items():
            if name.endswith("_ns"):
                fakes[name] = (lambda real=real: real() + int(self.offset * 1e9))
            else:
                fakes[name] = (lambda real=real: real() + self.offset)
            monkeypatch.setattr(time, name, fakes[name])
        self.bound: list[str] = []
        for modname, mod in list(sys.modules.items()):
            if modname != "watch_builder" and not modname.startswith("watch_builder."):
                continue
            for attr, val in list(vars(mod).items()):
                for name, real in _REAL.items():
                    if val is real:
                        monkeypatch.setattr(mod, attr, fakes[name])
                        self.bound.append(f"{modname}.{attr}")

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture()
def clocks(monkeypatch):
    return Clocks(monkeypatch)


def _headers(owner: str | None) -> dict:
    return {"X-Platform-User": owner, "X-Platform-Admin": "0"} if owner else {}


def owners_look() -> None:
    """Each running job's owner reads it over HTTP, as their open page does every 1.5 s, which
    renews its lease (jobs.LEASE_S). A scripted call calls this after it has moved the clocks, so
    a job whose calls take longer than the lease is still watched when each one returns, the way
    it is while its page is open. A test of a job nobody watches passes `look=None`."""
    client = TestClient(app)
    for job in list(jobs._JOBS.values()):
        if job.state in jobs.ACTIVE:
            r = client.get(f"/api/ai/jobs/{job.id}", headers=_headers(job.owner))
            assert r.status_code == 200, r.text


class Wire(list):
    """Every /v1/chat request as (body, timeout); each call then moves every clock by `cost`,
    and then `look` runs (owners_look: the page is open)."""
    cost = 0.0
    look = staticmethod(owners_look)


@pytest.fixture()
def wire(monkeypatch, clocks):
    """Every /v1/chat request: (body, timeout). The reply is a valid design document."""
    sent = Wire()

    def fake_urlopen(req, timeout=None):
        assert req.full_url.endswith("/v1/chat"), req.full_url
        sent.append((json.loads(req.data), timeout))
        clocks.advance(sent.cost)
        sent.look()
        return _Resp({"message": {"role": "assistant", "content": json.dumps(GOOD)}})

    monkeypatch.setattr(broker.urllib.request, "urlopen", fake_urlopen)
    return sent


def carried(sent) -> list:
    assert sent, "no call reached the facade, so nothing was asserted"
    return [body.get("requester", "ABSENT") for body, _ in sent]


def _ask(**kw):
    return broker.chat([{"role": "user", "content": "q"}], max_tokens=10, **kw)


def run_job(path: str, body: dict, headers: dict) -> dict:
    """POST an AI route, wait for its worker to return, and read the finished job."""
    client = TestClient(app)
    r = client.post(path, json=body, headers=headers)
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert jobs._wait(jid, 10), "the job's worker never returned"
    got = client.get(f"/api/ai/jobs/{jid}", headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


# --- the facade --------------------------------------------------------------------------


def test_the_designer_role_carries_the_requester(wire):
    _ask(model="@watch-builder-designer", requester=OWNER)
    assert carried(wire) == [OWNER]


def test_no_requester_means_none_on_the_wire(wire):
    _ask(model="@watch-builder-designer")
    assert carried(wire) == ["ABSENT"]


def test_a_foreign_role_never_carries_the_name(wire):
    for model in ("@iep", "mistral-small3.2:24b"):
        _ask(model=model, requester=OWNER)
    assert carried(wire) == ["ABSENT"] * 2


@pytest.mark.parametrize("cost", [0.0, 61.0, 250.0, 479.0])
def test_every_call_waits_the_whole_ceiling(wire, clocks, cost):
    """No call is given less than CHAT_TIMEOUT, so none can give up on a hung CLI call before
    the broker's local fallback has answered it, however long the calls before it took: each
    call moves every clock a budget could read (Clocks) by `cost`."""
    wire.cost = cost
    _ask(model="m")
    run_job("/api/ai/variations", {"design": GOOD, "count": 2}, MINE)
    assert [t for _, t in wire] == [broker.CHAT_TIMEOUT] * 3
    # The witness: the cost reached time.monotonic and the job's own clock.
    assert "watch_builder.jobs._mono" in clocks.bound, clocks.bound
    assert clocks.offset == 3 * cost


# --- the routes --------------------------------------------------------------------------


def test_describe_a_face_names_the_viewer(wire):
    job = run_job("/api/ai/design", {"prompt": "a minimal face"}, MINE)
    assert job["state"] == "done", job
    assert carried(wire) == [OWNER]


def test_every_variation_call_names_the_viewer(wire):
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 3}, MINE)
    assert job["state"] == "done", job
    assert all(v["ok"] for v in job["result"]["variants"]), job
    assert carried(wire) == [OWNER] * 3


def test_a_job_with_no_identity_names_nobody(wire, monkeypatch):
    """Standalone dev, where identity() answers user None: the job's calls carry no name."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    run_job("/api/ai/variations", {"design": GOOD, "count": 2}, {})
    assert carried(wire) == ["ABSENT"] * 2


def test_a_non_owner_is_named_as_themselves_only(wire):
    """The rail names the person asking and the broker decides who is a subscriber: a viewer
    who is not one is named as themselves (and answered locally there), never as the owner,
    even while the owner has a job of their own."""
    run_job("/api/ai/design", {"prompt": "the owner's"}, MINE)
    run_job("/api/ai/variations", {"design": GOOD, "count": 2}, THEIRS)
    assert carried(wire) == [OWNER, OTHER, OTHER]


class Scripted:
    """broker.chat stand-in: scripted replies, every call's kwargs recorded, and every clock a
    budget could read (Clocks: time.monotonic, the job's own, ...) moved `cost` seconds per
    call, after which `look` runs: owners_look (the page is open) unless a test says otherwise,
    None for nobody. `started` is time.monotonic() as each call came in."""

    def __init__(self, replies, clocks: Clocks, cost=0.0, look=owners_look):
        self.replies, self.clocks, self.cost, self.look = list(replies), clocks, cost, look
        self.calls: list[dict] = []
        self.started: list[float] = []

    def __call__(self, messages, **kw):
        self.calls.append(kw)
        self.started.append(time.monotonic())
        self.clocks.advance(self.cost)
        if self.look is not None:
            self.look()
        return self.replies.pop(0)


@pytest.fixture()
def scripted(monkeypatch, clocks):
    def install(*replies, cost=0.0, look=owners_look):
        s = Scripted(replies, clocks, cost, look)
        monkeypatch.setattr(broker, "chat", s)
        return s
    return install


def test_a_repair_carries_the_name_too(scripted):
    """The not-JSON reformat and the rejected-document repair are the same person's request."""
    s = scripted("not json", json.dumps(OVERLAPPING), json.dumps(GOOD))
    ai.design_from_prompt("a face", ["emery"], requester=OWNER)
    assert [c.get("requester") for c in s.calls] == [OWNER] * 3


def test_a_jobs_reformat_and_repair_carry_the_owner(scripted):
    s = scripted("not json", json.dumps(OVERLAPPING), json.dumps(GOOD))
    job = run_job("/api/ai/design", {"prompt": "a face"}, MINE)
    assert job["state"] == "done" and job["calls"] == 3, job
    assert [c.get("requester") for c in s.calls] == [OWNER] * 3


@pytest.mark.parametrize("cost", [0.0, 61.0, 250.0, 479.0])
def test_every_call_of_a_slow_design_names_the_owner(scripted, clocks, cost):
    """The request-budget defect on the design route. An earlier version gave
    design_from_prompt a deadline of its own, so after a slow first call its reformat and repair
    went nameless. A design job has no request budget:
    whatever the describe call cost, the reformat and the repair name the owner and pass no
    timeout of their own. Cost 0 is the case a budget never trips."""
    s = scripted("not json", json.dumps(OVERLAPPING), json.dumps(GOOD), cost=cost)
    job = run_job("/api/ai/design", {"prompt": "a face"}, MINE)
    assert job["state"] == "done" and job["calls"] == 3, job
    assert [(c.get("requester"), c.get("timeout")) for c in s.calls] == [(OWNER, None)] * 3
    gaps = [b - a for a, b in zip(s.started, s.started[1:])]
    assert len(gaps) == 2 and all(g >= cost for g in gaps), gaps
    assert "watch_builder.jobs._mono" in clocks.bound, clocks.bound


@pytest.mark.parametrize("cost", [0.0, 61.0, 250.0, 479.0])
def test_every_call_of_a_slow_pass_names_the_owner(scripted, clocks, cost):
    """The request-budget regression. Under the old 540 s request budget, read from
    time.monotonic, a call that started with less than the full CHAT_TIMEOUT left went
    nameless: 61 s per call named only the first, and 250 s trimmed every call after it. A job
    has no request budget, so whatever the calls before it cost, every call names the owner and
    passes no timeout of its own (the facade then waits the whole CHAT_TIMEOUT). At cost 0 a
    budget never trips, which is the case that shows the others fail for the time they took and
    nothing else."""
    s = scripted(*[json.dumps(GOOD)] * 5, cost=cost)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 5}, MINE)
    assert [v["ok"] for v in job["result"]["variants"]] == [True] * 5, job
    assert [(c.get("requester"), c.get("timeout")) for c in s.calls] == [(OWNER, None)] * 5
    # The witness: each call saw time.monotonic `cost` later than the one before, and the
    # job's own clock moved with it.
    gaps = [b - a for a, b in zip(s.started, s.started[1:])]
    assert len(gaps) == 4 and all(g >= cost for g in gaps), gaps
    assert "watch_builder.jobs._mono" in clocks.bound, clocks.bound


# --- the lease: a job nobody is watching stops ----------------------------------------------
#
# A call that costs LAPSE outlasts the lease by itself: the owner's tab was closed while it ran.

LAPSE = jobs.LEASE_S + 1.0


def test_a_pass_nobody_watches_stops_at_its_lease(scripted, clocks):
    """The first call outlasts the lease and nobody reads the job meanwhile. That call's answer
    is kept (it was paid for, and nobody asked for it to be dropped), no call starts after it,
    and the job ends done with what finished: the rest marked not attempted, saying why, and
    `stopped` naming the lease."""
    s = scripted(*[json.dumps(GOOD)] * 3, cost=LAPSE, look=None)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 3}, MINE)
    assert len(s.calls) == 1, f"{len(s.calls)} calls started; no call may start after the lapse"
    assert (job["state"], job["stopped"], job["calls"]) == ("done", "unwatched", 1), job
    variants = job["result"]["variants"]
    assert [v["ok"] for v in variants] == [True, False, False], variants
    assert all("nobody was watching" in v["error"] for v in variants[1:]), variants
    assert job["progress"]["done"] == 1 and [v["ok"] for v in job["partial"]] == [True], job
    assert job["lease_s"] == jobs.LEASE_S
    assert "watch_builder.jobs._mono" in clocks.bound, clocks.bound


def test_a_design_nobody_watches_stops_before_its_reformat(scripted):
    """A design's describe call outlasts the lease and comes back as prose: the reformat it
    needs never starts, and the job fails with the lease as its reason, not a model error."""
    s = scripted("not json", json.dumps(GOOD), cost=LAPSE, look=None)
    job = run_job("/api/ai/design", {"prompt": "a face"}, MINE)
    assert len(s.calls) == 1, s.calls
    assert (job["state"], job["stopped"], job["result"]) == ("failed", "unwatched", None), job
    assert "nobody was watching" in job["error"], job


@pytest.mark.parametrize("cost", [jobs.LEASE_S - 1.0, LAPSE])
def test_a_watched_job_runs_past_its_lease(scripted, clocks, cost):
    """The owner's page reads the job during every call (owners_look), so all five calls start
    however long each takes, though the pass as a whole runs four leases or more."""
    s = scripted(*[json.dumps(GOOD)] * 5, cost=cost)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 5}, MINE)
    assert len(s.calls) == 5, f"only {len(s.calls)} of 5 calls started"
    assert (job["state"], job["stopped"]) == ("done", None), job
    assert [v["ok"] for v in job["result"]["variants"]] == [True] * 5, job
    # The witness: the pass ran well past the lease on the job's own clock.
    assert clocks.offset >= 4 * jobs.LEASE_S - 4.0, clocks.offset


def test_only_the_owners_read_renews_the_lease(scripted):
    """Someone else reading the job's id is answered the 404 an unknown id gets, and the job is
    no less unwatched for it: it stops at its lease as if nobody had looked."""
    seen: list[int] = []

    def someone_else_looks():
        for jid in list(jobs._JOBS):
            seen.append(TestClient(app).get(f"/api/ai/jobs/{jid}", headers=THEIRS).status_code)

    s = scripted(*[json.dumps(GOOD)] * 3, cost=LAPSE, look=someone_else_looks)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 3}, MINE)
    assert seen == [404], seen
    assert len(s.calls) == 1 and job["stopped"] == "unwatched", job


def test_the_listing_a_reloaded_page_reads_renews_the_lease(scripted):
    """A reloaded page reads GET /api/ai/jobs before the job it picks: that read is the owner
    looking, and on its own keeps the job going."""
    def reload_lists():
        r = TestClient(app).get("/api/ai/jobs", headers=MINE)
        assert r.status_code == 200 and r.json()["jobs"], r.text

    s = scripted(*[json.dumps(GOOD)] * 3, cost=LAPSE, look=reload_lists)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 3}, MINE)
    assert len(s.calls) == 3 and job["stopped"] is None, job


def test_the_keep_alives_read_renews_the_lease(scripted):
    """The page's keep-alive reads with ?keepalive=1 when no page is mounted to show its answer
    (module.tsx keepTick), which leaves a finished job undelivered but is the owner looking all
    the same: on its own it keeps the job going while the viewer is on another rail."""
    def keep_alive_reads():
        for jid in list(jobs._JOBS):
            r = TestClient(app).get(f"/api/ai/jobs/{jid}?keepalive=1", headers=MINE)
            assert r.status_code == 200, r.text

    s = scripted(*[json.dumps(GOOD)] * 3, cost=LAPSE, look=keep_alive_reads)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 3}, MINE)
    assert len(s.calls) == 3 and job["stopped"] is None, job


# The realistic close (the 2026-10-09 review's probe): the page reads its job at once after the
# submit and for as long as it is open, then the tab is closed. Every other stop test here has no
# read at all, so a renewal that pinned the job for ever, or stacked a lease on each read, passed
# all of them while no closed tab's job would ever stop.


def test_a_job_watched_then_abandoned_stops_a_lease_after_the_last_read(scripted):
    """The page is open during the first call only, and reads the job as it returns (299 s), so
    the lease runs to 599 s. Calls start at 0, 299 and 598 s; the fourth, at 897 s, is refused,
    and the job says nobody was watching. A renewal that pins the lease lets all four start."""
    looks = {"n": 0}

    def page_then_closed():
        looks["n"] += 1
        if looks["n"] == 1:
            owners_look()

    s = scripted(*[json.dumps(GOOD)] * 4, cost=jobs.LEASE_S - 1.0, look=page_then_closed)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 4}, MINE)
    assert len(s.calls) == 3, f"{len(s.calls)} calls started after the tab was closed"
    assert job["stopped"] == "unwatched", job


def test_a_page_open_for_three_calls_buys_one_lease_not_three(scripted):
    """A renewal sets the lease to LEASE_S from that read; it never adds to what is left. The
    page reads during the first three 100 s calls, last at 300 s, so the lease ends at 600 s:
    calls start at 0 to 500 s and the seventh (600 s) is refused. Stacking a lease on each read
    would carry the job to 1200 s and start all nine."""
    looks = {"n": 0}

    def open_for_three_calls():
        looks["n"] += 1
        if looks["n"] <= 3:
            owners_look()

    s = scripted(*[json.dumps(GOOD)] * 9, cost=100.0, look=open_for_three_calls)
    job = run_job("/api/ai/variations", {"design": GOOD, "count": 9}, MINE)
    assert len(s.calls) == 6, f"{len(s.calls)} calls started"
    assert job["stopped"] == "unwatched", job


# The longest a hidden tab's keep-alive may wait: browsers clamp a hidden page's timers to about
# once a minute (module.tsx KEEPALIVE_MS fires every 30 s, so the clamp is what bounds it).
HIDDEN_TIMER_CLAMP_S = 60.0


def test_the_lease_outlasts_a_throttled_keep_alive():
    """A hidden tab renews the lease about once a minute at worst, so the lease must outlast
    several of those lost in a row (a dropped connection, a gateway restart), and still stop a
    closed tab's job well inside the 45-minute ceiling."""
    assert jobs.LEASE_S >= 4 * HIDDEN_TIMER_CLAMP_S, jobs.LEASE_S
    assert jobs.LEASE_S <= jobs.JOB_CEILING_S / 4, jobs.LEASE_S


# --- the chip and the estimate -------------------------------------------------------------


@pytest.fixture()
def chips(monkeypatch):
    cli = {"ready": True, "owners": [OWNER], "roles": ["watch-builder-designer"], "active": []}
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "watch-builder-designer",
                                                   "resolved": "mistral-small3.2:24b"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "mistral-small3.2:24b"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    client = TestClient(app)

    def read(headers):
        r = client.get("/api/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        return {m["slot"]: m["backend"] for m in r.json()["models"]}

    def estimate(headers):
        r = client.get("/api/ai/estimate?kind=variations&count=9", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    read.cli = cli  # type: ignore[attr-defined]
    read.estimate = estimate  # type: ignore[attr-defined]
    return read


def test_an_owner_sees_the_designer_on_claude(chips):
    assert chips(MINE) == {"designer": "claude"}


def test_a_non_owner_sees_ollama(chips):
    assert chips({"X-Platform-User": OTHER}) == {"designer": "ollama"}


def test_a_role_off_the_allowlist_reads_ollama(chips):
    chips.cli["roles"] = ["iep"]
    assert chips(MINE) == {"designer": "ollama"}


def test_the_estimate_claims_no_gpu_for_a_viewer_claude_serves(chips):
    got = chips.estimate(MINE)
    assert (got["backend"], got["holds_gpu"]) == ("claude", False), got
    assert "no GPU" in got["note"], got


def test_the_estimate_claims_the_gpu_for_everyone_else(chips):
    got = chips.estimate(THEIRS)
    assert (got["backend"], got["holds_gpu"]) == ("ollama", True), got
    chips.cli["roles"] = ["iep"]
    assert chips.estimate(MINE)["holds_gpu"] is True


def test_the_estimate_and_the_chip_agree_for_every_viewer(chips):
    """While the broker answers every read, the chip and the estimate apply one predicate
    (modelstate.cli_serves over /v1/status) to one identity, so they tell each viewer the same
    story about who answers. They can disagree only when /v1/roles or /v1/models fails and
    /v1/status does not; the next test pins which way that goes."""
    seen = []
    for roles in (["watch-builder-designer"], ["iep"]):
        chips.cli["roles"] = roles
        for headers in (MINE, THEIRS):
            backend = chips(headers)["designer"]
            assert chips.estimate(headers)["backend"] == backend, (roles, headers)
            seen.append(backend)
    assert set(seen) == {"claude", "ollama"}, "the premise: both answers were compared"


@pytest.mark.parametrize("fails", ["roles", "models"])
def test_a_partial_broker_failure_only_under_claims(chips, monkeypatch, fails):
    """The chip reads all three of /v1/roles, /v1/models and /v1/status and reports the broker
    unreachable (local) if any fails; the estimate reads /v1/status alone. With only roles or
    models failing, an owner's calls do go to Claude: the estimate says so, and the chip says
    local, an under-claim. The chip never says Claude where the estimate does not."""
    def down():
        raise broker.BrokerError(f"/v1/{fails}: HTTP Error 500")
    monkeypatch.setattr(broker, fails, down)
    assert chips(MINE) == {"designer": "ollama"}
    assert chips.estimate(MINE)["backend"] == "claude"
    assert chips(THEIRS) == {"designer": "ollama"}
    assert chips.estimate(THEIRS)["backend"] == "ollama"


def test_an_unreadable_status_estimates_the_gpu(monkeypatch):
    def down():
        raise broker.BrokerError("unreachable")
    monkeypatch.setattr(broker, "status", down)
    assert ai.estimate("design", 1, requester=OWNER)["holds_gpu"] is True


def test_the_variation_count_is_checked_before_a_job_exists(wire):
    r = TestClient(app).post("/api/ai/variations",
                             json={"design": GOOD, "count": settings.variation_max + 1},
                             headers=MINE)
    assert r.status_code == 422, r.text
    assert jobs._JOBS == {} and wire == []


def test_a_design_is_validated_before_a_job_exists(wire):
    client = TestClient(app)
    assert client.post("/api/ai/design", json={"prompt": "   "}, headers=MINE).status_code == 422
    bad = dict(GOOD, background="not a colour")
    assert client.post("/api/ai/variations", json={"design": bad},
                       headers=MINE).status_code == 422
    assert jobs._JOBS == {} and wire == []
