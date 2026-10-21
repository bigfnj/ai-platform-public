"""The frontend's node tests, run as part of this rail's suite.

frontend/tests/*.test.js exercise the dependency-free helpers the demos are built on
(src/streamSocket.ts, src/demos/embed-bench/rows.ts, src/visiblePoll.ts,
src/demos/rag/servedBy.ts) under plain node. A node test that nothing
runs is not a test, and run-tests.ps1 registers node targets by hand, so this rail drives its
own from here: the rail's pytest target then fails when they fail.

Node is needed. Without it these SKIP with that reason rather than pass, and
tests/mutate_audit_guards.py refuses to give verdicts over a baseline that skipped them.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TESTS = Path(__file__).resolve().parents[1] / "frontend" / "tests"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH; the frontend "
                                "helper tests cannot run on this box")


def _run(name: str, at_least: int) -> None:
    path = TESTS / name
    p = subprocess.run([NODE, "--test", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    # The witness: node ran this many tests and failed none. A runner that found nothing to run
    # also exits 0, which would read as a pass here otherwise.
    ran = re.search(r"\btests (\d+)", out)
    failed = re.search(r"\bfail (\d+)", out)
    assert ran and int(ran.group(1)) >= at_least, out[-3000:]
    assert failed and int(failed.group(1)) == 0, out[-3000:]


def test_lab_helpers_pass_under_node():
    _run("lab-helpers.test.js", at_least=10)


def test_visible_poll_passes_under_node():
    """src/visiblePoll.ts: the header's 6 s poll skips a hidden page."""
    _run("visible-poll.test.js", at_least=4)

def test_served_by_passes_under_node():
    """src/demos/rag/servedBy.ts: the RAG demo says when Claude, not the local model it labels,
    answered (or that the local model answered after a Claude fallback)."""
    _run("served-by.test.js", at_least=4)
