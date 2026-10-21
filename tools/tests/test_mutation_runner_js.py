"""The node harnesses' runner (`mutation_runner.mjs`), driven from pytest.

Same two properties as test_mutation_runner.py pins for the Python runner: a hung suite ends at
its limit with the whole tree killed, a grandchild holding the pipe included, and a restore is
proven byte for byte. Node is needed to run these; without it they SKIP with that reason in
the report rather than pass, because a runner nobody exercised is not a runner that works.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from test_mutation_runner import _alive, _kill

NODE = shutil.which("node")
RUNNER = (Path(__file__).resolve().parent / "mutation_runner.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH; the node "
                                "mutation runner cannot be exercised on this box")


def _node(script: str, timeout: float = 120) -> dict:
    """Run an ES-module snippet that imports the runner and prints one JSON line last."""
    p = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert p.returncode == 0 and lines, p.stdout + p.stderr
    return json.loads(lines[-1])


def test_a_finished_run_reports_its_status_and_output():
    r = _node(f"import {{ runBoundedSync }} from {json.dumps(RUNNER)}\n"
              "const r = runBoundedSync(process.execPath,"
              " ['-e', 'console.log(\"hello\"); process.exit(3)'], {timeoutMs: 60000})\n"
              "console.log(JSON.stringify(r))\n")
    assert r["status"] == 3 and r["timedOut"] is False
    assert "hello" in r["out"]


# These two bound a run at 20 s from its start, a taskkill included, where test_mutation_runner.py
# times its bounds from the kill. Here the kill happens inside the watchdog, a node process of its
# own, so no wrapper in this process can time it, and timing it there would take a hook in the
# shipped runner. Every harness case against them is caught by another assertion as well (the
# TIMEOUT marker, the status, the grandchild's death), and under a CPU hog plus eight
# process-spawning loops a run took 4.6-8.0 s (2026-10-14), so it takes a stall of some 12 s more
# to trip the bound.
def test_a_hung_run_ends_at_its_limit_as_timeout():
    t0 = time.monotonic()
    r = _node(f"import {{ runBoundedSync }} from {json.dumps(RUNNER)}\n"
              "const r = runBoundedSync(process.execPath,"
              " ['-e', 'setTimeout(() => {}, 60000)'], {timeoutMs: 1000})\n"
              "console.log(JSON.stringify(r))\n")
    assert time.monotonic() - t0 < 20
    assert r["status"] == "TIMEOUT" and r["timedOut"] is True
    assert "[TIMEOUT:" in r["out"]


def test_a_grandchild_holding_the_pipe_is_killed_with_the_tree(tmp_path):
    """`node --test` runs each test file in a child of its own; spawnSync's timeout killed only
    the orchestrator. The grandchild here is one a plain kill of its parent would MISS on this
    platform. On POSIX that is any child: the parent's death orphans it. On Windows libuv puts
    every non-detached child into a kill-on-close job object, so only a DETACHED grandchild
    survives its parent, and taskkill /T still finds it by parent PID. A non-detached one there
    would make this test pass with no tree kill at all (the mutation harness found exactly
    that)."""
    pidfile = tmp_path / "gc.pid"
    child = ("const { spawn } = require('node:child_process');"
             " const gc = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)'],"
             " { stdio: 'inherit', detached: process.platform === 'win32' });"
             " require('node:fs').writeFileSync(" + json.dumps(str(pidfile)) + ", String(gc.pid));"
             " setTimeout(() => {}, 60000)")
    gc_pid = None
    try:
        t0 = time.monotonic()
        r = _node(f"import {{ runBoundedSync }} from {json.dumps(RUNNER)}\n"
                  f"const r = runBoundedSync(process.execPath, ['-e', {json.dumps(child)}],"
                  " {timeoutMs: 2000})\n"
                  "console.log(JSON.stringify(r))\n")
        elapsed = time.monotonic() - t0
        gc_pid = int(pidfile.read_text())
        assert r["timedOut"] is True
        assert elapsed < 20, f"the runner waited {elapsed:.1f} s"
        deadline = time.monotonic() + 10
        while _alive(gc_pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(gc_pid), "the grandchild outlived the kill"
    finally:
        if gc_pid is None and pidfile.exists():
            gc_pid = int(pidfile.read_text())
        if gc_pid is not None and _alive(gc_pid):
            _kill(gc_pid)


def test_a_restore_is_proven_byte_for_byte(tmp_path):
    f = tmp_path / "f.ts"
    f.write_bytes(b"x\n")
    r = _node(f"import {{ restoreExact }} from {json.dumps(RUNNER)}\n"
              f"const file = {json.dumps(str(f))}\n"
              "restoreExact(file, Buffer.from('y\\r\\n'))\n"
              "const translating = { data: null,"
              " writeFileSync(_f, b) { this.data = Buffer.from(b.toString().replace(/\\n/g, '\\r\\n')) },"
              " readFileSync() { return this.data } }\n"
              "let threw = ''\n"
              "try { restoreExact(file, Buffer.from('z\\n'), translating) } catch (e) { threw = String(e) }\n"
              "console.log(JSON.stringify({ threw }))\n")
    assert f.read_bytes() == b"y\r\n"
    assert "TREE IS DIRTY" in r["threw"]
