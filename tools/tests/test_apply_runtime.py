"""deploy/installer/lib-apply.ps1 on a Podman-only box: the image and compose steps run.

The apply engine called `docker` for `image inspect`, `pull`, `ps` and `compose`. A Podman box has
podman.exe and docker-compose.exe and NO docker.exe, so those steps could not run there at all.
They now go through lib-runtime's Invoke-VolumeCli / Invoke-Compose.

These tests use REAL child processes: fake podman.cmd and docker-compose.cmd on a PATH holding
nothing else but System32, so docker.exe does not exist, exactly as on that box. Real processes
matter because the fakes write to stderr, and under the EAP=Stop that apply.ps1 sets, Windows
PowerShell 5.1 turns a native command's stderr into a terminating error (pwsh does not). The
in-script routing check (`apply.ps1 -SelfTest`) runs for both engines too.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# Redirection first (`>> file echo ...`): with it last, an argv ending in a digit would read as a
# file-descriptor redirect.
PODMAN = r"""@echo off
>> "%~dp0argv.txt" echo podman %*
if "%1"=="image" ( echo Error: platform-gateway-bundled:latest: image not known 1>&2 & exit /b 1 )
if "%1"=="pull" ( echo Trying to pull platform-gateway-bundled:latest... 1>&2 & exit /b 0 )
if "%1"=="ps" if exist "%~dp0machine-down" ( echo Cannot connect to Podman 1>&2 & exit /b 125 )
if "%1"=="ps" ( echo platform-gateway-1& exit /b 0 )
exit /b 0
"""
COMPOSE = r"""@echo off
>> "%~dp0argv.txt" echo docker-compose %*
>> "%~dp0argv.txt" echo DOCKER_HOST=%DOCKER_HOST%
echo  Container platform-gateway-1  Started 1>&2
exit /b 0
"""


@pytest.fixture(scope="module")
def podman_box(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("podbox")
    fake = base / "bin"
    fake.mkdir()
    (fake / "podman.cmd").write_bytes(PODMAN.replace("\n", "\r\n").encode("ascii"))
    (fake / "docker-compose.cmd").write_bytes(COMPOSE.replace("\n", "\r\n").encode("ascii"))
    root = base / "repo"
    (root / "deploy" / "installer").mkdir(parents=True)
    (root / "deploy" / ".env").write_text("PLATFORM_ENABLED_APPS=terminal-fun\n", encoding="utf-8")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    body = f"""
$ErrorActionPreference = 'Stop'          # what apply.ps1 runs under
$env:Path = {q(fake)} + ';' + {q(system32)}
Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
& {{
  . {q(INSTALLER / 'lib-modelplan.ps1')}
  . {q(INSTALLER / 'lib-apply.ps1')}
  $r = [ordered]@{{ docker_on_path = [bool](Get-Command docker -ErrorAction SilentlyContinue) }}
  $plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 8192 -Installed @{{}}
  $acts = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode podman -Root {q(root)}
  $img = $acts | Where-Object Id -eq 'image'
  $up = $acts | Where-Object Id -eq 'compose'
  $r.image_test = [bool](& $img.Test)
  & $img.Do *> $null
  $r.compose_test = [bool](& $up.Test)
  New-Item -ItemType File -Path {q(fake / 'machine-down')} | Out-Null
  $r.compose_test_machine_down = [bool](& $up.Test)
  & $up.Do *> $null
  $r | ConvertTo-Json -Compress
}}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout.strip().splitlines()[-1])
    result["argv"] = (fake / "argv.txt").read_text(encoding="ascii", errors="replace").splitlines()
    result["root"] = str(root)
    return result


def test_the_box_under_test_has_no_docker(podman_box):
    assert podman_box["docker_on_path"] is False


def test_the_image_step_asks_podman_and_reads_its_exit_code(podman_box):
    """`podman image inspect` exits 1 with an error on stderr: the Test must say 'not done', not
    throw (5.1, EAP=Stop) and not read the error line as success."""
    assert podman_box["image_test"] is False
    argv = podman_box["argv"]
    assert "podman image inspect platform-gateway-bundled:latest" in argv
    # Built through compose, not pulled: the image is a local build that is in no registry.
    root = podman_box["root"]
    env = os.path.join(root, "deploy", ".env")
    base = os.path.join(root, "deploy", "installer", "docker-compose.installer.yml")
    assert f"docker-compose --env-file {env} -f {base} build gateway" in argv, argv
    assert not [a for a in argv if a.startswith("podman pull")], argv


def test_the_compose_test_counts_only_a_listing_that_succeeded(podman_box):
    assert podman_box["compose_test"] is True
    # Machine down: `podman ps` prints an error and exits 125. Counting its lines read that as up.
    assert podman_box["compose_test_machine_down"] is False
    assert any(a.startswith("podman ps --filter name=platform-") for a in podman_box["argv"])


def test_compose_up_goes_through_docker_compose_with_the_podman_pipe(podman_box):
    root = podman_box["root"]
    env = os.path.join(root, "deploy", ".env")
    base = os.path.join(root, "deploy", "installer", "docker-compose.installer.yml")
    assert f"docker-compose --env-file {env} -f {base} --profile terminal-fun up -d" in podman_box["argv"]
    assert "DOCKER_HOST=npipe:////./pipe/podman-machine-default" in podman_box["argv"]


def test_apply_selftest_routes_every_runtime_to_its_own_cli(run_ps, shell, temp_dir_form):
    """And it cleans up after itself under either spelling of TEMP. With TEMP in the 8.3 short
    form the cleanup's Remove-Item threw straight past -ErrorAction SilentlyContinue, so on such a
    box the self-test printed "self-test ok", exited 1 and left its directory behind."""
    given, long_dir = temp_dir_form
    # The self-test runs each action's Test, and a model step's Test asks Ollama's API on
    # 127.0.0.1:11434 which models it has: with this stand-in, not the box's live Ollama, which
    # it asked once per model in the plan on every run (2026-10-15). A function wins command
    # resolution over the cmdlet, and the count proves the stand-in is what was asked.
    r = run_ps(shell, f"$env:TEMP = {q(given)}\n$env:TMP = {q(given)}\n"
                      "$global:__ollamaAsked = 0\n"
                      "function global:Invoke-RestMethod {\n"
                      "  $global:__ollamaAsked++; throw 'no Ollama in this test'\n}\n"
                      f"& {q(INSTALLER / 'apply.ps1')} -SelfTest\n$code = $LASTEXITCODE\n"
                      "Write-Output \"OLLAMA_ASKED=$global:__ollamaAsked\"\nexit $code\n",
               timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "runtime routing ok (podman, desktop, wsl" in r.stdout
    assert not list(long_dir.glob("ai-apply-selftest-*")), "the self-test left its temp dir behind"
    asked = re.search(r"OLLAMA_ASKED=(\d+)", r.stdout)
    assert asked and int(asked.group(1)) > 0, r.stdout



# --- PLATFORM_RUNTIME: written by the env step, never clobbered by the template -----------------

@pytest.fixture(scope="module")
def env_step(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("envstep")
    root = base / "repo"
    (root / "deploy").mkdir(parents=True)
    env = root / "deploy" / ".env"
    env.write_text("PLATFORM_RUNTIME=desktop\nPLATFORM_COMPOSE_OVERRIDE=C:\\box\\over.yml\n", encoding="utf-8")
    box = base / "box.env"
    box.write_text("PLATFORM_ADMIN_USER=old\nPLATFORM_RUNTIME=wsl\n", encoding="utf-8")
    body = f"""
$ErrorActionPreference = 'Stop'
& {{
  . {q(INSTALLER / 'lib-modelplan.ps1')}
  . {q(INSTALLER / 'lib-apply.ps1')}
  $plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 8192 -Installed @{{}}
  $acts = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode podman -Root {q(root)}
  $step = $acts | Where-Object Id -eq 'env'
  & $step.Do
  $settled = [bool](& $step.Test)
  $carried = Add-EnvCarryOver -ExistingPath {q(box)} -NewText (Get-Content -Raw -Encoding UTF8 {q(INSTALLER / 'env.lean.example')})
  @{{ settled = $settled; carried = $carried }} | ConvertTo-Json -Compress
}}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    got["written"] = env.read_text(encoding="utf-8")
    return got


def test_the_env_step_declares_the_runtime_it_was_run_for(env_step):
    """A box's own line is carried over and then replaced by the runtime this apply actually
    drives: an explicit -RuntimeMode beats a stale declaration, and the box's other keys stay."""
    lines = env_step["written"].splitlines()
    assert [ln for ln in lines if ln.startswith("PLATFORM_RUNTIME=")] == ["PLATFORM_RUNTIME=podman"], lines
    assert "PLATFORM_COMPOSE_OVERRIDE=C:\\box\\over.yml" in lines
    assert env_step["settled"] is True      # a re-run compares equal, so it does not rewrite for ever


def test_the_template_never_clobbers_a_declared_runtime(env_step):
    """env.lean.example only COMMENTS the key, so the carry-over keeps the box's declaration. A live
    line there would replace it with the template's on every re-run."""
    active = [ln for ln in env_step["carried"].splitlines() if ln.startswith("PLATFORM_RUNTIME=")]
    assert active == ["PLATFORM_RUNTIME=wsl"], env_step["carried"]


# --- no -AdminPassword: the gateway generates one ---------------------------------------------------
# apply.ps1's -AdminPassword defaults to '', and lib-apply's three [Parameter(Mandatory)][string]
# $AdminPassword refused it ("Cannot bind argument to parameter 'AdminPassword' because it is an
# empty string"), so apply.ps1 with no password exited 1 before it planned anything. A blank one is
# designed: deploy\.env carries PLATFORM_ADMIN_PASSWORD="", compose hands the gateway
# ${PLATFORM_ADMIN_PASSWORD:-} (empty), and the gateway's _seed_admin generates a strong password
# and prints it once. Run from a scratch copy of the installer, its podman and docker-compose fakes
# on a PATH holding nothing else but System32, and Ollama's API a function that throws, so nothing
# on the box is read but the broker service's registry key.

@pytest.fixture(scope="module")
def no_password(run_ps, shell, tmp_path_factory):
    import shutil
    base = tmp_path_factory.mktemp("nopass")
    fake = base / "bin"
    fake.mkdir()
    (fake / "podman.cmd").write_bytes(PODMAN.replace("\n", "\r\n").encode("ascii"))
    (fake / "docker-compose.cmd").write_bytes(COMPOSE.replace("\n", "\r\n").encode("ascii"))
    root = base / "repo"
    shutil.copytree(INSTALLER, root / "deploy" / "installer")
    (root / "services" / "broker").mkdir(parents=True)
    shutil.copy2(REPO / "services" / "broker" / "roles.json", root / "services" / "broker" / "roles.json")
    (root / "rails" / "terminal-fun").mkdir(parents=True)
    shutil.copy2(REPO / "rails" / "terminal-fun" / "rail.json", root / "rails" / "terminal-fun" / "rail.json")
    step_root = base / "step"
    (step_root / "deploy").mkdir(parents=True)
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    body = f"""
$env:Path = {q(fake)} + ';' + {q(system32)}
Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
function global:Invoke-RestMethod {{ throw 'no Ollama in this test' }}
Write-Output '<<<dry>>>'
try {{
  & {q(root / 'deploy' / 'installer' / 'apply.ps1')} -Rails terminal-fun -VramGb 8 -RuntimeMode podman *>&1 | ForEach-Object {{ [string]$_ }}
  Write-Output "EXIT=$LASTEXITCODE"
}} catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
Write-Output '<<<step>>>'
try {{
  & {{
    . {q(root / 'deploy' / 'installer' / 'lib-modelplan.ps1')}
    . {q(root / 'deploy' / 'installer' / 'lib-apply.ps1')}
    $plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 8192 -Installed @{{}}
    $acts = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'admin' -AdminPassword '' -Mode podman -Root {q(step_root)}
    & ($acts | Where-Object Id -eq 'env').Do
    Write-InstallerEnv -Rails @('terminal-fun') -AdminUser 'admin' -AdminPassword '' -Path {q(step_root / 'writer.env')} | Out-Null
    'written'
  }}
}} catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = text
    out["env"] = step_root / "deploy" / ".env"
    out["writer"] = step_root / "writer.env"
    return out


def test_apply_with_no_password_plans_every_step(no_password):
    out = no_password["dry"]
    assert "EXIT=0" in out and "THROWN" not in out, out
    assert "  would Write deploy\\.env from selections" in out, out
    assert "  would Build the gateway image" in out, out


def test_a_blank_password_reaches_the_gateway_blank_so_it_generates_one(no_password):
    """The env step, and Write-InstallerEnv, write the password compose reads back as empty, the
    value the gateway's _seed_admin answers with a generated password (apps/platform/backend:
    `settings.admin_password or secrets.token_urlsafe(12)`)."""
    import importlib.util
    assert "written" in no_password["step"] and "THROWN" not in no_password["step"], no_password["step"]
    spec = importlib.util.spec_from_file_location("_apply_deploy_snapshot", REPO / "tools" / "deploy_snapshot.py")
    ds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ds)
    for f in (no_password["env"], no_password["writer"]):
        got = ds.read_env(f, environ={})
        assert got["PLATFORM_ADMIN_PASSWORD"] == "" and got["PLATFORM_ADMIN_USER"] == "admin", (f.name, got)
        lines = f.read_text(encoding="utf-8").splitlines()
        assert 'PLATFORM_ADMIN_PASSWORD=""' in lines, (f.name, lines)
