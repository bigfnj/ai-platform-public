"""A file named test_*.py lives in a tests/ directory, so pytest never collects a script.

rails/edu-suite/apps/slide-audio held test_phase3.py, test_phase3_multi.py and test_phase4.py:
scripts that read a slide deck and call the live translation model (and, in phase 4, TTS) at
import, named like tests. A plain `pytest rails/edu-suite` would have collected them, and
collecting a module imports it. They are run_phase*.py now (found by the 2026-10-14 test-health
audit); this keeps another script from being named into a test run.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRUNE = {".git", "node_modules", "__pycache__", "dist", ".venv", ".venv-test", ".test-logs"}
# Hundreds of test files in either tree, public or private; a scan that found a handful saw nothing.
MIN_FILES = 150


def _test_files() -> list[str]:
    """Every tracked test_*.py, repo-relative: `git ls-files` in a checkout, a pruned walk in a
    built tree that is not its own git top level (the public snapshot)."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO:
            ls = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                                capture_output=True, text=True, timeout=60, check=True)
            return [ln for ln in ls.stdout.splitlines()
                    if ln.rsplit("/", 1)[-1].startswith("test_") and ln.endswith(".py")]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        out += [(Path(root) / f).relative_to(REPO).as_posix() for f in files
                if f.startswith("test_") and f.endswith(".py")]
    return out


def test_every_test_file_sits_in_a_tests_directory():
    files = _test_files()
    assert len(files) >= MIN_FILES, f"found only {len(files)} test_*.py files"
    stray = sorted(f for f in files if "tests" not in f.split("/")[:-1])
    assert stray == [], (f"named like tests but outside any tests/ directory, so a pytest run "
                         f"over its parent would collect (and import) them: {stray}")
