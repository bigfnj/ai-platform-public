"""platform-startup.ps1 releases its lock on every way out (an auditor's open item, A5 list).

Its early `exit 1`s left deploy\\logs\\startup.lock behind, and so did a terminating error. The next
logon honoured a lock whose PID was alive, and Windows reuses PIDs, so a startup could wait out the
two-minute timeout, decide another run owned the work, and leave the platform down. The REAL script
runs here from a scratch tree under both engines: once with no deploy\\.env (an exit 1), once with a
deploy\\.env compose would refuse (Read-DotEnvFile throws).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def tree(base: Path, env: str | None) -> Path:
    root = base / "repo"
    (root / "deploy" / "installer").mkdir(parents=True)
    for f in ("platform-startup.ps1", "lib-runtime.ps1"):
        shutil.copy2(INSTALLER / f, root / "deploy" / "installer" / f)
    if env is not None:
        (root / "deploy" / ".env").write_text(env, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def runs(run_ps, offline_script, shell, tmp_path_factory):
    roots = {"no-env": tree(tmp_path_factory.mktemp("lk"), None),
             "refused-env": tree(tmp_path_factory.mktemp("lk"), 'PLATFORM_ENABLED_APPS="terminal-fun\n')}
    body = ""
    for name, root in roots.items():
        # Its own process, through offline_script (conftest): past its early exit the script
        # would call the gateway on :1111, and a function of this body does not reach a child.
        child = offline_script(root / "deploy" / "installer" / "platform-startup.ps1", "-RuntimeMode desktop")
        body += f"""
Write-Output '<<<{name}>>>'
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(child)} *>&1 | Out-Null
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("LOCK=" + (Test-Path {q(root / 'deploy' / 'logs' / 'startup.lock')}))
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        log = roots[name] / "deploy" / "logs" / "startup.log"
        out[name] = (text, log.read_text(encoding="utf-8-sig") if log.exists() else "")
    return out


def test_an_early_exit_releases_the_lock(runs):
    text, log = runs["no-env"]
    assert "EXIT=1" in text and "LOCK=False" in text, (text, log)
    assert "aborting" in log


def test_a_terminating_error_releases_the_lock(runs):
    text, log = runs["refused-env"]
    assert "EXIT=0" not in text and "LOCK=False" in text, (text, log)
    assert "startup failed:" in log and "unterminated quoted value" in log, log
