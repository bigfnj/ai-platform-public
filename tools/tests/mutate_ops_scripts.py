"""Mutation harness for the PowerShell ops scripts' checks. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_ops_scripts.py [substring-of-a-label ...]

Every guard here exists because its absence was SILENT: a stage that asserted nothing exited 0,
a sweep where five targets never ran exited 0, a startup fallback that could not work printed
"retrying". A check like that is only worth keeping if breaking the thing it guards makes a
named test fail, so each mutation removes or bends one guard and names the test that must catch
it. Run this, not just pytest, before trusting a change to one of these scripts.

The cases live beside this file, one module per family of scripts (mutate_ops_<family>_cases.py,
FAMILIES below): a case goes with the family of the file it mutates. A label filter selects
across all of them.

Each suite runs the real scripts under powershell.exe AND pwsh, so a mutation FIRES only if the
named test fails; the verdict does not say which engine caught it.

Patterns are written with LF and matched against the file with its line endings normalised, then
written back with the endings it had: `* text=auto` checks these files out CRLF on this box and
LF on a Linux clone, and a pattern pinned to either one reports PATTERN on the other.
"""
from __future__ import annotations

import importlib.util
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

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
TIMEOUT = 600   # a hung PowerShell harness must not hang the mutation run
DRIVER = ".github/scripts/run_public_suite.py"
T_SUITE_PLAN = "tools/tests/test_public_suite_plan.py"
# A mutation whose file is not in this tree is skipped, and the run says so; a PATTERN error for a
# file that is not there would read as a broken harness.
ABSENT = set()
# .github/ is not published either, so the CI driver's mutation is skipped there too.
if not (REPO / DRIVER).is_file():
    ABSENT.add(DRIVER)
# test_backup_native.py ships, but every test in it skips where the backup script is withheld, so
# the cases on its own fixtures (BKT) cannot fire there: skipped and named, as the driver's are.
T_BKN = "tools/tests/test_backup_native.py"
if not (REPO / "deploy" / "backup-platform.ps1").is_file():
    ABSENT.add(T_BKN)

# The cases live in sibling modules, one per family of scripts, each a MUTATIONS and a CONTROLS
# list of (label, file, old, new, expected test, suite). They were one table here until
# 2026-10-15, when it outgrew two thirds of publish.json's data_max_bytes. Every one ships, so a
# missing one is a broken tree and loading it fails; the backup scripts' cases, below, are the
# one module a tree may lack.
#
# Controls: rewrites that keep the behaviour, each of which must SURVIVE (its suite stays green).
# A control that fires means the named test pins the text of a fix rather than what it does, and
# then a FIRED proves less than it says. Same shape as a mutation; "expected" names the test the
# rewrite touches.
FAMILIES = ("smoke", "lib_runtime", "installer", "startup", "rebuild", "compose", "tooling")
MUTATIONS: list[tuple[str, str, str, str, str, str]] = []
CONTROLS: list[tuple[str, str, str, str, str, str]] = []
for _family in FAMILIES:
    _spec = importlib.util.spec_from_file_location(
        f"mutate_ops_{_family}_cases", Path(__file__).with_name(f"mutate_ops_{_family}_cases.py"))
    _family_cases = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_family_cases)
    MUTATIONS += _family_cases.MUTATIONS
    CONTROLS += _family_cases.CONTROLS

# The backup scripts' cases (deploy/backup-platform.ps1, deploy/backup-voice.ps1) quote those
# scripts, which the public snapshot withholds as a map of this box, so they live in a sibling
# module that goes where the scripts go; so do the cases for publish.py's .ps1 scan, whose suite
# (test_publish.py) is withheld with it. Present, its cases join the run; absent, main() names
# what was skipped rather than shrinking the run in silence.
BACKUP_CASES = Path(__file__).with_name("mutate_ops_backup_cases.py")
if BACKUP_CASES.is_file():
    _spec = importlib.util.spec_from_file_location("mutate_ops_backup_cases", BACKUP_CASES)
    _cases = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cases)
    MUTATIONS += _cases.MUTATIONS
    CONTROLS += getattr(_cases, "CONTROLS", [])
    ABSENT |= {s for s in _cases.SCRIPTS if not (REPO / s).is_file()}


def run_suite(suite: str) -> tuple[int, str]:
    env = dict(os.environ)
    # run-tests.ps1's core roots, THIS tree first, so the suite tests the code this harness edits.
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / p) for p in (
        "services/broker", "apps/platform/backend", "packages/platform_core",
        "packages/edu-media-core/src"))
    try:
        p = mr.run_tree([PY, "-m", "pytest", suite, "-q", "--no-header", "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as e:
        return 124, f"TIMEOUT after {TIMEOUT}s\n{e.stdout or ''}{e.stderr or ''}"
    return p.returncode, p.stdout + p.stderr


def mutate_and_run(relpath: str, old: str, new: str, suite: str) -> tuple[int, int, str]:
    """Apply one rewrite, run its suite, restore the file byte for byte (verified).
    Returns (occurrences of the anchor, suite exit code, output); nothing is run unless the
    anchor matched exactly once."""
    path = REPO / relpath
    original = path.read_bytes()
    raw = original.decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    n = text.count(old)
    if n != 1:
        return n, -1, ""
    try:
        mutated = text.replace(old, new)
        path.write_bytes((mutated.replace("\n", "\r\n") if crlf else mutated).encode("utf-8"))
        code, out = run_suite(suite)
        return n, code, out
    finally:
        mr.restore_exact(path, original)   # bytes, so a BOM or CRLF in a .ps1 survives; verified


def main(argv: list[str]) -> int:
    argv = list(mr.parse_harness_args(MUTATIONS, CONTROLS, filter=True, argv=argv).only)
    chosen = [m for m in MUTATIONS if not argv or any(a in m[0] for a in argv)]
    controls = [c for c in CONTROLS if not argv or any(a in c[0] for a in argv)]
    if not BACKUP_CASES.is_file():
        print(f"  SKIPPED (not in this tree): the backup scripts' cases, which live in "
              f"{BACKUP_CASES.name} and ship only where the backup scripts do")
    for m in [m for m in chosen + controls if m[1] in ABSENT]:
        print(f"  SKIPPED (not in this tree): {m[0]}")
    chosen = [m for m in chosen if m[1] not in ABSENT]
    controls = [c for c in controls if c[1] not in ABSENT]
    suites = sorted({m[5] for m in chosen + controls})
    for suite in suites:
        code, out = run_suite(suite)
        if code != 0:
            print(f"BASELINE IS NOT GREEN ({suite}) -- fix that before trusting any mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green: {suite}: {out.strip().splitlines()[-1]}")
    print()

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected, suite in chosen:
        n, code, out = mutate_and_run(relpath, old, new, suite)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        fired = code != 0
        named = mr.named_failed(out, expected)
        verdict = "FIRED" if fired and named else ("FIRED-WRONG-TEST" if fired else "SURVIVED")
        results.append((label, verdict))
        print(f"  {verdict:17} {label}")
        if verdict != "FIRED":
            tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
            print("      " + "\n      ".join(tail))

    control_bad: list[tuple[str, str]] = []
    if controls:
        print("\ncontrols (each must be SURVIVED):")
    for label, relpath, old, new, expected, suite in controls:
        n, code, out = mutate_and_run(relpath, old, new, suite)
        verdict = "PATTERN" if n != 1 else ("SURVIVED" if code == 0 else "FIRED")
        print(f"  {verdict:17} {label}")
        if verdict != "SURVIVED":
            control_bad.append((label, verdict))
            tail = [ln for ln in out.splitlines() if ln.strip()][-12:] or [f"pattern found {n} times"]
            print("      " + "\n      ".join(tail))

    # Every suite, not all() (which stops at the first red), and the evidence for each red one: a
    # bare "STILL RED" cannot tell a dirty tree from a test that failed once under load.
    red = []
    for s in suites:
        code, out = run_suite(s)
        if code != 0:
            red.append(s)
            tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
            print(f"\n  restore check RED: {s}\n      " + "\n      ".join(tail))
    restored = not red
    print(f"\nrestored tree: {'green' if restored else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    if controls:
        print(f"{len(controls) - len(control_bad)}/{len(controls)} controls survived"
              + (" -- A TEST PINS TEXT, NOT BEHAVIOUR; READ THE COUNT ABOVE WITH THAT IN MIND"
                 if control_bad else ""))
        for label, verdict in control_bad:
            print(f"  {verdict}: {label}")
    return 0 if not bad and not control_bad and restored else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
