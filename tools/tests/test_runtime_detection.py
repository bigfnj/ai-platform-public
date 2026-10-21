"""deploy/installer/lib-runtime.ps1: which container runtime actually holds the platform.

smoke-test.ps1 used to pick 'podman' whenever podman was on PATH. The 24 GB box runs the stack on
Docker Desktop with podman installed beside it, so every stage there tested an empty, stopped
podman machine. Resolve-PlatformRuntime asks each CLI whether it LISTS the platform's gateway
container instead; these tests pin its order of evidence and the process layer under it.
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


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# name -> (podman listing, docker listing, expected mode, a phrase the reason must carry).
# A listing is None (CLI absent), "ERR" (present, `ps` failed), or the rows it prints.
CASES = {
    "docker-desktop-with-podman-beside-it": ("ERR", ["platform-gateway-1 running", "platform-caddy-1 running"],
                                             "desktop", "'docker ps' lists platform-gateway-1 running"),
    "podman-box-without-docker": (["platform-gateway-1 running"], None, "podman", "'podman ps' lists"),
    "stopped-stack-belongs-to-its-runtime": (["platform-gateway-1 exited"], [], "podman", "not running"),
    "a-stopped-copy-loses-to-a-running-one": (["platform-gateway-1 exited"], ["platform-gateway-1 running"],
                                              "desktop", "'docker ps' lists platform-gateway-1 running"),
    "fresh-box-falls-back-to-first-cli": ([], [], "podman", "no CLI lists platform-gateway-1"),
    # A leftover stopped copy in the runtime a box migrated away from, while the machine the
    # platform moved to is still down: not evidence, so the preference order decides, loudly.
    "a-stopped-leftover-cannot-outvote-an-unreachable-runtime": ("ERR", ["platform-gateway-1 exited"],
                                                                 "podman", "AMBIGUOUS"),
    # docker is the only engine CLI there: Docker Desktop, a known basis (delta audit 2026-10-15), so
    # rebuild-stale and backup-platform go on with the stack down. So with its engine not answering.
    "fresh-box-docker-only": (None, [], "desktop", "docker is the only engine CLI on PATH"),
    "docker-only-engine-down": (None, "ERR", "desktop", "docker is the only engine CLI on PATH"),
    "podman-only-lists-nothing": ([], None, "podman", "first CLI on PATH"),
    "no-cli-at-all": (None, None, "wsl", "neither podman nor docker"),
    "both-running-says-ambiguous": (["platform-gateway-1 running"], ["platform-gateway-1 running"],
                                    "podman", "AMBIGUOUS"),
    "a-prefix-match-is-not-the-gateway": (["platform-gateway-10 running", "xplatform-gateway-1 running"],
                                          ["platform-gateway-1 running"], "desktop", "'docker ps' lists"),
}


def _ps_listing(v) -> str:
    if v is None:
        return "@{ Present = $false; Ok = $false; Rows = @(); Detail = 'not on PATH' }"
    if v == "ERR":
        return "@{ Present = $true; Ok = $false; Rows = @(); Detail = 'ps exit 125: Cannot connect' }"
    rows = ", ".join(q(r) for r in v) or ""
    return f"@{{ Present = $true; Ok = $true; Rows = @({rows}); Detail = '{len(v)} container(s)' }}"


@pytest.fixture(scope="module")
def resolved(run_ps, shell):
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$all = [ordered]@{{}}
"""
    for name, (pod, dock, _, _) in CASES.items():
        body += f"""
function Get-RuntimeContainerList {{
  param([string]$Cli, [int]$TimeoutMs)
  if ($Cli -eq 'podman') {{ return {_ps_listing(pod)} }}
  return {_ps_listing(dock)}
}}
$all['{name}'] = Resolve-PlatformRuntime -EnvFile ''   # '' declares nothing: inference only
"""
    body += "$all | ConvertTo-Json -Depth 4 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case", list(CASES))
def test_the_runtime_holding_the_gateway_is_chosen(resolved, case):
    _, _, mode, phrase = CASES[case]
    got = resolved[case]
    assert got["Mode"] == mode, got
    assert phrase in got["Reason"], got


# Which picks a container proves and which are guesses: rebuild-stale and backup-platform refuse a
# guess, which drove compose (or read volumes) through the first CLI on PATH, podman on a box with
# both, while Docker Desktop was still starting or after `compose down`. docker as the only engine
# CLI is neither: 'only-cli', which they follow. podman alone is still a guess, as it was: every
# podman box is a lean one, whose installers declare PLATFORM_RUNTIME.
GUESSES = {"fresh-box-falls-back-to-first-cli", "a-stopped-leftover-cannot-outvote-an-unreachable-runtime",
           "podman-only-lists-nothing", "no-cli-at-all"}
ONLY_CLI = {"fresh-box-docker-only", "docker-only-engine-down"}


@pytest.mark.parametrize("case", list(CASES))
def test_a_pick_says_whether_a_container_proved_it(resolved, case):
    want = "guess" if case in GUESSES else "only-cli" if case in ONLY_CLI else "container"
    assert resolved[case]["Basis"] == want, resolved[case]


# --- a DECLARED runtime: PLATFORM_RUNTIME in deploy\.env ----------------------------------------

# name -> (deploy\.env text, podman listing, docker listing, mode, reason phrase, warning phrase)
DECLARED = {
    "declared-desktop-wins-over-inference": (
        "PLATFORM_RUNTIME=desktop\n", "ERR", [], "desktop",
        "declared in deploy\\.env (PLATFORM_RUNTIME=desktop)", ""),
    "declared-podman-is-followed-and-the-running-docker-gateway-is-named": (
        "PLATFORM_RUNTIME=podman\n", "ERR", ["platform-gateway-1 running"], "podman",
        "declared in deploy\\.env (PLATFORM_RUNTIME=podman)",
        "deploy\\.env declares PLATFORM_RUNTIME=podman, but 'docker ps' lists platform-gateway-1 running"),
    "a-quoted-value-with-a-comment-reads-like-compose": (
        'PLATFORM_RUNTIME="desktop"  # written by the installer\n', "ERR", [], "desktop", "declared", ""),
    "the-last-assignment-wins": (
        "PLATFORM_RUNTIME=podman\nPLATFORM_RUNTIME=desktop\n", "ERR", [], "desktop", "declared", ""),
    "case-is-normalised": ("PLATFORM_RUNTIME=Desktop\n", "ERR", [], "desktop", "declared", ""),
    "a-commented-line-declares-nothing": (
        "# PLATFORM_RUNTIME=desktop\n", ["platform-gateway-1 running"], None, "podman",
        "'podman ps' lists", ""),
    "a-value-that-is-no-runtime-is-ignored-and-said": (
        "PLATFORM_RUNTIME=kubernetes\n", "ERR", ["platform-gateway-1 running"], "desktop",
        "PLATFORM_RUNTIME='kubernetes', which is not podman, desktop or wsl, so it is ignored", ""),
}


@pytest.fixture(scope="module")
def declared(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("declared")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$all = [ordered]@{{}}
"""
    for name, (env, pod, dock, _, _, _) in DECLARED.items():
        f = d / f"{name}.env"
        f.write_text(env, encoding="utf-8")
        body += f"""
$global:asked = @()
function Get-RuntimeContainerList {{
  param([string]$Cli, [int]$TimeoutMs)
  $global:asked += "$Cli/$TimeoutMs"
  if ($Cli -eq 'podman') {{ return {_ps_listing(pod)} }}
  return {_ps_listing(dock)}
}}
$all['{name}'] = Resolve-PlatformRuntime -EnvFile {q(f)}
$all['{name}-asked'] = [string[]]@($global:asked)
"""
    body += "$all | ConvertTo-Json -Depth 4 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# What each declared case asks: only the OTHER runtime's CLI, bounded at 5 s (the contradiction
# warning rides on it); undeclared, both CLIs at the full 15 s. A declared box paid both full
# timeouts on every run.
ASKED = {
    "declared-desktop-wins-over-inference": ["podman/5000"],
    "declared-podman-is-followed-and-the-running-docker-gateway-is-named": ["docker/5000"],
    "a-quoted-value-with-a-comment-reads-like-compose": ["podman/5000"],
    "the-last-assignment-wins": ["podman/5000"],
    "case-is-normalised": ["podman/5000"],
    "a-commented-line-declares-nothing": ["podman/15000", "docker/15000"],
    "a-value-that-is-no-runtime-is-ignored-and-said": ["podman/15000", "docker/15000"],
}


@pytest.mark.parametrize("case", list(DECLARED))
def test_a_declared_runtime_asks_only_the_other_cli_briefly(declared, case):
    assert declared[f"{case}-asked"] == ASKED[case], declared[f"{case}-asked"]


@pytest.mark.parametrize("case", list(DECLARED))
def test_a_declared_runtime_is_read_first_and_a_contradiction_is_named(declared, case):
    _, _, _, mode, reason, warning = DECLARED[case]
    got = declared[case]
    assert got["Mode"] == mode, got
    assert reason in got["Reason"], got
    if warning:
        assert warning in got["Warning"], got
    else:
        assert got["Warning"] == "", got
    assert got["Basis"] == ("container" if case == "a-commented-line-declares-nothing" or
                            case == "a-value-that-is-no-runtime-is-ignored-and-said" else "declared"), got


# --- the process layer: real child processes, fake CLIs ---------------------------------------

FAKES = {
    # A runtime that answers: two rows on stdout, noise on stderr, exit 0.
    "docker.cmd": "@echo off\r\necho platform-gateway-1 running\r\necho   platform-caddy-1 exited  \r\n"
                  "echo progress noise 1>&2\r\nexit /b 0\r\n",
    # A runtime whose machine is down.
    "podman.cmd": "@echo off\r\necho Cannot connect to Podman 1>&2\r\nexit /b 125\r\n",
    # hangs.cmd is written per run by the fixture below: its marker must be this run's own.
}


def hang_marker() -> str:
    """A loopback address unique to one fixture run, so the orphan check counts only THIS run's
    child. A fixed marker (127.0.0.29) counted any process carrying it: another run of this
    test at the same moment, or the 28 s orphan the timed-out-CLI mutation deliberately leaves,
    failed a run whose own kill had worked. Windows answers ping on all of 127/8."""
    b = uuid.uuid4().bytes
    return f"127.{1 + b[0] % 254}.{b[1]}.{1 + b[2] % 254}"


# The hung probe is judged on two steps, each timed from its own start, by wrapping
# Stop-ProcessTree as test_platform_startup_sh.py wraps run_tree's kill. It used to be one
# stopwatch over all four probes (`< 5 s`) that held Stop-ProcessTree's taskkill, whose own wait
# is 10 s: under a CPU hog plus process-spawning loops it read 5.3-13.6 s and failed every run.
HANG_TIMEOUT_MS = 700
# From the probe's own start to its kill: the cut-off. ~0.8 s idle. A wait that ignored the
# caller's timeout would cut off at the 15 s default, and one with none never kills at all.
CUT_OFF = 10
# From the kill being issued to the probe's return: Stop-ProcessTree's own 10 s wait for taskkill
# and a margin for a host slow to start it. Under a second idle.
AFTER_KILL = 10 + 30


@pytest.fixture(scope="module")
def listings(run_ps, shell, tmp_path_factory):
    fake = tmp_path_factory.mktemp("fakecli")
    for name, text in FAKES.items():
        (fake / name).write_bytes(text.encode("ascii"))
    # A CLI that hangs, as podman does when its SSH transport dies, through a CHILD process that
    # inherits its handles (ping). Killing only the CLI left that child alive, holding this
    # harness's own stdout, for the rest of its 28 s, which is past every bound above.
    marker = hang_marker()
    (fake / "hangs.cmd").write_bytes(f"@echo off\r\nping -n 29 {marker} >nul\r\nexit /b 0\r\n".encode("ascii"))
    rx = marker.replace(".", "[.]") + r"(\s|$)"
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
# Only the fakes and System32 (cmd.exe, ping): the real podman and docker on this box must be
# invisible, or 'absent' could not be tested and a real runtime could answer for a fake.
$env:Path = {q(fake)} + ';' + {q(system32)}
$rx = {q(rx)}
function Get-HangChild {{
  @(Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object {{ $_.CommandLine -match $rx }}).Count
}}
# Stop-ProcessTree, timed: when the probe gave up on the CLI, and when the real kill was issued.
# In between it waits (bounded) for the CLI's child: on a loaded host cmd.exe can still be
# starting ping at the cut-off, and a kill that finds no child proves nothing about one.
$realStop = ${{function:Stop-ProcessTree}}
$global:kills = New-Object System.Collections.Generic.List[object]
function Stop-ProcessTree {{
  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)
  $k = @{{ Called = Get-Date; Child = 0 }}
  $until = (Get-Date).AddSeconds(20)
  do {{
    $k.Child = Get-HangChild
    if ($k.Child -gt 0) {{ break }}
    Start-Sleep -Milliseconds 200
  }} while ((Get-Date) -lt $until)
  $k.Issued = Get-Date
  & $realStop -Process $Process
  $global:kills.Add($k)
}}
$out = [ordered]@{{
  docker = Get-RuntimeContainerList -Cli docker
  podman = Get-RuntimeContainerList -Cli podman
  absent = Get-RuntimeContainerList -Cli nosuchcli
}}
$t0 = Get-Date
$out['hangs'] = Get-RuntimeContainerList -Cli hangs -TimeoutMs {HANG_TIMEOUT_MS}
$back = Get-Date
$out['kills'] = $global:kills.Count
if ($global:kills.Count -gt 0) {{
  $k = $global:kills[0]
  $out['cut_off_seconds'] = ($k.Called - $t0).TotalSeconds
  $out['after_kill_seconds'] = ($back - $k.Issued).TotalSeconds
  $out['child_at_kill'] = $k.Child
}}
# Bounded wait for exit: a terminated process can stay listed for a moment. Three seconds is
# far below the 28 s an orphan of the old Kill() would live, so the defect still fails here.
$deadline = (Get-Date).AddSeconds(3)
do {{
  $n = Get-HangChild
  if ($n -eq 0) {{ break }}
  Start-Sleep -Milliseconds 200
}} while ((Get-Date) -lt $deadline)
$out['orphans'] = $n
$out['marker'] = {q(marker)}
$out | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_listing_returns_trimmed_rows_and_ignores_stderr(listings):
    d = listings["docker"]
    assert d["Present"] and d["Ok"], d
    assert d["Rows"] == ["platform-gateway-1 running", "platform-caddy-1 exited"], d


def test_a_failing_cli_is_present_but_not_ok_and_says_why(listings):
    p = listings["podman"]
    assert p["Present"] and not p["Ok"] and p["Rows"] == [], p
    assert "exit 125" in p["Detail"] and "Cannot connect" in p["Detail"], p


def test_an_absent_cli_is_reported_absent(listings):
    a = listings["absent"]
    assert not a["Present"] and not a["Ok"], a


def test_a_hanging_cli_is_cut_off_at_the_timeout(listings):
    """podman with a dead SSH transport hangs forever; detection must not hang the smoke test.
    The probe gives up at its caller's timeout, kills the CLI's tree, and returns. The fake
    sleeps 28 s, so a probe that waited for it issues no kill."""
    h = listings["hangs"]
    assert h["Present"] and not h["Ok"] and "timed out" in h["Detail"], h
    assert listings["kills"] == 1, f"the probe gave up without Stop-ProcessTree: {listings}"
    assert listings["cut_off_seconds"] < CUT_OFF, (
        f"{listings['cut_off_seconds']:.1f} s from the probe's start to its kill, given "
        f"{HANG_TIMEOUT_MS} ms (bound {CUT_OFF} s)")
    assert listings["after_kill_seconds"] < AFTER_KILL, (
        f"{listings['after_kill_seconds']:.1f} s from the kill to the probe's return "
        f"(bound {AFTER_KILL} s)")


def test_a_hung_cli_is_killed_with_its_children(listings):
    """Not just the CLI: its child held this harness's output pipe open until it exited."""
    assert listings["orphans"] == 0, listings
    assert listings.get("child_at_kill", 0) >= 1, (
        f"the hung CLI's child was not running at the kill, so this proved nothing: {listings}")


# --- smoke-test.ps1 wiring --------------------------------------------------------------------

# Appended to the COPIED lib-runtime only: the real detection logic runs, over a fake listing.
FAKE_LISTING = r"""
function Get-RuntimeContainerList {
  param([string]$Cli, [int]$TimeoutMs)
  if ($Cli -eq 'podman') { return @{ Present = $true; Ok = $false; Rows = @(); Detail = 'podman ps exit 125: Cannot connect' } }
  return @{ Present = $true; Ok = $true; Rows = @('platform-gateway-1 running'); Detail = '1 container(s)' }
}
"""

STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:docker {
  $a = Get-Argv $args
  $global:calls.Add('docker ' + ($a -join ' '))
  if ($a[0] -eq 'compose' -and ($a -contains '--services')) { 'gateway'; return }
  if ($a[0] -eq 'compose' -and $a[-1] -eq 'config') { 'services:'; '  gateway:'; '    image: platform-gateway:latest'; return }
  if ($a[0] -eq 'images') { 'platform-gateway:latest'; return }
}
function global:podman { $global:calls.Add('podman ' + ((Get-Argv $args) -join ' ')) }
function global:docker-compose { $global:calls.Add('docker-compose ' + ((Get-Argv $args) -join ' ')) }
function global:wsl.exe { $global:calls.Add('wsl.exe ' + ((Get-Argv $args) -join ' ')) }
"""


@pytest.fixture(scope="module")
def smoke_runs(run_ps, shell, tmp_path_factory):
    root = tmp_path_factory.mktemp("smoke") / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    shutil.copy2(INSTALLER / "smoke-test.ps1", inst / "smoke-test.ps1")
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8")
    (inst / "lib-runtime.ps1").write_text(lib + FAKE_LISTING, encoding="utf-8-sig")
    env = root / "deploy" / ".env"
    script = inst / "smoke-test.ps1"
    out = {}
    # The fake listing says docker holds the stack; the last case DECLARES podman in deploy\.env.
    for name, extra, declare in (("detected", "", ""), ("override", "-Runtime podman", ""),
                                 ("declared-contradicted", "", "PLATFORM_RUNTIME=podman\n")):
        env.write_text("PLATFORM_ENABLED_APPS=terminal-fun\n" + declare, encoding="utf-8")
        body = STUBS + f"""
& {q(script)} -Stage build {extra} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
        r = run_ps(shell, body)
        assert r.returncode == 0, r.stdout + r.stderr
        out[name] = r.stdout
    return out


def test_smoke_test_uses_the_runtime_that_holds_the_platform_and_says_why(smoke_runs):
    out = smoke_runs["detected"]
    assert "runtime: desktop" in out, out
    assert "runtime chosen: 'docker ps' lists platform-gateway-1 running" in out, out
    clis = {c.split()[0] for c in out.split("CALLS=", 1)[1].strip().split(" | ") if c}
    assert clis == {"docker"}, clis
    assert "EXIT=0" in out, out


def test_runtime_mode_still_overrides_detection(smoke_runs):
    out = smoke_runs["override"]
    assert "runtime: podman" in out and "runtime chosen: from -RuntimeMode" in out, out


def test_smoke_test_follows_a_declaration_and_warns_when_the_evidence_disagrees(smoke_runs):
    out = smoke_runs["declared-contradicted"]
    assert "runtime: podman" in out and "runtime chosen: declared in deploy\\.env (PLATFORM_RUNTIME=podman)" in out, out
    assert ("[warn] runtime: deploy\\.env declares PLATFORM_RUNTIME=podman, but 'docker ps' lists "
            "platform-gateway-1 running") in out, out
