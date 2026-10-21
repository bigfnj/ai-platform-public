"""Mutation harness for what a scheduled run records when the rail answers with a failure, and
for the schedules the gateway accepts. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_scheduler_fire.py

Run this, not just pytest, before trusting a change to scheduler.fire or its summarisers, or to
platform_core.schedule's validate and its MAX_INTERVAL.

Every guard here fails quietly. Drop the rail's body and the tab still says "error", just
nothing useful. Drop the secret filter and nothing looks wrong until a rail echoes a token into
a record every admin can read. Drop the bound and SQLite stores the overflow happily while a
Postgres-backed gateway would refuse the write. Drop the interval limit and a schedule saves as
enabled with no next run, and simply never runs.

Each mutation names the test(s) that must fail for it, as `file::test`. A red run that fails
some OTHER test is FIRED-WRONG-TEST, not FIRED.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
PY = sys.executable
SUITE = ["apps/platform/backend/tests/test_scheduler_run_record.py",
         "apps/platform/backend/tests/test_scheduler.py"]
CORE_SUITE = ["tests/test_schedule.py"]     # run from CORE; see run_suite
# One pytest run takes ~5 s here. A mutation that makes a test hang must not hang the harness.
RUN_TIMEOUT = 300

S = "apps/platform/backend/platform_gateway_app/scheduler.py"
T = "test_scheduler_run_record.py::"
REC = "packages/platform_core/platform_core/schedule.py"
UI = "apps/platform/frontend/src/AdminPage.tsx"
TR = "test_schedule.py::"
TS = "test_scheduler.py::"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "the rail's error body is thrown away again",
        [(S, "        detail = _summarize_error(exc.response)\n", '        detail = ""\n')],
        [T + "test_a_failing_sync_task_keeps_the_counts_the_rail_returned",
         T + "test_a_string_detail_is_kept_with_its_status_code"],
    ),
    (
        "a status-code failure falls through to httpx's sentence about it",
        [(S, "    except httpx.HTTPStatusError as exc:\n"
             "        # The rail answered, and said why. Keep that, not httpx's sentence about it.\n"
             "        detail = _summarize_error(exc.response)\n"
             "        return _bounded(f\"error ({exc.response.status_code})"
             "{f' {detail}' if detail else ''}\")\n", "")],
        [T + "test_a_failing_sync_task_keeps_the_counts_the_rail_returned",
         T + "test_the_detail_reaches_the_schedule_row_through_run_now"],
    ),
    (
        "FastAPI's {detail: ...} wrapper is not unwrapped",
        [(S, '    if isinstance(body, dict) and "detail" in body:\n'
             '        body = body["detail"]\n', "")],
        [T + "test_a_failing_sync_task_keeps_the_counts_the_rail_returned",
         T + "test_a_string_detail_is_kept_with_its_status_code"],
    ),
    (
        "a validation error's messages are dropped",
        [(S, '        return _clean("; ".join(str(e["msg"]) for e in body\n',
             '        return _clean("; ".join(str(e["msg"]) for e in body if False\n')],
        [T + "test_a_validation_error_keeps_its_messages"],
    ),
    (
        "a body that is not JSON is dropped",
        [(S, '            return _clean(resp.text or "")\n', '            return ""\n')],
        [T + "test_a_body_that_is_not_json_is_kept_as_one_bounded_line"],
    ),
    (
        "secret-named keys reach the record",
        [(S, "if isinstance(v, (int, float, str, bool)) and not _SECRET_KEY.search(str(k)))",
             "if isinstance(v, (int, float, str, bool)))")],
        [T + "test_nothing_credential_shaped_reaches_the_record",
         T + "test_a_successful_summary_drops_secret_named_keys_too"],
    ),
    (
        "credentials in free text are not masked",
        [(S, '    return _AUTH_VALUE.sub(_mask, " ".join(text.split()))',
             '    return " ".join(text.split())')],
        [T + "test_nothing_credential_shaped_reaches_the_record"],
    ),
    (
        "the mask swallows prose that only mentions a credential word",
        [(S, r'(\s*[:=]\s*)', r'(\s*[:=]?\s*)')],
        [T + "test_nothing_credential_shaped_reaches_the_record"],
    ),
    (
        "a multi-line body is recorded with its newlines",
        [(S, '    return _AUTH_VALUE.sub(_mask, " ".join(text.split()))',
             "    return _AUTH_VALUE.sub(_mask, text)")],
        [T + "test_a_body_that_is_not_json_is_kept_as_one_bounded_line"],
    ),
    (
        "the record is not bounded",
        [(S, "    return status[:STATUS_MAX]", "    return status")],
        [T + "test_the_record_never_outgrows_its_column",
         T + "test_a_body_that_is_not_json_is_kept_as_one_bounded_line"],
    ),
    (
        "the bound is a constant that no longer follows the column",
        [(S, "STATUS_MAX = Schedule.__table__.c.last_status.type.length or 255",
             "STATUS_MAX = 500")],
        [T + "test_the_record_never_outgrows_its_column"],
    ),
    # -- the 2026-10-14 audit: a schedule the gateway accepts always has a next run
    (
        "validate accepts an interval next_run cannot reach again",
        [(REC, "    if interval > limit:\n", "    if False:\n")],
        [TR + "test_every_36_months_is_refused_and_says_why",
         TR + "test_one_past_the_limit_is_refused",
         TS + "test_a_schedule_that_would_never_fire_is_refused_and_the_row_is_kept",
         TS + "test_the_schedule_tab_is_told_why_in_the_gateways_words"],
    ),
    (
        "the monthly limit is one past next_run's reach",
        [(REC, '"monthly": 26}', '"monthly": 27}')],
        [TR + "test_the_longest_interval_is_found_just_after_a_run",
         TR + "test_every_26_months_is_found_after_a_run_in_any_month"],
    ),
    (
        "the weekly limit is one short of next_run's reach, refusing a schedule that would run",
        [(REC, '"weekly": _HORIZON_DAYS // 7,', '"weekly": _HORIZON_DAYS // 7 - 1,')],
        [TR + "test_one_past_the_limit_could_not_be_found_anyway"],
    ),
    (
        "a weekday outside 0..6 counts again, so [7] saves and never runs",
        [(REC, "if n is not None and 0 <= n <= 6}", "if n is not None}")],
        [TR + "test_a_weekday_outside_0_to_6_is_no_weekday"],
    ),
    (
        "the refusal stops saying why",
        [(REC, '                f"would never run again")', '                f"")')],
        [TS + "test_the_schedule_tab_is_told_why_in_the_gateways_words"],
    ),
    (
        "the Schedule tab's interval box stops at 52 for every frequency again",
        [(UI, "max={MAX_INTERVAL[rec.freq]}", "max={52}")],
        [TS + "test_the_schedule_tabs_interval_box_stops_where_the_gateway_does"],
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first: the venv's editable gateway points at one checkout.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def witness() -> str | None:
    """Prove the module under test resolves to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c", "import platform_gateway_app.scheduler as s, platform_core.schedule as r; "
                         "print(s.__file__); print(r.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    files = p.stdout.splitlines()
    if len(files) != 2:
        return f"the import probe printed {files!r}, not two module files"
    for f, root in zip(files, (BACKEND, CORE)):
        if not Path(f.strip()).resolve().is_relative_to(root):
            return f"imported {f}, which is not under {root}"
    return None


def _pytest(suite: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *suite, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=cwd, capture_output=True, text=True, env=_env(),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def run_suite() -> tuple[int, str]:
    """The gateway's tests, then platform_core's in a run of their own. In one run pytest takes
    its rootdir from the gateway's pyproject, and a FAILED line for a test outside that rootdir
    loses its file name, so failed_names could not tell which test failed."""
    code, out = _pytest(SUITE, REPO)
    if code == -1:
        return code, out
    core_code, core_out = _pytest(CORE_SUITE, CORE)
    return (core_code if core_code == -1 else code or core_code), out + "\n" + core_out


def failed_names(out: str) -> set[str]:
    """`file::test` for each pytest FAILED line, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            path, rest = ln[len("FAILED "):].split("::", 1)
            names.add(f"{Path(path).name}::{rest.split(' ', 1)[0].split('[', 1)[0]}")
    return names


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        originals: dict[Path, str] = {}
        original_bytes: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                originals.setdefault(path, path.read_text(encoding="utf-8"))
                current = path.read_text(encoding="utf-8")
                n = current.count(old)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                mr.write_like(path, current.replace(old, new), original_bytes[path])
            if not pattern_ok:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path in originals:
                mr.restore_exact(path, original_bytes[path])

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
