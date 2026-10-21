"""The watchdog's control-plane probe, and what the watchdog does on its word.

Test-ControlPlane redirected podman's stdout and stderr and never read them, so a child that wrote
past the pipe's buffer (measured under 5.1: ~5 KB never exits) blocked until the 10 s wait gave
up, and a HEALTHY podman read as a dead transport once a box's volume names passed ~4 KB; a slow
podman under load did the same. After two such cycles platform-watchdog.ps1 force-stopped the VM,
which is how the 2026-10-14 06:34 outage began. Now both pipes are drained, a timeout kills the
probe's tree, and the watchdog asks one slower probe before it force-stops anything.

Real child processes throughout: podman is a .cmd fake on a PATH holding nothing else but
System32, so the real podman on this box is never reached. The watchdog itself runs from a
scratch copy whose lib-runtime names a VM that does not exist, with Stop-VM, the process and CIM
tables, the System log, the health URL and the startup script shadowed by functions (a function
wins over a cmdlet or an exe), and Start-Sleep ending the loop after its second cycle.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
FAKE_VM = "watchdog-test-no-such-vm"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def marker() -> str:
    """A loopback address unique to this run (see test_runtime_detection.hang_marker): the orphan
    check counts only this run's own ping."""
    b = uuid.uuid4().bytes
    return f"127.{1 + b[0] % 254}.{b[1]}.{1 + b[2] % 254}"


def fake_podman(d: Path, volume_branch: str) -> Path:
    """A podman.cmd whose `volume ...` call runs `volume_branch`; anything else (the watchdog's
    `machine list`, `info`) exits 0 having printed nothing."""
    d.mkdir(parents=True)
    # 8 KB of volume names: twice the ~4 KB at which an undrained pipe stopped the old probe.
    (d / "big.txt").write_bytes(b"".join(b"platform_volume_%05d\r\n" % i for i in range(400)))
    (d / "podman.cmd").write_bytes(
        ('@echo off\r\nif not "%1"=="volume" exit /b 0\r\n' + volume_branch + "\r\n").encode("ascii"))
    return d


BIG = 'type "%~dp0big.txt"\r\necho a progress line 1>&2\r\nexit /b 0'
DOWN = "echo Cannot connect to Podman. Is the machine running? 1>&2\r\nexit /b 125"


# --- the probe ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def probes(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("cp")
    big, down = fake_podman(base / "big", BIG), fake_podman(base / "down", DOWN)
    m = marker()
    # Hangs as podman does when its SSH transport dies, through a CHILD that inherits its pipes.
    hangs = fake_podman(base / "hangs", f"ping -n 29 {m} >nul\r\nexit /b 0")
    rx = m.replace(".", "[.]") + r"(\s|$)"
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$rx = {q(rx)}
function Get-HangChild {{
  @(Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object {{ $_.CommandLine -match $rx }}).Count
}}
# Stop-ProcessTree, recorded: how many of the hung probe's children were alive at the kill. It
# waits (bounded) for the child first: on a loaded host cmd.exe can still be starting ping at the
# cut-off, and a kill that finds no child proves nothing about one.
$realStop = ${{function:Stop-ProcessTree}}
$global:kills = New-Object System.Collections.Generic.List[int]
function Stop-ProcessTree {{
  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)
  $n = 0; $until = (Get-Date).AddSeconds(20)
  do {{ $n = Get-HangChild; if ($n -gt 0) {{ break }}; Start-Sleep -Milliseconds 200 }} while ((Get-Date) -lt $until)
  & $realStop -Process $Process
  $global:kills.Add($n)
}}
$out = [ordered]@{{}}
$env:Path = {q(big)} + ';' + {q(SYSTEM32)}
$out['big'] = Get-ControlPlaneProbe
$out['big_bool'] = Test-ControlPlane
$env:Path = {q(down)} + ';' + {q(SYSTEM32)}
$out['down'] = Get-ControlPlaneProbe
$env:Path = {q(SYSTEM32)}
$out['absent'] = Get-ControlPlaneProbe
$env:Path = {q(hangs)} + ';' + {q(SYSTEM32)}
$out['hangs'] = Get-ControlPlaneProbe -TimeoutMs 700
$out['kills'] = @($global:kills)
# Bounded wait for exit: a terminated process can stay listed for a moment. Three seconds is far
# below the 28 s an orphan of a lone Kill() lives, so a probe that kills podman alone fails here.
$deadline = (Get-Date).AddSeconds(3)
do {{ $n = Get-HangChild; if ($n -eq 0) {{ break }}; Start-Sleep -Milliseconds 200 }} while ((Get-Date) -lt $deadline)
$out['orphans'] = $n
$out | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_podman_that_prints_8_kb_answers_the_probe(probes):
    """The old probe never read the pipe, so `type` blocked on it and the probe timed out."""
    assert probes["big"]["Alive"] is True, probes["big"]
    assert "answered in" in probes["big"]["Detail"], probes["big"]
    assert probes["big_bool"] is True


def test_a_failing_podman_is_dead_and_says_why(probes):
    d = probes["down"]
    assert d["Alive"] is False and "exit 125" in d["Detail"] and "Cannot connect" in d["Detail"], d


def test_no_podman_on_path_is_dead_and_says_so(probes):
    assert probes["absent"]["Alive"] is False and "not on PATH" in probes["absent"]["Detail"]


def test_a_hung_probe_times_out_and_its_tree_is_killed(probes):
    h = probes["hangs"]
    assert h["Alive"] is False and "no answer" in h["Detail"], h
    assert probes["kills"] and probes["kills"][0] >= 1, (
        f"the hung podman's child was not running at the kill, so this proved nothing: {probes}")
    assert probes["orphans"] == 0, probes


# --- the watchdog ---------------------------------------------------------------------------

STUBS = r"""
$global:polls = 0
$global:stopvm = New-Object System.Collections.Generic.List[string]
$global:startups = New-Object System.Collections.Generic.List[string]
$global:reaped = New-Object System.Collections.Generic.List[int]
# The loop's poll sleep ends the run after its second cycle; every other sleep is skipped.
function global:Start-Sleep {
  param([int]$Seconds, [int]$Milliseconds)
  if ($Seconds -eq 60) { $global:polls++; if ($global:polls -gt 2) { throw 'WATCHDOG-TEST-END' } }
}
function global:Invoke-WebRequest {
  [CmdletBinding()] param([Parameter(Position = 0)]$Uri, $TimeoutSec, [switch]$UseBasicParsing)
  [pscustomobject]@{ StatusCode = 200 }
}
function global:Get-CimInstance { [CmdletBinding()] param([Parameter(Position = 0)][string]$ClassName, [string]$Filter) }
function global:Invoke-CimMethod { [CmdletBinding()] param($InputObject, $MethodName) }
function global:Get-Process { [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name) }
function global:Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) $global:reaped.Add($Id) }
function global:Get-VM { [CmdletBinding()] param([string]$Name) }
function global:Stop-VM { [CmdletBinding()] param([string]$Name, [switch]$Force) $global:stopvm.Add($Name) }
function global:powershell.exe { $global:startups.Add(($args | ForEach-Object { [string]$_ }) -join ' '); $global:LASTEXITCODE = 0 }
# The shutdown check before a recovery reads the System log: no events, so no shutdown under way.
function global:Get-WinEvent { [CmdletBinding()] param([hashtable[]]$FilterHashtable, [int]$MaxEvents) }
"""

# name -> (the fake's `volume` branch, the watchdog's arguments)
SCENARIOS = {
    "big": (BIG, "-ConfirmProbeSeconds 5"),
    # Answers in ~15 s: past the fast probe, inside the confirming one at its shipped default.
    "slow15": ("ping -n 16 127.0.0.1 >nul\r\nexit /b 0", "-ProbeSeconds 2"),
    # Never answers in time, so the confirming probe fails too.
    "dead": ("ping -n 40 127.0.0.1 >nul\r\nexit /b 0", "-ProbeSeconds 1 -ConfirmProbeSeconds 3"),
}


@pytest.fixture(scope="module")
def watchdog_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("wd")
    body = STUBS
    roots = {}
    for name, (branch, args) in SCENARIOS.items():
        root = base / name / "repo"
        inst = root / "deploy" / "installer"
        inst.mkdir(parents=True)
        shutil.copy2(INSTALLER / "platform-watchdog.ps1", inst / "platform-watchdog.ps1")
        lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
        real_vm = "$PodmanMachine = 'podman-machine-default'"
        assert lib.count(real_vm) == 1, "lib-runtime no longer names its VM where this test expects"
        (inst / "lib-runtime.ps1").write_text(lib.replace(real_vm, f"$PodmanMachine = '{FAKE_VM}'"),
                                              encoding="utf-8-sig")
        fake = fake_podman(base / name / "bin", branch)
        appdata = base / name / "localappdata"          # no Programs\Podman for the script to prepend
        appdata.mkdir()
        roots[name] = root
        body += f"""
$global:polls = 0; $global:stopvm.Clear(); $global:startups.Clear(); $global:reaped.Clear()
$env:LOCALAPPDATA = {q(appdata)}
$env:Path = {q(fake)} + ';' + {q(SYSTEM32)}
$ended = ''
try {{ & {q(inst / 'platform-watchdog.ps1')} {args} *>&1 | Out-Null }} catch {{ $ended = $_.Exception.Message }}
Write-Output ('<<<{name}>>>' + (@{{ ended = $ended; polls = $global:polls; stopvm = @($global:stopvm)
  startups = @($global:startups); reaped = @($global:reaped) }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        log = roots[name] / "deploy" / "logs" / "watchdog.log"
        got["log"] = log.read_text(encoding="utf-8-sig") if log.is_file() else ""
        out[name] = got
    assert set(out) == set(SCENARIOS), r.stdout + r.stderr
    for name, got in out.items():
        # It ran its two cycles and stopped at the third poll, not at some fault of its own.
        assert "WATCHDOG-TEST-END" in got["ended"] and got["polls"] == 3, (name, got)
        assert got["reaped"] == [], (name, got)                 # the process table is empty
    return out


def failed_probes(log: str) -> list[str]:
    return [ln for ln in log.splitlines() if "control-plane probe failed" in ln]


def test_a_podman_that_prints_8_kb_is_never_counted_as_down(watchdog_runs):
    got = watchdog_runs["big"]
    assert failed_probes(got["log"]) == [], got["log"]
    assert got["stopvm"] == [] and got["startups"] == [], got


def test_a_podman_that_answers_in_15_s_is_not_dead(watchdog_runs):
    """Two fast probes miss it, the confirming one at its shipped default hears it: no force-stop,
    no restart, and the log says which probe decided."""
    got = watchdog_runs["slow15"]
    misses = failed_probes(got["log"])
    assert len(misses) == 2 and all("no answer in 2 s" in ln for ln in misses), got["log"]
    assert "confirming with a 60 s probe" in got["log"], got["log"]
    assert "confirming probe answered in" in got["log"] and "slow, not dead; no restart" in got["log"], got["log"]
    assert got["stopvm"] == [] and got["startups"] == [], got


def test_a_podman_that_never_answers_is_force_stopped_after_the_confirming_probe(watchdog_runs):
    got = watchdog_runs["dead"]
    log = got["log"]
    assert len(failed_probes(log)) == 2, log
    assert "confirming probe failed too (podman volume ls gave no answer in 3 s)" in log, log
    assert log.index("confirming probe failed too") < log.index("force-stopping Hyper-V VM"), log
    assert got["stopvm"] == [FAKE_VM], got
    assert len(got["startups"]) == 1, got                     # the control-plane recovery's start
