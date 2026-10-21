"""NSSM service logs rotate while the service runs, and rotated copies are pruned.

Every service set AppRotateFiles 1 and nothing more: a log rotated only at service start, grew
without bound while running, and no rotated copy was ever removed (the 24 GB box: 69 files, 123 MB
after 19 days). lib-runtime's Get-NssmLogSettings now adds online rotation at a size and an NSSM
Rotate/Post hook running Remove-RotatedServiceLogs. The prune runs under both engines here, and the
hook's own command line is executed exactly as NSSM would run it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
OTHERS = ["platform-broker.out.log", "platform-broker.err.log", "rebuild-stale-20261008-020653.log",
          "smoke-test.log", "notes-20261008T010203.txt"]


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def stamps(n: int) -> list[str]:
    return [f"202609{d:02d}T0{d % 10}0000.{d:03d}" for d in range(1, n + 1)]   # sortable, oldest first


def make_logs(d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    plan = {"platform-broker.out": stamps(12), "platform-broker.err": stamps(3), "ollama.out": stamps(11)}
    for base, ss in plan.items():
        for s in ss:
            (d / f"{base}-{s}.log").write_text("x", encoding="utf-8")
    for o in OTHERS:
        (d / o).write_text("live", encoding="utf-8")
    return plan


def check(d: Path, plan: dict) -> None:
    names = {p.name for p in d.iterdir()}
    for base, ss in plan.items():
        kept = sorted(s for s in ss if f"{base}-{s}.log" in names)
        assert kept == ss[-10:], (base, kept)                 # the NEWEST ten, nothing else removed
    assert all(o in names for o in OTHERS), names


@pytest.fixture(scope="module")
def pruned(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("logs") / "logs"
    plan = make_logs(d)
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$n = Remove-RotatedServiceLogs -LogDir {q(d)}
$again = Remove-RotatedServiceLogs -LogDir {q(d)}
@{{ removed = $n; again = $again
   settings = @(Get-NssmLogSettings -Name 'svc' -Stdout 'C:\\x\\logs\\svc.out.log' -Stderr 'C:\\x\\logs\\svc.err.log' -Lib 'C:\\x\\lib-runtime.ps1' | ForEach-Object {{ ,@($_) }}) }} | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return d, plan, json.loads(r.stdout.strip().splitlines()[-1])


def test_rotated_copies_beyond_the_newest_ten_are_removed_and_nothing_else(pruned):
    d, plan, got = pruned
    check(d, plan)
    assert got["removed"] == 3 and got["again"] == 0, got   # 2 broker.out + 1 ollama.out; then idempotent


def test_every_service_rotates_online_and_runs_the_prune_hook(pruned):
    s = {tuple(a[:3]): a for a in pruned[2]["settings"]}
    assert s[("set", "svc", "AppRotateFiles")][3] == "1"
    assert s[("set", "svc", "AppRotateOnline")][3] == "1"
    assert s[("set", "svc", "AppRotateBytes")][3] == "10485760"
    hook = s[("set", "svc", "AppEvents")]
    assert hook[3] == "Rotate/Post" and "-EncodedCommand " in hook[4], hook


@pytest.mark.skipif(sys.platform != "win32", reason="the hook is a Windows PowerShell command line")
def test_the_hook_command_line_prunes_when_run_as_nssm_runs_it(run_ps, tmp_path):
    d = tmp_path / "hook logs"                                 # a space, as a real path may have one
    plan = make_logs(d)
    r = run_ps("powershell.exe", f". {q(INSTALLER / 'lib-runtime.ps1')}\n"
                                 f"Get-NssmPruneHook -LogDir {q(d)} -Lib {q(INSTALLER / 'lib-runtime.ps1')}\n")
    hook = r.stdout.strip().splitlines()[-1]
    assert hook.lower().startswith(str(Path(r"C:\Windows")).lower()) or "powershell.exe" in hook.lower(), hook
    done = subprocess.run(hook, capture_output=True, text=True, timeout=120)  # CreateProcess, no shell
    assert done.returncode == 0, done.stdout + done.stderr
    check(d, plan)


def test_no_installer_sets_service_log_options_of_its_own():
    """Three installers each set the log options by hand, and all three stopped at AppRotateFiles 1.
    Every service's log settings come from Get-NssmLogSettings now."""
    hits = []
    for path in sorted((REPO / "deploy").rglob("*.ps1")):
        if path.name == "lib-runtime.ps1":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            code = line.split(" #", 1)[0]
            if not code.lstrip().startswith("#") and re.search(r"\bApp(Stdout|Stderr|Rotate\w*|Events)\b", code):
                hits.append(f"{path.relative_to(REPO)}:{n}: {code.strip()}")
    assert hits == [], hits
