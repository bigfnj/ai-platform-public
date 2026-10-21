"""The watchdog starts nothing while Windows is shutting down or restarting.

Seen on the podman box at the owner's power-off (2026-10-15): Explorer asked for it at 01:27:30
(System event 1074), the logoff stopped the podman machine, and at 01:29:01 the watchdog found the
platform down with nobody logged on and ran platform-startup.ps1 as LocalSystem, 13 s before the
event log stopped. Now every path that starts something (the cold start, a confirmed-down restart,
a control-plane recovery) asks lib-runtime's Get-ShutdownState first: a User32 1074 since the last
boot and inside its window, with no 1075 (cancelled) and no Kernel-Boot 27 (a boot, a fast-startup
one included) after it, means a shutdown is under way, and the watchdog logs one line and starts
nothing.

Both engines. Nothing here reads the real event log or runs the real watchdog service: the
decision runs on a fake event reader and a fixed clock, and the watchdog runs from a scratch copy
(as test_control_plane_probe.py runs it) with Get-WinEvent, the CIM and process tables, the health
URL, Stop-VM and the startup script shadowed by functions.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
FAKE_VM = "watchdog-test-no-such-vm"
BOOT = "Microsoft-Windows-Kernel-Boot"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def ev(provider: str, eid: int, minutes_ago: float, record: int) -> str:
    """One fake event as PowerShell, `minutes_ago` before $now. A 1074 carries the properties the
    real one does: [0] the process, [4] the shutdown type."""
    props = ("@([pscustomobject]@{ Value = 'C:\\Windows\\Explorer.EXE (BOX)' }, [pscustomobject]@{ Value = 'BOX' }, "
             "[pscustomobject]@{ Value = 'Other (Planned)' }, [pscustomobject]@{ Value = '0x80000000' }, "
             "[pscustomobject]@{ Value = 'power off' })") if eid == 1074 and provider == "User32" else "@()"
    return (f"[pscustomobject]@{{ ProviderName = '{provider}'; Id = {eid}; RecordId = {record}; "
            f"TimeCreated = $now.AddMinutes(-{minutes_ago}); Properties = {props} }}")


# --- the decision, on a fake reader and a fixed clock -----------------------------------------------

# name -> (minutes since the last boot or None (unreadable), the events, what the reader does)
CASES = {
    "nothing-logged": (120, [], "events"),
    "a-shutdown-asked-for": (120, [("User32", 1074, 1, 10)], "events"),
    "cancelled": (120, [("User32", 1074, 3, 10), ("User32", 1075, 2, 11)], "events"),
    "cancelled-then-asked-again": (120, [("User32", 1075, 3, 10), ("User32", 1074, 1, 11)], "events"),
    # Before the last boot: the previous power-off, the one this boot followed.
    "before-the-last-boot": (10, [("User32", 1074, 12, 10)], "events"),
    # A fast-startup boot leaves LastBootUpTime alone; Kernel-Boot 27 is written all the same.
    "a-boot-since-fast-startup": (600, [("User32", 1074, 8, 10), (BOOT, 27, 5, 11)], "events"),
    # Asked 40 minutes ago and never cancelled nor done: abandoned (its window is 30 minutes).
    "abandoned": (600, [("User32", 1074, 40, 10)], "events"),
    # Another provider's 27 (a NIC driver logs one) is no boot, and another provider's 1074 no shutdown.
    "foreign-events": (120, [("User32", 1074, 3, 10), ("e1dexpress", 27, 1, 11)], "events"),
    "foreign-1074-alone": (120, [("SomeService", 1074, 1, 10)], "events"),
    "no-boot-time": (None, [("User32", 1074, 1, 10)], "events"),
    "log-unreadable": (120, [], "throws"),
    "none-found": (120, [], "none-found"),
}


@pytest.fixture(scope="module")
def decisions(run_ps, shell):
    body = f"""
$ErrorActionPreference = 'Stop'
$Root = 'C:\\r'; $Installer = 'C:\\r\\deploy\\installer'; $TempBase = $env:TEMP; $RuntimeMode = 'podman'
$LogFile = Join-Path $env:TEMP 'shutdown-test.log'
function Write-Log($m) {{ }}
. {q(INSTALLER / 'lib-runtime.ps1')}
$now = [datetime]'2026-10-15 01:29:01'
$out = [ordered]@{{}}
"""
    for name, (boot, events, mode) in CASES.items():
        evs = ", ".join(ev(*e) for e in events)
        reader = {
            "events": f"{{ param($Since) $global:since = $Since; @({evs}) }}",
            "throws": "{ param($Since) throw 'The RPC server is unavailable' }",
            "none-found": ("{ param($Since) throw [System.Management.Automation.ErrorRecord]::new("
                           "[Exception]::new('No events were found'), "
                           "'NoMatchingEventsFound,Microsoft.PowerShell.Commands.GetWinEventCommand', "
                           "'ObjectNotFound', $null) }"),
        }[mode]
        last = f"$now.AddMinutes(-{boot})" if boot is not None else "$null"
        body += f"""
$global:since = $null
$s = Get-ShutdownState -Now $now -LastBoot {last} -ReadEvents {reader}
$out[{q(name)}] = @{{ in = $s.InProgress; detail = $s.Detail
  since = $(if ($global:since) {{ [int]($now - $global:since).TotalMinutes }} else {{ $null }}) }}
"""
    body += "$out | ConvertTo-Json -Compress -Depth 3\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", ["a-shutdown-asked-for", "cancelled-then-asked-again", "foreign-events",
                                  "no-boot-time"])
def test_a_1074_with_no_cancel_or_boot_after_it_is_a_shutdown(decisions, name):
    got = decisions[name]
    assert got["in"] is True, got
    assert got["detail"].startswith("Windows is shutting down or restarting (System event 1074 at 2026-10-15 01:2"), got
    assert "C:\\Windows\\Explorer.EXE (BOX), power off" in got["detail"], got


@pytest.mark.parametrize("name", ["nothing-logged", "cancelled", "before-the-last-boot", "a-boot-since-fast-startup",
                                  "abandoned", "foreign-1074-alone", "none-found"])
def test_anything_else_is_no_shutdown(decisions, name):
    got = decisions[name]
    assert got["in"] is False and got["detail"] == "", got


def test_the_log_is_read_from_the_later_of_the_last_boot_and_the_window(decisions):
    assert decisions["a-shutdown-asked-for"]["since"] == 30, decisions          # booted 2 h ago: the window
    assert decisions["before-the-last-boot"]["since"] == 10, decisions          # booted 10 min ago: the boot
    assert decisions["no-boot-time"]["since"] == 30, decisions


def test_a_log_that_cannot_be_read_is_no_shutdown_and_says_so(decisions):
    got = decisions["log-unreadable"]
    assert got["in"] is False and "the System log could not be read" in got["detail"], got
    assert "RPC server is unavailable" in got["detail"], got


def test_the_real_reader_asks_the_system_log_for_the_three_events_only():
    """The fake readers above stand in for this one; it is never run here."""
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
    start = lib.index("$ShutdownEventReader = {")
    reader = lib[start:lib.index("\n}", start)]
    assert "Get-WinEvent -FilterHashtable" in reader and "LogName = 'System'" in reader, reader
    assert "ProviderName = @('User32', 'Microsoft-Windows-Kernel-Boot')" in reader, reader
    assert "Id = @(1074, 1075, 27)" in reader and "StartTime = $Since" in reader, reader
    assert "-ErrorAction Stop" in reader, reader


# --- the watchdog ----------------------------------------------------------------------------------

# Every outside call a function. The loop's poll sleep ends the run at its third poll; every other
# sleep is skipped. Health answers $global:healthy. Get-WinEvent answers this scenario's events,
# recording each read; Win32_OperatingSystem says the box booted two hours ago and the process table
# is empty (nobody is logged on, as at a power-off's logoff).
STUBS = r"""
$global:polls = 0
$global:stopvm = New-Object System.Collections.Generic.List[string]
$global:startups = New-Object System.Collections.Generic.List[string]
$global:reads = New-Object System.Collections.Generic.List[string]
function global:Start-Sleep {
  param([int]$Seconds, [int]$Milliseconds)
  if ($Seconds -eq 60) { $global:polls++; if ($global:polls -gt 2) { throw 'WATCHDOG-TEST-END' } }
}
function global:Invoke-WebRequest {
  [CmdletBinding()] param([Parameter(Position = 0)]$Uri, $TimeoutSec, [switch]$UseBasicParsing)
  if (-not $global:healthy) { throw 'Unable to connect to the remote server' }
  [pscustomobject]@{ StatusCode = 200 }
}
function global:Get-CimInstance {
  [CmdletBinding()] param([Parameter(Position = 0)][string]$ClassName, [string]$Filter)
  if ($ClassName -eq 'Win32_OperatingSystem') { [pscustomobject]@{ LastBootUpTime = (Get-Date).AddHours(-2) } }
}
function global:Invoke-CimMethod { [CmdletBinding()] param($InputObject, $MethodName) }
function global:Get-Process { [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name) }
function global:Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) throw 'no process may be stopped here' }
function global:Get-VM { [CmdletBinding()] param([string]$Name) }
function global:Stop-VM { [CmdletBinding()] param([string]$Name, [switch]$Force) $global:stopvm.Add($Name) }
function global:powershell.exe { $global:startups.Add(($args | ForEach-Object { [string]$_ }) -join ' '); $global:LASTEXITCODE = 0 }
function global:Get-WinEvent {
  [CmdletBinding()] param([hashtable[]]$FilterHashtable, [int]$MaxEvents)
  $f = $FilterHashtable[0]
  $global:reads.Add("$($f.LogName) $(@($f.ProviderName) -join ',') $(@($f.Id) -join ',') since=$([bool]$f.StartTime)")
  $now = Get-Date
  & $global:events $now
}
"""

ANSWERS = '@echo off\r\nif "%1"=="volume" echo platform_gateway_data\r\nexit /b 0'
NEVER = "ping -n 40 127.0.0.1 >nul\r\nexit /b 0"
# name -> (healthy, the fake podman's `volume` branch, the watchdog's arguments, the events)
SCENARIOS = {
    "down-shutting-down": (False, ANSWERS, "", [(BOOT, 27, 120, 1), ("User32", 1074, 1, 2)]),
    "down-cancelled": (False, ANSWERS, "", [(BOOT, 27, 120, 1), ("User32", 1074, 3, 2), ("User32", 1075, 2, 3)]),
    "down-nothing-logged": (False, ANSWERS, "", []),
    "healthy-shutting-down": (True, ANSWERS, "", [("User32", 1074, 1, 2)]),
    "dead-transport-shutting-down": (True, NEVER, "-ProbeSeconds 1 -ConfirmProbeSeconds 2",
                                     [("User32", 1074, 1, 2)]),
}


def fake_podman(d: Path, branch: str) -> Path:
    d.mkdir(parents=True)
    body = branch if branch.startswith("@echo off") else '@echo off\r\nif not "%1"=="volume" exit /b 0\r\n' + branch
    (d / "podman.cmd").write_bytes((body + "\r\n").encode("ascii"))
    return d


@pytest.fixture(scope="module")
def watchdog_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("wdshut")
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
    real_vm = "$PodmanMachine = 'podman-machine-default'"
    assert lib.count(real_vm) == 1, "lib-runtime no longer names its VM where this test expects"
    lib = lib.replace(real_vm, f"$PodmanMachine = '{FAKE_VM}'")
    body = STUBS
    roots = {}
    for name, (healthy, branch, args, events) in SCENARIOS.items():
        root = base / name / "repo"
        inst = root / "deploy" / "installer"
        inst.mkdir(parents=True)
        shutil.copy2(INSTALLER / "platform-watchdog.ps1", inst / "platform-watchdog.ps1")
        (inst / "lib-runtime.ps1").write_text(lib, encoding="utf-8-sig")
        fake = fake_podman(base / name / "bin", branch)
        appdata = base / name / "localappdata"          # no Programs\Podman for the script to prepend
        appdata.mkdir()
        roots[name] = root
        evs = ", ".join(ev(*e) for e in events)
        body += f"""
$global:polls = 0; $global:stopvm.Clear(); $global:startups.Clear(); $global:reads.Clear()
$global:healthy = ${str(healthy).lower()}
$global:events = {{ param($now) @({evs}) }}
$env:LOCALAPPDATA = {q(appdata)}
$env:Path = {q(fake)} + ';' + {q(SYSTEM32)}
$ended = ''
try {{ & {q(inst / 'platform-watchdog.ps1')} {args} *>&1 | Out-Null }} catch {{ $ended = $_.Exception.Message }}
Write-Output ('<<<{name}>>>' + (@{{ ended = $ended; polls = $global:polls; stopvm = @($global:stopvm)
  startups = @($global:startups); reads = @($global:reads) }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        for k in ("stopvm", "startups", "reads"):
            v = got[k]
            got[k] = [] if v is None else (v if isinstance(v, list) else [v])
        log = roots[name] / "deploy" / "logs" / "watchdog.log"
        got["log"] = log.read_text(encoding="utf-8-sig") if log.is_file() else ""
        out[name] = got
    assert set(out) == set(SCENARIOS), r.stdout + r.stderr
    for name, got in out.items():
        # It ran its two cycles and stopped at the third poll, not at some fault of its own.
        assert "WATCHDOG-TEST-END" in got["ended"] and got["polls"] == 3, (name, got)
    return out


def skips(log: str) -> list[str]:
    return [ln for ln in log.splitlines() if "not starting anything" in ln]


def test_a_cold_start_during_a_shutdown_starts_nothing_and_says_so(watchdog_runs):
    """Six cold-start attempts at service start, then two confirmed-down cycles: each logs one
    line and starts nothing."""
    got = watchdog_runs["down-shutting-down"]
    assert got["startups"] == [] and got["stopvm"] == [], got
    lines = skips(got["log"])
    assert len(lines) == 8, got["log"]
    assert sum("cold start (attempt" in ln for ln in lines) == 6, lines
    assert sum("platform confirmed down - Windows is shutting down or restarting" in ln for ln in lines) == 2, lines
    assert all("(System event 1074 at " in ln and "C:\\Windows\\Explorer.EXE (BOX), power off" in ln for ln in lines), lines
    assert "starting as LocalSystem" not in got["log"], got["log"]
    assert len(got["reads"]) == 8 and all(r.startswith("System User32,") for r in got["reads"]), got["reads"]


@pytest.mark.parametrize("name", ["down-cancelled", "down-nothing-logged"])
def test_with_no_shutdown_under_way_the_cold_start_runs_as_before(watchdog_runs, name):
    got = watchdog_runs[name]
    assert skips(got["log"]) == [], got["log"]
    # Six cold-start attempts and two confirmed-down restarts, each as LocalSystem (nobody logged on).
    assert len(got["startups"]) == 8, got
    assert got["log"].count("nobody is logged on; starting as LocalSystem") == 8, got["log"]
    assert len(got["reads"]) == 8, got["reads"]


def test_a_healthy_cycle_never_reads_the_event_log(watchdog_runs):
    """The check costs a log query: it is made only on a path about to start something."""
    got = watchdog_runs["healthy-shutting-down"]
    assert got["reads"] == [] and got["startups"] == [] and skips(got["log"]) == [], got


def test_a_dead_transport_during_a_shutdown_is_not_force_stopped(watchdog_runs):
    got = watchdog_runs["dead-transport-shutting-down"]
    assert "confirming probe failed too" in got["log"], got["log"]
    assert got["stopvm"] == [] and got["startups"] == [], got
    lines = skips(got["log"])
    assert len(lines) == 1 and "control-plane recovery - Windows is shutting down or restarting" in lines[0], got["log"]
    assert "force-stopping Hyper-V VM" not in got["log"], got["log"]
