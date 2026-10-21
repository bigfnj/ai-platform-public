"""`tools/rail_template.py new` scaffolds a rail whose suite is born with the offline guard.

It used to make an empty tests/, so a new rail's first test_*.py failed
test_offline_guard_coverage.py until somebody wrote a conftest. Now it seeds tests/conftest.py from
tools/rail_templates/conftest.py.tmpl, once: the file is the rail's from then on. Everything is
built under tmp_path, the scaffold's repo root included; nothing writes into rails/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import test_offline_guard_coverage as coverage

TOOLS_TESTS = Path(__file__).resolve().parent


@pytest.fixture
def scaffold(template, tmp_path, monkeypatch):
    """A scratch repo root with one manifest, scaffolded by `new`; returns the rail's root."""
    monkeypatch.setattr(template, "REPO", tmp_path)
    monkeypatch.setattr(template, "RAILS", tmp_path / "rails")
    rail = tmp_path / "rails" / "demo"
    rail.mkdir(parents=True)
    (rail / "rail.json").write_text(json.dumps({"id": "demo", "package": "demo_rail",
                                                "layout": "src"}), encoding="utf-8")
    assert template.cmd_new(argparse.Namespace(id="demo")) == 0
    return rail


def test_a_new_rail_is_born_with_the_offline_guard(scaffold):
    conftest = scaffold / "tests" / "conftest.py"
    assert conftest.is_file()
    compile(conftest.read_text(encoding="utf-8"), str(conftest), "exec")
    assert coverage.takes_the_guard(conftest)


def test_the_seeded_conftest_is_the_rails_from_then_on(template, scaffold):
    conftest = scaffold / "tests" / "conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + "\nOWN = 1\n", encoding="utf-8")
    assert template.cmd_new(argparse.Namespace(id="demo")) == 0
    assert conftest.read_text(encoding="utf-8").endswith("\nOWN = 1\n")


def test_the_coverage_check_passes_for_a_freshly_scaffolded_rail(scaffold, monkeypatch):
    """The author's first test is what makes tests/ a suite; the check then finds it guarded."""
    (scaffold / "tests" / "test_auth.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(coverage, "REPO", scaffold.parents[1])
    assert "rails/demo/tests" in coverage.suites()
    coverage.test_every_suite_takes_the_guard()


def test_the_seeded_conftest_guards_its_suite(scaffold):
    """End to end, in a pytest of its own: the guard beside it, a swallowed call errors."""
    root = scaffold.parents[1]
    (root / "tools" / "tests").mkdir(parents=True)
    shutil.copyfile(TOOLS_TESTS / "offline_guard.py", root / "tools" / "tests" / "offline_guard.py")
    (scaffold / "tests" / "test_planted.py").write_text(
        "import socket\n\n\ndef test_planted():\n    try:\n"
        "        socket.create_connection(('192.0.2.1', 9), timeout=0.5)\n"
        "    except OSError:\n        pass\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "--rootdir", str(root), str(scaffold / "tests")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=root, env=env, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 1 and "1 passed, 1 error" in out, out
    assert "test_planted tried to reach the network, and was refused: [('192.0.2.1', 9)]" in out, out
