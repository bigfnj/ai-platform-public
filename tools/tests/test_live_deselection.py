"""`-k "not LiveTests"` deselects only real live-test classes, never a guard.

run-tests.ps1 and .github/scripts/run_public_suite.py both pass `-k "not LiveTests"`. pytest's
-k is a SUBSTRING match, case-insensitive, over a test's own name, its class, its module and
its package. So a guard named `test_livetests_stay_opt_in`, a class `NoLiveTestsLeak`, or a
file `test_livetests_gate.py` would be deselected along with the real live tests, silently: the
run reports fewer tests and stays green.

The filter stays, as defence in depth. The CLI_LIVE / JOB_AID_LIVE_TESTS opt-ins now gate the
tests that spend quota, but several public-board classes (job-aid's Workable, Workday, Lever,
Ashby) are still held back by this filter and their network check alone. What this file
enforces: every name the filter can catch is a test CLASS named `...LiveTests`, the shape a
real live test has here, and nothing else in the tree matches it.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _filter_terms() -> set[str]:
    """The term each runner deselects, read from the runners themselves, so a changed filter
    cannot leave this test checking the old one."""
    ps1 = (REPO / "run-tests.ps1").read_text(encoding="utf-8")
    terms = set(re.findall(r"'-k',\s*'not (\w+)'", ps1))
    public = REPO / ".github" / "scripts" / "run_public_suite.py"
    if public.is_file():
        terms |= set(re.findall(r'"-k",\s*"not (\w+)"', public.read_text(encoding="utf-8")))
    return terms


# Not `git ls-files`: the public snapshot is checked by a dry run over a built directory that is
# not a git checkout, and this test has to hold there too. Walked, with the trees no test lives in
# (and that are large) pruned.
_PRUNE = {".git", "node_modules", ".venv", ".venv-test", "__pycache__", ".test-logs", "dist",
          "local", "var"}


def _python_test_files() -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _PRUNE and not d.startswith(".venv")]
        rel = Path(root).relative_to(REPO).as_posix()
        for f in files:
            if f.endswith(".py") and (f.startswith("test_") or "/tests" in f"/{rel}"):
                out.append(Path(root) / f)
    return out


def test_both_runners_still_deselect_by_one_known_term():
    assert _filter_terms() == {"LiveTests"}, (
        f"the runners' -k filter changed to {_filter_terms()}; update this test with it")


def test_nothing_but_a_live_test_class_matches_the_filter():
    term = next(iter(_filter_terms())).lower()
    swallowed: list[str] = []
    live: list[str] = []
    for path in _python_test_files():
        rel = path.relative_to(REPO).as_posix()
        if term in path.stem.lower() or any(term in p.lower() for p in path.parent.parts):
            swallowed.append(f"{rel}: the whole module or package matches")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if term not in node.name.lower():
                continue
            if isinstance(node, ast.ClassDef) and node.name.endswith("LiveTests"):
                live.append(f"{rel}::{node.name}")
            else:
                swallowed.append(f"{rel}:{node.lineno} {node.name}")
    assert live, "found no live-test class at all; the walk is not reading the tree"
    assert not swallowed, ("these are deselected by -k 'not LiveTests' but are not live-test "
                           "classes, so they never run:\n  " + "\n  ".join(swallowed))
