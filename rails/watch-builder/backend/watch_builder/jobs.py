"""The two AI features as background jobs the page polls.

WHY JOBS
--------
POST /api/ai/design and /api/ai/variations used to make their model calls inside the request.
A design is up to three calls and a variation pass up to three per variant, so the whole thing
had to finish inside the gateway's 600 s buffered read (and Cloudflare's ~100 s edge timeout for
a remote viewer). The rail budgeted the request to fit, and a call that started late in it got
less than the broker's full timeout: too little to wait out a hung CLI call plus the broker's
local fallback, so it had to go nameless. Only the first minute or so of a request could reach
Claude, and an owner's later variants ran on the shared GPU while the Designer chip said Claude.

Now each route creates a job and answers 202 at once. A worker thread makes the calls and the
page polls GET /api/ai/jobs/{job_id}. No request waits on a model, so every call waits the whole
broker.CHAT_TIMEOUT and every call carries the person who asked.

WHOSE NAME A JOB'S CALLS CARRY
------------------------------
The worker is not the request, so the requester rule is held here, where the worker starts:

  * The person asked. A job exists only because someone POSTed one of the two AI routes.
    Nothing in this rail creates, retries or resumes a job on its own, and a job lost to a
    restart is gone, never re-run.
  * The job is theirs. ``owner`` is the submitter's identity (the route's ``ident.user``), and
    get, listing and cancel answer only that identity; anyone else gets the 404 an unknown id
    gets.
  * The name is theirs. ``Run.requester``, the name every call of the job carries, IS the job's
    ``owner``: it is fixed when ``submit`` creates the job and handed to the work as a value, so
    no later request, and no other user, can change whose name the calls carry.

broker._cli_requester still keeps the name only on the designer's own role, and the broker
still decides whether that name belongs to a Claude subscriber (BROKER_CLI_OWNERS); for anyone
else it answers the call from the local model.

THE BOUNDS, AND WHY THESE NUMBERS
---------------------------------
* JOB_CEILING_S: no call STARTS once a job has run 45 minutes (``Run.gate``). The call in flight
  then is never cut short, and it waits at most broker.CHAT_TIMEOUT, so a job ends within its
  ceiling plus one call. The longest pass the rail allows is variation_max (16) variants at up
  to three calls each, 48 calls; at the 40 s a call is estimated at on Claude that is 32
  minutes, so 45 stops a runaway (a broker answering every call just under its timeout, for
  hours) without cutting short a pass whose calls take what the estimate says.
* One active job per person. A second submission while that person's job is queued or running
  is REFUSED (Busy, a 409 naming the job), not queued: the page shows one job at a time and
  attaches to the named one, and a silent second job would double a cost the person was shown
  once. A submission after a cancel is accepted and waits as ``queued`` until the cancelled
  job's last call has returned, so one person never has two jobs making calls at once.
* MAX_WORKERS_PER_PERSON (2): a person holds at most two jobs whose worker has not returned,
  the cancelled one still in a call and one queued behind it. Neither may be dropped while its
  worker lives (see MAX_JOBS), so without this, cancelling the queued one and submitting again,
  over and over, would fill the store and lock everyone else out for as long as the first call
  takes. A submission past it is refused the same way (Busy, a 409 naming the cancelled job).
* LEASE_S (300 s): a job nobody is watching stops. ``submit`` leases the job for LEASE_S, and
  every read of it by its owner sets the lease to LEASE_S from that read (``_renew_locked``: it
  never adds to what is left): GET /api/ai/jobs/{job_id} (``get``), the keep-alive's reads of
  it included (``?keepalive=1`` or not), and the listing a reloaded page reads to pick its job
  up again (``listing``). The page reads the job every 1.5 s while it
  is visible and in touch with the rail, and every 30 s on a keep-alive otherwise (module.tsx
  KEEPALIVE_MS): while its tab is hidden, while the viewer is on another rail in the same
  platform tab (the keep-alive lives outside the component, which the shell unmounts), and
  after its poll has lost touch with the rail. Once the lease has lapsed, ``Run.gate`` refuses
  to start the next call, the same way the ceiling does. Why 300: a hidden tab's keep-alive is
  a timer, and browsers clamp a hidden tab's timers to about once a minute (Chrome's intensive
  throttling), so five minutes outlasts four missed renewals in a row; a dropped connection or
  a gateway restart costs the job nothing as long as one read gets through within the lease,
  because the page keeps asking at the keep-alive's pace until the rail answers again (it does
  not give up after its poll's 90 s of failures, module.tsx MAX_FAILED_POLLS); and a closed tab
  or browser stops its job within five minutes of its last read plus the call then in flight,
  instead of running on in its owner's name for the 45-minute ceiling.
  A call already sent when the lease lapses still finishes: it was paid for when it was sent,
  and nobody asked for it to be dropped (that is what a cancel is). If that completes the
  variant or the design, it is kept, and an owner who comes back within KEEP_FINISHED_S is shown
  it. If the variant or the design still needs another call (the reformat of an answer that was
  not JSON, the repair of a rejected document; ai._generate), the gate refuses that call, and
  the variant is marked stopped with the reason, or the design fails with it. If the owner comes
  back while the call is in flight, their read renews the lease and the job carries on. Only the
  owner renews: anyone else's read is the 404 an unknown id gets and touches nothing.
* How a job that stopped itself ends (``Job.stopped``). The gate refusing a call for the ceiling
  or a lapsed lease is not a cancel (nobody asked), so the job ends as its work does when a call
  is refused: a variation pass is a ``done`` job whose finished variants are kept and whose rest
  (the one whose next call was refused, and every one after it) are marked with the reason, and
  a design is a ``failed`` job whose error is the reason. ``stopped`` says which of the two
  stopped it (CEILING or UNWATCHED), so the page can say so ("Stopped: nobody was watching
  it") instead of showing an error.
* KEEP_FINISHED_S: a finished job is kept 30 minutes, long enough for someone who left for
  another rail, or reloaded before a page was shown the result, to come back and collect it. The listing offers it (``delivered``
  false) until a plain read marks it delivered (``get``), and every read the page shows is
  one: its poll, its read as it mounts, its pickup, and its keep-alive's while a page of Watch
  Builder is mounted to show the answer (a hidden tab, or one out of touch). The keep-alive's
  read with no page mounted does not deliver (``?keepalive=1``, ``deliver=False``): nobody is
  shown its answer (the viewer is on another rail), so a pass that finished then is still
  offered to whoever opens Watch Builder next as its owner, by a reload, a new tab or a return
  from another rail. A pass that finished in a hidden tab is delivered by that tab's read, so
  a later reload of that tab (a browser discard) is not offered it again.
* MAX_JOBS: at most 64 jobs at once. A record holds at most variation_max design documents of a
  few KB each, so a full store is a few MB. At the cap the oldest finished jobs are dropped
  first. A job that is still queued or running, or whose worker is still in a call, is never
  dropped, so a store full of those refuses a new job (Full, a 503) rather than lose work in
  flight. That also bounds the worker threads: each one belongs to a job the store still holds.

Single uvicorn worker (the Dockerfile's CMD), so a process-local dict behind one lock is the
whole store. A restart loses every job in flight: the worker threads are daemons and nothing is
written to disk. The page learns that from the 404 a polled id then gets, and says so.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from watch_builder import ai

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
ACTIVE = (QUEUED, RUNNING)
TERMINAL = (DONE, FAILED, CANCELLED)
# Why the gate stopped a job that nobody cancelled (Job.stopped).
CEILING, UNWATCHED = "ceiling", "unwatched"

JOB_CEILING_S = 2700.0
LEASE_S = 300.0
KEEP_FINISHED_S = 1800.0
MAX_JOBS = 64
MAX_WORKERS_PER_PERSON = 2

# Monotonic, for the ceiling, the lease and the keep window; a test replaces it with a fake clock.
_mono = time.monotonic

_LOCK = threading.Lock()
_JOBS: dict[str, "Job"] = {}
# One per person, held by a worker for as long as it makes calls (see "One active job per
# person" above).
_OWNER_LOCKS: dict[Any, threading.Lock] = {}


class Busy(Exception):
    """This person already has a job queued or running, and ``job_id`` names it. Or, with
    ``finishing``, they hold MAX_WORKERS_PER_PERSON cancelled jobs whose worker has not returned,
    and ``job_id`` names the oldest, the one whose call the others wait behind."""

    def __init__(self, job_id: str, finishing: bool = False) -> None:
        super().__init__(job_id)
        self.job_id = job_id
        self.finishing = finishing


class Full(Exception):
    """MAX_JOBS jobs are held and none of them may be dropped yet."""


@dataclass
class Job:
    id: str
    owner: str | None
    kind: str
    total: int
    unit: str
    state: str = QUEUED
    done: int = 0
    calls: int = 0
    partial: list = field(default_factory=list)
    result: Any = None
    error: str | None = None
    created: float = 0.0          # wall clock, for the page
    started: float | None = None
    finished: float | None = None
    finished_mono: float | None = None
    delivered: bool = False       # the owner has been shown it finished
    lease_until: float = 0.0      # _mono() past which nobody is watching it (LEASE_S)
    stopped: str | None = None    # CEILING or UNWATCHED once the gate has refused a call for it
    cancel: threading.Event = field(default_factory=threading.Event)
    exited: threading.Event = field(default_factory=threading.Event)


class Run:
    """What the work of one job is handed: the name its calls carry, and the two hooks."""

    def __init__(self, job: Job) -> None:
        self._job = job
        self._t0 = _mono()
        # The job's owner, and nothing else: see "The name is theirs" above.
        self.requester = job.owner

    def gate(self) -> None:
        """Called before every broker call (ai.Gate). Refuses the call once the job is cancelled,
        past its ceiling, or unwatched past its lease, and counts it otherwise. Under the lock a
        cancel and a renewal take, so neither can land between the check and the call."""
        job = self._job
        with _LOCK:
            if job.cancel.is_set():
                raise ai.Stopped("stopped: the job was cancelled, and no new call starts after "
                                 "that")
            if _mono() - self._t0 >= JOB_CEILING_S:
                job.stopped = CEILING
                raise ai.Stopped(f"stopped: the job reached its {JOB_CEILING_S / 60:g}-minute "
                                 "ceiling, and no call starts after that")
            if _mono() >= job.lease_until:
                job.stopped = UNWATCHED
                raise ai.Stopped(f"stopped: nobody was watching it for {LEASE_S / 60:g} "
                                 "minutes, and no call starts after that")
            job.calls += 1

    def variant_done(self, entry: dict) -> None:
        """One variant finished: it joins the partial results the page shows while it waits.
        That holds for a variant whose last call was in flight when the lease lapsed too (see
        LEASE_S). Every finished entry comes here, a variant whose calls failed included, so
        ``done`` counts those too; the page counts the ``ok`` ones as what a pass kept."""
        with _LOCK:
            if self._job.state == CANCELLED:
                # Its call was in flight at the cancel. Dropped like a cancelled design's answer
                # (_finish): the page stopped polling at the cancel and was told what it had.
                return
            self._job.partial.append(entry)
            self._job.done += 1


Work = Callable[[Run], dict]


def _spawn(target: Callable[[], None], name: str) -> None:
    """Start a worker. A daemon, so a shutdown never waits on a model call (the job is lost,
    which the page is told by its next poll)."""
    threading.Thread(target=target, name=name, daemon=True).start()


def _expire_locked() -> None:
    now = _mono()
    for jid in [j.id for j in _JOBS.values()
                if j.state in TERMINAL and j.exited.is_set() and j.finished_mono is not None
                and now - j.finished_mono > KEEP_FINISHED_S]:
        del _JOBS[jid]


def _make_room_locked() -> None:
    _expire_locked()
    spare = sorted((j for j in _JOBS.values() if j.state in TERMINAL and j.exited.is_set()),
                   key=lambda j: j.finished_mono or 0.0)
    while len(_JOBS) >= MAX_JOBS and spare:
        del _JOBS[spare.pop(0).id]
    if len(_JOBS) >= MAX_JOBS:
        raise Full()


def submit(owner: str | None, kind: str, total: int, unit: str, work: Work) -> dict:
    """Create a job for ``owner`` and start its worker; returns the job's view at once.

    ``owner`` is the person asking. It is the job's owner and the name every call carries.

    If the worker cannot be started (Thread.start raised), the job is failed with the reason and
    its worker marked returned, so it neither blocks its owner nor holds a place in the store,
    and the exception goes on to the route (a 500)."""
    with _LOCK:
        waiting = []
        for j in _JOBS.values():
            if j.owner != owner:
                continue
            if j.state in ACTIVE:
                raise Busy(j.id)
            if not j.exited.is_set():
                waiting.append(j)
        # Only cancelled jobs are left in `waiting` (an active one was refused above).
        if len(waiting) >= MAX_WORKERS_PER_PERSON:
            raise Busy(min(waiting, key=lambda j: j.created).id, finishing=True)
        _make_room_locked()
        job = Job(id=uuid.uuid4().hex, owner=owner, kind=kind, total=total, unit=unit,
                  created=time.time(), lease_until=_mono() + LEASE_S)
        _JOBS[job.id] = job
        lock = _OWNER_LOCKS.setdefault(owner, threading.Lock())
        view = _view(job)
    try:
        _spawn(lambda: _run(job, work, lock), f"wb-job-{job.id[:8]}")
    except BaseException as exc:
        _never_started(job, exc)
        raise
    return view


def _never_started(job: Job, exc: BaseException) -> None:
    """The job's worker could not be started, so nothing will ever run it or end it."""
    with _LOCK:
        if job.state == QUEUED:
            job.state = FAILED
            job.error = (f"internal error: the job's worker could not be started: "
                         f"{type(exc).__name__}: {exc}")
            job.finished, job.finished_mono = time.time(), _mono()
    # No worker exists to set it, and the store drops only a job whose worker has returned.
    job.exited.set()


def _run(job: Job, work: Work, lock: threading.Lock) -> None:
    try:
        # Held while this job makes calls: a later job of the same person waits here, queued,
        # until a cancelled one's last call has returned.
        with lock:
            with _LOCK:
                if job.state != QUEUED:       # cancelled while it waited: no call is made
                    return
                job.state = RUNNING
                job.started = time.time()
            run = Run(job)
            try:
                result = work(run)
            except ai.AIError as exc:
                _finish(job, FAILED, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - a job must end whatever went wrong
                log.exception("AI job %s failed", job.id)
                _finish(job, FAILED, error=f"internal error: {type(exc).__name__}: {exc}")
            else:
                _finish(job, DONE, result=result)
    finally:
        job.exited.set()


def _finish(job: Job, state: str, *, result: Any = None, error: str | None = None) -> None:
    with _LOCK:
        if job.state == CANCELLED:
            # Cancelled while its last call was in flight. The answer arrived for nobody: the
            # page stopped polling at the cancel, so it is dropped and the state stays.
            return
        job.state, job.result, job.error = state, result, error
        if state == DONE and job.unit == "design":
            job.done = job.total
        job.finished, job.finished_mono = time.time(), _mono()


def _view(job: Job) -> dict:
    return {
        "job_id": job.id,
        "kind": job.kind,
        "state": job.state,
        "progress": {"done": job.done, "total": job.total, "unit": job.unit},
        "calls": job.calls,
        "partial": list(job.partial),
        "result": job.result,
        "error": job.error,
        "created": job.created,
        "started": job.started,
        "finished": job.finished,
        "ceiling_s": JOB_CEILING_S,
        "stopped": job.stopped,
        "lease_s": LEASE_S,
    }


def _theirs(job_id: str, owner: str | None) -> Job | None:
    job = _JOBS.get(job_id)
    return job if job is not None and job.owner == owner else None


def _renew_locked(job: Job) -> None:
    """Its owner is looking at it: nobody-watching starts LEASE_S from now. It SETS the lease and
    never adds to it, so a page open for an hour buys its job the same LEASE_S after its last
    read as one read does, and a closed tab's job stops LEASE_S after that tab's last read."""
    job.lease_until = _mono() + LEASE_S


def get(job_id: str, owner: str | None, *, deliver: bool = True) -> dict | None:
    """The job's view, or None when it is not ``owner``'s (or is unknown, or expired). Reading
    a finished job marks it delivered, so a reload does not show it a second time, unless
    ``deliver`` is False: the page's keep-alive with no page mounted (GET ...?keepalive=1),
    whose answer nobody is shown (see KEEP_FINISHED_S). The owner's read renews the job's
    lease (LEASE_S), the keep-alive's included; anyone else's finds nothing and renews nothing."""
    with _LOCK:
        _expire_locked()
        job = _theirs(job_id, owner)
        if job is None:
            return None
        _renew_locked(job)
        if deliver and job.state in TERMINAL:
            job.delivered = True
        return _view(job)


def cancel(job_id: str, owner: str | None) -> dict | None:
    """Stop ``owner``'s job: no new call starts after this. A call already sent finishes and its
    answer is dropped. The cancel does not reach the broker (the rail's request stays open), so
    a call sent before it still runs there in full: a CLI call that was waiting for a slot
    still starts once one frees, and a CLI failure still falls back to the local model.
    Cancelling a finished job changes nothing."""
    with _LOCK:
        _expire_locked()
        job = _theirs(job_id, owner)
        if job is None:
            return None
        if job.state in ACTIVE:
            job.cancel.set()
            job.state = CANCELLED
            job.finished, job.finished_mono = time.time(), _mono()
        job.delivered = True
        return _view(job)


def listing(owner: str | None) -> list[dict]:
    """``owner``'s jobs, newest first, without their results: what a reloaded page reads to
    find a job to attach to. It is the owner looking, so it renews each one's lease, which
    covers the moment between this listing and the page's first read of the job it picks."""
    with _LOCK:
        _expire_locked()
        mine = sorted((j for j in _JOBS.values() if j.owner == owner),
                      key=lambda j: j.created, reverse=True)
        for j in mine:
            _renew_locked(j)
        return [{"job_id": j.id, "kind": j.kind, "state": j.state,
                 "progress": {"done": j.done, "total": j.total, "unit": j.unit},
                 "created": j.created, "finished": j.finished, "delivered": j.delivered}
                for j in mine]


def _wait(job_id: str, timeout: float) -> bool:
    """True once the job's worker has returned. For tests and the smoke probe."""
    with _LOCK:
        job = _JOBS.get(job_id)
    return job is not None and job.exited.wait(timeout)
