"""A wsl box's containers reach the Ollama app, and every other runtime's install is what it was.

In wsl mode the containers reach the host at WINDOWS_HOST, the Windows end of the WSL vNIC, and the
Ollama app bound 127.0.0.1:11434 (nothing set OLLAMA_HOST), so the gateway's PLATFORM_OLLAMA_URL
(Admin > Models > Delete) and ai-playground's AI_PLAYGROUND_OLLAMA_URL were refused there. Now a
wsl install sets the user's OLLAMA_HOST=0.0.0.0:11434 and restarts a running app on it
(lib-runtime Set-WslOllamaHost, called by install.ps1's provisioning), and the elevated step's block
rule covers :11434 beside :11500 under a new name (Get-BrokerFirewallRules; the rules themselves
are checked in test_lean_broker_auth.py). Under WSL's mirrored networking the default route is the
LAN router, so the WINDOWS_HOST detection says so and writes nothing (Get-WslNetworkInfo; the bash
side is in test_platform_startup_sh.py).

There is no WSL engine on the boxes this runs on, and nothing here writes outside its own process:
the user-environment store is the one thing the scratch lib-runtime changes (its $OllamaHostStore
keeps the value in a variable, asserted to match once), every process, listener and job call is a
function standing in for the cmdlet, and wsl.exe is never started. Both engines throughout.
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
WINPS = os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0")
WANT = "0.0.0.0:11434"
USER_STORE = ("$OllamaHostStore = @{ Get = { [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User') }; "
              "Set = { param($v) [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $v, 'User') } }")
TEST_STORE = "$OllamaHostStore = @{ Get = { $global:persisted }; Set = { param($v) $global:persisted = $v } }"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def scratch_lib() -> str:
    """lib-runtime with the user's environment replaced by $global:persisted, the one write that
    would leave this process. Everything else, the process environment included, is real."""
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
    assert lib.count(USER_STORE) == 1, "the user-environment store is not where this test expects"
    return lib.replace(USER_STORE, TEST_STORE)


# --- Set-WslOllamaHost, one scenario at a time --------------------------------------------------

# Every outside call is a function: the :11434 listener (a list of addresses, the next one after
# each Start-Process of the app), the running processes, and what was stopped and started.
STUBS = r"""
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-NetTCPConnection {
  [CmdletBinding()] param([int]$LocalPort, [string]$State)
  $global:calls.Add("listen? $LocalPort $State")
  foreach ($a in @($global:listen)) { if ($a) { [pscustomobject]@{ LocalAddress = $a; LocalPort = $LocalPort } } }
}
function global:Get-Process {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name)
  $global:calls.Add("get-process $($Name -join ',')")
  foreach ($p in @($global:procs)) { if ($Name -contains $p.ProcessName) { $p } }
}
function global:Get-CimInstance {
  [CmdletBinding()] param([string]$ClassName, [string]$Filter)
  $global:calls.Add("cim $ClassName $Filter")
  foreach ($p in @($global:cim)) { $p }
}
function global:Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) $global:calls.Add("stop $Id") }
function global:Wait-Process { [CmdletBinding()] param([int[]]$Id, [int]$Timeout) $global:calls.Add("wait $($Id -join ',')") }
function global:Start-Sleep { [CmdletBinding()] param([int]$Seconds, [int]$Milliseconds) }
function global:Start-Process {
  [CmdletBinding()] param([Parameter(Position = 0)][string]$FilePath)
  $global:calls.Add("start $FilePath OLLAMA_HOST=$env:OLLAMA_HOST")
  $global:listen = @($global:after)
}
"""

APP = r"C:\x\Programs\Ollama\ollama app.exe"
# name -> (listener addresses, app running, cim processes, listener after a restart, OLLAMA_HOST before)
SCENARIOS = {
    "nothing-listens": ([], False, [], [], ""),
    "app-on-loopback": (["127.0.0.1"], True,
                        [(202, r'"C:\x\ollama.exe" serve'), (303, r'"C:\x\ollama.exe" run gemma3:4b')],
                        ["0.0.0.0"], ""),
    "server-not-from-the-app": (["127.0.0.1"], False, [(202, r'"C:\x\ollama.exe" serve')], [], ""),
    "already-wide": (["0.0.0.0"], True, [], [], WANT),
    "restart-does-not-take": (["127.0.0.1"], True, [(202, "ollama.exe serve")], ["127.0.0.1"], "127.0.0.1:11434"),
}


@pytest.fixture(scope="module")
def host_runs(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("olh")
    lib = d / "lib-runtime.ps1"
    lib.write_text(scratch_lib(), encoding="utf-8-sig")
    app = d / "Programs" / "Ollama" / "ollama app.exe"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"")
    body = f"$ErrorActionPreference = 'Stop'\n. {q(lib)}\n{STUBS}\n"
    # Every other runtime first: nothing may be read, stopped, started or set for them.
    # $global:persisted stands for the user's environment (scratch_lib); env is this process's.
    report = ("(@{{ lines = $lines; calls = @($global:calls); env = [string]$env:OLLAMA_HOST; "
              "persisted = [string]$global:persisted }} | ConvertTo-Json -Compress)")
    for mode in ("podman", "desktop", ""):
        body += f"""
Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
$global:persisted = $null
$global:calls.Clear(); $global:listen = @('127.0.0.1'); $global:procs = @([pscustomobject]@{{ ProcessName = 'ollama app'; Id = 101 }})
$global:cim = @(); $global:after = @()
$lines = @(Set-WslOllamaHost -Mode {q(mode)} -AppPath {q(app)})
Write-Output ('<<<mode:{mode or 'none'}>>>' + {report.format()})
"""
    for name, (listen, app_up, cim, after, before) in SCENARIOS.items():
        procs = "@([pscustomobject]@{ ProcessName = 'ollama app'; Id = 101 })" if app_up else "@()"
        cims = ", ".join(f"[pscustomobject]@{{ ProcessId = {pid}; CommandLine = {q(cl)} }}" for pid, cl in cim)
        body += f"""
Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
$global:persisted = {q(before) if before else '$null'}
$global:calls.Clear(); $global:listen = @({', '.join(q(a) for a in listen)}); $global:procs = {procs}
$global:cim = @({cims}); $global:after = @({', '.join(q(a) for a in after)})
$lines = @(Set-WslOllamaHost -Mode wsl -AppPath {q(app)} -WaitSeconds 3)
Write-Output ('<<<{name}>>>' + {report.format()})
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        key, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        for k in ("lines", "calls"):
            v = got[k]
            got[k] = [] if v is None else (v if isinstance(v, list) else [v])
        out[key] = got
    assert len(out) == 3 + len(SCENARIOS), r.stdout + r.stderr
    out["app"] = str(app)
    return out


@pytest.mark.parametrize("mode", ["podman", "desktop", "none"])
def test_every_other_runtime_reads_sets_and_restarts_nothing(host_runs, mode):
    got = host_runs[f"mode:{mode}"]
    assert got == {"lines": [], "calls": [], "env": "", "persisted": ""}, got


def test_wsl_sets_the_variable_and_an_app_started_next_inherits_it(host_runs):
    got = host_runs["nothing-listens"]
    assert got["persisted"] == WANT, got          # the user's environment, for the next logon
    assert got["env"] == WANT, got                # this process's, for the app started next
    assert got["lines"][0].startswith(f"OLLAMA_HOST={WANT} set in your user environment"), got
    assert "nothing listens on :11434" in got["lines"][1], got
    assert not [c for c in got["calls"] if c.startswith(("stop", "start"))], got     # step 3 starts it


def test_wsl_restarts_a_loopback_app_and_its_server_and_nothing_else(host_runs):
    got = host_runs["app-on-loopback"]
    calls = got["calls"]
    assert "stop 101" in calls and "stop 202" in calls, calls          # the app and its `serve`
    assert "stop 303" not in calls, calls                              # an `ollama run` is left alone
    start = [c for c in calls if c.startswith("start ")]
    assert start == [f"start {host_runs['app']} OLLAMA_HOST={WANT}"], calls   # restarted with the value
    assert calls.index("stop 202") < calls.index(start[0]), calls
    assert got["lines"][-1] == "restarted the Ollama app; it listens on 0.0.0.0:11434", got


def test_a_server_the_app_did_not_start_is_named_not_stopped(host_runs):
    got = host_runs["server-not-from-the-app"]
    assert not [c for c in got["calls"] if c.startswith(("stop", "start"))], got
    assert got["lines"][-1].startswith("WARNING: Ollama listens on 127.0.0.1 port 11434, but not from the Ollama app"), got
    assert f"OLLAMA_HOST={WANT}" in got["lines"][-1], got


def test_an_app_already_on_every_interface_is_left_running(host_runs):
    got = host_runs["already-wide"]
    assert got["lines"] == [f"OLLAMA_HOST={WANT} is already set in your user environment",
                            "Ollama already listens on 0.0.0.0:11434"], got
    assert not [c for c in got["calls"] if c.startswith(("stop", "start", "get-process"))], got
    assert got["env"] == WANT, got                # this process still gets it, for its own children


def test_a_restart_that_keeps_the_loopback_bind_is_said(host_runs):
    got = host_runs["restart-does-not-take"]
    assert "(it was 127.0.0.1:11434)" in got["lines"][0], got
    assert got["lines"][-1].startswith("WARNING: restarted the Ollama app, but :11434 is still not on 0.0.0.0"), got


# --- the WSL networking probe ----------------------------------------------------------------------

NAT_ROUTE = "route:\ndefault via 172.29.80.1 dev eth0 proto kernel\n"
MIRRORED_ROUTE = "route:\ndefault via 192.168.1.1 dev eth0 proto kernel metric 25\n"
# name -> (probe text, .wslconfig text, expected mode, expected host, warned)
PROBES = {
    "nat": ("wslinfo:\nnat\n" + NAT_ROUTE, "", "nat", "172.29.80.1", False),
    "old-wsl-no-wslinfo": ("wslinfo:\n" + NAT_ROUTE, "", "nat", "172.29.80.1", False),
    "mirrored": ("wslinfo:\nmirrored\n" + MIRRORED_ROUTE + "loopback0:yes\n", "", "mirrored", None, True),
    "mirrored-by-loopback0": ("wslinfo:\n" + MIRRORED_ROUTE + "loopback0:yes\n", "", "mirrored", None, True),
    "mirrored-by-wslconfig": ("", "[wsl2]\r\nmemory=8GB\r\nnetworkingMode = mirrored  # chosen\r\n", "mirrored", None, True),
    "mirrored-by-old-wslconfig": ("", "[experimental]\nnetworkingMode=Mirrored\n", "mirrored", None, True),
    "the-running-vm-wins": ("wslinfo:\nnat\n" + NAT_ROUTE, "[wsl2]\nnetworkingMode=mirrored\n", "nat", "172.29.80.1", False),
    "a-comment-is-not-a-setting": ("wslinfo:\n" + NAT_ROUTE, "[wsl2]\n# networkingMode=mirrored\n", "nat", "172.29.80.1", False),
    "another-section": ("wslinfo:\n" + NAT_ROUTE, "[general]\nnetworkingMode=mirrored\n", "nat", "172.29.80.1", False),
    "no-answer": ("", "", "nat", None, False),
}


@pytest.fixture(scope="module")
def probes(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("wslnet")
    body = f"$ErrorActionPreference = 'Stop'\n. {q(INSTALLER / 'lib-runtime.ps1')}\n$out = [ordered]@{{}}\n"
    for name, (text, cfg, _, _, _) in PROBES.items():
        cfgfile = d / f"{name}.wslconfig"
        if cfg:
            cfgfile.write_bytes(cfg.encode("utf-8"))
        body += (f"$t = {q(text)}\n"
                 f"$n = Get-WslNetworkInfo -Probe {{ $t }} -WslConfigPath {q(cfgfile)}\n"
                 f"$out[{q(name)}] = @{{ mode = $n.Mode; host = $n.Host; warning = $n.Warning }}\n")
    body += "$out | ConvertTo-Json -Depth 3 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", list(PROBES))
def test_the_probe_reads_the_mode_and_the_address(probes, name):
    _, _, mode, host, warned = PROBES[name]
    got = probes[name]
    assert (got["mode"], got["host"], bool(got["warning"])) == (mode, host, warned), got


def test_mirrored_mode_warns_with_what_to_do_instead_of_naming_the_router(probes):
    w = probes["mirrored"]["warning"]
    assert "mirrored networking mode" in w and "LAN router (192.168.1.1)" in w, w
    assert "WINDOWS_HOST is not set" in w and "networkingMode=nat" in w and "wsl --shutdown" in w, w


def test_the_probe_command_survives_both_engines_quoting():
    """No double quote and no $( ): powershell.exe 5.1 passes an argument's embedded quotes
    unescaped, and the distro's login shell would expand $( ) before sh -c saw it."""
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
    line = next(ln for ln in lib.splitlines() if ln.startswith("$WslNetworkProbe = "))
    cmd = line.split("= ", 1)[1].strip("'")
    assert '"' not in cmd and "$(" not in cmd and "`" not in cmd, cmd
    assert "wslinfo --networking-mode" in cmd and "ip route show default" in cmd and "loopback0" in cmd, cmd


# --- the real install.ps1 -Provision, per runtime, up to its first model pull ----------------------

# install-native.ps1 stands in as a recorder that succeeds, so provisioning goes on to step 3.
RECORDER = r"""param([string]$PlatformRoot, [switch]$SkipOllama, [string]$BrokerHost, [switch]$OpenWslFirewall)
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'native-called.json'),
  (@{ BrokerHost = $BrokerHost; OpenWslFirewall = [bool]$OpenWslFirewall } | ConvertTo-Json -Compress))
exit 0
"""
# The first `ollama pull` records what it was given and fails, which ends the run before compose.
OLLAMA = ("@echo off\r\n"
          ">> \"%~dp0ollama-calls.txt\" echo %* OLLAMA_HOST=%OLLAMA_HOST%\r\n"
          "exit /b 3\r\n")
# gvproxy's answer, for podman's host probe.
PODMAN = ("@echo off\r\n"
          "if \"%1\"==\"run\" echo 192.168.127.254   host.containers.internal\r\n"
          "exit /b 0\r\n")
PROVISION_STUBS = r"""
$global:log = New-Object System.Collections.Generic.List[string]
$global:appStarted = $false
function global:Start-Process {
  param([Parameter(Position = 0)][string]$FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru,
        [string]$Verb, [string]$WindowStyle)
  if ($FilePath -eq 'powershell') {            # the elevated broker step: the recorder, for real, not elevated
    $p = Microsoft.PowerShell.Management\Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru -NoNewWindow
    if ($PassThru) { $p }
    return
  }
  $global:log.Add("start $FilePath OLLAMA_HOST=$env:OLLAMA_HOST")
  if ($FilePath -like '*ollama app.exe') { $global:appStarted = $true }
}
function global:Invoke-WebRequest {
  [CmdletBinding()] param([Parameter(Position = 0)]$Uri, $TimeoutSec, [switch]$UseBasicParsing)
  if (-not $global:appStarted) { throw 'No connection could be made because the target machine actively refused it' }
  [pscustomobject]@{ StatusCode = 200 }
}
function global:Start-Sleep { [CmdletBinding()] param([Parameter(Position = 0)][int]$Seconds, [int]$Milliseconds) }
function global:Get-NetTCPConnection { [CmdletBinding()] param([int]$LocalPort, [string]$State) $global:log.Add('listen?') }
function global:Get-Process { [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name) $global:log.Add("get-process $Name") }
function global:Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) $global:log.Add("stop $Id") }
# The WSL probe's job: no wsl.exe is started, the job answers with this scenario's text.
function global:Start-Job { [CmdletBinding()] param([scriptblock]$ScriptBlock, [object[]]$ArgumentList) $global:log.Add("probe $($ArgumentList -join ' ')"); [pscustomobject]@{ Id = 1 } }
function global:Wait-Job { [CmdletBinding()] param([Parameter(Position = 0)]$Job, [int]$Timeout) $true }
function global:Receive-Job { [CmdletBinding()] param([Parameter(Position = 0)]$Job) $global:probe }
function global:Stop-Job { [CmdletBinding()] param([Parameter(Position = 0)]$Job) }
function global:Remove-Job { [CmdletBinding()] param([Parameter(Position = 0)]$Job, [switch]$Force) }
"""
# name -> (runtime mode, probe text)
MODES = {
    "podman": ("podman", ""),
    "desktop": ("desktop", ""),
    "wsl": ("wsl", "wslinfo:\nnat\n" + NAT_ROUTE),
    "wsl-mirrored": ("wsl", "wslinfo:\nmirrored\n" + MIRRORED_ROUTE + "loopback0:yes\n"),
}


@pytest.fixture(scope="module")
def provisions(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("olh-install")
    fake = base / "bin"
    fake.mkdir()
    (fake / "ollama.cmd").write_bytes(OLLAMA.encode("ascii"))
    (fake / "podman.cmd").write_bytes(PODMAN.encode("ascii"))
    lib = scratch_lib()
    body = PROVISION_STUBS + f"""
$ErrorActionPreference = 'Continue'
$env:Path = {q(fake)} + ';' + {q(SYSTEM32)} + ';' + {q(WINPS)}
"""
    roots = {}
    for name, (mode, probe) in MODES.items():
        root = base / name / "repo"
        inst = root / "deploy" / "installer"
        inst.mkdir(parents=True)
        for f in ("install.ps1", "env.lean.example", "roles.lean.json"):
            shutil.copy2(INSTALLER / f, inst / f)
        (inst / "lib-runtime.ps1").write_text(lib, encoding="utf-8-sig")
        (inst / "install-native.ps1").write_text(RECORDER, encoding="utf-8-sig")
        (root / "services" / "broker").mkdir(parents=True)
        prof = base / name / "profile"
        (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
        local = base / name / "localappdata"
        (local / "Programs" / "Ollama").mkdir(parents=True)
        (local / "Programs" / "Ollama" / "ollama app.exe").write_bytes(b"")
        roots[name] = root
        body += f"""
Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
Remove-Item -LiteralPath {q(fake / 'ollama-calls.txt')} -ErrorAction SilentlyContinue
$global:log.Clear(); $global:appStarted = $false; $global:probe = {q(probe)}; $global:persisted = $null
$env:USERPROFILE = {q(prof)}; $env:LOCALAPPDATA = {q(local)}
& {q(inst / 'install.ps1')} -Provision -RuntimeMode {mode} -AdminUser admin -AdminPass pw -EnabledApps terminal-fun *>&1 | Out-Null
$pulls = [string[]]@(Get-Content -LiteralPath {q(fake / 'ollama-calls.txt')} -ErrorAction SilentlyContinue | ForEach-Object {{ ([string]$_).Trim() }})
Write-Output ('<<<{name}>>>' + (@{{ log = @($global:log); pulls = $pulls; env = [string]$env:OLLAMA_HOST
                                  persisted = [string]$global:persisted }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        for k in ("log", "pulls"):
            v = got[k]
            got[k] = [] if v is None else (v if isinstance(v, list) else [v])
        root = roots[name]
        got["install_log"] = (root / "deploy" / "logs" / "install.log").read_text(encoding="utf-8-sig")
        got["env_file"] = (root / "deploy" / ".env").read_text(encoding="utf-8")
        called = root / "deploy" / "installer" / "native-called.json"
        got["native"] = json.loads(called.read_text(encoding="utf-8-sig")) if called.exists() else None
        out[name] = got
    assert set(out) == set(MODES), r.stdout + r.stderr
    return out


def _reached_the_pull(got):
    assert got["native"] is not None, got["install_log"]
    assert "ollama pull gemma3:4b failed (exit 3)" in got["install_log"], got["install_log"]
    assert len(got["pulls"]) == 1, got


@pytest.mark.parametrize("name", ["podman", "desktop"])
def test_podman_and_docker_desktop_installs_are_what_they_were(provisions, name):
    got = provisions[name]
    _reached_the_pull(got)
    assert "OLLAMA_HOST" not in got["install_log"] and "mirrored" not in got["install_log"], got["install_log"]
    # The app was started as before, with nothing added to its environment, and nothing was asked
    # about listeners or processes, stopped, or probed through WSL.
    assert [l for l in got["log"] if not l.startswith("start ")] == [], got["log"]
    assert len(got["log"]) == 1 and got["log"][0].endswith("ollama app.exe OLLAMA_HOST="), got["log"]
    assert got["pulls"] == ["pull gemma3:4b OLLAMA_HOST="], got["pulls"]
    assert got["env"] == "" and got["persisted"] == "", got
    assert got["native"]["OpenWslFirewall"] is False and got["native"]["BrokerHost"] == "127.0.0.1", got["native"]


def test_a_wsl_install_sets_ollama_host_before_the_app_starts(provisions):
    got = provisions["wsl"]
    _reached_the_pull(got)
    assert f"OLLAMA_HOST={WANT} set in your user environment" in got["install_log"], got["install_log"]
    assert got["persisted"] == WANT, got
    starts = [l for l in got["log"] if l.startswith("start ")]
    assert len(starts) == 1 and starts[0].endswith(f"ollama app.exe OLLAMA_HOST={WANT}"), got["log"]
    assert got["log"].index("listen?") < got["log"].index(starts[0]), got["log"]
    assert got["pulls"] == [f"pull gemma3:4b OLLAMA_HOST={WANT}"], got["pulls"]   # the CLI still reaches it
    assert got["native"]["OpenWslFirewall"] is True and got["native"]["BrokerHost"] == "0.0.0.0", got["native"]
    assert "WINDOWS_HOST=172.29.80.1" in got["env_file"].splitlines(), got["env_file"]
    assert "WARNING" not in got["install_log"], got["install_log"]


def test_a_mirrored_wsl_install_warns_and_writes_no_router_address(provisions):
    got = provisions["wsl-mirrored"]
    _reached_the_pull(got)
    log = got["install_log"]
    assert "WARNING: WSL runs in mirrored networking mode (from wslinfo)" in log, log
    assert "WINDOWS_HOST" not in got["env_file"], got["env_file"]
    assert "192.168.1.1" not in got["env_file"], got["env_file"]
    assert f"OLLAMA_HOST={WANT} set in your user environment" in log, log       # still a wsl box


def test_install_ps1_sets_the_host_in_its_own_unelevated_process_with_the_user_default():
    """The scratch copies above swap the user's environment for a variable; the real call passes no
    store, and sits in Invoke-Provision (the user's own, unelevated process) before Ollama is
    checked or started."""
    text = (INSTALLER / "install.ps1").read_text(encoding="utf-8-sig")
    code = [ln.split(" #", 1)[0].strip() for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    call = "foreach ($l in @(Set-WslOllamaHost -Mode $RuntimeMode)) { Write-Log $l }"
    hits = [ln for ln in code if "Set-WslOllamaHost" in ln]
    assert hits == [call], hits
    body = text[text.index("function Invoke-Provision"):text.index("function Invoke-ConsoleInstall")]
    assert body.index(call) < body.index("function Test-OllamaUp") < body.index("Start-Process $app"), body
    native = (INSTALLER / "install-native.ps1").read_text(encoding="utf-8-sig")
    assert "OLLAMA_HOST" not in "\n".join(ln for ln in native.splitlines() if not ln.lstrip().startswith("#")), \
        "install-native runs elevated, possibly as another account: the user's variable is not its to set"


# --- apply.ps1: an unelevated run sets it, an elevated one says what to do ----------------------------

# The apply flow elevates once, up front, possibly as another account, so a wsl box it provisioned got
# the block rule and not the variable. Invoke-Apply now ends with Invoke-ApplyOllamaHost: it sets the
# user's OLLAMA_HOST when not elevated, and prints the instruction otherwise. Run here through the
# real Invoke-Apply (lib-apply, from a scratch folder beside the scratch lib-runtime), with the
# elevation check redefined after the library loads, the store a variable (a Set that can be made
# to throw), and every process and listener call a function, as above.
APPLY_STORE = ("$OllamaHostStore = @{ Get = { $global:persisted }; "
               "Set = { param($v) if ($global:storeFails) { throw 'STORE-FAILS' }; $global:persisted = $v } }")
# name -> (mode, elevated, -Execute, OLLAMA_HOST before, the store's Set throws)
APPLY_RUNS = {
    "wsl-user-execute": ("wsl", False, True, "", False),
    "wsl-elevated-execute": ("wsl", True, True, "", False),
    "wsl-user-dry": ("wsl", False, False, "", False),
    "wsl-user-dry-done": ("wsl", False, False, WANT, False),
    "wsl-elevated-dry": ("wsl", True, False, "", False),
    "podman-user-execute": ("podman", False, True, "", False),
    "desktop-elevated-execute": ("desktop", True, True, "", False),
    "wsl-user-store-fails": ("wsl", False, True, "", True),
}


@pytest.fixture(scope="module")
def apply_runs(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("olh-apply") / "deploy" / "installer"
    d.mkdir(parents=True)
    lib = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8-sig")
    assert lib.count(USER_STORE) == 1, "the user-environment store is not where this test expects"
    (d / "lib-runtime.ps1").write_text(lib.replace(USER_STORE, APPLY_STORE), encoding="utf-8-sig")
    shutil.copy2(INSTALLER / "lib-apply.ps1", d / "lib-apply.ps1")
    local = d.parents[1] / "localappdata"           # where the app's default path points
    (local / "Programs" / "Ollama").mkdir(parents=True)
    (local / "Programs" / "Ollama" / "ollama app.exe").write_bytes(b"")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(d / 'lib-apply.ps1')}
{STUBS}
$global:elevated = $false; $global:persisted = $null; $global:storeFails = $false
function Test-ApplyElevated {{ $global:elevated }}
$done = New-ApplyAction -Id 'roles' -Label 'Write the role map' -Test {{ $true }} -Do {{ throw 'a done step ran' }}
"""
    for name, (mode, elevated, execute, before, fails) in APPLY_RUNS.items():
        body += f"""
Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
$global:elevated = ${str(elevated).lower()}; $global:storeFails = ${str(fails).lower()}
$global:persisted = {q(before) if before else '$null'}
$global:calls.Clear(); $global:listen = @('127.0.0.1'); $global:after = @('0.0.0.0'); $global:cim = @()
$global:procs = @([pscustomobject]@{{ ProcessName = 'ollama app'; Id = 101 }})
$env:LOCALAPPDATA = {q(local)}
$all = @(Invoke-Apply -Actions @($done) -Mode {mode} -Execute:${str(execute).lower()} 6>&1)
$rc = @($all | Where-Object {{ $_ -is [int] }})
$lines = @($all | Where-Object {{ $_ -isnot [int] }} | ForEach-Object {{ [string]$_ }})
Write-Output ('<<<{name}>>>' + (@{{ rc = $rc; lines = $lines; calls = @($global:calls)
  persisted = [string]$global:persisted }} | ConvertTo-Json -Compress))
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        key, _, text = chunk.partition(">>>")
        got = json.loads(text.strip().splitlines()[0])
        for k in ("rc", "lines", "calls"):
            v = got[k]
            got[k] = [] if v is None else (v if isinstance(v, list) else [v])
        out[key] = got
    assert set(out) == set(APPLY_RUNS), r.stdout + r.stderr
    for name, got in out.items():
        assert got["lines"][0] == "  skip  Write the role map  (already done)", (name, got)
    return out


def test_an_unelevated_apply_on_wsl_sets_the_users_variable_and_rebinds_the_app(apply_runs):
    got = apply_runs["wsl-user-execute"]
    assert got["persisted"] == WANT and got["rc"] == [0], got
    assert got["lines"][1] == f"  run   set OLLAMA_HOST={WANT} for your account ...", got
    assert any(ln.strip().startswith(f"OLLAMA_HOST={WANT} set in your user environment") for ln in got["lines"]), got
    assert "stop 101" in got["calls"] and any(c.startswith("start ") for c in got["calls"]), got   # the app rebound
    assert got["lines"][-1].strip() == "restarted the Ollama app; it listens on 0.0.0.0:11434", got


@pytest.mark.parametrize("name", ["wsl-elevated-execute", "wsl-elevated-dry"])
def test_an_elevated_apply_on_wsl_sets_nothing_and_says_what_to_do(apply_runs, name):
    """An elevation can be another account, whose environment is not the user's."""
    got = apply_runs[name]
    assert got["persisted"] == "" and got["calls"] == [] and got["rc"] == [0], got
    assert len(got["lines"]) == 2, got
    note = got["lines"][1]
    assert note.startswith(f"  note  OLLAMA_HOST={WANT} is not set by an elevated run"), note
    assert "Edit environment variables for your account" in note and "unelevated" in note, note


def test_a_dry_apply_on_wsl_says_what_it_would_set_and_changes_nothing(apply_runs):
    got = apply_runs["wsl-user-dry"]
    assert got["persisted"] == "" and got["calls"] == [] and got["rc"] == [0], got
    assert got["lines"][1:] == [f"  would set OLLAMA_HOST={WANT} for your account, and restart the Ollama "
                                "app if it listens on loopback"], got
    done = apply_runs["wsl-user-dry-done"]
    assert done["lines"][1:] == [f"  skip  OLLAMA_HOST={WANT} for your account  (already done)"], done


@pytest.mark.parametrize("name", ["podman-user-execute", "desktop-elevated-execute"])
def test_apply_on_any_other_runtime_says_and_sets_nothing_about_ollama(apply_runs, name):
    got = apply_runs[name]
    assert got["lines"][1:] == [] and got["calls"] == [] and got["persisted"] == "" and got["rc"] == [0], got


def test_a_failed_write_is_an_apply_failure_not_a_crash(apply_runs):
    got = apply_runs["wsl-user-store-fails"]
    assert got["rc"] == [1] and got["persisted"] == "", got
    assert got["lines"][-1].startswith("        error: setting OLLAMA_HOST:") and "STORE-FAILS" in got["lines"][-1], got


def test_apply_ps1_hands_its_runtime_to_the_step():
    """Invoke-Apply runs the step for the -Mode it is given, so the one call apply.ps1 makes must
    pass the runtime its actions were built for."""
    text = (INSTALLER / "apply.ps1").read_text(encoding="utf-8-sig")
    code = [ln.split(" #", 1)[0].strip() for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    hits = [ln for ln in code if "Invoke-Apply " in ln]
    assert hits == ["$rc = Invoke-Apply -Actions $actions -Execute:$Execute -Mode $RuntimeMode"], hits
