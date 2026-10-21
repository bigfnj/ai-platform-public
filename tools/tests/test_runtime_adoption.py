"""Every script that picks a container runtime asks Resolve-PlatformRuntime, not "is podman on PATH".

The old rule picked podman whenever podman was installed. On a box that runs the platform on
Docker Desktop with podman beside it, that sent platform-startup to CREATE a podman machine, the
preflight board to ask for one, the installer to set the box up for podman, and the role cutover
to recreate rails on a runtime that holds none of them. Each site here is driven for real, from a
scratch copy of deploy/, with only the container listing faked (appended to the copied
lib-runtime) and every CLI shadowed by a global function, under both PowerShell engines.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# Podman is installed in every scenario; $global:FakeRuntime says which runtime holds the stack.
FAKE_LISTING = r"""
function Get-RuntimeContainerList {
  param([string]$Cli, [int]$TimeoutMs)
  if ($Cli -eq 'podman') {
    if ($global:FakeRuntime -eq 'podman-holds') { return @{ Present = $true; Ok = $true; Rows = @('platform-gateway-1 running'); Detail = '1 container(s)' } }
    return @{ Present = $true; Ok = $false; Rows = @(); Detail = 'podman ps exit 125: Cannot connect to Podman' }
  }
  if ($global:FakeRuntime -eq 'docker-holds') { return @{ Present = $true; Ok = $true; Rows = @('platform-gateway-1 running'); Detail = '1 container(s)' } }
  return @{ Present = $true; Ok = $true; Rows = @(); Detail = '0 container(s)' }
}
"""

STUBS = r"""
$ErrorActionPreference = 'Continue'
function global:podman {
  $a = ($args | ForEach-Object { [string]$_ }) -join ' '
  $global:LASTEXITCODE = 0
  if ($a -like 'machine list*') { 'podman-machine-default  hyperv  now  4  8GiB  60GiB'; return }
  if ($a -like 'info*') { if ($global:FakeRuntime -ne 'podman-holds') { $global:LASTEXITCODE = 125 }; return }
  if ($a -like '--version*') { 'podman version 5.6.0'; return }
}
function global:docker { $global:LASTEXITCODE = 0 }
function global:docker-compose { $global:LASTEXITCODE = 0 }
function global:wsl.exe { $global:LASTEXITCODE = 1 }
function global:nvidia-smi { '8192' }
function global:python { 'Python 3.11.9' }
"""

SCENARIOS = [
    # (name, runtime holding the stack, extra args)
    ("startup", "docker-holds", "platform-startup", "-ShowRuntime"),
    ("startup-podman", "podman-holds", "platform-startup", "-ShowRuntime"),
    ("startup-override", "docker-holds", "platform-startup", "-RuntimeMode podman -ShowRuntime"),
    ("activate", "docker-holds", "activate", "-ShowRuntime"),
    ("activate-override", "docker-holds", "activate", "-RuntimeMode podman -ShowRuntime"),
    ("install", "docker-holds", "install", "-Check"),
    ("install-override", "docker-holds", "install", "-Check -RuntimeMode podman"),
]


@pytest.fixture(scope="module")
def runs(run_ps, shell, tmp_path_factory):
    root = tmp_path_factory.mktemp("adopt") / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    for name in ("platform-startup.ps1", "install.ps1", "lib-preflight.ps1"):
        shutil.copy2(DEPLOY / "installer" / name, inst / name)
    lib = (DEPLOY / "installer" / "lib-runtime.ps1").read_text(encoding="utf-8")
    (inst / "lib-runtime.ps1").write_text(lib + FAKE_LISTING, encoding="utf-8-sig")
    shutil.copy2(DEPLOY / "activate-model-roles.ps1", root / "deploy" / "activate-model-roles.ps1")
    shutil.copy2(DEPLOY / "docker-compose.yml", root / "deploy" / "docker-compose.yml")   # its role vars
    # The installer's Python minimum is read from the broker's own pyproject.
    (root / "services" / "broker").mkdir(parents=True)
    shutil.copy2(REPO / "services" / "broker" / "pyproject.toml", root / "services" / "broker" / "pyproject.toml")
    scripts = {"platform-startup": inst / "platform-startup.ps1", "install": inst / "install.ps1",
               "activate": root / "deploy" / "activate-model-roles.ps1"}
    body = STUBS
    for name, holder, script, args in SCENARIOS:
        body += f"""
$global:FakeRuntime = '{holder}'
Write-Output '<<<{name}>>>'
& {q(scripts[script])} {args} *>&1 | ForEach-Object {{ [string]$_ }}
"""
    # The Python row: 3.10 was accepted while the broker requires 3.11.
    for ver in ("3.10.11", "3.12.1"):
        body += f"""
$global:FakeRuntime = 'docker-holds'
function global:python {{ 'Python {ver}' }}
Write-Output '<<<install-python-{ver}>>>'
& {q(scripts['install'])} -Check *>&1 | ForEach-Object {{ [string]$_ }}
"""
    body += "function global:python { 'Python 3.11.9' }\n"
    # The preflight row is a library call; StrictMode stays inside this child scope.
    for holder in ("docker-holds", "podman-holds"):
        body += f"""
$global:FakeRuntime = '{holder}'
Write-Output '<<<preflight-{holder}>>>'
& {{ . {q(inst / 'lib-preflight.ps1')}; $r = Test-RuntimeCheck; @{{ mode = $r.Data.mode; why = $r.Data.why }} | ConvertTo-Json -Compress }}
"""
    # A box that DECLARES podman while docker lists the gateway running: every site must follow the
    # declaration and say, naming both, that the evidence disagrees.
    env = root / "deploy" / ".env"
    body += f"""
[IO.File]::WriteAllText({q(env)}, "PLATFORM_RUNTIME=podman`n")
$global:FakeRuntime = 'docker-holds'
Write-Output '<<<startup-declared>>>'
& {q(scripts['platform-startup'])} -ShowRuntime *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output '<<<activate-declared>>>'
& {q(scripts['activate'])} -ShowRuntime *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output '<<<install-declared>>>'
& {q(scripts['install'])} -Check *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output '<<<preflight-declared>>>'
& {{ . {q(inst / 'lib-preflight.ps1')}; $r = Test-RuntimeCheck; @{{ mode = $r.Data.mode; warning = $r.Data.warning; detail = $r.Detail }} | ConvertTo-Json -Compress }}
Remove-Item -LiteralPath {q(env)}
"""
    r = run_ps(shell, body, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = text
    return out


def test_platform_startup_drives_the_runtime_that_holds_the_platform(runs):
    assert "runtime: desktop ('docker ps' lists platform-gateway-1 running" in runs["startup"], runs["startup"]
    assert "runtime: podman ('podman ps' lists platform-gateway-1 running" in runs["startup-podman"]


def test_platform_startup_runtime_mode_still_overrides(runs):
    assert "runtime: podman (from -RuntimeMode)" in runs["startup-override"], runs["startup-override"]


def test_preflight_names_the_runtime_that_holds_the_platform(runs):
    docker = json.loads(runs["preflight-docker-holds"].strip().splitlines()[-1])
    assert docker["mode"] == "desktop" and "'docker ps' lists" in docker["why"], docker
    podman = json.loads(runs["preflight-podman-holds"].strip().splitlines()[-1])
    assert podman["mode"] == "podman", podman


def test_role_activation_recreates_rails_on_the_runtime_that_holds_them(runs):
    out = runs["activate"]
    assert "runtime: desktop ('docker ps' lists platform-gateway-1 running" in out, out
    assert "compose:" in out and " compose" in out.split("compose:", 1)[1]   # `docker compose`


def test_role_activation_runtime_mode_overrides(runs):
    assert "runtime: podman (from -RuntimeMode)" in runs["activate-override"], runs["activate-override"]


def test_installer_prereqs_name_the_runtime_that_holds_the_platform(runs):
    out = runs["install"]
    assert "Docker Desktop engine running" in out, out
    assert "runtime chosen: 'docker ps' lists platform-gateway-1 running" in out, out


def test_the_installer_doctor_requires_the_brokers_python(runs):
    """services/broker/pyproject.toml requires 3.11; the doctor passed 3.10, and pip then failed."""
    assert "[FAIL] Python 3.11+" in runs["install-python-3.10.11"], runs["install-python-3.10.11"]
    assert "[ OK ] Python 3.11+" in runs["install-python-3.12.1"], runs["install-python-3.12.1"]
    assert "[ OK ] Python 3.11+" in runs["install"], runs["install"]          # the 3.11.9 stub


def test_the_doctors_minimum_is_every_core_packages_requires_python():
    import re
    for pp in ("services/broker/pyproject.toml", "packages/platform_core/pyproject.toml",
               "apps/platform/backend/pyproject.toml"):
        t = (REPO / pp).read_text(encoding="utf-8")
        m = re.search(r'requires-python\s*=\s*">=\s*(\d+\.\d+)', t)
        assert m and m.group(1) == "3.11", (pp, m and m.group(1))


def test_installer_runtime_mode_still_overrides_the_prereq_doctor(runs):
    out = runs["install-override"]
    assert "Podman machine" in out and "runtime chosen: from -RuntimeMode" in out, out


CONTRADICTION = "deploy\\.env declares PLATFORM_RUNTIME=podman, but 'docker ps' lists platform-gateway-1 running"


def test_platform_startup_follows_the_declaration_and_warns(runs):
    out = runs["startup-declared"]
    assert "runtime: podman (declared in deploy\\.env (PLATFORM_RUNTIME=podman)" in out, out
    assert "WARNING: " + CONTRADICTION in out, out


def test_role_activation_follows_the_declaration_and_warns(runs):
    out = runs["activate-declared"]
    assert "runtime: podman (declared in deploy\\.env" in out and "WARNING: " + CONTRADICTION in out, out


def test_installer_prereqs_follow_the_declaration_and_warn(runs):
    out = runs["install-declared"]
    assert "runtime chosen: declared in deploy\\.env" in out and "[WARN] runtime: " + CONTRADICTION in out, out


def test_preflight_follows_the_declaration_and_says_so_on_the_row(runs):
    got = json.loads(runs["preflight-declared"].strip().splitlines()[-1])
    assert got["mode"] == "podman" and CONTRADICTION in got["warning"] and CONTRADICTION in got["detail"], got
