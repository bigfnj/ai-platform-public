"""deploy/rebuild-stale.ps1 derives what it rebuilds from compose, not from a list in the script.

Its hardcoded list had fallen four services behind the stack (openmaic, course-builder,
servicenow, watch-builder), and a rail missing from it was silently never rebuilt. Each test runs
the REAL script with -WhatIfOnly and `docker` shadowed by a global function that answers
`compose config --format json`, so nothing is built and no daemon is asked anything.

It also follows the box: on a podman box (no docker CLI at all, the lean stack plus a box-local
override, a compose profile per enabled rail) it ran `docker compose` in deploy\\ and could
rebuild nothing. The lean cases run the real script end to end with `docker-compose` and `podman`
shadowed by functions that record every call, and `docker` by one that fails the run if called.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _yaml():
    """pyyaml, or a FAILURE naming it: imported here, not at module level, so its absence neither
    stops the core target at collection nor folds this module into one quiet skip (see
    test_rail_manifest_schema._jsonschema). run-tests.ps1 -Doctor names it as test tooling."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        pytest.fail("pyyaml is not installed, so the rebuild plan is not checked against the compose "
                    "file: pip install pyyaml (run-tests.ps1 -Doctor lists the test tooling)", pytrace=False)
    return yaml


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "rebuild-stale.ps1"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def real_stack() -> dict:
    """The shape `compose config --format json` gives, from the tracked compose file: only the
    keys the script reads (build, image), in compose's alphabetical service order."""
    doc = _yaml().safe_load((REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    out = {}
    for name in sorted(doc["services"]):
        s = doc["services"][name]
        out[name] = {k: s[k] for k in ("build", "image") if k in s}
    return {"services": out}


def test_the_compose_file_names_services_to_build():
    """Pure Python, so it runs on every OS. It is the vacuity guard for the plan tests below
    (they compare against what real_stack() reads, so a compose file or a YAML read that yielded
    no build stanza would let them pass on nothing). It is also the test that needs pyyaml on
    Linux CI, where every PowerShell test in this module skips before reaching it."""
    built = [n for n, s in real_stack()["services"].items() if "build" in s]
    assert built, "deploy/docker-compose.yml names no service with a build: stanza"


# Written in a deliberately unhelpful order: the front door first, the shared-image builder last.
SYNTHETIC = {"services": {
    "gateway": {"build": {"context": "."}, "image": "platform-gateway:latest"},
    "pulled": {"image": "docker.io/library/caddy:2"},
    "sharer": {"image": "shared:latest"},
    "zeta": {"build": {"context": "."}, "image": "zeta:latest"},
    "alpha": {"build": {"context": "."}, "image": "shared:latest"},
}}

# (name, render, deploy\.env, args). Every scenario runs against its OWN deploy folder
# (-DeployDir), never this checkout's: the live checkout's deploy\.env disables a rail, and a test
# that read it would pass or fail by which box it ran on.
# Pinned to Docker Desktop and the full stack: those are the Docker Desktop box's commands, which
# must stay exactly what they were (the fake throws on any other docker call).
DESKTOP = "-RuntimeMode desktop -Stack full -WhatIfOnly"
SCENARIOS = [
    ("real", "real", "", DESKTOP),
    ("synthetic", "synthetic", "", DESKTOP),
    ("comma-joined", "synthetic", "", f"{DESKTOP} -Service 'zeta,sharer'"),
    ("unknown", "synthetic", "", f"{DESKTOP} -Service nosuch"),
    ("config-fails", "fail", "", DESKTOP),
    ("disabled", "synthetic", "PLATFORM_DISABLED_APPS=zeta\n", DESKTOP),
    ("disabled-asked-for", "synthetic", "PLATFORM_DISABLED_APPS=zeta\n", f"{DESKTOP} -Service zeta"),
]


@pytest.fixture(scope="module")
def plans(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("rebuild")
    renders = {"real": base / "real.json", "synthetic": base / "synthetic.json"}
    renders["real"].write_text(json.dumps(real_stack()), encoding="utf-8")
    renders["synthetic"].write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    body = f"""
$ErrorActionPreference = 'Continue'
function global:docker {{
  $argv = @(foreach ($x in $args) {{ foreach ($y in @($x)) {{ [string]$y }} }})
  $global:LASTEXITCODE = 0
  # The disabled-rail step lists and inspects containers: zeta is running, restart always.
  if ($argv[0] -eq 'ps') {{ 'platform-zeta-1 running'; return }}
  if ($argv[0] -eq 'inspect') {{ '/platform-zeta-1 always'; return }}
  if ($argv[0] -in @('update', 'stop', 'start')) {{ throw "a dry run must not change anything: $argv" }}
  if (($argv -join ' ') -ne 'compose config --format json') {{ throw "unexpected docker call: $argv" }}
  if ($global:render -eq 'fail') {{ Write-Error 'env file deploy\\.env not found'; $global:LASTEXITCODE = 1; return }}
  Get-Content -Raw -LiteralPath $global:render
}}
"""
    for name, render, env, args in SCENARIOS:
        path = q(renders[render]) if render in renders else "'fail'"
        deploy = base / f"deploy-{name}"
        deploy.mkdir()
        (deploy / ".env").write_text(env, encoding="utf-8")
        body += f"""
$global:render = {path}
Write-Output '<<<{name}>>>'
& {q(SCRIPT)} -LogDir {q(base / 'logs')} -DeployDir {q(deploy)} {args} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        code = int(text.rsplit("EXIT=", 1)[1].split()[0])
        lists = {}
        for key in ("build", "recreate"):
            line = next((ln for ln in text.splitlines() if re.match(rf"\s*{key}\s*:", ln)), "")
            lists[key] = [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()] if line else []
        out[name] = (code, lists, text)
    return out


def test_every_service_the_compose_file_builds_is_in_the_plan(plans):
    code, lists, text = plans["real"]
    assert code == 0, text
    services = real_stack()["services"]
    built = {n for n, s in services.items() if "build" in s}
    images = {s["image"] for n, s in services.items() if "build" in s and "image" in s}
    sharers = {n for n, s in services.items() if "build" not in s and s.get("image") in images}
    assert set(lists["build"]) == built, text
    assert set(lists["recreate"]) == sharers, text
    assert not (set(lists["build"]) | set(lists["recreate"])) & {
        n for n, s in services.items() if "build" not in s and n not in sharers}   # pulled images
    if "gateway" in built:
        assert lists["build"][-1] == "gateway"


def test_a_shared_image_builds_first_and_the_front_door_last(plans):
    code, lists, text = plans["synthetic"]
    assert code == 0, text
    assert lists["build"] == ["alpha", "zeta", "gateway"], text
    assert lists["recreate"] == ["sharer"], text


def test_a_comma_joined_service_list_is_split_and_a_half_pair_warns(plans):
    code, lists, text = plans["comma-joined"]
    assert code == 0, text
    assert lists["build"] == ["zeta"] and lists["recreate"] == ["sharer"], text
    assert "recreating sharer without rebuilding alpha" in text


def test_an_unknown_service_fails(plans):
    code, _, text = plans["unknown"]
    assert code == 2 and "nosuch" in text, text


def test_a_failed_compose_render_fails_instead_of_planning_nothing(plans):
    code, lists, text = plans["config-fails"]
    assert code == 2, text
    assert "could not read the stack" in text and "env file" in text
    assert lists == {"build": [], "recreate": []}



def test_a_rail_disabled_on_this_box_is_left_out_of_the_plan_and_named(plans):
    """`up -d --build` would START it on a box that declared it does not run it."""
    code, lists, text = plans["disabled"]
    assert code == 0, text
    assert "zeta" not in lists["build"] and lists["build"] == ["alpha", "gateway"], text
    assert "skipped : zeta (zeta is disabled on this box)" in text
    # ...and the dry run says it would park the running container.
    assert "would set restart=no on platform-zeta-1" in text and "would stop platform-zeta-1" in text


def test_asking_for_a_disabled_rail_by_name_is_refused(plans):
    code, _, text = plans["disabled-asked-for"]
    assert code == 2, text
    assert "disabled on this box by PLATFORM_DISABLED_APPS: zeta (zeta)" in text


# --- a podman box on the lean stack ---------------------------------------------------------------

def lean_render(root: Path, outside: Path) -> dict:
    """compose config's shape on a lean box: absolute build contexts, services in compose's
    alphabetical order. openmaic-app is built by a box override from a checkout OUTSIDE this one;
    zeta is disabled on the box."""
    return {"services": {
        "ai-playground": {"build": {"context": str(root / "rails" / "ai-playground")}, "image": "ai-playground:latest"},
        "caddy": {"image": "docker.io/library/caddy:2"},
        "gateway": {"build": {"context": str(root)}, "image": "platform-gateway:latest"},
        "openmaic-app": {"build": {"context": str(outside / "OpenMAIC")}, "image": "openmaic-app:latest"},
        "terminal-fun": {"build": {"context": str(root / "rails" / "terminal-fun")}, "image": "terminal-fun-backend:latest"},
        "zeta": {"build": {"context": str(root / "rails" / "zeta")}, "image": "zeta:latest"},
    }}


LEAN_FAKES = """
$ErrorActionPreference = 'Continue'
Remove-Item Env:DOCKER_HOST, Env:PLATFORM_ENABLED_APPS, Env:PLATFORM_DISABLED_APPS -ErrorAction SilentlyContinue
function global:Flat($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:docker {
  Add-Content -LiteralPath $global:calls -Value ('docker ' + ((Flat $args) -join ' '))
  $global:LASTEXITCODE = 1
  throw 'a podman box has no docker CLI'
}
function global:docker-compose {
  $argv = Flat $args
  Add-Content -LiteralPath $global:calls -Value ('docker-compose ' + ($argv -join ' ') + " | DOCKER_HOST=$env:DOCKER_HOST")
  $global:LASTEXITCODE = 0
  if ((($argv | Select-Object -Last 3) -join ' ') -eq 'config --format json') { Get-Content -Raw -LiteralPath $global:render }
}
function global:podman {
  $argv = Flat $args
  Add-Content -LiteralPath $global:calls -Value ('podman ' + ($argv -join ' '))
  # Where the script pointed its helpers' log and temp files, read from the script's own scope.
  Add-Content -LiteralPath $global:where -Value "$TempBase|$LogFile"
  $global:LASTEXITCODE = 0
  if ($argv[0] -eq 'ps') { 'platform-zeta-1 running'; return }
  if ($argv[0] -eq 'inspect' -and $argv[2] -eq '{{json .Config.Labels}}') {
    if ($global:gone) { 'Error: no such object: platform-gateway-1'; $global:LASTEXITCODE = 125; return }
    $global:labels; return
  }
  if ($argv[0] -eq 'inspect') { '/platform-zeta-1 always'; return }
}
"""


@pytest.fixture(scope="module")
def lean(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("lean")
    root = base / "repo"
    deploy = root / "deploy"
    (deploy / "installer").mkdir(parents=True)
    outside = base / "elsewhere"
    override = base / "box-local" / "compose.override.yml"
    override.parent.mkdir()
    override.write_text("services: {}\n", encoding="utf-8")
    (deploy / ".env").write_text(
        "PLATFORM_ENABLED_APPS=ai-playground,openmaic\nPLATFORM_DISABLED_APPS=zeta\n"
        f"PLATFORM_COMPOSE_OVERRIDE={override}\n", encoding="utf-8")
    render = base / "render.json"
    render.write_text(json.dumps(lean_render(root, outside)), encoding="utf-8")
    installer_yml = deploy / "installer" / "docker-compose.installer.yml"
    labels = json.dumps({"com.docker.compose.project": "platform",
                         "com.docker.compose.project.config_files": f"{installer_yml},{override}"})
    # rebuild-stale.ps1 builds $TempBase and $LogFile from USERPROFILE: a real run under the user's
    # own profile writes their real Temp.
    profile = base / "profile"
    (profile / "AppData" / "Local" / "Temp").mkdir(parents=True)
    body = LEAN_FAKES + (f"$env:USERPROFILE = {q(profile)}\n"
                         f"$global:render = {q(render)}\n$global:labels = {q(labels)}\n")
    runs = {
        # No -Stack: it is read off the gateway container's compose label (the podman fake).
        "run": "-RuntimeMode podman",
        "named": "-RuntimeMode podman -Stack lean -WhatIfOnly -Service openmaic-app",
        # No -Stack and no gateway container to read it from (after `compose down`).
        "nogateway": "-RuntimeMode podman -WhatIfOnly",
    }
    for name, args in runs.items():
        body += f"""
$global:gone = {'$true' if name == 'nogateway' else '$false'}
$global:calls = {q(base / f'calls-{name}.txt')}
$global:where = {q(base / f'where-{name}.txt')}
New-Item -ItemType File -Force -Path $global:calls | Out-Null
Write-Output '<<<{name}>>>'
& {q(SCRIPT)} -LogDir {q(base / 'logs')} -DeployDir {q(deploy)} {args} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        code = int(text.rsplit("EXIT=", 1)[1].split()[0])
        line = next((ln for ln in text.splitlines() if re.match(r"\s*build\s*:", ln)), "")
        build = [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()] if line else []
        calls = (base / f"calls-{name}.txt").read_text(encoding="utf-8").splitlines()
        out[name] = (code, build, text, calls)
    out["where"] = {name: (base / f"where-{name}.txt").read_text(encoding="utf-8").splitlines()
                    for name in runs if (base / f"where-{name}.txt").is_file()}
    out["profile"] = profile
    prefix = (f"--env-file {deploy / '.env'} -f {installer_yml} -f {override} "
              "--profile ai-playground --profile openmaic")
    return out, prefix, outside


PIPE = " | DOCKER_HOST=npipe:////./pipe/podman-machine-default"


def test_the_lean_runs_keep_the_helpers_log_and_temp_files_in_a_scratch_profile(lean):
    """The "run" case is a real run (no -WhatIfOnly). lib-runtime's helpers append to $LogFile and
    keep temp files under $TempBase (its contract), and rebuild-stale.ps1 builds both from
    USERPROFILE, so without a profile of the test's own they pointed at the user's real Temp."""
    out, _, _ = lean
    temp = str(out["profile"] / "AppData" / "Local" / "Temp")
    assert set(out["where"]) == {"run", "named", "nogateway"}, out["where"]
    for name, seen in out["where"].items():
        assert seen and all(s == f"{temp}|{temp}\\ai-platform-rebuild-stale.log" for s in seen), (name, seen)


def test_a_lean_podman_box_renders_and_builds_with_its_own_files_and_profiles(lean):
    out, prefix, _ = lean
    code, build, text, calls = out["run"]
    assert code == 0, text
    assert "stack   : lean (platform-gateway-1 was created from" in text, text
    compose = [c for c in calls if c.startswith("docker-compose ")]
    assert compose[0] == f"docker-compose {prefix} config --format json{PIPE}", compose
    assert build == ["ai-playground", "terminal-fun", "gateway"], text      # the front door last
    assert compose[1:] == [f"docker-compose {prefix} up -d --build --no-deps {s}{PIPE}" for s in build], compose


def test_a_lean_podman_box_parks_disabled_rails_with_podman_and_never_calls_docker(lean):
    out, _, _ = lean
    code, build, text, calls = out["run"]
    assert code == 0, text
    assert "zeta" not in build and "skipped : zeta (zeta is disabled on this box)" in text, text
    assert "podman update --restart=no platform-zeta-1" in calls, calls
    assert "podman stop platform-zeta-1" in calls, calls
    assert not [c for c in calls if c.startswith("docker ")], calls


def test_a_podman_box_with_no_gateway_container_renders_the_lean_stack(lean):
    """It rendered and built the FULL stack into podman: Resolve-PlatformStack said 'full'
    whenever there was no label to read."""
    out, prefix, _ = lean
    code, build, text, calls = out["nogateway"]
    assert code == 0, text
    assert "stack   : lean (no platform-gateway-1 to read" in text, text
    compose = [c for c in calls if c.startswith("docker-compose ")]
    assert compose == [f"docker-compose {prefix} config --format json{PIPE}"], compose


def test_a_service_built_outside_the_checkout_is_skipped_unless_named(lean):
    out, prefix, outside = lean
    code, build, text, _ = out["run"]
    assert "openmaic-app" not in build, text
    assert f"openmaic-app (external build context {outside / 'OpenMAIC'};" in text, text
    code, build, text, calls = out["named"]
    assert code == 0 and build == ["openmaic-app"], text
    assert "external build context" not in text, text
    assert [c for c in calls if c.startswith("docker-compose ")] == [
        f"docker-compose {prefix} config --format json{PIPE}"], calls


STACK_PROBE = r"""
$global:asked = @()
function global:Answer {
  $global:asked += ,(@($args | ForEach-Object { [string]$_ }) -join ' ')
  $global:LASTEXITCODE = 0
  switch ($global:case) {
    'lean'    { '{"com.docker.compose.project.config_files":"C:\\r\\deploy\\installer\\docker-compose.installer.yml,C:\\box\\override.yml"}' }
    'full'    { '{"com.docker.compose.project.config_files":"C:\\r\\deploy\\docker-compose.yml"}' }
    'other'   { '{"com.docker.compose.project.config_files":"C:\\x\\compose.yaml"}' }
    'nolabel' { '{}' }
    'gone'    { 'Error: no such container platform-gateway-1'; $global:LASTEXITCODE = 125 }
    'nocli'   { throw [System.Management.Automation.CommandNotFoundException]::new('the term is not recognized') }
  }
}
function global:podman { Answer @args }
function global:docker { Answer @args }
function global:wsl.exe { if ($args[0] -eq 'docker') { Answer @($args | Select-Object -Skip 1) } }
$out = [ordered]@{}
foreach ($m in 'podman', 'desktop', 'wsl') {
  foreach ($c in 'lean', 'full', 'other', 'nolabel', 'gone', 'nocli') {
    $global:case = $c
    $r = Resolve-PlatformStack -Mode $m
    $out["$m-$c"] = @{ stack = $r.Stack; reason = $r.Reason; proven = $r.Proven }
  }
}
$out['asked'] = $global:asked[0]
$out | ConvertTo-Json -Compress -Depth 4
"""


@pytest.fixture(scope="module")
def stacks(run_ps, shell):
    lib = REPO / "deploy" / "installer" / "lib-runtime.ps1"
    body = (f"$ErrorActionPreference = 'Stop'\n$Root = 'C:\\r'; $Installer = 'C:\\r\\deploy\\installer'\n"
            f"$TempBase = $env:TEMP; $RuntimeMode = 'podman'; $LogFile = Join-Path $env:TEMP 'stack-test.log'\n"
            f"function Write-Log($m) {{ }}\n. {q(lib)}\n" + STACK_PROBE)
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


CASES = ("lean", "full", "other", "nolabel", "gone")


def test_resolve_platform_stack_reads_the_gateways_compose_label(stacks):
    got = stacks
    assert got["asked"] == "inspect --format {{json .Config.Labels}} platform-gateway-1", got
    for m in ("podman", "desktop", "wsl"):
        assert got[f"{m}-lean"]["stack"] == "lean" and got[f"{m}-full"]["stack"] == "full", got
        assert got[f"{m}-lean"]["proven"] is True and got[f"{m}-full"]["proven"] is True, got
        assert "docker-compose.installer.yml" in got[f"{m}-lean"]["reason"], got
        assert "neither stack's file" in got[f"{m}-other"]["reason"], got
        assert "inspect exit 125" in got[f"{m}-gone"]["reason"], got
        for c in ("other", "nolabel", "gone", "nocli"):
            assert got[f"{m}-{c}"]["proven"] is False, (m, c, got)
        # A CLI that is not there is a fallback, not a throw into the caller (platform-startup's
        # trap ended the startup on one).
        assert got[f"{m}-nocli"]["stack"] == ("full" if m == "desktop" else "lean"), got
        assert "not recognized" in got[f"{m}-nocli"]["reason"], got


def test_without_a_label_podman_means_the_lean_stack(stacks):
    """podman runs only the lean stack. 'full' here had rebuild-stale build the full stack into a
    lean box's podman machine whenever its gateway container was gone."""
    assert {c: stacks[f"podman-{c}"]["stack"] for c in ("other", "nolabel", "gone")} == {
        "other": "lean", "nolabel": "lean", "gone": "lean"}, stacks
    assert "the only one podman runs" in stacks["podman-gone"]["reason"], stacks


def test_without_a_label_wsl_means_the_lean_stack(stacks):
    """Docker inside WSL runs only the lean stack too (the installer's wsl mode; the full stack is
    Docker Desktop's), and 'full' there was the same wrong build (delta audit 2026-10-15)."""
    assert {c: stacks[f"wsl-{c}"]["stack"] for c in ("other", "nolabel", "gone", "nocli")} == {
        "other": "lean", "nolabel": "lean", "gone": "lean", "nocli": "lean"}, stacks
    assert "the only one a WSL engine runs" in stacks["wsl-gone"]["reason"], stacks


def test_without_a_label_docker_still_means_the_full_stack(stacks):
    """The full stack's Docker Desktop path is what it was."""
    assert {c: stacks[f"desktop-{c}"]["stack"] for c in ("other", "nolabel", "gone")} == {
        "other": "full", "nolabel": "full", "gone": "full"}, stacks


# --- a runtime that is only a guess is refused ----------------------------------------------------

def guess_run(shell: str, tmp_path: Path, clis: dict[str, str], env_text: str, extra: list[str]):
    """The REAL script as its own process, with fake CLIs (.cmd files recording every call) on a PATH
    holding nothing else but System32 and the engine, so Resolve-PlatformRuntime's own probes run."""
    deploy = tmp_path / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    shutil.copy2(SCRIPT, deploy / SCRIPT.name)
    shutil.copy2(REPO / "deploy" / "installer" / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    (deploy / ".env").write_text(env_text, encoding="utf-8")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "render.json").write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    for name, body in clis.items():
        (fake / f"{name}.cmd").write_bytes(
            (f'@echo off\r\n>> "%~dp0calls.txt" echo {name} %*\r\n' + body).encode("ascii"))
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = dict(os.environ, PATH=os.pathsep.join([str(fake), system32, os.path.dirname(shell)]))
    env.pop("DOCKER_HOST", None)
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(deploy / SCRIPT.name), "-WhatIfOnly", *extra],
                       capture_output=True, encoding="utf-8", errors="replace", env=env, cwd=tmp_path,
                       timeout=180)
    calls = (fake / "calls.txt").read_text(encoding="ascii", errors="replace").splitlines() \
        if (fake / "calls.txt").exists() else []
    return r, calls


LISTS_NOTHING = "exit /b 0\r\n"
RENDERS = 'if "%1"=="compose" ( type "%~dp0render.json" & exit /b 0 )\r\nexit /b 0\r\n'


@pytest.mark.parametrize("clis", [{"podman": LISTS_NOTHING, "docker": LISTS_NOTHING}, {"podman": LISTS_NOTHING}],
                         ids=["both", "podman-only"])
def test_an_undeclared_runtime_no_cli_proves_is_refused(shell, tmp_path, clis):
    r, calls = guess_run(shell, tmp_path, clis, "", [])
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "[FAIL] the runtime is a guess" in out and "-RuntimeMode" in out, out
    assert calls and all(" ps " in c for c in calls), calls      # it asked, then did nothing else


def test_docker_as_the_only_engine_cli_plans_docker_desktop_with_no_gateway(shell, tmp_path):
    """A full-stack box declares no runtime (only the lean writers set PLATFORM_RUNTIME), so with
    the stack down nothing lists the gateway; refused as a guess, its rebuild never ran and the daily
    prune after the refusal never either. docker alone is Docker Desktop (delta audit 2026-10-15)."""
    r, calls = guess_run(shell, tmp_path, {"docker": RENDERS}, "", ["-Stack", "full"])
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "runtime : desktop (" in out and "docker is the only engine CLI on PATH" in out, out
    assert "[FAIL]" not in out and "build   : alpha, zeta, gateway" in r.stdout, out


def test_a_declared_runtime_is_not_a_guess(shell, tmp_path):
    r, calls = guess_run(shell, tmp_path, {"docker": RENDERS}, "PLATFORM_RUNTIME=desktop\n", ["-Stack", "full"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "build   : alpha, zeta, gateway" in r.stdout, r.stdout + r.stderr


def test_a_runtime_whose_cli_lists_the_gateway_is_not_a_guess(shell, tmp_path):
    lists = 'if "%1"=="ps" ( echo platform-gateway-1 exited& exit /b 0 )\r\n' + RENDERS
    r, calls = guess_run(shell, tmp_path, {"docker": lists}, "", ["-Stack", "full"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "build   : alpha, zeta, gateway" in r.stdout, r.stdout + r.stderr


def test_run_with_file_finds_its_own_folder(shell, tmp_path):
    """`powershell -File deploy\\rebuild-stale.ps1` with no -DeployDir or -LogDir, as it is run by
    hand. Windows PowerShell 5.1 hands an ADVANCED script's param() defaults an empty $PSScriptRoot,
    so defaults built from it there threw before the script began; they are set in the body."""
    deploy = tmp_path / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    shutil.copy2(SCRIPT, deploy / SCRIPT.name)
    shutil.copy2(REPO / "deploy" / "installer" / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    (deploy / ".env").write_text("", encoding="utf-8")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "render.json").write_text(json.dumps(SYNTHETIC), encoding="utf-8")
    (fake / "docker.cmd").write_bytes(
        b'@echo off\r\nif "%1"=="compose" ( type "%~dp0render.json" & exit /b 0 )\r\nexit /b 0\r\n')
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = dict(os.environ, PATH=os.pathsep.join([str(fake), system32, os.path.dirname(shell)]))
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(deploy / SCRIPT.name), "-WhatIfOnly", "-RuntimeMode", "desktop", "-Stack", "full"],
                       capture_output=True, encoding="utf-8", errors="replace", env=env, cwd=tmp_path,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "build   : alpha, zeta, gateway" in r.stdout, r.stdout + r.stderr
    assert (deploy / "logs").is_dir(), r.stdout
