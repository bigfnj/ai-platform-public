"""Rotated service logs are pruned on a box whose NSSM has no event hooks, and the installers say so.

The installers set an NSSM `AppEvents Rotate/Post` hook to run Remove-RotatedServiceLogs after each
rotation (test_service_logs.py), but NSSM 2.24, the build Install-NssmExe fetches, predates event
hooks: the `nssm set` fails, its exit code was never read, and no hook key exists after
registration, so rotated logs piled up again (2026-10-14: 339 rotated files in deploy\\logs). Now
the watchdog prunes deploy\\logs from its monitor loop once a day (lib-runtime
Invoke-LogPruneIfDue), and install-services, install-native and register-watchdog read the hook
back after registration and warn when it is missing.

Both engines throughout. Nothing here runs a real installer, register-watchdog, the watchdog's
service or nssm: the watchdog runs from a scratch copy with every outside call a function (as in
test_control_plane_probe.py), the installers' Install-Svc and register-watchdog's
Set-WatchdogServiceLogs are lifted out by the parser and run against a stand-in nssm.cmd, and the
registry read is a function standing in for Get-ItemProperty.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
KEEP = 10                                        # lib-runtime's $NssmLogKeep
STREAMS = ["platform-broker.out", "platform-broker.err", "platform-course-builder.out", "watchdog-svc"]
OTHERS = ["platform-broker.out.log", "watchdog-svc.log", "rebuild-stale-20261008-020653.log", "smoke-test.log"]
ROTATED = re.compile(r"^(?P<base>.+)-\d{8}T\d{6}\.\d{3}\.log$")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def stamp(i: int) -> str:
    """NSSM rotation stamps for i in 0..29, oldest first: day i + 1 of September 2026."""
    return f"202609{i + 1:02d}T000000.{i:03d}"


def make_logs(d: Path, per_stream: int = 30) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for base in STREAMS:
        for i in range(per_stream):
            (d / f"{base}-{stamp(i)}.log").write_text("x", encoding="utf-8")
    for o in OTHERS:
        (d / o).write_text("live", encoding="utf-8")


def counts(d: Path) -> dict:
    out: dict = {}
    for p in d.iterdir():
        m = ROTATED.match(p.name)
        if m:
            out[m.group("base")] = out.get(m.group("base"), 0) + 1
    return out


# --- the prune, on a fake clock -------------------------------------------------------------------

PLANT = """
function Plant([string]$Dir, [string]$Base, [int]$N) {
  for ($i = 0; $i -lt $N; $i++) { Set-Content -LiteralPath (Join-Path $Dir ('{0}-20261013T0{1}0000.000.log' -f $Base, $i)) -Value 'new' }
}
"""


@pytest.fixture(scope="module")
def clock_runs(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("prune") / "logs"
    make_logs(d)
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
{PLANT}
$rx = '^(?<base>.+)-\\d{{8}}T\\d{{6}}\\.\\d{{3}}\\.log$'
function Count {{
  $h = [ordered]@{{}}
  foreach ($f in @(Get-ChildItem -LiteralPath {q(d)} -File | Where-Object {{ $_.Name -match $rx }})) {{
    $b = [regex]::Match($f.Name, $rx).Groups['base'].Value; $h[$b] = 1 + [int]$h[$b]
  }}
  $h
}}
$t0 = [datetime]'2026-10-14 09:00:00'
$out = [ordered]@{{}}
$r = Invoke-LogPruneIfDue -LogDir {q(d)} -Now $t0
$out.first = @{{ ran = $r.Ran; removed = $r.Removed; last = $r.Last.ToString('o'); counts = (Count) }}
Plant {q(d)} 'platform-broker.out' 5
$steps = [ordered]@{{ hour = $t0.AddHours(1); almost = $t0.AddHours(23).AddMinutes(59); day = $t0.AddDays(1) }}
foreach ($k in $steps.Keys) {{
  $r = Invoke-LogPruneIfDue -LogDir {q(d)} -Last $r.Last -Now $steps[$k]
  $out[$k] = @{{ ran = $r.Ran; removed = $r.Removed; last = $r.Last.ToString('o'); counts = (Count) }}
}}
Plant {q(d)} 'watchdog-svc' 3
# The clock went back two days: due at once, not two days from now.
$r = Invoke-LogPruneIfDue -LogDir {q(d)} -Last $r.Last -Now $t0.AddDays(-1)
$out.back = @{{ ran = $r.Ran; removed = $r.Removed; last = $r.Last.ToString('o'); counts = (Count) }}
$out | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), d


def test_one_prune_pass_leaves_the_keep_count_of_each_stream_and_nothing_else(clock_runs):
    got, d = clock_runs
    first = got["first"]
    assert first["ran"] is True and first["removed"] == len(STREAMS) * (30 - KEEP), first
    assert first["counts"] == {s: KEEP for s in STREAMS}, first
    assert all((d / o).read_text(encoding="utf-8") == "live" for o in OTHERS)     # live logs untouched
    assert first["last"].startswith("2026-10-14T09:00:00"), first


def test_the_prune_runs_at_most_once_a_day(clock_runs):
    got, _ = clock_runs
    for k in ("hour", "almost"):
        assert got[k]["ran"] is False and got[k]["removed"] == 0, (k, got[k])
        assert got[k]["counts"]["platform-broker.out"] == KEEP + 5, (k, got[k])   # the five planted stay
        assert got[k]["last"] == got["first"]["last"], (k, got[k])               # the clock is not reset
    day = got["day"]
    assert day["ran"] is True and day["removed"] == 5, day
    assert day["counts"] == {s: KEEP for s in STREAMS}, day
    assert day["last"].startswith("2026-10-15T09:00:00"), day


def test_a_clock_that_went_back_does_not_hold_the_prune_off(clock_runs):
    back = clock_runs[0]["back"]
    assert back["ran"] is True and back["removed"] == 3, back
    assert back["counts"]["watchdog-svc"] == KEEP and back["last"].startswith("2026-10-13T09:00:00"), back


# --- a box with no watchdog: the daily prune from a script that runs and exits ----------------------

# The same days as the clock above, but each call a fresh one: Last lives in deploy\logs\log-prune.last.
STAMP_BODY = """
$t0 = [datetime]::SpecifyKind([datetime]'2026-10-14 09:00:00', 'Utc')
$stamp = Join-Path {d} 'log-prune.last'
function Step($at) {{
  $r = Invoke-LogPruneFromStamp -LogDir {d} -Now $at
  $s = if (Test-Path -LiteralPath $stamp) {{ [IO.File]::ReadAllText($stamp) }} else {{ '' }}
  @{{ ran = $r.Ran; removed = $r.Removed; stamp = $s; counts = (Count) }}
}}
$out = [ordered]@{{}}
$out.first = Step $t0
Plant {d} 'platform-broker.out' 5
$out.hour = Step $t0.AddHours(1)
$out.almost = Step $t0.AddHours(23).AddMinutes(59)
$out.day = Step $t0.AddDays(1)
Plant {d} 'watchdog-svc' 3
$out.back = Step $t0.AddDays(-1)
Plant {d} 'platform-broker.err' 2
[IO.File]::WriteAllText($stamp, 'not a time')
$out.garbled = Step $t0.AddDays(-1).AddMinutes(5)
$out | ConvertTo-Json -Depth 4 -Compress
"""


def clock_count(d: Path) -> str:
    """A Count function over $d's rotated logs, as the clock fixture above defines it."""
    return ("$rx = '^(?<base>.+)-\\d{8}T\\d{6}\\.\\d{3}\\.log$'\n"
            "function Count {\n  $h = [ordered]@{}\n"
            f"  foreach ($f in @(Get-ChildItem -LiteralPath {q(d)} -File | Where-Object {{ $_.Name -match $rx }})) {{\n"
            "    $b = [regex]::Match($f.Name, $rx).Groups['base'].Value; $h[$b] = 1 + [int]$h[$b]\n  }\n  $h\n}\n")


@pytest.fixture(scope="module")
def stamp_runs(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("stamp") / "logs"
    make_logs(d)
    body = (f"$ErrorActionPreference = 'Stop'\n. {q(INSTALLER / 'lib-runtime.ps1')}\n{PLANT}\n{clock_count(d)}\n"
            + STAMP_BODY.format(d=q(d)))
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_script_run_prunes_once_a_day_through_its_stamp(stamp_runs):
    got = stamp_runs
    first = got["first"]
    assert first["ran"] is True and first["removed"] == len(STREAMS) * (30 - KEEP), first
    assert first["counts"] == {s: KEEP for s in STREAMS}, first
    assert first["stamp"].startswith("2026-10-14T09:00:00") and first["stamp"].endswith("Z"), first
    for k in ("hour", "almost"):                     # separate calls, the same day: not again
        assert got[k]["ran"] is False and got[k]["counts"]["platform-broker.out"] == KEEP + 5, (k, got[k])
        assert got[k]["stamp"] == first["stamp"], (k, got[k])
    assert got["day"]["ran"] is True and got["day"]["removed"] == 5, got["day"]
    assert got["day"]["stamp"].startswith("2026-10-15T09:00:00"), got["day"]


def test_a_clock_set_back_or_a_garbled_stamp_is_due(stamp_runs):
    assert stamp_runs["back"]["ran"] is True and stamp_runs["back"]["removed"] == 3, stamp_runs["back"]
    garbled = stamp_runs["garbled"]
    assert garbled["ran"] is True and garbled["removed"] == 2, garbled
    assert garbled["stamp"].startswith("2026-10-13T09:05:00"), garbled


# --- the full stack's rebuild-stale.ps1 prunes, on a fake full-stack box ----------------------------

REBUILD = REPO / "deploy" / "rebuild-stale.ps1"
# compose renders two services to build; every other docker call (ps, inspect, up) answers 0, silent.
FULL_RENDER = {"services": {"alpha": {"build": {"context": "."}, "image": "a:latest"},
                            "gateway": {"build": {"context": "."}, "image": "g:latest"}}}
FAKE_DOCKER = ('@echo off\r\n>> "%~dp0calls.txt" echo docker %*\r\n'
               'if "%1"=="compose" if "%2"=="config" ( type "%~dp0render.json" & exit /b 0 )\r\n'
               'exit /b 0\r\n')


def _full_stack_box(base: Path) -> tuple[Path, Path]:
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    shutil.copy2(REBUILD, deploy / REBUILD.name)
    shutil.copy2(INSTALLER / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    (deploy / ".env").write_text("PLATFORM_RUNTIME=desktop\n", encoding="utf-8")
    make_logs(deploy / "logs")
    fake = base / "bin"
    fake.mkdir()
    (fake / "render.json").write_text(json.dumps(FULL_RENDER), encoding="utf-8")
    (fake / "docker.cmd").write_bytes(FAKE_DOCKER.encode("ascii"))
    (base / "profile" / "AppData" / "Local" / "Temp").mkdir(parents=True)
    return deploy, fake


def _rebuild(shell: str, deploy: Path, fake: Path, *extra: str):
    env = dict(os.environ, PATH=os.pathsep.join([str(fake), SYSTEM32, os.path.dirname(shell)]),
               USERPROFILE=str(deploy.parent.parent / "profile"))
    env.pop("DOCKER_HOST", None)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                           str(deploy / REBUILD.name), "-Stack", "full", *extra],
                          capture_output=True, encoding="utf-8", errors="replace", env=env,
                          cwd=deploy.parent.parent, timeout=240)


@pytest.fixture(scope="module")
def full_stack_rebuilds(shell, tmp_path_factory):
    """On one fake full-stack box (Docker Desktop declared, a docker that renders two services and
    builds them): a dry run, a real run, five newer rotated logs, a second real run the same day."""
    deploy, fake = _full_stack_box(tmp_path_factory.mktemp("rebuild-prune"))
    logs = deploy / "logs"
    out = {"dry": _rebuild(shell, deploy, fake, "-WhatIfOnly")}
    out["dry_counts"] = counts(logs)
    out["dry_stamp"] = (logs / "log-prune.last").exists()
    # -LogDir is where rebuild-stale writes its OWN run log; the services' logs are still deploy\logs.
    runlogs = deploy.parent.parent / "runlogs"
    out["real"] = _rebuild(shell, deploy, fake, "-LogDir", str(runlogs))
    out["real_counts"] = counts(logs)
    out["runlogs"] = sorted(p.name for p in runlogs.iterdir()) if runlogs.is_dir() else []
    out["stamp"] = (logs / "log-prune.last").read_text(encoding="utf-8") if (logs / "log-prune.last").exists() else ""
    for i in range(5):
        (logs / f"platform-broker.out-20261013T0{i}0000.000.log").write_text("new", encoding="utf-8")
    out["again"] = _rebuild(shell, deploy, fake)
    out["again_counts"] = counts(logs)
    out["logs"] = logs
    out["calls"] = (fake / "calls.txt").read_text(encoding="ascii", errors="replace").splitlines()
    return out


def test_a_dry_rebuild_prunes_nothing(full_stack_rebuilds):
    got = full_stack_rebuilds
    assert got["dry"].returncode == 0, got["dry"].stdout + got["dry"].stderr
    assert got["dry_counts"] == {s: 30 for s in STREAMS} and got["dry_stamp"] is False, got["dry_counts"]


def test_a_full_stack_rebuild_prunes_the_services_rotated_logs_to_ten(full_stack_rebuilds):
    got = full_stack_rebuilds
    r = got["real"]
    assert r.returncode == 0, r.stdout + r.stderr
    assert any("compose up -d --build --no-deps gateway" in c for c in got["calls"]), got["calls"]   # it built
    assert got["real_counts"] == {s: KEEP for s in STREAMS}, got["real_counts"]
    assert all((got["logs"] / o).is_file() for o in OTHERS), sorted(p.name for p in got["logs"].iterdir())
    # Its own run log went where -LogDir said; the prune still read deploy\logs.
    assert len(got["runlogs"]) == 1 and got["runlogs"][0].startswith("rebuild-stale-"), got["runlogs"]
    assert f"log prune: removed {len(STREAMS) * (30 - KEEP)} rotated service log file(s)" in r.stdout, r.stdout
    from datetime import datetime, timedelta, timezone
    when = datetime.fromisoformat(got["stamp"].strip())                  # the run's own time, in UTC
    assert when.utcoffset() == timedelta(0), got["stamp"]
    assert abs(datetime.now(timezone.utc) - when) < timedelta(hours=1), got["stamp"]


def test_a_second_rebuild_the_same_day_leaves_the_newer_logs(full_stack_rebuilds):
    got = full_stack_rebuilds
    assert got["again"].returncode == 0, got["again"].stdout + got["again"].stderr
    assert got["again_counts"]["platform-broker.out"] == KEEP + 5, got["again_counts"]
    assert "log prune" not in got["again"].stdout, got["again"].stdout


# --- the watchdog's monitor loop --------------------------------------------------------------------

FAKE_VM = "watchdog-test-no-such-vm"
WATCHDOG_STUBS = r"""
$global:polls = 0
# The loop's poll sleep: at the second poll five newer rotated broker logs appear (a day's worth of
# rotations, in seconds), and the third poll ends the run. Every other sleep is skipped.
function global:Start-Sleep {
  param([int]$Seconds, [int]$Milliseconds)
  if ($Seconds -ne 60) { return }
  $global:polls++
  if ($global:polls -eq 2) {
    for ($i = 0; $i -lt 5; $i++) {
      Set-Content -LiteralPath (Join-Path $global:logDir ('platform-broker.out-20261013T0{0}0000.000.log' -f $i)) -Value 'new'
    }
  }
  if ($global:polls -gt 2) { throw 'WATCHDOG-TEST-END' }
}
function global:Invoke-WebRequest {
  [CmdletBinding()] param([Parameter(Position = 0)]$Uri, $TimeoutSec, [switch]$UseBasicParsing)
  [pscustomobject]@{ StatusCode = 200 }
}
function global:Get-CimInstance { [CmdletBinding()] param([Parameter(Position = 0)][string]$ClassName, [string]$Filter) }
function global:Invoke-CimMethod { [CmdletBinding()] param($InputObject, $MethodName) }
function global:Get-Process { [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name) }
function global:Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) throw 'no process may be stopped here' }
function global:Get-VM { [CmdletBinding()] param([string]$Name) }
function global:Stop-VM { [CmdletBinding()] param([string]$Name, [switch]$Force) throw 'no VM may be stopped here' }
function global:powershell.exe { throw 'no startup may run here' }
function global:Get-WinEvent { [CmdletBinding()] param([hashtable[]]$FilterHashtable, [int]$MaxEvents) throw 'no event log may be read here' }
"""


# "failing": the scratch lib-runtime's Remove-RotatedServiceLogs throws, as an unreadable log folder
# might make it.
REAL_PRUNE = "function Remove-RotatedServiceLogs {\n"
FAILING_PRUNE = ("function Remove-RotatedServiceLogs { throw 'PRUNE-TEST-FAILURE' }\n"
                 "function Remove-RotatedServiceLogs-NotCalled {\n")


@pytest.fixture(scope="module")
def watchdog_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("wdprune")
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    real_vm = "$PodmanMachine = 'podman-machine-default'"
    assert lib.count(real_vm) == 1, "lib-runtime no longer names its VM where this test expects"
    assert lib.count(REAL_PRUNE) == 1, "lib-runtime no longer defines the prune where this test expects"
    lib = lib.replace(real_vm, f"$PodmanMachine = '{FAKE_VM}'")
    fake = base / "bin"                              # a podman whose control plane always answers
    fake.mkdir()
    (fake / "podman.cmd").write_bytes(b"@echo off\r\nif \"%1\"==\"volume\" echo platform_gateway_data\r\nexit /b 0\r\n")
    appdata = base / "localappdata"                  # no Programs\Podman for the script to prepend
    appdata.mkdir()
    body = WATCHDOG_STUBS + f"""
$env:LOCALAPPDATA = {q(appdata)}
$env:Path = {q(fake)} + ';' + {q(SYSTEM32)}
"""
    roots = {}
    for name, scratch_lib in (("ok", lib), ("failing", lib.replace(REAL_PRUNE, FAILING_PRUNE))):
        root = base / name / "repo"
        inst = root / "deploy" / "installer"
        inst.mkdir(parents=True)
        shutil.copy2(INSTALLER / "platform-watchdog.ps1", inst / "platform-watchdog.ps1")
        (inst / "lib-runtime.ps1").write_text(scratch_lib, encoding="utf-8-sig")
        make_logs(root / "deploy" / "logs")
        roots[name] = root
        body += f"""
$global:polls = 0; $global:logDir = {q(root / 'deploy' / 'logs')}
$ended = ''
try {{ & {q(inst / 'platform-watchdog.ps1')} *>&1 | Out-Null }} catch {{ $ended = $_.Exception.Message }}
Write-Output ('<<<{name}>>>' + (@{{ ended = $ended; polls = $global:polls }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        logs = roots[name] / "deploy" / "logs"
        log = logs / "watchdog.log"
        got["log"] = log.read_text(encoding="utf-8-sig") if log.is_file() else ""
        got["logs"] = logs
        out[name] = got
    assert set(out) == {"ok", "failing"}, r.stdout + r.stderr
    return out


def test_the_watchdog_prunes_rotated_logs_on_its_first_cycle_and_not_again_that_day(watchdog_runs):
    got = watchdog_runs["ok"]
    logs = got["logs"]
    # It ran its two cycles and stopped at the third poll, not at some fault of its own.
    assert "WATCHDOG-TEST-END" in got["ended"] and got["polls"] == 3, got
    n = counts(logs)
    others = {s: c for s, c in n.items() if s != "platform-broker.out"}
    assert others == {s: KEEP for s in STREAMS if s != "platform-broker.out"}, n
    # The first cycle's prune left ten; the five that appeared before the second cycle are still
    # there, so the second cycle did not prune again.
    assert n["platform-broker.out"] == KEEP + 5, n
    assert all((logs / o).is_file() for o in OTHERS), sorted(p.name for p in logs.iterdir())
    lines = [ln for ln in got["log"].splitlines() if "log prune" in ln]
    assert len(lines) == 1, got["log"]
    assert f"removed {len(STREAMS) * (30 - KEEP)} rotated log file(s)" in lines[0], lines
    assert "FATAL" in got["log"] and "WATCHDOG-TEST-END" in got["log"]       # how the test ended it


def test_a_failed_prune_is_logged_and_the_loop_goes_on(watchdog_runs):
    """A prune that throws must not end the watchdog (its catch would log FATAL and NSSM would
    restart it), and is tried again the next day, not every minute."""
    got = watchdog_runs["failing"]
    assert "WATCHDOG-TEST-END" in got["ended"] and got["polls"] == 3, got
    failed = [ln for ln in got["log"].splitlines() if "log prune failed" in ln]
    assert len(failed) == 1 and "PRUNE-TEST-FAILURE" in failed[0], got["log"]
    assert "FATAL: watchdog loop threw: PRUNE-TEST-FAILURE" not in got["log"], got["log"]
    assert counts(got["logs"]) == {s: 30 for s in STREAMS} | {"platform-broker.out": 35}, got["log"]


# --- the installers read the hook back after registration ------------------------------------------

# A stand-in nssm: records each call's first three arguments, prints a line as nssm does, and
# answers `set <svc> AppEvents ...` with %FAKE_NSSM_HOOK_EXIT% (NSSM 2.24 refuses the setting).
FAKE_NSSM = (
    "@echo off\r\n"
    ">> \"%~dp0calls.txt\" echo %1 %2 %3\r\n"
    "echo fake-nssm %1 %3\r\n"
    "if /i \"%3\"==\"AppEvents\" exit /b %FAKE_NSSM_HOOK_EXIT%\r\n"
    "exit /b 0\r\n"
)
# The registry, as Get-ItemProperty sees it: a hook key is there only for the names listed.
REGISTRY_STUBS = r"""
$global:hookKeys = @()
$global:services = @()
function global:Get-ItemProperty {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [Parameter(Position = 1)][string[]]$Name)
  $p = @(@($LiteralPath) + @($Path) | Where-Object { $_ })[0]
  foreach ($svc in $global:hookKeys) {
    if ($p -eq "HKLM:\SYSTEM\CurrentControlSet\Services\$svc\Parameters\AppEvents\Rotate") {
      return [pscustomobject]@{ Post = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -EncodedCommand x' }
    }
  }
}
function global:Get-Service {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name)
  foreach ($n in $Name) { if ($global:services -contains $n) { [pscustomobject]@{ Name = $n; Status = 'Running' } } }
}
function global:Start-Sleep { param([int]$Seconds, [int]$Milliseconds) }
function global:Write-Host { }
"""

# name -> (the hook call's exit code, services whose hook key exists afterwards, services registered)
SCENARIOS = {
    "nssm-2.24": (1, [], []),
    "nssm-2.24-watchdog": (1, [], ["platform-watchdog"]),
    "hooks": (0, ["svc", "platform-watchdog"], []),
    "silent": (0, [], ["platform-watchdog"]),
}


def _lift(script: Path, name: str, replace: dict | None = None) -> str:
    text = f"$f = $ast.Find({{ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq {q(name)} }}, $true)\n"
    src = "$f.Extent.Text"
    for old, new in (replace or {}).items():
        src += f".Replace({q(old)}, {q(new)})"
    return (f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(script)}, [ref]$null, [ref]$null)\n"
            + text + f". ([scriptblock]::Create({src}))\n")


INSTALLERS = {
    # A function made from text has no script file, so its $PSScriptRoot is empty: name the folder.
    "install-services": (REPO / "deploy" / "install-services.ps1", {"$PSScriptRoot": "'C:\\x\\deploy'"}),
    "install-native": (INSTALLER / "install-native.ps1", {}),
}


@pytest.fixture(scope="module")
def installer_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("hook")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
{REGISTRY_STUBS}
$LogDir = 'C:\\x\\logs'; $LibRuntime = 'C:\\x\\lib-runtime.ps1'
"""
    dirs = {}
    for inst, (script, repl) in INSTALLERS.items():
        body += _lift(script, "Invoke-Nssm") + _lift(script, "Install-Svc", repl)
        for scen, (code, keys, svcs) in SCENARIOS.items():
            d = base / inst / scen
            d.mkdir(parents=True)
            (d / "nssm.cmd").write_bytes(FAKE_NSSM.encode("ascii"))
            dirs[(inst, scen)] = d
            body += f"""
$Nssm = {q(d / 'nssm.cmd')}
$env:FAKE_NSSM_HOOK_EXIT = '{code}'
$global:hookKeys = @({', '.join(q(k) for k in keys)}); $global:services = @({', '.join(q(s) for s in svcs)})
$w = @(Install-Svc 'svc' 'C:\\x\\py.exe' '-m app' 'C:\\x' @('A=1') 3>&1 | Where-Object {{ $_ -is [System.Management.Automation.WarningRecord] }} | ForEach-Object {{ $_.Message }})
Write-Output ('<<<{inst}|{scen}>>>' + (@{{ warnings = $w }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        key, _, text = chunk.partition(">>>")
        inst, scen = key.split("|")
        got = json.loads(text.strip().splitlines()[0])
        w = got["warnings"]
        got["warnings"] = [] if w is None else (w if isinstance(w, list) else [w])
        got["calls"] = (dirs[(inst, scen)] / "calls.txt").read_text(encoding="utf-8").split("\n")
        out[(inst, scen)] = got
    assert len(out) == len(INSTALLERS) * len(SCENARIOS), r.stdout + r.stderr
    return out


@pytest.mark.parametrize("inst", list(INSTALLERS))
def test_an_installer_warns_when_nssm_kept_no_hook(installer_runs, inst):
    got = installer_runs[(inst, "nssm-2.24")]
    assert len(got["warnings"]) == 1, got
    w = got["warnings"][0]
    assert w.startswith("svc: this NSSM has no event hooks") and "exited 1" in w, w
    # No watchdog on this box (the full stack registers none): the line names what prunes there
    # instead, and claims no watchdog prunes. It used to say nothing did.
    assert "No platform-watchdog service is registered" in w, w
    assert "deploy\\rebuild-stale.ps1 (at most once a day)" in w and "smoke-test.ps1" in w, w
    assert "The platform-watchdog service prunes" not in w and "nothing prunes" not in w, w
    # Not fatal: the service's remaining settings were still made after the refused hook.
    calls = [c.strip() for c in got["calls"] if c.strip()]
    hook = next(i for i, c in enumerate(calls) if c == "set svc AppEvents")
    assert "set svc AppExit" in calls[hook + 1:], calls


@pytest.mark.parametrize("inst", list(INSTALLERS))
def test_an_installer_on_a_watchdog_box_says_the_watchdog_prunes(installer_runs, inst):
    w = installer_runs[(inst, "nssm-2.24-watchdog")]["warnings"]
    assert len(w) == 1 and "The platform-watchdog service prunes deploy\\logs once a day" in w[0], w
    assert "No platform-watchdog service is registered" not in w[0] and "rebuild-stale" not in w[0], w


def test_what_the_warning_names_as_pruning_does_prune_deploy_logs():
    """The no-watchdog line names smoke-test.ps1 and platform-startup.ps1: each prunes deploy\\logs
    (Remove-RotatedServiceLogs over its $LogDir) and rotates its own log there (Step-ScriptLog, which
    since 2026-10-15 prunes only that log's own copies, test_ops_small_defects.py)."""
    for script in ("smoke-test.ps1", "platform-startup.ps1"):
        text = (INSTALLER / script).read_text(encoding="utf-8-sig")
        assert "$LogDir    = Join-Path $Root 'deploy\\logs'" in text, script
        assert re.search(r"^\$LogFile\s+= Join-Path \$LogDir '[a-z-]+\.log'$", text, re.M), script
        assert re.search(r"^\$null = Remove-RotatedServiceLogs -LogDir \$LogDir$", text, re.M), script
        assert re.search(r"^\$null = Step-ScriptLog -Path \$LogFile$", text, re.M), script


@pytest.mark.parametrize("inst", list(INSTALLERS))
def test_an_installer_says_nothing_when_the_hook_is_there(installer_runs, inst):
    assert installer_runs[(inst, "hooks")]["warnings"] == [], installer_runs[(inst, "hooks")]


@pytest.mark.parametrize("inst", list(INSTALLERS))
def test_an_installer_reads_the_hook_back_rather_than_trusting_the_exit_code(installer_runs, inst):
    """nssm said 0 and still no key: the key is what decides."""
    w = installer_runs[(inst, "silent")]["warnings"]
    assert len(w) == 1 and "reported success" in w[0] and "no event hooks" in w[0], w


# --- register-watchdog -------------------------------------------------------------------------------

REGISTRAR = INSTALLER / "register-watchdog.ps1"


@pytest.fixture(scope="module")
def registrar_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("reg")
    body = f"""
$ErrorActionPreference = 'Continue'
. {q(INSTALLER / 'lib-runtime.ps1')}
{REGISTRY_STUBS}
""" + _lift(REGISTRAR, "Set-WatchdogServiceLogs")
    logs = {}
    for scen, code, keys in (("nssm-2.24", 1, []), ("hooks", 0, ["platform-watchdog"])):
        d = base / scen
        d.mkdir()
        (d / "nssm.cmd").write_bytes(FAKE_NSSM.encode("ascii"))
        logs[scen] = d / "register-watchdog.txt"
        body += f"""
$env:FAKE_NSSM_HOOK_EXIT = '{code}'
$global:hookKeys = @({', '.join(q(k) for k in keys)})
$all = @(Set-WatchdogServiceLogs -NssmExe {q(d / 'nssm.cmd')} -Name 'platform-watchdog' -LogFile 'C:\\x\\logs\\watchdog-svc.log' `
          -Lib 'C:\\x\\lib-runtime.ps1' -Out {q(logs[scen])} 3>&1)
$warn = @($all | Where-Object {{ $_ -is [System.Management.Automation.WarningRecord] }} | ForEach-Object {{ $_.Message }})
$ret = @($all | Where-Object {{ $_ -isnot [System.Management.Automation.WarningRecord] }})
Write-Output ('<<<{scen}>>>' + (@{{ warnings = $warn; returned = ($ret -join '|'); outputs = $ret.Count }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        scen, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        w = got["warnings"]
        got["warnings"] = [] if w is None else (w if isinstance(w, list) else [w])
        got["log"] = logs[scen].read_text(encoding="utf-8-sig") if logs[scen].is_file() else ""
        out[scen] = got
    assert set(out) == {"nssm-2.24", "hooks"}, r.stdout + r.stderr
    return out


def test_register_watchdog_logs_and_warns_when_nssm_kept_no_hook(registrar_runs):
    got = registrar_runs["nssm-2.24"]
    assert len(got["warnings"]) == 1 and got["outputs"] == 1 and got["returned"] == got["warnings"][0], got
    w = got["warnings"][0]
    assert w.startswith("platform-watchdog: this NSSM has no event hooks") and "exited 1" in w, w
    assert "No platform-watchdog service is registered" not in w, w      # it is the watchdog
    assert f"WARNING: {w}" in got["log"], got["log"]
    assert "fake-nssm set AppEvents" in got["log"], got["log"]         # nssm's own output is kept


def test_register_watchdog_says_nothing_when_the_hook_is_there(registrar_runs):
    got = registrar_runs["hooks"]
    assert got["warnings"] == [] and got["returned"] == "" and "WARNING" not in got["log"], got
    assert "fake-nssm set AppEvents" in got["log"], got["log"]


def test_register_watchdog_registers_through_the_function_this_file_runs():
    """The lifted function is only proof if the registrar calls it, once, with its own service."""
    text = REGISTRAR.read_text(encoding="utf-8-sig")
    code = [ln.split(" #", 1)[0].strip() for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    hits = [ln for ln in code if "Set-WatchdogServiceLogs" in ln]
    assert len(hits) == 2 and hits[0].startswith("function Set-WatchdogServiceLogs("), hits   # defined, called
    assert hits[1] == "$null = Set-WatchdogServiceLogs -NssmExe $NssmExe -Name $svc -LogFile $logFile -Lib $lib -Out $out", hits
