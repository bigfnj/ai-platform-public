"""The box-local compose override: deploy/installer/lib-runtime.ps1 Get-ComposeFiles and its callers.

A machine names its own extra compose file in deploy/.env (PLATFORM_COMPOSE_OVERRIDE), and every
path that brings the stack up must pass it as a second -f. The failure this guards is quiet: a
caller that hand-builds a single -f brings the stack up WITHOUT the override, nothing errors, and
whatever the override added (a bind-mounted dist, a gateway env var) is simply gone after the next
reboot or watchdog restart.

Run under Windows PowerShell 5.1 (powershell.exe), not pwsh, because that is what the logon
startup and the watchdog run under. Skipped where there is no powershell.exe.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
PS = shutil.which("powershell.exe")

pytestmark = pytest.mark.skipif(PS is None, reason="needs Windows PowerShell 5.1")


def ps(script: str, tmp_path: Path) -> str:
    f = tmp_path / "harness.ps1"
    # UTF-8 out, decoded as UTF-8: 5.1 otherwise writes the OEM code page, and a non-ASCII temp or
    # profile path came back mangled (é as ‚), failing the test on such a box for no real reason.
    f.write_text("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script,
                 encoding="utf-8-sig")   # 5.1 reads a BOM-less .ps1 as ANSI
    r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(f)],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    return r.stdout


def q(p: Path | str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(p).replace("'", "''") + "'"


def make_root(tmp_path: Path, env: str) -> Path:
    root = tmp_path / "repo"
    (root / "deploy" / "installer").mkdir(parents=True)
    (root / "deploy" / ".env").write_text(env, encoding="utf-8")
    return root


def compose_paths(tmp_path: Path, root: Path, mode: str = "podman") -> dict:
    out = ps(f"""
$ErrorActionPreference = 'Stop'
$Root = {q(root)}
$Installer = Join-Path $Root 'deploy\\installer'
$LogFile = Join-Path $Root 'log.txt'
$TempBase = $Root
$RuntimeMode = '{mode}'
function Write-Log($m) {{ Add-Content -LiteralPath $LogFile -Value $m -Encoding utf8 }}
. {q(INSTALLER / 'lib-runtime.ps1')}
$p = Get-ComposePaths
@{{ Files = @($p.Files); FileArgs = @($p.FileArgs); Compose = $p.Compose; Env = $p.Env }} | ConvertTo-Json -Compress
""", tmp_path)
    data = json.loads(out.strip().splitlines()[-1])
    log = root / "log.txt"
    data["log"] = log.read_text(encoding="utf-8-sig") if log.exists() else ""
    return data


def test_no_override_is_one_file(tmp_path):
    root = make_root(tmp_path, "PLATFORM_ENABLED_APPS=terminal-fun\n")
    p = compose_paths(tmp_path, root)
    base = str(root / "deploy" / "installer" / "docker-compose.installer.yml")
    assert p["Files"] == [base]
    assert p["FileArgs"] == ["-f", base]
    assert p["log"] == ""


def test_an_existing_override_is_the_second_file(tmp_path):
    over = tmp_path / "box" / "compose.override.yml"
    over.parent.mkdir()
    over.write_text("services: {}\n", encoding="utf-8")
    root = make_root(tmp_path, f"A=1\nPLATFORM_COMPOSE_OVERRIDE={over}\nB=2\n")
    p = compose_paths(tmp_path, root)
    assert p["Files"][1] == str(over)
    assert p["FileArgs"] == ["-f", p["Files"][0], "-f", str(over)]


def test_a_missing_override_is_skipped_and_logged(tmp_path):
    over = tmp_path / "nope" / "compose.override.yml"
    root = make_root(tmp_path, f"PLATFORM_COMPOSE_OVERRIDE={over}\n")
    p = compose_paths(tmp_path, root)
    assert len(p["Files"]) == 1
    assert "WARNING" in p["log"] and str(over) in p["log"]


def test_quotes_are_stripped_and_the_last_assignment_wins(tmp_path):
    first, last = tmp_path / "first.yml", tmp_path / "last one.yml"
    for f in (first, last):
        f.write_text("services: {}\n", encoding="utf-8")
    root = make_root(tmp_path, f"PLATFORM_COMPOSE_OVERRIDE={first}\n"
                               f"PLATFORM_COMPOSE_OVERRIDE=\"{last}\"\n")
    assert compose_paths(tmp_path, root)["Files"][1] == str(last)


def test_a_relative_override_resolves_against_the_repo_root(tmp_path):
    root = make_root(tmp_path, "PLATFORM_COMPOSE_OVERRIDE=local\\over.yml\n")
    (root / "local").mkdir()
    (root / "local" / "over.yml").write_text("services: {}\n", encoding="utf-8")
    assert compose_paths(tmp_path, root)["Files"][1] == str(root / "local" / "over.yml")


def test_an_empty_value_is_no_override(tmp_path):
    root = make_root(tmp_path, "PLATFORM_COMPOSE_OVERRIDE=\n")
    p = compose_paths(tmp_path, root)
    assert len(p["Files"]) == 1 and p["log"] == ""


def test_an_inline_comment_and_a_non_ascii_path_are_read_like_compose(tmp_path):
    """compose drops an unquoted value's ` # comment`, and every writer produces UTF-8 without a
    BOM, which 5.1 reads as ANSI unless told otherwise: either way the override went Missing."""
    d = tmp_path / "José"
    d.mkdir()
    over = d / "over.yml"
    over.write_text("services: {}\n", encoding="utf-8")
    root = make_root(tmp_path, f"PLATFORM_COMPOSE_OVERRIDE={over}  # this box\n")
    p = compose_paths(tmp_path, root)
    assert p["Files"][1] == str(over) and p["log"] == ""


def test_every_compose_up_caller_uses_file_args():
    """Every line that hands compose an --env-file must hand it FileArgs too: a hand-built
    `'-f', <one file>` is exactly the regression, it drops the override. The first version of
    this test only looked for the two strings anywhere in the file, and still passed with
    smoke-test's config line reverted to a single -f."""
    for name in ("install.ps1", "platform-startup.ps1", "smoke-test.ps1"):
        lines = [ln for ln in (INSTALLER / name).read_text(encoding="utf-8").splitlines()
                 if "'--env-file'" in ln and not ln.lstrip().startswith("#")]
        assert lines, f"{name}: no compose --env-file line; this test needs updating"
        for ln in lines:
            assert "FileArgs" in ln or "fileArgs" in ln, f"{name}: no FileArgs in: {ln.strip()}"


def test_the_wsl_logon_script_matches_the_powershell_paths():
    """platform-startup.sh is the logon path on a WSL box. It passed a single -f, so that box lost
    its override at every logon, and its profile list stopped at two rails, so it lost five more."""
    sh = (INSTALLER / "platform-startup.sh").read_text(encoding="utf-8")
    up = [ln for ln in sh.splitlines() if "docker compose" in ln and " up " in ln]
    assert up and all('"${OVERRIDE_ARGS[@]}"' in ln for ln in up)
    rt = (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8")
    ps_profiles = sorted(re.findall(r"'([a-z-]+)'",
                                    re.search(r"foreach \(\$a in @\(([^)]*)\)\)", rt).group(1)))
    sh_profiles = sorted(re.search(r"^\s*((?:[a-z-]+\|)+[a-z-]+)\)\s*$", sh, re.M).group(1).split("|"))
    assert sh_profiles == ps_profiles


def test_a_template_rewrite_carries_the_box_keys_over(tmp_path):
    """Both installer paths rebuild deploy/.env from the template, which knows nothing of the
    override or a broker token; without the carry-over a reinstall dropped them and composed
    without the override in the same run."""
    existing = tmp_path / ".env"
    existing.write_text("PLATFORM_ADMIN_USER=old\nPLATFORM_COMPOSE_OVERRIDE=C:\\o.yml\n"
                        "BROKER_AUTH_TOKEN=t\nOPENMAIC_LLM_API_KEY=k\n", encoding="utf-8")
    new = tmp_path / "new.txt"
    new.write_text("PLATFORM_ADMIN_USER=new\n# OPENMAIC_LLM_API_KEY=\nPLATFORM_ENABLED_APPS=x\n",
                   encoding="utf-8")
    out = ps(f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
Add-EnvCarryOver -NewText (Get-Content -Raw -Encoding UTF8 {q(new)}) -ExistingPath {q(existing)}
""", tmp_path)
    assert "PLATFORM_ADMIN_USER=new" in out and "PLATFORM_ADMIN_USER=old" not in out
    for kept in ("PLATFORM_COMPOSE_OVERRIDE=C:\\o.yml", "BROKER_AUTH_TOKEN=t",
                 "OPENMAIC_LLM_API_KEY=k"):        # the last is only COMMENTED in the template
        assert kept in out


def test_the_apply_engine_passes_env_file_and_override(tmp_path):
    """lib-apply's compose step runs inside a GetNewClosure block, which cannot see functions.

    Drives the REAL step with `docker` shadowed by a global function that records its argv, so
    the closure's capture of Get-ComposeFiles, the --env-file, and the -f list are all exercised.
    Loaded inside `& {{ }}`, a CHILD scope, as apply.ps1 effectively is: under `-File` the top level
    is the global scope, where a closure can see a by-name function call anyway, so the test passed
    with the capture removed. Also runs the env step: deploy/.env comes out with no BOM, its
    re-check passes on 5.1, and the box's override survives the rewrite.
    """
    over = tmp_path / "compose.override.yml"
    over.write_text("services: {}\n", encoding="utf-8")
    root = make_root(tmp_path, f"PLATFORM_COMPOSE_OVERRIDE={over}\n")
    argv = tmp_path / "argv.txt"
    ps(f"""
$ErrorActionPreference = 'Stop'
& {{
. {q(INSTALLER / 'lib-modelplan.ps1')}
. {q(INSTALLER / 'lib-apply.ps1')}
$plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 24564 -Installed @{{}}
$actions = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Root {q(root)}
function global:docker {{ [IO.File]::WriteAllLines({q(argv)}, [string[]]$args) }}
& ($actions | Where-Object Id -eq 'compose').Do
}}
""", tmp_path)
    args = argv.read_text(encoding="utf-8").splitlines()
    env = str(root / "deploy" / ".env")
    base = str(root / "deploy" / "installer" / "docker-compose.installer.yml")
    assert args[:2] == ["compose", "--env-file"] and args[2] == env
    assert args[3:7] == ["-f", base, "-f", str(over)]
    assert args[-2:] == ["up", "-d"]

    ps(f"""
$ErrorActionPreference = 'Stop'
& {{
. {q(INSTALLER / 'lib-modelplan.ps1')}
. {q(INSTALLER / 'lib-apply.ps1')}
$plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 24564 -Installed @{{}}
$actions = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Root {q(root)}
$step = $actions | Where-Object Id -eq 'env'
& $step.Do
if (-not (& $step.Test)) {{ throw 'env step re-check is false right after its own Do' }}
}}
""", tmp_path)
    written = (root / "deploy" / ".env").read_bytes()
    assert not written.startswith(b"\xef\xbb\xbf")
    assert f"PLATFORM_COMPOSE_OVERRIDE={over}".encode() in written
