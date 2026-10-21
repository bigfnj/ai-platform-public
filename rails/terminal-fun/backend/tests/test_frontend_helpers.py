"""The frontend's node tests, run as part of this rail's suite.

frontend/tests/*.test.js exercise the dependency-free helpers the module is built on:
src/chatGuard.ts, which decides whether an assistant reply still belongs on screen,
and src/visiblePoll.ts, which keeps the header's 6 s poll quiet on a hidden page.
A node test that nothing runs is not a test, and run-tests.ps1 registers node targets by hand, so
this rail drives its own from here: the rail's pytest target then fails when they fail.

Node is needed. Without it these SKIP with that reason rather than pass, and
mutate_audit_guards.py refuses to give verdicts over a baseline that skipped them.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TESTS = Path(__file__).resolve().parents[2] / "frontend" / "tests"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH; the frontend "
                                "helper tests cannot run on this box")


def _run(name: str, at_least: int) -> None:
    p = subprocess.run([NODE, "--test", str(TESTS / name)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    # The witness: node ran the tests and failed none. A runner that found nothing also exits 0.
    ran = re.search(r"\btests (\d+)", out)
    failed = re.search(r"\bfail (\d+)", out)
    assert ran and int(ran.group(1)) >= at_least, out[-3000:]
    assert failed and int(failed.group(1)) == 0, out[-3000:]


def test_chat_guard_passes_under_node():
    _run("chat-guard.test.js", at_least=6)


def test_visible_poll_passes_under_node():
    _run("visible-poll.test.js", at_least=4)
