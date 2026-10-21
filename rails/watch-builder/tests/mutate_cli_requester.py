"""Mutation harness for `requester`, the call ceiling and the AI jobs in watch-builder. NOT a
pytest file.

    .venv\\Scripts\\python.exe rails\\watch-builder\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`,
`broker.CHAT_TIMEOUT`, `ai._ask` / `ai.on_claude` / `ai.estimate`, jobs.py (the requester a
job's calls carry, cancel, the ceiling, the lease, ownership, one job per person, the store's
bounds), the AI routes, or the capabilities chip.

The role guard fails OPEN and SILENTLY. A job that drops its owner's name runs every call on the
shared GPU while the Designer chip says Claude, which looks like a slow model rather than a bug;
a cancel that starts the next call anyway, or a store that drops a running job, looks like a
flaky page. Its sibling in another rail is rails/terminal-fun/backend/tests/mutate_cli_requester.py.

A MUTATIONS entry is (label, file, old, new, expected test), or (label, [(file, old, new), ...],
expected test) for a plant that needs more than one edit; every edit's pattern must match
exactly once, in the file as the edits before it left it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

RAIL = Path(__file__).resolve().parents[1]              # rails/watch-builder
REPO = RAIL.parents[1]
PY = sys.executable
PYTHONPATH = [str(RAIL / "backend")]
TIMEOUT_S = 600

SUITE = [str(RAIL / "tests" / "test_cli_requester.py"), str(RAIL / "tests" / "test_jobs.py")]

PKG = RAIL / "backend" / "watch_builder"
BROKER = PKG / "broker.py"
AI = PKG / "ai.py"
APP = PKG / "api" / "app.py"
JOBS = PKG / "jobs.py"

# Shared by the two request-budget plants: a budget for the whole pass, set where the pass
# starts and read from time.monotonic, as an earlier version's ai._deadline was.
_OLD_BUDGET_EDITS = [
    (AI, "import json\nimport re\n",
     "import json\nimport re\nimport time\n\n_OLD_BUDGET = [0.0]\n"),
    (AI, "    n = variation_count(count)\n    base_json",
     "    n = variation_count(count)\n    _OLD_BUDGET[0] = time.monotonic() + 540.0\n    base_json"),
]

MUTATIONS: list[tuple] = [
    # --- the guard ---
    (
        "any role rides the viewer's name to the CLI",
        BROKER,
        "    return requester if requester and model == settings.designer_model else None",
        "    return requester",
        "test_a_foreign_role_never_carries_the_name",
    ),
    # --- the wait ---
    (
        "a call waits less than the whole ceiling",
        BROKER,
        '    got = _request("/v1/chat", payload, timeout=CHAT_TIMEOUT)',
        '    got = _request("/v1/chat", payload)',
        "test_every_call_waits_the_whole_ceiling",
    ),
    # "The call ceiling goes back under the CLI timeout" (480 -> 180) moved to the fleet smoke
    # check's own harness (maintainer tooling, not in the public snapshot): the ordering against
    # BROKER_CLI_TIMEOUT is checked live there, from a registry held equal to CHAT_TIMEOUT.
    (
        # The request-budget defect's outcome with no clock in it: under the old request budget
        # only the first ~60 s of a pass were named. It fires at every cost, so it proves the
        # requester check and nothing about the cost axis; the two budget plants below do that.
        "only the first variant of a pass names the owner",
        AI,
        "temperature=min(1.1, temperature),\n"
        "                               requester=requester, gate=gate)",
        "temperature=min(1.1, temperature),\n"
        "                               requester=requester if i == 0 else None, gate=gate)",
        "test_every_call_of_a_slow_pass_names_the_owner",
    ),
    (
        # The request-budget shape itself: a call starting with less than the whole
        # CHAT_TIMEOUT left of a budget read from time.monotonic goes nameless. Only the slow
        # cases can see it (at cost 0 the budget never trips), so it survived while the test
        # moved only jobs._mono.
        "a request budget read from time.monotonic drops the owner's name on late calls",
        [*_OLD_BUDGET_EDITS,
         (AI, "    if gate is not None:\n        gate()\n    try:\n",
          "    if gate is not None:\n        gate()\n"
          "    if _OLD_BUDGET[0] and _OLD_BUDGET[0] - time.monotonic() < broker.CHAT_TIMEOUT:\n"
          "        requester = None\n    try:\n")],
        "test_every_call_of_a_slow_pass_names_the_owner",
    ),
    (
        # The same budget on the design route, where an earlier version also set one
        # (design_from_prompt's own deadline): after a slow describe call, the reformat and the
        # repair went nameless.
        "a design's own request budget drops the owner's name on its reformat and repair",
        [_OLD_BUDGET_EDITS[0],
         (AI, '    prompt = (prompt or "").strip()\n',
          '    _OLD_BUDGET[0] = time.monotonic() + 540.0\n    prompt = (prompt or "").strip()\n'),
         (AI, "    if gate is not None:\n        gate()\n    try:\n",
          "    if gate is not None:\n        gate()\n"
          "    if _OLD_BUDGET[0] and _OLD_BUDGET[0] - time.monotonic() < broker.CHAT_TIMEOUT:\n"
          "        requester = None\n    try:\n")],
        "test_every_call_of_a_slow_design_names_the_owner",
    ),
    (
        # The other half of the request-budget defect: the same budget trims the timeout a
        # late call is given.
        "a request budget read from time.monotonic gives late calls less than the whole ceiling",
        [*_OLD_BUDGET_EDITS,
         (AI, "temperature=temperature, requester=requester)",
          "temperature=temperature, requester=requester,\n"
          "                           timeout=min(broker.CHAT_TIMEOUT, _OLD_BUDGET[0] - "
          "time.monotonic()) if _OLD_BUDGET[0] else broker.CHAT_TIMEOUT)"),
         (BROKER, "         temperature: float = 0.7, requester: str | None = None) -> str:",
          "         temperature: float = 0.7, requester: str | None = None,\n"
          "         timeout: float = CHAT_TIMEOUT) -> str:"),
         (BROKER, '    got = _request("/v1/chat", payload, timeout=CHAT_TIMEOUT)',
          '    got = _request("/v1/chat", payload, timeout=timeout)')],
        "test_every_call_waits_the_whole_ceiling",
    ),
    # --- wiring: each one makes the CLI silently inert for that call ---
    (
        "the facade never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    if False:\n        payload["requester"] = who',
        "test_the_designer_role_carries_the_requester",
    ),
    (
        "the facade sends an absent requester as null",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    payload["requester"] = who',
        "test_no_requester_means_none_on_the_wire",
    ),
    (
        "_ask drops it",
        AI,
        "temperature=temperature, requester=requester)",
        "temperature=temperature)",
        "test_describe_a_face_names_the_viewer",
    ),
    (
        "the not-JSON reformat drops it",
        AI,
        "        doc = extract_json(_ask(messages, temperature, requester=requester, gate=gate))",
        "        doc = extract_json(_ask(messages, temperature, gate=gate))",
        "test_a_repair_carries_the_name_too",
    ),
    (
        "the rejected-document repair drops it",
        AI,
        "    doc2 = extract_json(_ask(messages, temperature, requester=requester, gate=gate))",
        "    doc2 = extract_json(_ask(messages, temperature, gate=gate))",
        "test_a_repair_carries_the_name_too",
    ),
    (
        "the variation loop stops handing it to each variant",
        AI,
        "temperature=min(1.1, temperature),\n"
        "                               requester=requester, gate=gate)",
        "temperature=min(1.1, temperature),\n"
        "                               gate=gate)",
        "test_every_variation_call_names_the_viewer",
    ),
    (
        "the describe job drops it",
        APP,
        "ai.design_from_prompt(req.prompt, req.platforms, requester=run.requester,",
        "ai.design_from_prompt(req.prompt, req.platforms, requester=None,",
        "test_describe_a_face_names_the_viewer",
    ),
    (
        "the variations job drops it",
        APP,
        "        variants = ai.variations(base, n, requester=run.requester, gate=run.gate,",
        "        variants = ai.variations(base, n, requester=None, gate=run.gate,",
        "test_every_variation_call_names_the_viewer",
    ),
    (
        "a job's calls carry nobody's name",
        JOBS,
        "        self.requester = job.owner",
        "        self.requester = None",
        "test_describe_a_face_names_the_viewer",
    ),
    (
        "a job is created with no owner",
        APP,
        "        return jobs.submit(ident.user, kind, total, unit, work)",
        "        return jobs.submit(None, kind, total, unit, work)",
        "test_describe_a_face_names_the_viewer",
    ),
    # --- the chip ---
    (
        "the chip stops opting the designer in, so an owner is told it runs locally",
        APP,
        "                    requester=ident.user, cli_slots=CLI_SLOTS)",
        "                    )",
        "test_an_owner_sees_the_designer_on_claude",
    ),
    (
        "the chip judges a different identity from the one the calls carry",
        APP,
        "                    requester=ident.user, cli_slots=CLI_SLOTS)",
        "                    requester=None, cli_slots=CLI_SLOTS)",
        "test_an_owner_sees_the_designer_on_claude",
    ),
    # --- the estimate (W4) ---
    (
        "the estimate claims the GPU for a viewer Claude serves",
        AI,
        "    if on_claude(requester):",
        "    if False:",
        "test_the_estimate_claims_no_gpu_for_a_viewer_claude_serves",
    ),
    (
        "the estimate claims Claude for everyone",
        AI,
        "    return modelstate.cli_serves(cli, requester, settings.designer_model)",
        "    return True",
        "test_the_estimate_claims_the_gpu_for_everyone_else",
    ),
    (
        "the estimate route judges nobody",
        APP,
        "    return ai.estimate(kind, count, requester=ident.user)",
        "    return ai.estimate(kind, count)",
        "test_the_estimate_claims_no_gpu_for_a_viewer_claude_serves",
    ),
    (
        "an unreadable status estimates Claude",
        AI,
        "    except (broker.BrokerError, ValueError):\n        return False\n    cli = st.get",
        "    except (broker.BrokerError, ValueError):\n        return True\n    cli = st.get",
        "test_an_unreadable_status_estimates_the_gpu",
    ),
    # --- the job's lifecycle ---
    (
        "the POST runs the job before it answers",
        JOBS,
        "    threading.Thread(target=target, name=name, daemon=True).start()",
        "    target()",
        "test_the_post_answers_before_the_first_call_returns",
    ),
    (
        "variants show only once the job is done",
        JOBS,
        "            self._job.partial.append(entry)\n            self._job.done += 1",
        "            self._job.done += 1",
        "test_variants_appear_as_they_finish",
    ),
    (
        "a bad count is refused only after a job exists",
        APP,
        "        n = ai.variation_count(req.count)",
        "        n = req.count or settings.variation_count",
        "test_the_variation_count_is_checked_before_a_job_exists",
    ),
    # --- cancel ---
    (
        "a cancel does not stop the next call",
        JOBS,
        "            if job.cancel.is_set():\n                raise ai.Stopped(",
        "            if False:\n                raise ai.Stopped(",
        "test_cancel_starts_no_new_call",
    ),
    (
        "a cancelled job's late answer overwrites the cancel",
        JOBS,
        "        if job.state == CANCELLED:\n            # Cancelled while",
        "        if False:\n            # Cancelled while",
        "test_cancel_starts_no_new_call",
    ),
    (
        "a variant answered after the cancel still joins the job",
        JOBS,
        "            if self._job.state == CANCELLED:\n                # Its call was in flight",
        "            if False:\n                # Its call was in flight",
        "test_cancel_starts_no_new_call",
    ),
    (
        "a job cancelled while queued still runs",
        JOBS,
        "                if job.state != QUEUED:       # cancelled while it waited: no call is made",
        "                if False:",
        "test_a_queued_job_cancelled_makes_no_call",
    ),
    # --- the ceiling ---
    (
        "the ceiling never stops a job",
        JOBS,
        "            if _mono() - self._t0 >= JOB_CEILING_S:",
        "            if False:",
        "test_a_job_past_its_ceiling_starts_no_call",
    ),
    (
        "the ceiling is shorter than the longest pass at the estimate",
        JOBS,
        "JOB_CEILING_S = 2700.0",
        "JOB_CEILING_S = 900.0",
        "test_the_ceiling_outlasts_the_longest_pass_at_the_estimate",
    ),
    (
        "a ceiling stop leaves no reason, so the page cannot say why",
        JOBS,
        "                job.stopped = CEILING\n",
        "",
        "test_a_job_past_its_ceiling_starts_no_call",
    ),
    # --- the lease ---
    (
        "the lease never lapses, so a closed tab's job runs to the ceiling",
        JOBS,
        "            if _mono() >= job.lease_until:",
        "            if False:",
        "test_a_pass_nobody_watches_stops_at_its_lease",
    ),
    (
        "submit leases nothing, so no job ever starts a call",
        JOBS,
        "created=time.time(), lease_until=_mono() + LEASE_S)",
        "created=time.time())",
        "test_a_watched_job_runs_past_its_lease",
    ),
    (
        "the owner's read does not renew the lease, so a watched job stops",
        JOBS,
        "        _renew_locked(job)\n        if deliver and job.state in TERMINAL:",
        "        if deliver and job.state in TERMINAL:",
        "test_a_watched_job_runs_past_its_lease",
    ),
    (
        # The 2026-10-09 review's plant: one read pins the job for ever, so the page's first read
        # after a submit keeps a closed tab's job running to the ceiling. Every stop test before
        # the probe had no read at all, and all of them passed this.
        "one read renews the lease for ever, so a closed tab's job never stops",
        JOBS,
        "    job.lease_until = _mono() + LEASE_S\n",
        "    job.lease_until = float('inf')\n",
        "test_a_job_watched_then_abandoned_stops_a_lease_after_the_last_read",
    ),
    (
        # And its sibling: each read stacks a lease on what is left, so ten open minutes (400
        # polls) buy the job about 33 hours.
        "each read adds a lease to what is left instead of setting it",
        JOBS,
        "    job.lease_until = _mono() + LEASE_S\n",
        "    job.lease_until = max(job.lease_until, _mono()) + LEASE_S\n",
        "test_a_page_open_for_three_calls_buys_one_lease_not_three",
    ),
    (
        "anyone's read renews the lease",
        JOBS,
        "        job = _theirs(job_id, owner)\n        if job is None:\n            return None\n"
        "        _renew_locked(job)",
        "        if job_id in _JOBS:\n            _renew_locked(_JOBS[job_id])\n"
        "        job = _theirs(job_id, owner)\n        if job is None:\n            return None\n"
        "        _renew_locked(job)",
        "test_only_the_owners_read_renews_the_lease",
    ),
    (
        "the listing a reloaded page reads renews nothing",
        JOBS,
        "        for j in mine:\n            _renew_locked(j)\n",
        "",
        "test_the_listing_a_reloaded_page_reads_renews_the_lease",
    ),
    (
        "a lapsed lease leaves no reason, so the page shows a bare error",
        JOBS,
        "                job.stopped = UNWATCHED\n",
        "",
        "test_a_pass_nobody_watches_stops_at_its_lease",
    ),
    (
        "the answer of a call in flight when the lease lapsed is dropped",
        JOBS,
        "            if self._job.state == CANCELLED:\n                # Its call was in flight",
        "            if self._job.state == CANCELLED or _mono() >= self._job.lease_until:\n"
        "                # Its call was in flight",
        "test_a_pass_nobody_watches_stops_at_its_lease",
    ),
    (
        "the lease is shorter than a throttled hidden tab's keep-alive can hold",
        JOBS,
        "LEASE_S = 300.0",
        "LEASE_S = 90.0",
        "test_the_lease_outlasts_a_throttled_keep_alive",
    ),
    (
        "the lease is checked before the cancel, so a cancelled job reads as unwatched",
        [(JOBS, "            if job.cancel.is_set():\n"
                "                raise ai.Stopped(\"stopped: the job was cancelled, and no new call "
                "starts after \"\n                                 \"that\")\n", ""),
         (JOBS, "            job.calls += 1\n",
          "            if job.cancel.is_set():\n"
          "                raise ai.Stopped(\"stopped: the job was cancelled\")\n"
          "            job.calls += 1\n")],
        "test_a_lapsed_lease_leaves_the_cancel_working",
    ),
    # --- ownership ---
    (
        "anyone can read or cancel a job",
        JOBS,
        "    return job if job is not None and job.owner == owner else None",
        "    return job",
        "test_another_user_cannot_see_or_cancel_a_job",
    ),
    (
        "the listing shows everyone's jobs",
        JOBS,
        "        mine = sorted((j for j in _JOBS.values() if j.owner == owner),",
        "        mine = sorted((j for j in _JOBS.values()),",
        "test_the_listing_is_the_viewers_own",
    ),
    (
        "reading a finished job never marks it delivered, so a reload shows it again",
        JOBS,
        "        if deliver and job.state in TERMINAL:\n            job.delivered = True",
        "        if False:\n            job.delivered = True",
        "test_a_finished_job_read_by_its_owner_is_delivered",
    ),
    # The 2026-10-09 second review: a keep-alive read, whose answer nobody may be looking at,
    # marked a pass that finished on another rail delivered, so no reload or new tab offered it.
    (
        "a keep-alive read marks a finished job delivered, so no reload offers it",
        JOBS,
        "        if deliver and job.state in TERMINAL:",
        "        if job.state in TERMINAL:",
        "test_a_keepalive_read_leaves_a_finished_job_on_offer",
    ),
    (
        "the route drops the keep-alive marker, so its read delivers the job",
        APP,
        "    view = jobs.get(job_id, ident.user, deliver=not keepalive)",
        "    view = jobs.get(job_id, ident.user)",
        "test_a_keepalive_read_leaves_a_finished_job_on_offer",
    ),
    (
        "a keep-alive read renews nothing, so a job on another rail stops",
        JOBS,
        "        _renew_locked(job)\n        if deliver and job.state in TERMINAL:",
        "        if deliver:\n            _renew_locked(job)\n        if deliver and job.state in TERMINAL:",
        "test_the_keep_alives_read_renews_the_lease",
    ),
    (
        "the marker is a Query() default, so the smoke probe's direct call reads as a keep-alive",
        APP,
        "def ai_job(job_id: str, keepalive: bool = False, ident",
        "def ai_job(job_id: str, keepalive: bool = Query(False), ident",
        "test_a_direct_call_of_the_route_reads_as_the_page_does",
    ),
    # --- one job per person ---
    (
        "a second job runs beside the first",
        JOBS,
        "            if j.state in ACTIVE:\n                raise Busy(j.id)",
        "            if False:\n                raise Busy(j.id)",
        "test_a_second_job_while_one_runs_is_refused_and_names_it",
    ),
    (
        "submit-and-cancel piles up jobs past the per-person bound",
        JOBS,
        "        if len(waiting) >= MAX_WORKERS_PER_PERSON:",
        "        if False:",
        "test_submit_and_cancel_cannot_pile_up_jobs",
    ),
    (
        "jobs whose worker has returned still count against the bound, so it never frees",
        JOBS,
        "            if not j.exited.is_set():\n                waiting.append(j)",
        "            if True:\n                waiting.append(j)",
        "test_submit_and_cancel_cannot_pile_up_jobs",
    ),
    (
        "a refusal past the bound reads as a running job, so the page attaches to a cancelled one",
        APP,
        "        if exc.finishing:",
        "        if False:",
        "test_submit_and_cancel_cannot_pile_up_jobs",
    ),
    # --- a worker that cannot start ---
    (
        "a worker that cannot start leaves its job queued, blocking its owner",
        JOBS,
        "        _never_started(job, exc)\n        raise",
        "        raise",
        "test_a_worker_that_cannot_start_fails_its_job",
    ),
    (
        "a worker that cannot start is never marked returned, so its job is never dropped",
        JOBS,
        "has returned.\n    job.exited.set()",
        "has returned.\n    pass",
        "test_a_worker_that_cannot_start_fails_its_job",
    ),
    (
        "a worker that cannot start answers 202 as if the job had started",
        JOBS,
        "        _never_started(job, exc)\n        raise\n",
        "        _never_started(job, exc)\n",
        "test_a_worker_that_cannot_start_fails_its_job",
    ),
    (
        "the refusal does not name the running job",
        APP,
        '            "error": "busy", "job_id": exc.job_id,',
        '            "error": "busy",',
        "test_a_second_job_while_one_runs_is_refused_and_names_it",
    ),
    (
        "a job after a cancel does not wait for the last call",
        JOBS,
        "        with lock:\n            with _LOCK:",
        "        with threading.Lock():\n            with _LOCK:",
        "test_a_job_after_a_cancel_waits_for_the_last_call",
    ),
    # --- the store's bounds ---
    (
        "a finished job never expires",
        JOBS,
        "                and now - j.finished_mono > KEEP_FINISHED_S]:",
        "                and False]:",
        "test_a_finished_job_expires_after_the_keep_window",
    ),
    (
        "a running job expires like a finished one",
        JOBS,
        "                if j.state in TERMINAL and j.exited.is_set() and j.finished_mono is not None\n"
        "                and now - j.finished_mono > KEEP_FINISHED_S]:",
        "                if now - (j.finished_mono or -1e9) > KEEP_FINISHED_S]:",
        "test_a_running_job_never_expires",
    ),
    (
        "the cap drops nothing, so a full store of finished jobs refuses work",
        JOBS,
        "    while len(_JOBS) >= MAX_JOBS and spare:",
        "    while False and spare:",
        "test_at_the_cap_the_oldest_finished_job_makes_room",
    ),
    (
        "the cap drops a running job",
        JOBS,
        "    spare = sorted((j for j in _JOBS.values() if j.state in TERMINAL and j.exited.is_set()),",
        "    spare = sorted((j for j in _JOBS.values()),",
        "test_a_store_full_of_running_jobs_refuses_a_new_one",
    ),
    (
        "the cap drops a cancelled job whose worker is still in a call",
        JOBS,
        "    spare = sorted((j for j in _JOBS.values() if j.state in TERMINAL and j.exited.is_set()),",
        "    spare = sorted((j for j in _JOBS.values() if j.state in TERMINAL),",
        "test_a_cancelled_job_still_in_a_call_holds_its_place",
    ),
    # --- a restart ---
    (
        "the 404 no longer says a restart loses jobs in flight",
        APP,
        '         "restart of this rail loses every job in flight; nothing is running for this id")',
        '         "id is unknown")',
        "test_a_restart_loses_the_job_and_says_so",
    ),
]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if _read(path) != text:
        raise RuntimeError(f"{path} did not read back as written -- TREE IS DIRTY")


def _eol(pattern: str, text: str) -> str:
    """Patterns are written with \\n; match them in the file's own line ending."""
    return pattern.replace("\n", "\r\n") if "\r\n" in text else pattern


def _edits(entry: tuple) -> tuple[str, list[tuple[Path, str, str]], str]:
    """(label, [(file, old, new), ...], expected test) from either form of a MUTATIONS entry."""
    if len(entry) == 3:
        return entry
    label, path, old, new, expected = entry
    return label, [(path, old, new)], expected


def _plant(edits: list[tuple[Path, str, str]]) -> tuple[dict[Path, str], str | None]:
    """Every file the edits touch, as they leave it, or the reason a pattern did not match once."""
    texts: dict[Path, str] = {}
    for path, old, new in edits:
        text = texts.get(path) or _read(path)
        old, new = _eol(old, text), _eol(new, text)
        n = text.count(old)
        if n != 1:
            return texts, f"pattern found {n} times in {path.name}, expected 1"
        texts[path] = text.replace(old, new)
    return texts, None


def witness() -> str | None:
    """Prove the suite imports THIS tree's watch_builder, or say which one it imports."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    probe = ("import watch_builder.broker as b, watch_builder.ai as a, watch_builder.jobs as j\n"
             "for m in (b, a, j): print('WITNESS', m.__file__)")
    p = mr.run_tree([PY, "-c", probe], cwd=REPO, capture_output=True, text=True, env=env,
                    timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in p.stdout.splitlines()
             if ln.startswith("WITNESS ")]
    if p.returncode != 0 or len(files) != 3:
        return f"import probe failed:\n{p.stdout}{p.stderr}"
    stray = [str(f) for f in files if REPO.resolve() not in f.parents]
    return f"imports resolve outside this tree: {stray}" if stray else None


def run_suite() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env.pop("PLATFORM_STANDALONE", None)
    try:
        p = mr.run_tree([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=RAIL, capture_output=True, text=True, env=env,
                        timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: the suite ran past {TIMEOUT_S}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    if (why := witness()) is not None:
        print(f"WITNESS FAILED -- {why}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for entry in MUTATIONS:
        label, edits, expected = _edits(entry)
        planted, why = _plant(edits)
        if why is not None:
            print(f"  ERROR  {label}: {why}")
            results.append((label, "PATTERN"))
            continue
        originals = {path: _read(path) for path in planted}
        try:
            for path, text in planted.items():
                _write(path, text)
            code, out = run_suite()
            fired = code != 0
            named = mr.named_failed(out, expected)
            verdict = "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, text in originals.items():
                _write(path, text)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
