"""Mutation harness for the live-test opt-ins. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_live_gate.py

What these guard fails in the most expensive direction there is: quietly. A LiveTests class
whose opt-in is gone still passes (it calls the real CLI and the real CLI answers), and the only
trace is the owner's quota going down. So each mutation removes one part of the gate and names
the test that must notice: the nested bare-pytest run in test_live_gate.py, or the run-tests.ps1
scenarios in test_run_tests_ps1.py.

The suites run through mutation_runner, which strips CLI_LIVE and JOB_AID_LIVE_TESTS from every
child, so this harness cannot itself opt anything in. With the LiveTests gate mutated away, the
nested run spawns the FAKE claude the test puts first on PATH (an empty file), never a real one.
"""
from __future__ import annotations

import sys
from pathlib import Path

from mutation_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args,
                             restore_exact, run_bounded)

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
SUITE = ["tools/tests/test_live_gate.py", "tools/tests/test_run_tests_ps1.py",
         "tools/tests/test_live_deselection.py"]

CB = "services/broker/tests/test_cli_backend.py"
RT = "run-tests.ps1"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a guard's name contains the -k term, so `not LiveTests` deselects it in silence",
        CB,
        "def test_env_disables_the_auto_updater():",
        "def test_env_disables_the_auto_updater_unlike_LiveTests():",
        "test_nothing_but_a_live_test_class_matches_the_filter",
    ),
    (
        "run-tests.ps1's filter changes and the deselection check keeps testing the old term",
        RT,
        "@('-k', 'not LiveTests')",
        "@('-k', 'not Live')",
        "test_both_runners_still_deselect_by_one_known_term",
    ),
    (
        "the broker's LiveTests lose their opt-in, so a bare pytest spends real Claude quota",
        CB,
        '@unittest.skipUnless(os.environ.get(LIVE_OPT_IN) == "1",',
        "@unittest.skipUnless(True,",
        "test_a_bare_pytest_of_the_broker_suite_makes_no_cli_call",
    ),
    # job-aid's two keyed boards have their own harness, in that rail's tests/
    # (mutate_live_opt_in.py): this file ships in the public snapshot, which withholds job-aid,
    # and a published tool must not name a withheld file (publish.py's withheld-refs check).
    (
        "-IncludeLive no longer sets the opt-ins, so the live tests it asks for all skip",
        RT,
        "  if ($IncludeLive) { Set-Item -Path \"Env:$v\" -Value '1' }",
        "  if ($false) { Set-Item -Path \"Env:$v\" -Value '1' }",
        "test_include_live_sets_both_opt_ins_and_then_restores_the_caller",
    ),
    (
        "a plain run passes the caller's leftover CLI_LIVE straight through",
        RT,
        "  else { Remove-Item -Path \"Env:$v\" -ErrorAction SilentlyContinue }\n}",
        "  else { }\n}",
        "test_a_live_opt_in_left_in_the_callers_shell_does_not_reach_a_plain_run",
    ),
    (
        "the caller's own opt-ins are not put back after the run",
        RT,
        "    else { Set-Item -Path \"Env:$v\" -Value $liveSaved[$v] }",
        "    else { }",
        "test_a_live_opt_in_left_in_the_callers_shell_does_not_reach_a_plain_run",
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=REPO)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected)
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
