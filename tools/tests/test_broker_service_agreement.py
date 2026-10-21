"""The lean installer and install-services.ps1 register the same broker on a lean box.

install-native.ps1 re-registered the lean broker with its spec ALONE (media off, the token),
dropping the installed service's BROKER_CLI_* and BROKER_*_FILE settings, and apply's services
step re-runs it whenever Test-BrokerServiceCurrent says no. install-services.ps1, the documented
way to set the Claude CLI (-BrokerCliEnvFile), registered `--host 0.0.0.0` with media on on every
box, so after it that check was false on every lean box: the next `apply -Execute` silently
turned the CLI off, and in between a loopback broker listened on every interface. Also
install-native's Invoke-Nssm restored an undefined $old.

install-services itself refuses to run unelevated (#Requires -RunAsAdministrator), so its decision
is lib-runtime's Get-InstallServicesBrokerPlan, run here under both engines, plus a check that the
script registers what the plan says; install-native runs for real with -PlanOnly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
SERVICES = REPO / "deploy" / "install-services.ps1"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def ps_list(items) -> str:
    return "@(" + ", ".join(q(i) for i in items) + ")"


# An installed lean broker as the podman box's registry holds it: a CLI backend set up through
# install-services -BrokerCliEnvFile, an upstream registry, media paths a full-stack run added, and
# the trailing blank element NSSM's lists end in there.
INSTALLED_LEAN = [
    "BROKER_MEDIA_ENABLED=false", "BROKER_AUTH_TOKEN=old-token", "HF_HOME=C:\\work\\cache\\hf-cache",
    "BROKER_CLI_ENABLED=true", "BROKER_CLI_OWNERS=owner", "BROKER_CLI_EXE=C:\\tools\\claude.exe",
    "BROKER_UPSTREAMS_FILE=C:\\box\\upstreams.json", "BROKER_ROLES_FILE=C:\\elsewhere\\roles.json", "",
]
INSTALLED_FULL = ["HF_HOME=C:\\work\\cache\\hf-cache", "BROKER_AUTH_TOKEN=tok", "BROKER_CLI_ENABLED=true"]


@pytest.fixture(scope="module")
def plans(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("agree")
    root = base / "repo"
    (root / "deploy").mkdir(parents=True)
    envs = {}
    for mode in ("podman", "desktop", "wsl", ""):
        e = base / f"{mode or 'undeclared'}.env"
        e.write_text(f"PLATFORM_RUNTIME={mode}\n" if mode else "OTHER=1\n", encoding="utf-8")
        envs[mode or "undeclared"] = e
    cases = {
        # name -> (installed env, -Stack, env file, token, file params)
        "lean-podman": (INSTALLED_LEAN, "", "podman", "tok", []),
        "lean-desktop": (INSTALLED_LEAN, "", "desktop", "tok", []),
        "lean-wsl": (INSTALLED_LEAN, "", "wsl", "tok", []),
        "lean-undeclared": (INSTALLED_LEAN, "", "undeclared", "tok", []),
        "full": (INSTALLED_FULL, "", "desktop", "tok", []),
        "none-installed": ([], "", "podman", "tok", []),
        "forced-lean": (INSTALLED_FULL, "lean", "podman", "tok", []),
        "forced-full": (INSTALLED_LEAN, "full", "podman", "tok", []),
        "lean-roles-file": (INSTALLED_LEAN, "", "podman", "tok", ["RolesFile"]),
        "lean-no-token": (INSTALLED_LEAN, "", "podman", "", []),
    }
    body = f"$ErrorActionPreference = 'Stop'\n. {q(INSTALLER / 'lib-runtime.ps1')}\n$out = [ordered]@{{}}\n"
    for name, (installed, stack, env, token, fp) in cases.items():
        body += (f"$out['{name}'] = Get-InstallServicesBrokerPlan -Existing {ps_list(installed)} -Stack '{stack}' "
                 f"-EnvFile {q(envs[env])} -Root {q(root)} -Token '{token}' -FileParams {ps_list(fp)}\n")
    # What the apply engine expects of a lean podman box, and whether the environment
    # install-services builds there (its own entries, then the plan's overlay) satisfies it.
    body += f"""
$p = $out['lean-podman']
$expect = Get-BrokerServiceSpec -BindHost (Get-BrokerBindHost -Mode podman) -Token 'tok' -Root {q(root)}
$built = @(Merge-ServiceEnv -Base {ps_list(INSTALLED_LEAN)} -Overlay $p.Overlay)
$global:reg = @{{ AppParameters = $p.AppParameters; AppEnvironmentExtra = [string[]]$built }}
function Get-ItemProperty {{ param($Path, $ErrorAction) [pscustomobject]$global:reg }}
$out['agrees'] = [bool](Test-BrokerServiceCurrent -Spec $expect)
$full = $out['full']
$global:reg = @{{ AppParameters = $full.AppParameters; AppEnvironmentExtra = [string[]]{ps_list(INSTALLED_LEAN)} }}
$out['full-agrees'] = [bool](Test-BrokerServiceCurrent -Spec $expect)
Remove-Item Function:\\Get-ItemProperty
$out | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), root


def test_a_lean_box_gets_the_lean_installers_broker(plans):
    got, root = plans
    p = got["lean-podman"]
    assert p["Stack"] == "lean" and p["Problem"] == "", p
    assert "BROKER_MEDIA_ENABLED=false" in p["Why"], p
    assert p["AppParameters"] == "-m uvicorn app.main:app --app-dir services\\broker --host 127.0.0.1 --port 11500", p
    local = root / "deploy" / "local"
    assert p["Overlay"] == ["BROKER_MEDIA_ENABLED=false", "BROKER_AUTH_TOKEN=tok",
                            f"BROKER_ROLES_FILE={local / 'roles.json'}", f"BROKER_DISABLED_FILE={local / 'disabled.json'}"], p


@pytest.mark.parametrize("name,bind", [("lean-podman", "127.0.0.1"), ("lean-desktop", "127.0.0.1"),
                                       ("lean-wsl", "0.0.0.0"), ("lean-undeclared", "127.0.0.1")])
def test_the_lean_bind_follows_the_declared_runtime(plans, name, bind):
    p = plans[0][name]
    assert p["BindHost"] == bind and f"--host {bind} --port 11500" in p["AppParameters"], p


def test_the_full_stack_keeps_its_documented_broker(plans):
    got, _ = plans
    for name in ("full", "none-installed", "forced-full"):
        p = got[name]
        assert p["Stack"] == "full" and p["Overlay"] == [] and p["Problem"] == "", (name, p)
        assert p["AppParameters"] == "-m uvicorn app.main:app --app-dir services\\broker --host 0.0.0.0 --port 11500", p
    assert got["forced-lean"]["Stack"] == "lean" and got["forced-lean"]["Why"] == "from -Stack"


def test_after_install_services_the_apply_check_agrees_on_a_lean_box(plans):
    got, _ = plans
    assert got["agrees"] is True, got
    assert got["full-agrees"] is False, got     # the old registration (0.0.0.0, no maps) did not


def test_a_lean_box_refuses_a_roles_file_and_a_missing_token(plans):
    got, _ = plans
    p = got["lean-roles-file"]
    assert "-RolesFile" in p["Problem"] and "deploy\\local" in p["Problem"] and p["Overlay"] == [], p
    p = got["lean-no-token"]
    assert "BROKER_AUTH_TOKEN" in p["Problem"] and "install-native.ps1" in p["Problem"], p


def _ast_json(script: Path, query: str) -> object:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("needs pwsh for the AST read")
    cmd = (f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(script)}, [ref]$null, [ref]$null)\n"
           + query)
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_install_services_registers_the_broker_the_plan_names():
    """No literal command line: the platform-broker Install-Svc takes the plan's AppParameters, and
    the plan's overlay is merged into its environment before that call."""
    got = _ast_json(SERVICES, r"""
$calls = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Install-Svc' -and $n.CommandElements.Count -ge 3 -and
    $n.CommandElements[1].Extent.Text -eq "'platform-broker'" }, $true))
$merge = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Merge-ServiceEnv' -and $n.Extent.Text -match '-Overlay \$BrokerPlan\.Overlay' }, $true))
@{ args = @($calls | ForEach-Object { $_.CommandElements[3].Extent.Text })
   install_line = @($calls | ForEach-Object { $_.Extent.StartLineNumber })
   merge_line = @($merge | ForEach-Object { $_.Extent.StartLineNumber }) } | ConvertTo-Json -Compress
""")
    assert got["args"] == ["$BrokerPlan.AppParameters"], got
    assert len(got["merge_line"]) == 1 and got["merge_line"][0] < got["install_line"][0], got


# --- install-native: a re-register carries the box's settings forward ----------------------------

@pytest.fixture(scope="module")
def native(run_ps, shell, tmp_path_factory):
    root = tmp_path_factory.mktemp("agree-native") / "repo"
    (root / "deploy" / "installer").mkdir(parents=True)
    shutil.copy2(INSTALLER / "install-native.ps1", root / "deploy" / "installer" / "install-native.ps1")
    # The installed service's registry read, answered with INSTALLED_LEAN: this box's own is never read.
    (root / "deploy" / "installer" / "lib-runtime.ps1").write_text(
        (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8")
        + f"\nfunction Get-InstalledBrokerEnv {{ param([string]$Key) {ps_list(INSTALLED_LEAN)} }}\n",
        encoding="utf-8-sig")
    (root / "deploy" / ".env").write_text("PLATFORM_RUNTIME=podman\nBROKER_AUTH_TOKEN=tok\n", encoding="utf-8")
    body = f"""
$ErrorActionPreference = 'Stop'
$plan = & {q(root / 'deploy' / 'installer' / 'install-native.ps1')} -PlatformRoot {q(root)} -SkipOllama -PlanOnly 3>$null | Select-Object -Last 1
. {q(root / 'deploy' / 'installer' / 'lib-runtime.ps1')}
$spec = Get-BrokerServiceSpec -BindHost 127.0.0.1 -Token tok -Root {q(root)}
@{{ plan = ($plan | ConvertFrom-Json); env = @(Get-LeanBrokerServiceEnv -Spec $spec -Existing (Get-InstalledBrokerEnv)) }} |
  ConvertTo-Json -Depth 5 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), root


def test_a_lean_re_register_keeps_the_cli_settings_and_the_upstreams(native):
    got, _ = native
    names = got["plan"]["EnvironmentNames"]
    for k in ("BROKER_CLI_ENABLED", "BROKER_CLI_OWNERS", "BROKER_CLI_EXE", "BROKER_UPSTREAMS_FILE",
              "BROKER_MEDIA_ENABLED", "BROKER_AUTH_TOKEN", "BROKER_ROLES_FILE", "BROKER_DISABLED_FILE"):
        assert k in names, (k, names)
    assert "HF_HOME" not in names and "" not in names, names     # a media path, and the blank, dropped
    assert got["plan"]["CarriedNames"] == ["BROKER_CLI_ENABLED", "BROKER_CLI_OWNERS", "BROKER_CLI_EXE",
                                            "BROKER_UPSTREAMS_FILE", "BROKER_ROLES_FILE"], got["plan"]


def test_the_lean_spec_wins_over_what_is_carried(native):
    got, root = native
    env = got["env"]
    assert "BROKER_AUTH_TOKEN=tok" in env and "BROKER_AUTH_TOKEN=old-token" not in env, env
    assert f"BROKER_ROLES_FILE={root / 'deploy' / 'local' / 'roles.json'}" in env, env
    assert "BROKER_CLI_EXE=C:\\tools\\claude.exe" in env, env
    assert len({e.split("=", 1)[0].upper() for e in env}) == len(env), env      # each key once


def test_invoke_nssm_restores_the_preference_it_saved(run_ps, shell, tmp_path):
    """Under Set-StrictMode the old `finally { $ErrorActionPreference = $old }` is an error, $old
    never having been set (EAP Continue inside the function let the call go on and return the exit
    code); without strict mode the undefined read was silently $null."""
    got = _ast_json(INSTALLER / "install-native.ps1", r"""
$f = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-Nssm' }, $true)
@{ text = $f.Extent.Text } | ConvertTo-Json -Compress
""")
    body = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Nssm = Join-Path $env:SystemRoot 'System32\\cmd.exe'
{got['text']}
$Error.Clear()
$code = Invoke-Nssm /c 'exit 3'
"code=$code eap=$ErrorActionPreference errors=$($Error.Count)"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "code=3 eap=Stop errors=0" in r.stdout, r.stdout + r.stderr
