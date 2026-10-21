"""A ~/.docker/config.json that is a DIRECTORY: podman reads an empty config of ours instead.

On a podman box whose %USERPROFILE%\\.docker\\config.json was a directory, docker-compose only
warned, but every `podman run` / `pull` / `build` failed with exit 125 ("reading JSON file ...
Incorrect function"), and smoke-test.ps1 -Stage all reported 26 FAILs, every data check that runs
a container. lib-runtime's Repair-DockerConfigEnv points DOCKER_CONFIG at a directory of ours
holding `{}` for this process and its children, names the stray directory once, and never touches
it. smoke-test.ps1 and platform-startup.ps1 call it before they run any container.

Every scenario runs the REAL scripts under both engines with a fake profile directory
(USERPROFILE), so nothing here reads or writes the real ~/.docker or any real runtime.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def profile(base: Path, shape: str) -> Path:
    """A fake user profile whose .docker\\config.json is a directory, a file, or absent."""
    prof = base / "profile"
    (prof / ".docker").mkdir(parents=True)
    cfg = prof / ".docker" / "config.json"
    if shape == "dir":
        cfg.mkdir()
        (cfg / "left-alone.txt").write_text("the user's", encoding="utf-8")
    elif shape == "file":
        cfg.write_text('{"auths": {}}', encoding="utf-8")
    return prof


def assert_empty_config(path: Path) -> None:
    """What podman needs of the file: a JSON object with no BOM (Go's JSON reader rejects one,
    and Windows PowerShell 5.1's Set-Content -Encoding UTF8 writes one)."""
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), raw
    assert json.loads(raw.decode("utf-8")) == {}, raw


SCENARIOS = {               # name -> (shape of config.json, DOCKER_CONFIG already set to)
    "dir": ("dir", None),
    "file": ("file", None),
    "absent": ("absent", None),
    "preset": ("dir", "C:\\somewhere\\of\\theirs"),
}


@pytest.fixture(scope="module")
def lib(run_ps, shell, tmp_path_factory):
    """Repair-DockerConfigEnv called twice per scenario, in one engine process."""
    body = "$ErrorActionPreference = 'Stop'\n"
    places = {}
    for name, (shape, preset) in SCENARIOS.items():
        base = tmp_path_factory.mktemp(f"dc-{name}")
        prof, temp, log = profile(base, shape), base / "temp", base / "log.txt"
        places[name] = (prof, temp, log)
        set_env = (f"$env:DOCKER_CONFIG = {q(preset)}" if preset
                   else "Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue")
        body += f"""
& {{
  $env:USERPROFILE = {q(prof)}
  {set_env}
  $Root = {q(REPO)}; $Installer = {q(INSTALLER)}; $TempBase = {q(temp)}; $LogFile = {q(log)}
  function Write-Log($m) {{ Add-Content -LiteralPath $LogFile -Value $m -Encoding utf8 }}
  . {q(INSTALLER / 'lib-runtime.ps1')}
  $first = Repair-DockerConfigEnv
  $second = Repair-DockerConfigEnv
  Write-Output ('<<<{name}>>>' + (@{{ first = $first; second = $second; env = $env:DOCKER_CONFIG }} |
                ConvertTo-Json -Compress))
}}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        prof, temp, log = places[name]
        got = json.loads(text.strip().splitlines()[0])
        got.update(profile=prof, temp=temp,
                   log=log.read_text(encoding="utf-8-sig") if log.exists() else "")
        out[name] = got
    assert set(out) == set(SCENARIOS), r.stdout + r.stderr
    return out


def test_a_config_json_directory_gets_an_empty_config_of_ours(lib):
    got = lib["dir"]
    ours = got["temp"] / "platform-docker-config"
    assert got["env"] == str(ours)
    assert_empty_config(ours / "config.json")
    assert got["first"]["Config"] == str(ours)
    stray = got["profile"] / ".docker" / "config.json"
    assert got["first"]["Stray"] == str(stray)
    # Named once, with what to do about it; the second call found DOCKER_CONFIG set.
    assert got["second"] is None
    warnings = [ln for ln in got["log"].splitlines() if ln.startswith("WARNING:")]
    assert len(warnings) == 1 and str(stray) in warnings[0] and "delete" in warnings[0], got["log"]


def test_the_stray_directory_itself_is_never_touched(lib):
    stray = lib["dir"]["profile"] / ".docker" / "config.json"
    assert stray.is_dir()
    assert [p.name for p in stray.iterdir()] == ["left-alone.txt"]


@pytest.mark.parametrize("name", ["file", "absent", "preset"])
def test_a_readable_or_absent_config_and_a_set_docker_config_are_left_alone(lib, name):
    got = lib[name]
    assert got["first"] is None and got["second"] is None
    assert got["env"] == (SCENARIOS[name][1] or None)
    assert not (got["temp"] / "platform-docker-config").exists()
    assert got["log"] == ""
    if name == "file":
        assert (got["profile"] / ".docker" / "config.json").read_text(encoding="utf-8") == \
            '{"auths": {}}'


# --- the callers ------------------------------------------------------------------------------

def scratch(base: Path, script: str, env: str | None) -> Path:
    root = base / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    for name in (script, "lib-runtime.ps1"):
        shutil.copy2(INSTALLER / name, inst / name)
    if env is not None:
        (root / "deploy" / ".env").write_text(env, encoding="utf-8")
    return root


# Every CLI the build stage can reach is a function (a function wins over an exe), recording the
# DOCKER_CONFIG it was called with.
STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:docker {
  $a = Get-Argv $args
  $global:calls.Add('docker ' + ($a -join ' ') + ' DOCKER_CONFIG=' + $env:DOCKER_CONFIG)
  if ($a[0] -eq 'compose' -and ($a -contains '--services')) { 'gateway'; return }
  if ($a[0] -eq 'compose' -and $a[-1] -eq 'config') { 'services:'; '  gateway:'; '    image: platform-gateway:latest'; return }
  if ($a[0] -eq 'images') { 'platform-gateway:latest'; return }
}
function global:podman { $global:calls.Add('podman ' + ((Get-Argv $args) -join ' ') + ' DOCKER_CONFIG=' + $env:DOCKER_CONFIG) }
function global:docker-compose { $global:calls.Add('docker-compose ' + ((Get-Argv $args) -join ' ')) }
function global:wsl.exe { $global:calls.Add('wsl.exe ' + ((Get-Argv $args) -join ' ')) }
"""


@pytest.fixture(scope="module")
def smoke_run(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("dc-smoke")
    root = scratch(base, "smoke-test.ps1", "PLATFORM_ENABLED_APPS=terminal-fun\n")
    prof = profile(base, "dir")
    body = STUBS + f"""
$env:USERPROFILE = {q(prof)}
Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
& {q(root / 'deploy' / 'installer' / 'smoke-test.ps1')} -Stage build -RuntimeMode desktop *>&1 |
  ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout, prof


def test_smoke_test_runs_its_containers_with_a_readable_docker_config(smoke_run):
    out, prof = smoke_run
    ours = prof / "AppData" / "Local" / "Temp" / "platform-docker-config"
    calls = out.split("CALLS=", 1)[1].strip().split(" | ")
    runtime_calls = [c for c in calls if c.startswith(("docker ", "podman "))]
    assert runtime_calls, out                      # positive control: the stage did call the CLI
    assert all(c.endswith(f"DOCKER_CONFIG={ours}") for c in runtime_calls), calls
    assert_empty_config(ours / "config.json")
    assert "[warn]" in out and str(prof / ".docker" / "config.json") in out, out


@pytest.fixture(scope="module")
def startup_run(run_ps, offline_script, shell, tmp_path_factory):
    """The REAL platform-startup.ps1 from a scratch tree with no deploy\\.env, so it stops right
    after its preamble (test_startup_lock.py's early exit); the repair comes before that. Its own
    process, through offline_script (conftest), as past that exit it would call the gateway."""
    base = tmp_path_factory.mktemp("dc-startup")
    root = scratch(base, "platform-startup.ps1", None)
    prof = profile(base, "dir")
    child = offline_script(root / "deploy" / "installer" / "platform-startup.ps1", "-RuntimeMode desktop")
    body = f"""
$env:USERPROFILE = {q(prof)}
Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(child)} *>&1 | Out-Null
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    log = root / "deploy" / "logs" / "startup.log"
    return (log.read_text(encoding="utf-8-sig") if log.exists() else ""), prof


@pytest.fixture(scope="module")
def install_run(run_ps, shell, tmp_path_factory):
    """The REAL install.ps1 -Provision on podman, from a scratch tree, up to the elevated step:
    Start-Process is a function that throws there (a function wins over the cmdlet), so nothing is
    elevated, installed or pulled. Get-ContainerHostIp's probe runs before it, through the podman
    stub, which records the DOCKER_CONFIG each call saw."""
    base = tmp_path_factory.mktemp("dc-install")
    root = scratch(base, "install.ps1", None)
    for name in ("env.lean.example", "roles.lean.json"):
        shutil.copy2(INSTALLER / name, root / "deploy" / "installer" / name)
    (root / "services" / "broker").mkdir(parents=True)
    prof = profile(base, "dir")
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)   # install.ps1's temp base
    body = STUBS + f"""
function global:Start-Process {{ throw 'stub: stopped at the elevated step' }}
$env:USERPROFILE = {q(prof)}
Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
& {q(root / 'deploy' / 'installer' / 'install.ps1')} -Provision -RuntimeMode podman -AdminUser a `
  -AdminPass b -EnabledApps terminal-fun *>&1 | Out-Null
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    log = root / "deploy" / "logs" / "install.log"
    fail = prof / "AppData" / "Local" / "Temp" / "ai-platform-install.fail"
    return {"calls": r.stdout.split("CALLS=", 1)[1].strip().split(" | "), "prof": prof,
            "log": log.read_text(encoding="utf-8-sig") if log.exists() else "",
            "fail": fail.read_text(encoding="utf-8-sig") if fail.exists() else ""}


def test_install_probes_the_host_address_with_a_readable_docker_config(install_run):
    prof = install_run["prof"]
    ours = prof / "AppData" / "Local" / "Temp" / "platform-docker-config"
    assert "stopped at the elevated step" in install_run["fail"], install_run   # that far, no further
    runs = [c for c in install_run["calls"] if c.startswith("podman run ")]
    assert runs, install_run["calls"]              # positive control: the probe did run a container
    podman = [c for c in install_run["calls"] if c.startswith("podman ")]
    assert all(c.endswith(f"DOCKER_CONFIG={ours}") for c in podman), podman
    assert_empty_config(ours / "config.json")
    log = install_run["log"]
    assert f"WARNING: {prof / '.docker' / 'config.json'} is a DIRECTORY" in log, log
    assert log.index("is a DIRECTORY") < log.index("detecting the container -> Windows host address")


def test_platform_startup_repairs_the_config_before_it_runs_a_container(startup_run):
    log, prof = startup_run
    ours = prof / "AppData" / "Local" / "Temp" / "platform-docker-config"
    assert "aborting" in log, log                  # it got as far as the early exit, no further
    assert f"WARNING: {prof / '.docker' / 'config.json'} is a DIRECTORY" in log, log
    assert log.index("is a DIRECTORY") < log.index("aborting")
    assert_empty_config(ours / "config.json")
