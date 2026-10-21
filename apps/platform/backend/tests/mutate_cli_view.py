"""Mutation harness for who sees the CLI status block. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_cli_view.py

Run this, not just pytest, before trusting a change to cli_view.py or to the
/api/platform/status route.

Every guard here fails SILENTLY and in the direction that looks fine. Spread the raw status and
the widget keeps working, every request is a 200, and each logged-in user's browser receives
the owner accounts and the CLI's machine config. Nothing a user or an operator can see changes.
A filter whose removal has no visible symptom is exactly the kind that needs a test proven able
to fail.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
reported as FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
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
SUITE = ["apps/platform/backend/tests/test_cli_view.py"]

V = "apps/platform/backend/platform_gateway_app/cli_view.py"
MAIN = "apps/platform/backend/platform_gateway_app/main.py"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "the admin check is removed, so every user gets the operator block",
        V,
        "    if is_admin is True:\n"
        "        return copy.deepcopy(cli)",
        "    if True:\n"
        "        return copy.deepcopy(cli)",
        ["test_operator_fields_never_reach_a_non_admin",
         "test_a_non_admin_status_never_names_an_owner"],
    ),
    (
        "the owner filter on active is removed, so another owner's running call leaks",
        V,
        '        if _norm(entry.get("requester")) != me:\n'
        "            continue",
        "        if False:\n"
        "            continue",
        ["test_an_owner_sees_only_their_own_active_entries"],
    ),
    (
        "requester is not stripped from an owner's own active entries",
        V,
        '_ACTIVE_KEYS = ("role", "rail", "since")',
        '_ACTIVE_KEYS = ("role", "rail", "since", "requester")',
        ["test_requester_is_stripped_from_own_active_entries"],
    ),
    (
        "a non-owner gets the reduced block instead of nothing",
        V,
        '    if not me or me not in _owner_set(cli.get("owners")):\n'
        "        return None",
        "    if not me:\n"
        "        return None",
        ["test_a_non_owner_gets_nothing",
         "test_a_non_admin_status_never_names_an_owner"],
    ),
    (
        "the status route spreads the raw broker status again",
        MAIN,
        '    return {"broker_reachable": True, **_status_for(status, user)}',
        '    return {"broker_reachable": True, **status}',
        ["test_a_non_admin_status_never_names_an_owner"],
    ),
    (
        "owner matching folds case again, so a case-variant account sees the owner's view",
        V,
        "    return name.strip() if isinstance(name, str) else \"\"",
        "    return name.strip().lower() if isinstance(name, str) else \"\"",
        ["test_a_case_variant_viewer_is_not_the_owner"],
    ),
    (
        "a bare-string owners value is iterated, so a one-letter account becomes an owner",
        V,
        "    if not isinstance(owners, (list, tuple)):",
        "    if not isinstance(owners, (list, tuple, str)):",
        ["test_malformed_cli_never_raises"],
    ),
    (
        "last_by_role is not filtered, so one owner sees another's latest outcome",
        V,
        '        if _norm(outcome.get("requester")) != me:\n'
        "            continue",
        "        if False:\n"
        "            continue",
        ["test_an_owner_sees_only_their_own_last_outcomes",
         "test_an_outcome_with_no_requester_is_shown_to_no_owner"],
    ),
    (
        "requester is not stripped from an owner's own last outcomes",
        V,
        # Re-anchored 2026-10-14: the tuple admits an interruption's `cause` now.
        '_LAST_KEYS = ("ok", "at", "reason", "cause")',
        '_LAST_KEYS = ("ok", "at", "reason", "cause", "requester")',
        ["test_requester_is_stripped_from_own_last_outcomes"],
    ),
    (
        "an owner's interrupted outcome loses its cause",
        V,
        '_LAST_KEYS = ("ok", "at", "reason", "cause")',
        '_LAST_KEYS = ("ok", "at", "reason")',
        ["test_an_owners_interrupted_outcome_keeps_its_cause_and_nothing_else_new"],
    ),
    (
        "an owner's outcome carries everything the broker adds, the Claude model included",
        V,
        "        out[role] = {k: outcome[k] for k in _LAST_KEYS if k in outcome}\n",
        '        out[role] = {k: v for k, v in outcome.items() if k != "requester"}\n',
        ["test_an_owners_interrupted_outcome_keeps_its_cause_and_nothing_else_new"],
    ),
    (
        "an outcome with no requester (an older broker) is shown to every owner",
        V,
        '        if _norm(outcome.get("requester")) != me:',
        '        if "requester" in outcome and _norm(outcome.get("requester")) != me:',
        ["test_an_outcome_with_no_requester_is_shown_to_no_owner"],
    ),
    (
        "last-outcome matching skips the strip, so a padded requester's outcome goes missing",
        V,
        '        if _norm(outcome.get("requester")) != me:',
        '        if outcome.get("requester") != me:',
        ["test_last_outcome_matching_ignores_whitespace"],
    ),
    (
        "the per-requester map is ignored, so an owner's outcome vanishes when another calls",
        V,
        "    if isinstance(by_requester, dict):",
        "    if False:",
        ["test_an_owner_keeps_their_own_outcome_after_another_owner_calls_the_role"],
    ),
    (
        "the per-requester bucket is matched without normalising, so ' Owner-One ' finds none",
        V,
        "        mine = next((o for who, o in by_requester.items() if _norm(who) == me), None)",
        "        mine = next((o for who, o in by_requester.items() if who == me), None)",
        ["test_the_bucket_is_matched_the_way_the_broker_keys_it"],
    ),
    (
        "cli_view never hands the per-requester map over",
        V,
        '                                          cli.get("last_by_requester")),',
        "                                          None),",
        ["test_an_owner_keeps_their_own_outcome_after_another_owner_calls_the_role"],
    ),
]

# One pytest run takes a few seconds. A mutation that makes a test hang must not hang the
# harness; a run that times out is reported as TIMEOUT, never as FIRED.
RUN_TIMEOUT = 300


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first. The repo venv carries editable installs that point at one checkout; run
    # from a worktree without this and pytest imports the gateway from somewhere else, so every
    # mutation "survives" against code the run never touched.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    # No .pyc written. A mutation and its restore can land in the same second with the same
    # file size, and the import system would then trust a stale cached compile of the OTHER one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import platform_gateway_app.cli_view as v, platform_gateway_app.main as m;"
         "print(v.__file__); print(m.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(BACKEND):
            return f"imported {f}, which is not under {BACKEND}"
    return None


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
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
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        original = path.read_text(encoding="utf-8")
        original_bytes = path.read_bytes()
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            mr.write_like(path, original.replace(old, new), original_bytes)
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = "TIMEOUT" if code == -1 else "FIRED" if fired and not missing else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                # The evidence, not just the verdict: which expected test held, and the tail
                # of the run, so a SURVIVED can be read without re-running by hand.
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original_bytes)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
