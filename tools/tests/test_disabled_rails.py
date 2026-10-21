"""A rail disabled on this box (PLATFORM_DISABLED_APPS) is not RUNNING on this box either.

The gateway stopped routing a disabled rail while its container kept running and came back at every
boot (`restart: always`, which Docker Desktop and podman-restart honour by themselves), and
rebuild-stale's derived plan rebuilt and started it. lib-runtime's Sync-DisabledRails parks such a
container (restart=no, stopped) and brings a re-enabled one back; these tests drive it, and the
parser that reads the one declaration, under both PowerShell engines against stub runtimes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
KEY = "PLATFORM_DISABLED_APPS"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# --- the parser: the same answers as tools/rail_smoke.py (itself the gateway's mirror) ----------

# name -> (deploy/.env text, environment value or None). The file half is read as compose's dotenv
# reads it, since compose is what hands the gateway the value: "indented" and "last-line-wins" used
# to expect the OPPOSITE of compose (nothing, and the first line), and "trailing-comment" is the
# audit's case that parked nothing while the gateway disabled servicenow.
PARSE_CASES = {
    "absent": ("OTHER=1\n", None),
    "one": (f"{KEY}=servicenow\n", None),
    "spaces-and-empties": (f"{KEY}= servicenow , openmaic ,,\n", None),
    "quoted": (f'{KEY}="servicenow,iep"\n', None),
    "json-array": (f'{KEY}=["servicenow", "iep"]\n', None),
    "commented": (f"# {KEY}=servicenow\n", None),
    "indented": (f"  {KEY}=servicenow\n", None),
    "tab-indented": (f"\t{KEY}=servicenow\n", None),
    "last-line-wins": (f"{KEY}=servicenow\n{KEY}=openmaic\n", None),
    "trailing-comment": (f"{KEY}=servicenow  # a trailing comment\n", None),
    "blank": (f"{KEY}=\n", None),
    "environment-wins": (f"{KEY}=servicenow\n", "openmaic,iep"),
    "blank-environment-does-not-shadow": (f"{KEY}=servicenow\n", "   "),
}


@pytest.fixture(scope="module")
def ps_parsed(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("parse")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$all = [ordered]@{{}}
"""
    for name, (text, envval) in PARSE_CASES.items():
        f = d / f"{name}.env"
        f.write_text(text, encoding="utf-8")
        set_env = f"$env:{KEY} = {q(envval)}" if envval is not None else f"Remove-Item Env:{KEY} -ErrorAction SilentlyContinue"
        body += f"""
{set_env}
$all['{name}'] = @(Get-DeclaredAppList -Key '{KEY}' -EnvFile {q(f)})
"""
    body += f"Remove-Item Env:{KEY} -ErrorAction SilentlyContinue\n$all | ConvertTo-Json -Depth 3 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    raw = json.loads(r.stdout.strip().splitlines()[-1])
    # ConvertTo-Json renders a one-element array as a bare string, and an empty one as null or [].
    return {k: ([] if v is None else [v] if isinstance(v, str) else list(v)) for k, v in raw.items()}


# The forms written out, so the reader is checked in the public snapshot too, where rail_smoke.py
# (the parity reference below) is withheld.
EXPECTED = {
    "absent": [], "one": ["servicenow"], "spaces-and-empties": ["servicenow", "openmaic"],
    "quoted": ["servicenow", "iep"], "json-array": ["servicenow", "iep"], "commented": [],
    "indented": ["servicenow"], "tab-indented": ["servicenow"], "last-line-wins": ["openmaic"],
    "trailing-comment": ["servicenow"],
    "blank": [],
    "environment-wins": ["openmaic", "iep"], "blank-environment-does-not-shadow": ["servicenow"],
}
_RAIL_SMOKE = REPO / "tools" / "rail_smoke.py"
# Both readers now read deploy/.env the way compose does (of two lines the LAST wins, an
# indented key is read, a trailing comment is dropped), so every case must agree; the strict xfails
# that marked the PowerShell half as pending are gone. The full measured case list both readers are
# held to is tools/tests/dotenv_compose_cases.json (test_dotenv_reader.py, test_dotenv_ps_reader.py).
_PARITY_CASES = list(PARSE_CASES)


@pytest.mark.parametrize("case", list(PARSE_CASES))
def test_the_powershell_reader_reads_every_form_the_gateway_accepts(ps_parsed, case):
    assert ps_parsed[case] == EXPECTED[case], (case, ps_parsed[case])


@pytest.mark.skipif(not _RAIL_SMOKE.is_file(), reason=(
    "tools/rail_smoke.py is withheld from the public snapshot (publish.json paths.exclude), so the "
    "parity reference is not here; the documented-forms test above still runs"))
@pytest.mark.parametrize("case", _PARITY_CASES)
def test_the_powershell_reader_agrees_with_rail_smoke(ps_parsed, case, smoke, tmp_path, monkeypatch):
    """One declaration, read one way: the ops scripts, rail_smoke and the gateway must agree."""
    text, envval = PARSE_CASES[case]
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / ".env").write_text(text, encoding="utf-8")
    monkeypatch.setattr(smoke, "REPO", tmp_path)
    if envval is None:
        monkeypatch.delenv(KEY, raising=False)
    else:
        monkeypatch.setenv(KEY, envval)
    expected = list(smoke.parse_apps(smoke.declared(KEY)[0]))
    assert ps_parsed[case] == expected, (case, ps_parsed[case], expected)


# --- the step: park the disabled, bring back the re-enabled --------------------------------------

SERVICES = {  # name -> (restart, ai-platform.rail label)
    "dashboard": ("always", ""), "iep": ("always", ""), "servicenow": ("always", ""),
    "openmaic": ("always", ""), "openmaic-app": ("always", "openmaic"), "ai-voice": ("always", ""),
    "gateway": ("always", ""),
}
# name -> (state, restart policy) on the stub runtime. ai-voice was parked by an earlier run and its
# rail is not disabled any more, so this run must bring it back.
INITIAL = {
    "dashboard": ("running", "always"), "iep": ("running", "always"),
    "servicenow": ("running", "always"), "openmaic": ("running", "always"),
    "openmaic-app": ("running", "always"), "ai-voice": ("exited", "no"),
    "gateway": ("running", "always"),
}

STUB = r"""
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:Fake-Runtime($cli, $a) {
  $global:calls.Add("$cli " + ($a -join ' '))
  $global:LASTEXITCODE = 0
  switch ($a[0]) {
    'ps'      { foreach ($k in @($global:state.Keys)) { "platform-$k-1 $($global:state[$k].State)" } }
    'inspect' { foreach ($n in @($a | Select-Object -Skip 3)) { $k = $n -replace '^platform-(.+)-1$', '$1'
                  $p = $global:state[$k].Policy; if ($global:emptyNo -and $p -eq 'no') { $p = '' }; "/$n $p" } }
    'update'  { if ($global:failUpdate) { 'Error: no such flag'; $global:LASTEXITCODE = 125; return }
                $k = $a[2] -replace '^platform-(.+)-1$', '$1'; $global:state[$k].Policy = ($a[1] -replace '^--restart=', '') }
    'stop'    { $k = $a[1] -replace '^platform-(.+)-1$', '$1'; $global:state[$k].State = 'exited' }
    'start'   { $k = $a[1] -replace '^platform-(.+)-1$', '$1'; $global:state[$k].State = 'running' }
  }
}
function global:docker { Fake-Runtime 'docker' (Get-Argv $args) }
function global:podman { Fake-Runtime 'podman' (Get-Argv $args) }
"""


@pytest.fixture(scope="module")
def sync(run_ps, shell, tmp_path_factory):
    rails = tmp_path_factory.mktemp("rails")
    (rails / "edu").mkdir()
    (rails / "edu" / "rail.json").write_text(json.dumps(
        {"id": "edu-suite", "compose_service": "dashboard", "also_serves": ["iep"]}), encoding="utf-8")
    (rails / "sn").mkdir()
    (rails / "sn" / "rail.json").write_text(json.dumps({"id": "servicenow"}), encoding="utf-8")
    config = json.dumps({"services": {
        n: {"restart": r, **({"labels": {"ai-platform.rail": lab}} if lab else {})}
        for n, (r, lab) in SERVICES.items()}})
    state = "; ".join(f"'{k}' = [pscustomobject]@{{ State = '{s}'; Policy = '{p}' }}" for k, (s, p) in INITIAL.items())
    body = STUB + f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$svc = @(Get-ComposeServices -ConfigJson {q(config)})
$disabled = @('servicenow', 'openmaic', 'iep', 'nosuch')
$out = [ordered]@{{}}
function Snap {{ $s = [ordered]@{{}}; foreach ($k in $global:state.Keys) {{ $s[$k] = "$($global:state[$k].State)/$($global:state[$k].Policy)" }}; $s }}
function Reset {{ $global:state = [ordered]@{{ {state} }}; $global:calls.Clear(); $global:failUpdate = $false; $global:emptyNo = $false }}

Reset
$r = Sync-DisabledRails -Disabled $disabled -Services $svc -RailsDir {q(rails)} -Mode desktop -DryRun
$out['dry'] = @{{ lines = $r.Lines; calls = @($global:calls); state = (Snap) }}

Reset
$r = Sync-DisabledRails -Disabled $disabled -Services $svc -RailsDir {q(rails)} -Mode desktop
$out['run'] = @{{ lines = $r.Lines; failed = $r.Failed; calls = @($global:calls); state = (Snap) }}
$global:calls.Clear()
$r = Sync-DisabledRails -Disabled $disabled -Services $svc -RailsDir {q(rails)} -Mode desktop
$out['again'] = @{{ lines = $r.Lines; calls = @($global:calls) }}

Reset
$r = Sync-DisabledRails -Disabled @('servicenow') -Services $svc -RailsDir {q(rails)} -Mode podman
$out['podman'] = @{{ calls = @($global:calls) }}

# A podman that prints an EMPTY restart policy for "no".
Reset
$global:emptyNo = $true
$r = Sync-DisabledRails -Disabled @('servicenow') -Services $svc -RailsDir {q(rails)} -Mode podman
$out['empty-run'] = @{{ calls = @($global:calls); state = (Snap) }}
$global:calls.Clear()
$r = Sync-DisabledRails -Disabled @('servicenow') -Services $svc -RailsDir {q(rails)} -Mode podman
$out['empty-again'] = @{{ calls = @($global:calls) }}

Reset
$global:failUpdate = $true
$r = Sync-DisabledRails -Disabled @('servicenow') -Services $svc -RailsDir {q(rails)} -Mode desktop
$out['fails'] = @{{ lines = $r.Lines; failed = $r.Failed }}

$out['resolve-edu'] = @((Resolve-DisabledServices -Disabled @('edu-suite') -Services $svc -RailsDir {q(rails)}).Services.Keys)
$out | ConvertTo-Json -Depth 5 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def mutating(calls):
    return [c for c in calls if c.split()[1] in ("update", "stop", "start")]


def test_a_podman_that_reports_no_restart_policy_as_empty_is_still_idempotent(sync):
    """An empty RestartPolicy.Name is "no". Read as missing, every run re-parked servicenow and the
    parked ai-voice was never brought back."""
    st = sync["empty-run"]["state"]
    assert st["servicenow"] == "exited/no" and st["ai-voice"] == "running/always", st
    assert mutating(sync["empty-again"]["calls"]) == [], sync["empty-again"]["calls"]


def test_disabled_rails_are_stopped_and_kept_from_restarting(sync):
    st = sync["run"]["state"]
    for name in ("servicenow", "openmaic", "iep"):
        assert st[name] == "exited/no", (name, st)
    assert st["dashboard"] == "running/always" and st["gateway"] == "running/always", st
    assert sync["run"]["failed"] == 0
    assert "docker update --restart=no platform-servicenow-1" in sync["run"]["calls"]


def test_a_service_that_exists_only_for_a_disabled_rail_is_parked_with_it(sync):
    """openmaic-app has no rail of its own; its compose label ties it to openmaic."""
    assert sync["run"]["state"]["openmaic-app"] == "exited/no", sync["run"]["state"]


def test_a_rails_compose_service_and_its_extra_tiles_map_from_the_manifests(sync):
    assert sync["resolve-edu"] == ["dashboard"]           # edu-suite runs as `dashboard`
    assert sync["run"]["state"]["iep"] == "exited/no"      # a tile edu-suite also serves


def test_re_enabling_a_rail_brings_its_container_back(sync):
    """ai-voice was parked earlier and is not disabled any more: policy restored, started."""
    assert sync["run"]["state"]["ai-voice"] == "running/always", sync["run"]["state"]
    assert "docker update --restart=always platform-ai-voice-1" in sync["run"]["calls"]


def test_a_second_run_changes_nothing(sync):
    assert mutating(sync["again"]["calls"]) == [], sync["again"]["calls"]
    assert any("already down and kept down" in ln for ln in sync["again"]["lines"])


def test_a_dry_run_changes_nothing_and_says_what_it_would_do(sync):
    assert mutating(sync["dry"]["calls"]) == [], sync["dry"]["calls"]
    assert sync["dry"]["state"]["servicenow"] == "running/always"
    assert any(ln.startswith("would set restart=no on platform-servicenow-1") for ln in sync["dry"]["lines"])


def test_a_name_that_matches_nothing_is_said(sync):
    assert any("'nosuch', which no rail or service here serves" in ln for ln in sync["run"]["lines"])


def test_podman_is_driven_through_podman(sync):
    assert "podman update --restart=no platform-servicenow-1" in sync["podman"]["calls"]
    assert not [c for c in sync["podman"]["calls"] if c.startswith("docker ")]


def test_a_failed_action_is_counted_and_named(sync):
    assert sync["fails"]["failed"] >= 1
    assert any(ln.startswith("FAILED to set restart=no on platform-servicenow-1") for ln in sync["fails"]["lines"])


# --- platform-startup: up WITHOUT the disabled rails' profiles, and park AFTER up ----------------

def test_platform_startup_parks_after_compose_up_and_brings_up_only_enabled_profiles():
    """An ORDER and a CONDITION, not a presence check: parking before `up` would let `up` start the
    container again, and `up` with the full enabled list would start a disabled rail's profile."""
    text = (INSTALLER / "platform-startup.ps1").read_text(encoding="utf-8")
    up = text.index("Invoke-Compose -Arguments ($cargs + @('up', '-d'))")
    park = text.index("Sync-DisabledRails -Disabled $disabled")
    assert up < park
    assert "$cargs += Get-ComposeProfiles -Apps $upApps" in text[:up]
    assert "$_ -notin $disabled" in text[text.index("$upApps ="):up]


# --- sync-disabled-rails.ps1: the by-hand entry point ------------------------------------------

SCRIPT_STUB = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
$global:where = New-Object System.Collections.Generic.List[string]
function global:docker {
  $a = @(foreach ($x in $args) { foreach ($y in @($x)) { [string]$y } })
  $global:calls.Add('docker ' + ($a -join ' '))
  # Where the script pointed its log and its helpers' temp files, read from the script's own scope.
  $global:where.Add("$TempBase|$LogFile")
  $global:LASTEXITCODE = 0
  if ($a[0] -eq 'compose') { '{"services": {"servicenow": {"restart": "always"}, "gateway": {"restart": "always"}}}'; return }
  if ($a[0] -eq 'ps') { 'platform-servicenow-1 running'; 'platform-gateway-1 running'; return }
  if ($a[0] -eq 'inspect') { '/platform-servicenow-1 always'; '/platform-gateway-1 always'; return }
  if ($a[0] -eq 'update' -and $global:failUpdate) { 'Error: refused'; $global:LASTEXITCODE = 1; return }
}
"""


@pytest.fixture(scope="module")
def script_runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("cli")
    deploy = base / "deploy"
    deploy.mkdir()
    (deploy / ".env").write_text(f"{KEY}=servicenow\n", encoding="utf-8")
    # sync-disabled-rails.ps1 builds $TempBase and $LogFile from USERPROFILE and appends its log
    # there: under the user's own profile these runs wrote ai-platform-sync-disabled-rails.log into
    # the real Temp. A profile of the fixture's own, as warned_run below has.
    prof = base / "profile"
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
    script = INSTALLER / "sync-disabled-rails.ps1"
    out = {"profile": prof}
    for name, extra, fail in (("dry", "-DryRun", "$false"), ("run", "", "$false"), ("fails", "", "$true")):
        body = f"$env:USERPROFILE = {q(prof)}\n" + SCRIPT_STUB + f"""
$global:failUpdate = {fail}
& {q(script)} -FullStack -RuntimeMode desktop -DeployDir {q(deploy)} {extra} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("WHERE=" + (@($global:where | Select-Object -Unique) -join ' | '))
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
        r = run_ps(shell, body)
        assert r.returncode == 0, r.stdout + r.stderr
        out[name] = r.stdout
    return out


def test_the_script_runs_keep_their_log_and_temp_files_in_a_scratch_profile(script_runs):
    """Each run's docker stand-in records $TempBase and $LogFile as the script set them: all in the
    fixture's own profile, never the user's Temp."""
    temp = str(script_runs["profile"] / "AppData" / "Local" / "Temp")
    for name in ("dry", "run", "fails"):
        seen = script_runs[name].split("WHERE=", 1)[1].strip().splitlines()[0].split(" | ")
        assert seen == [f"{temp}|{temp}\\ai-platform-sync-disabled-rails.log"], (name, seen)


def test_the_script_dry_run_names_the_disabled_rail_and_changes_nothing(script_runs):
    out = script_runs["dry"]
    assert "disabled on this box: servicenow" in out and "would set restart=no on platform-servicenow-1" in out, out
    calls = out.split("CALLS=", 1)[1]
    assert " update " not in f" {calls} " and "docker stop" not in calls, calls
    assert "EXIT=0" in out


def test_the_script_parks_the_rail_and_reports_a_failure_in_its_exit_code(script_runs):
    assert "docker update --restart=no platform-servicenow-1" in script_runs["run"]
    assert "docker stop platform-servicenow-1" in script_runs["run"] and "EXIT=0" in script_runs["run"]
    calls = script_runs["run"].split("CALLS=", 1)[1].strip().split(" | ")
    changed = [c for c in calls if c.split()[1] in ("update", "stop", "start")]
    assert not [c for c in changed if "platform-gateway-1" in c], changed   # an enabled rail is untouched
    assert "EXIT=1" in script_runs["fails"] and "FAILED to set restart=no" in script_runs["fails"]


# --- a compose that warns on stderr, on a box whose ~/.docker/config.json is a directory --------
# Measured on the podman box (2026-10-14): `sync-disabled-rails.ps1 -DryRun` exited 1 having done
# nothing, because Invoke-Compose merged compose's "WARNING: ..." stderr line into the render and
# ConvertFrom-Json refused it; and the script never pointed DOCKER_CONFIG away from the directory.
# A REAL child process here (a .cmd fake on a PATH of only itself and System32): a PowerShell
# function cannot write native stderr.
WARNING_DOCKER = r"""@echo off
>> "%~dp0calls.txt" echo docker %1 %2 DOCKER_CONFIG=%DOCKER_CONFIG%
if "%1"=="compose" goto compose
if "%1"=="ps" goto ps
if "%1"=="inspect" goto inspect
exit /b 0
:compose
echo WARNING: The NOT_SET variable is not set. Defaulting to a blank string. 1>&2
type "%~dp0render.json"
exit /b 0
:ps
echo platform-servicenow-1 running
echo platform-gateway-1 running
exit /b 0
:inspect
echo /platform-servicenow-1 always
echo /platform-gateway-1 always
exit /b 0
"""


@pytest.fixture(scope="module")
def warned_run(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("warn")
    deploy = base / "deploy"
    deploy.mkdir()
    (deploy / ".env").write_text(f"{KEY}=servicenow\n", encoding="utf-8")
    fake = base / "bin"
    fake.mkdir()
    (fake / "docker.cmd").write_bytes(WARNING_DOCKER.replace("\n", "\r\n").encode("ascii"))
    (fake / "render.json").write_text(json.dumps(
        {"services": {"servicenow": {"restart": "always"}, "gateway": {"restart": "always"}}}), encoding="utf-8")
    prof = base / "profile"
    (prof / ".docker" / "config.json").mkdir(parents=True)         # the stray directory
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)      # the script's log and temp base
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    body = f"""
$env:Path = {q(fake)} + ';' + {q(system32)}
$env:USERPROFILE = {q(prof)}
Remove-Item Env:DOCKER_CONFIG, Env:DOCKER_HOST, Env:{KEY} -ErrorAction SilentlyContinue
& {q(INSTALLER / 'sync-disabled-rails.ps1')} -FullStack -RuntimeMode desktop -DeployDir {q(deploy)} -DryRun *>&1 |
  ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (fake / "calls.txt").read_text(encoding="ascii").splitlines() if (fake / "calls.txt").is_file() else []
    return r.stdout, calls, prof


def test_a_compose_warning_on_stderr_does_not_stop_the_dry_run(warned_run):
    out, calls, _ = warned_run
    assert "EXIT=0" in out, out
    assert "would set restart=no on platform-servicenow-1" in out, out
    assert [c for c in calls if c.startswith("docker compose")], calls    # the render did run


def test_the_script_runs_its_clis_with_a_readable_docker_config(warned_run):
    out, calls, prof = warned_run
    ours = prof / "AppData" / "Local" / "Temp" / "platform-docker-config"
    assert calls and all(c.endswith(f"DOCKER_CONFIG={ours}") for c in calls), calls
    assert f"{prof / '.docker' / 'config.json'} is a DIRECTORY" in out, out


def test_no_script_parses_a_render_that_carries_compose_stderr():
    """Invoke-Compose merges stderr into its output; a parsed render goes through
    Invoke-ComposeConfigJson, which keeps it apart. Both callers of the parking step use it."""
    import re
    hits = []
    for p in sorted((REPO / "deploy").rglob("*.ps1")):
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            if "Invoke-Compose " in line and re.search(r"'config',\s*'--format',\s*'json'", line):
                hits.append(f"{p.relative_to(REPO)}:{n}")
    assert hits == [], hits
    for name in ("sync-disabled-rails.ps1", "platform-startup.ps1"):
        assert "Invoke-ComposeConfigJson -Arguments" in (INSTALLER / name).read_text(encoding="utf-8-sig"), name


def test_the_render_keeps_stderr_out_of_the_json(run_ps, shell, tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker.cmd").write_bytes(WARNING_DOCKER.replace("\n", "\r\n").encode("ascii"))
    (fake / "render.json").write_text('{"services": {"gateway": {}}}', encoding="utf-8")
    (fake / "fails.cmd").write_bytes(b"@echo off\r\necho no such file: compose.yml 1>&2\r\nexit /b 14\r\n")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    body = f"""
$ErrorActionPreference = 'Stop'
$env:Path = {q(fake)} + ';' + {q(system32)}
. {q(INSTALLER / 'lib-runtime.ps1')}
$ok = Invoke-ComposeConfigJson -Arguments @('--env-file', 'x.env') -Mode desktop
function Get-ComposeCommand {{ param([string]$Mode) @{{ Exe = 'fails'; Prefix = @(); DockerHost = '' }} }}
$bad = Invoke-ComposeConfigJson -Mode desktop
@{{ ok = $ok; parsed = (($ok.Json | ConvertFrom-Json).services.PSObject.Properties.Name); bad = $bad }} | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["ok"]["Code"] == 0 and got["parsed"] == "gateway", got
    err = got["ok"]["Err"] if isinstance(got["ok"]["Err"], list) else [got["ok"]["Err"]]
    assert any("NOT_SET variable is not set" in e for e in err) and "WARNING" not in got["ok"]["Json"], got
    assert got["bad"]["Code"] == 14, got
    bad_err = got["bad"]["Err"] if isinstance(got["bad"]["Err"], list) else [got["bad"]["Err"]]
    assert any("no such file" in e for e in bad_err), got
