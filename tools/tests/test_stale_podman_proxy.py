"""lib-runtime's Clear-StalePodmanProxy reaps exactly the orphaned api proxy, under both engines.

Its filter was `$_.Id -ne $PID -and (try { ... } catch { $true })`. `try` is a statement, not an
expression, so inside parentheses PowerShell parses it as a COMMAND named try: every call threw
"The term 'try' is not recognized" and nothing was reaped. On the podman box the watchdog hit it
three times, once straight after a Stop-VM, which is the moment the stale proxy blocks every
machine start. The real function runs here with the process table, the kill, the sleep and the
machine state faked; nothing real is listed or stopped.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "deploy" / "installer" / "lib-runtime.ps1"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _reap(run_ps, shell) -> dict:
    body = f"""
$ErrorActionPreference = 'Stop'
$Root = 'C:\\nowhere'; $Installer = 'C:\\nowhere\\deploy\\installer'; $TempBase = $env:TEMP
$LogFile = Join-Path $env:TEMP 'stale-proxy-test.log'; $RuntimeMode = 'podman'
. {q(LIB)}
# Fakes, defined AFTER the dot-source so they win over lib-runtime's own definitions; a function
# also wins over the cmdlet of the same name.
$global:log = New-Object System.Collections.Generic.List[string]
$global:killed = New-Object System.Collections.Generic.List[int]
function Write-Log($m) {{ $global:log.Add([string]$m) }}
function Get-PodmanMachineState {{ 'stopped' }}
function Start-Sleep {{ param([int]$Seconds) }}
function Stop-Process {{ [CmdletBinding()] param([int]$Id, [switch]$Force) $global:killed.Add($Id) }}
function New-FakeProcess([string]$Name, [int]$Id, $Start) {{
  $p = [pscustomobject]@{{ Name = $Name; Id = $Id }}
  if ($Start -eq 'throws') {{
    # What Process.StartTime does for another account's process: the getter throws.
    $p | Add-Member -MemberType ScriptProperty -Name StartTime -Value {{ throw 'Access is denied' }}
  }} else {{ $p | Add-Member -MemberType NoteProperty -Name StartTime -Value $Start }}
  $p
}}
$global:table = @(
  New-FakeProcess 'gvproxy' 101 (Get-Date)                    # always stale with the VM down
  New-FakeProcess 'podman'  202 (Get-Date).AddMinutes(-5)      # the orphaned api-proxy parent
  New-FakeProcess 'podman'  303 (Get-Date)                     # a CLI call in flight
  New-FakeProcess 'podman'  404 'throws'                       # another account's: unreadable
  New-FakeProcess 'podman'  $PID (Get-Date).AddMinutes(-5)     # this very process
)
function Get-Process {{
  [CmdletBinding()] param([Parameter(Position = 0)][string]$Name)
  $global:table | Where-Object {{ $_.Name -eq $Name }}
}}
$result = $null; $err = ''
try {{ $result = Clear-StalePodmanProxy }} catch {{ $err = $_.Exception.Message }}
@{{ result = $result; error = $err; killed = @($global:killed | Sort-Object); pid = $PID
   log = @($global:log) }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_orphaned_proxy_is_reaped_and_nothing_in_flight_is(run_ps, shell):
    got = _reap(run_ps, shell)
    assert got["error"] == "", got                      # "The term 'try' is not recognized" before
    assert got["killed"] == [101, 202, 404], got        # gvproxy, the old podman, the unreadable one
    assert got["pid"] not in got["killed"], got
    assert got["result"] is True, got
    assert sum("reaped stale" in ln for ln in got["log"]) == 3, got["log"]


def test_the_stale_test_reads_start_time_against_its_cutoff(run_ps, shell):
    """Test-StalePodmanProcess alone: the -Before cutoff decides, an unreadable start is stale."""
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(LIB)}
$cut = Get-Date '2026-10-13T12:00:00'
$old = [pscustomobject]@{{ Id = 1; StartTime = $cut.AddSeconds(-1) }}
$new = [pscustomobject]@{{ Id = 2; StartTime = $cut.AddSeconds(1) }}
$none = [pscustomobject]@{{ Id = 3; StartTime = $null }}
$self = [pscustomobject]@{{ Id = $PID; StartTime = $cut.AddDays(-1) }}
@{{ old = (Test-StalePodmanProcess $old -Before $cut); new = (Test-StalePodmanProcess $new -Before $cut)
   none = (Test-StalePodmanProcess $none -Before $cut); self = (Test-StalePodmanProcess $self -Before $cut) }} |
  ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {"old": True, "new": False, "none": True, "self": False}, got
