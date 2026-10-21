"""deploy/installer/platform-startup.ps1 -SelfTest: the machine-start memory fallbacks.

The scenarios live in the script itself so the owner can run the same check on the 8 GB Podman
box, where the failure happened (static 12 GiB Hyper-V VM, 12.4 GB free, start refused with
0x8007000E). This runs it under both engines: the logon task and the watchdog use 5.1.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STARTUP = REPO / "deploy" / "installer" / "platform-startup.ps1"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def test_platform_startup_selftest_passes(run_ps, shell):
    r = run_ps(shell, f"& {q(STARTUP)} -SelfTest\nexit $LASTEXITCODE\n", timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "self-test ok (6 machine-start scenarios" in r.stdout, r.stdout + r.stderr
