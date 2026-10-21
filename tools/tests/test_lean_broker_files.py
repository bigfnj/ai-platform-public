"""A lean box keeps its role map and disabled list in deploy\\local\\, never in tracked files.

The lean installer copied roles.lean.json over the TRACKED services/broker/roles.json, and with no
BROKER_ROLES_FILE the broker wrote every Admin > Rails repoint and disabled model into the tracked
roles.json and disabled.json. get.ps1's update is a `git reset --hard`, so re-running the one-liner
(which it tells its users to do) put the 24 GB map back and dropped every admin edit, hot-read by
the broker at once. Now install.ps1 and apply's roles step write deploy\\local\\roles.json
(deploy\\.gitignore ignores the folder), the lean broker service is registered with
BROKER_ROLES_FILE / BROKER_DISABLED_FILE naming deploy\\local, and get.ps1 refuses to reset a box
whose tracked broker files carry edits.

Everything runs the real scripts under both engines from scratch trees; get.ps1 updates a scratch
clone of a scratch origin, so nothing is fetched from anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Whether a tree's own .gitignore chain ignores a path, asked without its checkout; shared with
# rails/ai-voice's test_db_connections.py.
from own_ignore_rules import ignored_by_its_own_rules

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


TRACKED_ROLES = '{\n  "chat": "mistral-small3*:24b",\n  "recipe": "gemma4*:26b"\n}\n'
BOX_ROLES = '{\n  "chat": "gemma3:4b",\n  "recipe": "qwen3:1.7b"\n}\n'


def broker_tree(root: Path, tracked_roles: str | None = TRACKED_ROLES, tracked_disabled: str | None = "[]\n") -> Path:
    (root / "services" / "broker").mkdir(parents=True)
    (root / "deploy").mkdir(exist_ok=True)
    if tracked_roles is not None:
        (root / "services" / "broker" / "roles.json").write_text(tracked_roles, encoding="utf-8", newline="\n")
    if tracked_disabled is not None:
        (root / "services" / "broker" / "disabled.json").write_text(tracked_disabled, encoding="utf-8", newline="\n")
    return root


# --- the service spec and the seeding helper (lib-runtime) ----------------------------------------

@pytest.fixture(scope="module")
def lib(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("lbf")
    roots = {
        # An old lean box: the tracked files hold its lean map and a disabled model.
        "old-box": broker_tree(base / "old-box", BOX_ROLES, '["llava:7b"]\n'),
        # Already migrated: its own files exist and must not be overwritten.
        "migrated": broker_tree(base / "migrated"),
        # No tracked files at all.
        "bare": broker_tree(base / "bare", None, None),
    }
    (roots["migrated"] / "deploy" / "local").mkdir()
    (roots["migrated"] / "deploy" / "local" / "roles.json").write_text(BOX_ROLES, encoding="utf-8")
    (roots["migrated"] / "deploy" / "local" / "disabled.json").write_text('["phi3:mini"]\n', encoding="utf-8")
    body = f"$ErrorActionPreference = 'Stop'\n. {q(INSTALLER / 'lib-runtime.ps1')}\n$out = [ordered]@{{}}\n"
    for name, root in roots.items():
        body += f"""
$out['{name}'] = [ordered]@{{
  spec = (Get-BrokerServiceSpec -BindHost 127.0.0.1 -Token tok -Root {q(root)}).Environment
  first = @(Initialize-LeanBrokerFiles -Root {q(root)})
  second = @(Initialize-LeanBrokerFiles -Root {q(root)})
}}
"""
    body += "$out | ConvertTo-Json -Depth 4 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), roots


def test_the_lean_broker_reads_its_maps_from_deploy_local(lib):
    got, roots = lib
    local = roots["old-box"] / "deploy" / "local"
    assert got["old-box"]["spec"] == ["BROKER_MEDIA_ENABLED=false", "BROKER_AUTH_TOKEN=tok",
                                      f"BROKER_ROLES_FILE={local / 'roles.json'}",
                                      f"BROKER_DISABLED_FILE={local / 'disabled.json'}"], got


def test_a_missing_box_file_is_seeded_from_what_the_broker_reads_now(lib):
    got, roots = lib
    root = roots["old-box"]
    local = root / "deploy" / "local"
    assert (local / "roles.json").read_bytes() == (root / "services" / "broker" / "roles.json").read_bytes()
    assert json.loads((local / "disabled.json").read_text(encoding="utf-8")) == ["llava:7b"]
    assert len(got["old-box"]["first"]) == 2 and got["old-box"]["second"] == [], got
    # The tracked files are only read.
    assert (root / "services" / "broker" / "roles.json").read_text(encoding="utf-8") == BOX_ROLES


def test_an_existing_box_file_is_never_overwritten(lib):
    got, roots = lib
    local = roots["migrated"] / "deploy" / "local"
    assert got["migrated"]["first"] == [], got
    assert (local / "roles.json").read_text(encoding="utf-8") == BOX_ROLES
    assert json.loads((local / "disabled.json").read_text(encoding="utf-8")) == ["phi3:mini"]


def test_with_no_tracked_files_the_box_files_start_empty(lib):
    _, roots = lib
    local = roots["bare"] / "deploy" / "local"
    assert json.loads((local / "roles.json").read_text(encoding="utf-8")) == {}
    assert json.loads((local / "disabled.json").read_text(encoding="utf-8")) == []
    assert not (local / "roles.json").read_bytes().startswith(b"\xef\xbb\xbf")


def test_deploy_local_is_ignored_where_the_snapshot_ships_it(tmp_path):
    """deploy/.gitignore ships with deploy/ (publish.json includes the folder wholesale), so the
    rule reaches a public clone too, which is the clone get.ps1 resets. Asked of the rules this
    tree holds, so it runs in the snapshot CI builds, which is not a checkout."""
    rules = (REPO / "deploy" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "local/" in rules, rules
    assert ignored_by_its_own_rules(REPO, "deploy/local/roles.json", tmp_path), \
        "git does not ignore deploy/local/roles.json by the rules this tree ships"


def test_only_a_trees_own_rules_are_credited_to_it(tmp_path, monkeypatch):
    """A box whose own git setup ignores local/ (a global config seeding info/exclude, a default
    excludes file) passed the old check, a check-ignore in the checkout, for a tree that ships
    no such rule. Here the box's rules are shown to be real, and still not credited."""
    tree = tmp_path / "tree"
    (tree / "deploy").mkdir(parents=True)
    (tree / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tree / "deploy" / ".gitignore").write_text("bin/\n", encoding="utf-8")
    template = tmp_path / "template"
    (template / "info").mkdir(parents=True)
    (template / "info" / "exclude").write_text("local/\n", encoding="utf-8")
    config = tmp_path / "box.gitconfig"
    config.write_text(f"[init]\n\ttemplateDir = {template.as_posix()}\n", encoding="utf-8")
    (tmp_path / "xdg" / "git").mkdir(parents=True)
    (tmp_path / "xdg" / "git" / "ignore").write_text("local/\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    subprocess.run(["git", "init", "-q", str(tree)], check=True, timeout=60, capture_output=True)
    asked = subprocess.run(["git", "-C", str(tree), "check-ignore", "-q", "deploy/local/roles.json"],
                           capture_output=True, timeout=60)
    assert asked.returncode == 0, "the box's own rules do not ignore it, so this proves nothing"
    assert not ignored_by_its_own_rules(tree, "deploy/local/roles.json", tmp_path / "a")
    (tree / "deploy" / ".gitignore").write_text("bin/\nlocal/\n", encoding="utf-8")
    assert ignored_by_its_own_rules(tree, "deploy/local/roles.json", tmp_path / "b")


# --- install-native -PlanOnly names them --------------------------------------------------------

@pytest.fixture(scope="module")
def plan(run_ps, shell, tmp_path_factory):
    root = broker_tree(tmp_path_factory.mktemp("lbf-plan") / "repo")
    (root / "deploy" / "installer").mkdir()
    shutil.copy2(INSTALLER / "install-native.ps1", root / "deploy" / "installer" / "install-native.ps1")
    # This box's own registered broker is never read: no installed service to carry from.
    (root / "deploy" / "installer" / "lib-runtime.ps1").write_text(
        (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8")
        + "\nfunction Get-InstalledBrokerEnv { param([string]$Key) @() }\n", encoding="utf-8-sig")
    (root / "deploy" / ".env").write_text("PLATFORM_RUNTIME=podman\n", encoding="utf-8")
    body = f"& {q(root / 'deploy' / 'installer' / 'install-native.ps1')} -PlatformRoot {q(root)} -SkipOllama -PlanOnly 3>$null | Select-Object -Last 1\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), root


def test_the_plan_registers_the_box_files_and_creates_nothing(plan):
    p, root = plan
    assert {"BROKER_ROLES_FILE", "BROKER_DISABLED_FILE"} <= set(p["EnvironmentNames"]), p
    assert p["BrokerFiles"]["Roles"] == str(root / "deploy" / "local" / "roles.json"), p
    assert p["BrokerFiles"]["Disabled"] == str(root / "deploy" / "local" / "disabled.json"), p
    assert p["BrokerFiles"]["RolesExists"] is False, p
    assert not (root / "deploy" / "local").exists()          # -PlanOnly writes no map


# --- install.ps1 -Provision writes the lean map to deploy\local -----------------------------------

@pytest.fixture(scope="module")
def provision(run_ps, shell, tmp_path_factory):
    """The REAL install.ps1 -Provision (desktop, so no container probe runs), stopped at its
    elevated step by a Start-Process function that throws (a function wins over the cmdlet)."""
    base = tmp_path_factory.mktemp("lbf-install")
    root = broker_tree(base / "repo")
    (root / "deploy" / "installer").mkdir()
    for f in ("install.ps1", "lib-runtime.ps1", "env.lean.example", "roles.lean.json"):
        shutil.copy2(INSTALLER / f, root / "deploy" / "installer" / f)
    prof = base / "profile"
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
    body = f"""
$ErrorActionPreference = 'Continue'
function global:Start-Process {{ throw 'stub: stopped at the elevated step' }}
$env:USERPROFILE = {q(prof)}
& {q(root / 'deploy' / 'installer' / 'install.ps1')} -Provision -RuntimeMode desktop -AdminUser a `
  -AdminPass b -EnabledApps terminal-fun *>&1 | Out-Null
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    fail = prof / "AppData" / "Local" / "Temp" / "ai-platform-install.fail"
    return root, (fail.read_text(encoding="utf-8-sig") if fail.exists() else "")


def test_install_writes_the_lean_map_to_the_box_file_and_leaves_the_tracked_one(provision):
    root, fail = provision
    assert "stopped at the elevated step" in fail, fail          # it got that far, no further
    local = root / "deploy" / "local"
    assert (local / "roles.json").read_bytes() == (INSTALLER / "roles.lean.json").read_bytes()
    assert (root / "services" / "broker" / "roles.json").read_text(encoding="utf-8") == TRACKED_ROLES
    assert json.loads((local / "disabled.json").read_text(encoding="utf-8")) == []


# --- apply's roles step writes deploy\local --------------------------------------------------------

@pytest.fixture(scope="module")
def apply_roles(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("lbf-apply")
    roots = {"old-box": broker_tree(base / "old-box", BOX_ROLES), "migrated": broker_tree(base / "migrated")}
    (roots["migrated"] / "deploy" / "local").mkdir()
    (roots["migrated"] / "deploy" / "local" / "roles.json").write_text(
        '{\n  "chat": "box-own:2b"\n}\n', encoding="utf-8")
    body = f"$ErrorActionPreference = 'Stop'\n"
    for name, root in roots.items():
        body += f"""
& {{
  . {q(INSTALLER / 'lib-modelplan.ps1')}
  . {q(INSTALLER / 'lib-apply.ps1')}
  $plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 8192 -Installed @{{}}
  $acts = @(Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode podman -Root {q(root)})
  & ($acts | Where-Object Id -eq 'roles').Do
}}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return roots


def test_the_apply_roles_step_writes_the_box_file_over_what_the_broker_reads_now(apply_roles):
    old = apply_roles["old-box"]
    written = json.loads((old / "deploy" / "local" / "roles.json").read_text(encoding="utf-8"))
    assert written["chat"] == "gemma3:4b", written           # the box's map, not the 24 GB one
    assert (old / "services" / "broker" / "roles.json").read_text(encoding="utf-8") == BOX_ROLES
    migrated = apply_roles["migrated"]
    written = json.loads((migrated / "deploy" / "local" / "roles.json").read_text(encoding="utf-8"))
    assert written["chat"] == "box-own:2b", written          # the box file is the base once it exists
    assert (migrated / "services" / "broker" / "roles.json").read_text(encoding="utf-8") == TRACKED_ROLES


def test_the_model_plan_defaults_come_from_the_box_file_when_it_exists(run_ps, shell, tmp_path):
    root = broker_tree(tmp_path / "repo")
    (root / "deploy" / "installer").mkdir()
    for f in ("lib-modelplan.ps1", "model-catalog.json"):
        shutil.copy2(INSTALLER / f, root / "deploy" / "installer" / f)
    lib = q(root / "deploy" / "installer" / "lib-modelplan.ps1")
    body = f"""
$ErrorActionPreference = 'Stop'
$a = & {{ . {lib}; Get-RoleDefaults }}
New-Item -ItemType Directory -Force -Path {q(root / 'deploy' / 'local')} | Out-Null
[IO.File]::WriteAllText({q(root / 'deploy' / 'local' / 'roles.json')}, '{{"chat": "gemma3:4b"}}')
$b = & {{ . {lib}; Get-RoleDefaults }}
@{{ tracked = $a; local = $b }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["tracked"]["chat"] == "mistral-small3*:24b", got
    assert got["local"] == {"chat": "gemma3:4b"}, got


# --- get.ps1 refuses to reset a box whose tracked broker files carry edits --------------------------

def git(*args, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


STUB_INSTALLER = "param([switch]$Check, [switch]$Console)\n'STUB-INSTALLER'\n"


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """A scratch origin with two commits, the second the 'update' get.ps1 fetches."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    src = tmp_path_factory.mktemp("lbf-origin") / "origin"
    broker_tree(src)
    (src / "deploy" / "installer").mkdir()
    (src / "deploy" / "installer" / "install.ps1").write_text(STUB_INSTALLER, encoding="utf-8")
    shutil.copy2(REPO / "deploy" / ".gitignore", src / "deploy" / ".gitignore")
    git("init", "-q", "-b", "main", cwd=src)
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("core.autocrlf", "false")):
        git("config", k, v, cwd=src)
    git("add", "-A", cwd=src)
    git("commit", "-q", "-m", "A", cwd=src)
    first = git("rev-parse", "HEAD", cwd=src)
    (src / "README.md").write_text("update\n", encoding="utf-8")
    git("add", "README.md", cwd=src)
    git("commit", "-q", "-m", "B", cwd=src)
    return src, first, git("rev-parse", "HEAD", cwd=src)


def clone_at(origin_dir: Path, first: str, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", "--no-checkout", origin_dir.as_uri(), str(dest)], check=True)
    git("-c", "core.autocrlf=false", "checkout", "-q", first, cwd=dest)
    git("config", "core.autocrlf", "false", cwd=dest)
    return dest


def run_get(shell: str, clone: Path, tmp: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, AIPLATFORM_DIR=str(clone), AIPLATFORM_REF="main",
               GIT_CONFIG_GLOBAL=str(tmp / "gitconfig"))        # get.ps1 sets core.longpaths --global
    return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "get.ps1")],
                          input="\nQ\n", capture_output=True, encoding="utf-8", errors="replace",
                          env=env, timeout=180)


def test_get_refuses_to_reset_tracked_broker_edits_and_says_how_to_keep_them(origin, shell, tmp_path):
    src, first, _ = origin
    clone = clone_at(src, first, tmp_path / "box")
    rp = clone / "services" / "broker" / "roles.json"
    rp.write_text(BOX_ROLES, encoding="utf-8")                # what an old lean install left there
    r = run_get(shell, clone, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert git("rev-parse", "HEAD", cwd=clone) == first, out  # not updated
    assert rp.read_text(encoding="utf-8") == BOX_ROLES        # not reset
    assert "services/broker/roles.json" in out and "deploy\\local" in out, out
    assert f"git -C '{clone}' checkout -- services/broker" in out, out
    assert "install-native.ps1" in out, out


def test_get_updates_a_clean_box_and_leaves_its_own_map_in_effect(origin, shell, tmp_path):
    src, first, second = origin
    clone = clone_at(src, first, tmp_path / "box")
    local = clone / "deploy" / "local"
    local.mkdir()
    (local / "roles.json").write_text(BOX_ROLES, encoding="utf-8")
    r = run_get(shell, clone, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert git("rev-parse", "HEAD", cwd=clone) == second, out  # updated
    assert "STUB-INSTALLER" in out, out                        # and went on to the doctor
    assert (local / "roles.json").read_text(encoding="utf-8") == BOX_ROLES
    assert git("status", "--porcelain", cwd=clone) == "", "deploy/local shows as untracked"
