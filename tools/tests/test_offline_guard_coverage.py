"""Every pytest suite run-tests.ps1 runs takes the offline guard (tools/tests/offline_guard.py).

The guard holds only where a conftest imports its hooks, and a suite added next month has no
conftest until somebody writes one. So the suites are found here by run-tests.ps1's own rules
(core's five directories, and every `tests` directory under rails/ holding a test_*.py, outside
native/, node_modules/ and .venv/), and each must have a conftest.py in that directory that
imports the guard's two hooks. A suite that cannot hold the guard goes in UNGUARDED with the
reason, which is a decision somebody made rather than a conftest nobody wrote.

In the public snapshot the withheld rails are absent, so only the shipped suites are found.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORE = ("services/broker/tests", "apps/platform/backend/tests", "packages/platform_core/tests",
        "tools/tests", "packages/edu-media-core/tests")
SKIP_DIRS = {"native", "node_modules", ".venv"}
HOOKS = {"pytest_runtest_setup", "pytest_runtest_teardown"}

#: repo-relative test directory -> why it runs without the guard.
UNGUARDED: dict[str, str] = {}


def suites() -> list[str]:
    found = [d for d in CORE if (REPO / d).is_dir()]
    rails = REPO / "rails"
    for rail in sorted(p for p in rails.iterdir() if p.is_dir()) if rails.is_dir() else []:
        for root, dirs, files in os.walk(rail):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and d != ".git")
            if Path(root).name == "tests" and any(f.startswith("test_") and f.endswith(".py")
                                                  for f in files):
                found.append(Path(root).relative_to(REPO).as_posix())
    return found


def takes_the_guard(conftest: Path) -> bool:
    tree = ast.parse(conftest.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "offline_guard":
            if HOOKS <= {a.name for a in node.names}:
                return True
    return False


def test_the_suites_are_found():
    found = suites()
    assert set(CORE) <= set(found)
    assert any(d.startswith("rails/") for d in found), found


def test_every_suite_takes_the_guard():
    missing = [d for d in suites() if d not in UNGUARDED
               and not ((REPO / d / "conftest.py").is_file()
                        and takes_the_guard(REPO / d / "conftest.py"))]
    assert not missing, ("these suites' conftest.py does not import the offline guard's hooks "
                         "(from offline_guard import outbound, pytest_runtest_setup, "
                         f"pytest_runtest_teardown), or there is none: {missing}")


def test_an_unguarded_suite_is_one_that_exists_and_says_why():
    found = set(suites())
    for d, why in UNGUARDED.items():
        assert d in found, f"{d} is excused from the offline guard but is not a suite"
        assert why.strip(), f"{d} is excused from the offline guard with no reason"
