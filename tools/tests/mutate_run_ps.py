"""Mutation harness for the run_ps fixture's launch retry. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_run_ps.py

run_ps (conftest.py) launches a PowerShell child once more when it exits non-zero having printed
nothing, warns that it did, and fails naming a launch failure when the second launch is silent
too. Each part fails quietly in its own direction: without the retry a child that never started
reads as a script's verdict, a retry of a child that printed runs a real script twice, and a
retry nobody hears about hides a loaded box. So each mutation removes one part and names the test
in test_run_ps_retry.py that must notice; each control rewrites a part without changing what it
does and must SURVIVE, so the tests are shown to pin behaviour and not spelling.

The suite fakes subprocess.run, so no mutation here starts a PowerShell process.
"""
from __future__ import annotations

import sys
from pathlib import Path

from mutation_runner import SuiteRun, mutate_bytes, parse_harness_args, restore_exact, run_bounded

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
SUITE = ["tools/tests/test_run_ps_retry.py"]
CF = "tools/tests/conftest.py"

ONCE = "test_a_child_silent_once_is_launched_again_and_its_second_result_returned"
TWICE = "test_a_child_silent_twice_fails_as_a_launch_failure"
PRINTED = "test_a_child_that_printed_anything_is_its_own_verdict"
CLEAN = "test_a_clean_exit_with_no_output_is_not_launched_again"

# (label, file, old, new, test that must fail -- None for a control, which must survive)
MUTATIONS: list[tuple[str, str, str, str, str | None]] = [
    (
        "a silent exit is returned as the verdict, never launched again (the BACKLOG item)",
        CF,
        "        r = launch()\n        if silent_failure(r):\n",
        "        r = launch()\n        if False:\n",
        ONCE,
    ),
    (
        "a second silent exit is returned as the verdict, not failed as a launch",
        CF,
        "            r = launch()\n            if silent_failure(r):\n",
        "            r = launch()\n            if False:\n",
        TWICE,
    ),
    (
        "the retry is not heard: no warning",
        CF,
        "            warnings.warn(f\"run_ps:",
        "            (lambda *_a, **_k: None)(f\"run_ps:",
        ONCE,
    ),
    (
        "the launch failure does not name its cause",
        CF,
        "{cause} Rerun it with less running.",
        "Rerun it with less running.",
        TWICE,
    ),
    (
        "a child that printed is launched again too",
        CF,
        "        return p.returncode != 0 and not p.stdout and not p.stderr\n",
        "        return p.returncode != 0\n",
        PRINTED,
    ),
    (
        "stderr alone is not output, so a script's error record is launched again",
        CF,
        "        return p.returncode != 0 and not p.stdout and not p.stderr\n",
        "        return p.returncode != 0 and not p.stdout\n",
        PRINTED,
    ),
    (
        "a clean exit with no output is launched again",
        CF,
        "        return p.returncode != 0 and not p.stdout and not p.stderr\n",
        "        return not p.stdout and not p.stderr\n",
        CLEAN,
    ),
    # --- controls: the same behaviour, spelled differently ------------------------------------
    (
        "control: the silence test written as one negation",
        CF,
        "        return p.returncode != 0 and not p.stdout and not p.stderr\n",
        "        return bool(p.returncode) and not (p.stdout or p.stderr)\n",
        None,
    ),
    (
        "control: the failure shown with pytest's traceback",
        CF,
        "                            pytrace=False)\n",
        "                            pytrace=True)\n",
        None,
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header", "-rf",
                        "-p", "no:cacheprovider"], cwd=REPO)


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    return {ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0]
            for ln in out.splitlines() if ln.startswith("FAILED ") and "::" in ln}


def main() -> int:
    parse_harness_args([m for m in MUTATIONS if m[4] is not None], [m for m in MUTATIONS if m[4] is None])
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str, bool]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        control = expected is None
        path = REPO / relpath
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN", control))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            verdict = ("TIMEOUT" if hung else
                       "SURVIVED" if not fired else
                       "FIRED" if control or expected in failed_names(out) else "FIRED-WRONG-TEST")
            results.append((label, verdict, control))
            print(f"  {verdict:17} {label}")
            if verdict != ("SURVIVED" if control else "FIRED"):
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    cases = [r for r in results if not r[2]]
    controls = [r for r in results if r[2]]
    bad = [r for r in cases if r[1] != "FIRED"] + [r for r in controls if r[1] != "SURVIVED"]
    print(f"{sum(r[1] == 'FIRED' for r in cases)}/{len(cases)} mutations fired the right test; "
          f"{sum(r[1] == 'SURVIVED' for r in controls)}/{len(controls)} controls survived")
    for label, verdict, _ in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
