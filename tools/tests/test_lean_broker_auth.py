"""A lean install's broker is authenticated, and bound to loopback wherever containers reach it so.

It used to be registered on 0.0.0.0:11500 with no token, while the rails sent ${BROKER_AUTH_TOKEN:-}
(empty) and the lean gateway sent nothing at all: an open GPU and role control plane for every
container on the box and, wherever a python allow rule existed, the LAN. The apply
engine's 'services' step ran the FULL stack's install-services.ps1 and called any service of that
name done, and its 'media-venv' step ran a script path that does not exist.

Driven for real under both engines: install-native.ps1 -PlanOnly (the plan the service is then
registered from), lib-apply's 'services' step with a stand-in install-native.ps1 that records how it
was called, and the service-current check against stubbed registry reads.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def env_token(env: Path) -> str:
    vals = [ln.split("=", 1)[1] for ln in env.read_text(encoding="utf-8").splitlines()
            if ln.startswith("BROKER_AUTH_TOKEN=")]
    assert len(vals) == 1, vals
    return vals[0]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# --- install-native.ps1 -PlanOnly: the plan the broker service is registered from ------------------

NO_INSTALLED_BROKER = "\nfunction Get-InstalledBrokerEnv { param([string]$Key) @() }\n"

ROOTS = {   # name -> (deploy\.env text, extra arguments)
    "podman": ("PLATFORM_RUNTIME=podman\n", ""),
    "desktop": ("PLATFORM_RUNTIME=desktop\n", ""),
    "undeclared": ("PLATFORM_ENABLED_APPS=terminal-fun\n", ""),
    "wsl": ("PLATFORM_RUNTIME=wsl\n", "-OpenWslFirewall"),
    "kept": ("PLATFORM_RUNTIME=podman\nBROKER_AUTH_TOKEN=box-own-token  # set by hand\n", ""),
    "explicit": ("PLATFORM_RUNTIME=podman\n", "-BrokerHost 0.0.0.0"),
}


@pytest.fixture(scope="module")
def plans(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("native")
    body = "$ErrorActionPreference = 'Stop'\n$out = [ordered]@{}\n"
    roots = {}
    for name, (env, extra) in ROOTS.items():
        root = base / name
        (root / "deploy" / "installer").mkdir(parents=True)
        shutil.copy2(INSTALLER / "install-native.ps1", root / "deploy" / "installer" / "install-native.ps1")
        # This box's own registered broker is never read: no installed service to carry from.
        (root / "deploy" / "installer" / "lib-runtime.ps1").write_text(
            (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8") + NO_INSTALLED_BROKER, encoding="utf-8-sig")
        (root / "deploy" / ".env").write_text(env, encoding="utf-8")
        roots[name] = root
        script = root / "deploy" / "installer" / "install-native.ps1"
        for run in (1, 2):
            body += f"""
$o = & {q(script)} -PlatformRoot {q(root)} -SkipOllama -PlanOnly {extra} 3>$null
$out['{name}-{run}'] = $o | Select-Object -Last 1
$out['{name}-{run}-env'] = [Convert]::ToBase64String([IO.File]::ReadAllBytes({q(root / 'deploy' / '.env')}))
"""
    body += "$out | ConvertTo-Json -Depth 3 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    raw = json.loads(r.stdout.strip().splitlines()[-1])
    got = {}
    for name in ROOTS:
        got[name] = [json.loads(raw[f"{name}-{n}"]) for n in (1, 2)]
        got[name + "-env"] = [raw[f"{name}-{n}-env"] for n in (1, 2)]
        got[name + "-root"] = roots[name]
    return got


@pytest.mark.parametrize("name", ["podman", "desktop", "undeclared"])
def test_the_broker_binds_loopback_outside_wsl(plans, name):
    p = plans[name][0]
    assert p["BindHost"] == "127.0.0.1", p
    assert "--host 127.0.0.1 --port 11500" in p["AppParameters"], p
    assert p["Firewall"] == [], p


def test_a_lean_install_generates_a_token_into_env_and_the_service_environment(plans):
    first, second = plans["podman"]
    token = env_token(plans["podman-root"] / "deploy" / ".env")
    assert HEX64.match(token), token                         # nothing compose's dotenv would alter
    assert "BROKER_AUTH_TOKEN" in first["EnvironmentNames"] and "BROKER_MEDIA_ENABLED" in first["EnvironmentNames"]
    assert first["TokenGenerated"] is True and first["TokenSha256"] == sha(token)
    # A second run takes the same token and leaves deploy\.env byte-identical.
    assert second["TokenGenerated"] is False and second["TokenSha256"] == sha(token)
    assert plans["podman-env"][0] == plans["podman-env"][1]


def test_a_box_that_has_a_token_keeps_it(plans):
    p = plans["kept"][0]
    assert p["TokenGenerated"] is False
    assert p["TokenSha256"] == sha("box-own-token")          # as compose reads it: comment dropped


def test_the_broker_service_logs_rotate_online_and_are_pruned(plans):
    """The broker the lean installer registers rotates at a size and prunes rotated copies."""
    import base64
    s = {a[2]: a for a in plans["podman"][0]["NssmLogSettings"]}
    assert s["AppRotateOnline"][3] == "1" and s["AppRotateBytes"][3] == "10485760", s
    hook = s["AppEvents"]
    assert hook[3] == "Rotate/Post", hook
    cmd = base64.b64decode(hook[4].split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
    root = plans["podman-root"]
    assert str(root / "deploy" / "installer" / "lib-runtime.ps1") in cmd, cmd
    assert f"Remove-RotatedServiceLogs -LogDir '{root / 'deploy' / 'logs'}'" in cmd, cmd


def test_an_explicit_bind_address_wins(plans):
    assert plans["explicit"][0]["BindHost"] == "0.0.0.0"


def _blocked(rules, addr: str, port: int = 11500) -> tuple[bool, bool]:
    a = ipaddress.ip_address(addr)

    def hit(rule):
        for spec in rule["RemoteAddress"]:
            if "-" in spec:
                lo, hi = (ipaddress.ip_address(x) for x in spec.split("-"))
                if lo <= a <= hi:
                    return True
            elif a in ipaddress.ip_network(spec):
                return True
        return False
    block = any(hit(r) for r in rules if r["Action"] == "Block" and port in r["LocalPort"])
    allow = any(hit(r) for r in rules if r["Action"] == "Allow" and port in r["LocalPort"])
    return block, allow


def test_wsl_binds_every_interface_and_the_firewall_scopes_it_to_the_wsl_range(plans):
    p = plans["wsl"][0]
    assert p["BindHost"] == "0.0.0.0" and "--host 0.0.0.0" in p["AppParameters"]
    rules = p["Firewall"]
    for wsl_side in ("172.17.208.1", "172.31.255.254", "172.16.0.1"):
        assert _blocked(rules, wsl_side) == (False, True), wsl_side
    assert _blocked(rules, "127.0.0.1")[0] is False
    for outside in ("192.0.2.136", "10.0.0.5", "8.8.8.8", "172.15.255.255", "172.32.0.0", "0.0.0.0"):
        assert _blocked(rules, outside)[0] is True, outside


def test_wsl_scopes_ollamas_port_as_it_scopes_the_brokers(plans):
    """A wsl install binds Ollama to 0.0.0.0 too (install.ps1, Set-WslOllamaHost), and Ollama has no
    authentication: the LAN is blocked from :11434 exactly as from :11500, the WSL range and loopback
    are not. The old block rule named :11500 alone."""
    rules = plans["wsl"][0]["Firewall"]
    for wsl_side in ("172.17.208.1", "172.31.255.254", "172.16.0.1"):
        assert _blocked(rules, wsl_side, 11434) == (False, True), wsl_side
    assert _blocked(rules, "127.0.0.1", 11434)[0] is False
    for outside in ("192.0.2.136", "10.0.0.5", "8.8.8.8", "172.15.255.255", "172.32.0.0", "0.0.0.0"):
        assert _blocked(rules, outside, 11434)[0] is True, outside


def test_the_widened_block_rule_has_a_name_of_its_own(plans):
    """install-native keeps a rule it finds by name, so the old name would have left a box installed
    before with its :11500-only rule; the allow rule keeps its name, so a reinstall still finds it."""
    names = {r["Action"]: r["DisplayName"] for r in plans["wsl"][0]["Firewall"]}
    assert names["Allow"] == "AI-Platform: WSL -> broker/ollama", names
    assert names["Block"] != "AI-Platform: broker, block all but WSL and loopback", names
    assert len(plans["wsl"][0]["Firewall"]) == 2, plans["wsl"][0]["Firewall"]


# --- the lean compose file hands the token to every service that calls the broker ----------------

def test_every_lean_service_that_reaches_the_host_carries_the_token():
    cfg = yaml.safe_load((INSTALLER / "docker-compose.installer.yml").read_text(encoding="utf-8"))
    callers = [n for n, s in cfg["services"].items()
               if any("host.docker.internal" in str(h) for h in (s.get("extra_hosts") or []))]
    assert "gateway" in callers, callers
    for name in callers:
        env = cfg["services"][name].get("environment") or {}
        assert env.get("BROKER_AUTH_TOKEN") == "${BROKER_AUTH_TOKEN:-}", name


# --- lib-apply: the 'services' step, the token it writes, no media-venv step ----------------------

STAND_IN = r"""param([string]$PlatformRoot, [switch]$SkipOllama, [string]$BrokerHost, [switch]$OpenWslFirewall)
$line = "PlatformRoot=$PlatformRoot SkipOllama=$([bool]$SkipOllama) BrokerHost=$BrokerHost OpenWslFirewall=$([bool]$OpenWslFirewall)"
Add-Content -LiteralPath (Join-Path $PSScriptRoot 'called.txt') -Value $line -Encoding utf8
"""


@pytest.fixture(scope="module")
def apply_steps(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("apply")
    out = {}
    body = "$ErrorActionPreference = 'Stop'\n$r = [ordered]@{}\n"
    for mode in ("podman", "wsl"):
        root = base / mode
        (root / "deploy" / "installer").mkdir(parents=True)
        (root / "deploy" / "installer" / "install-native.ps1").write_text(STAND_IN, encoding="utf-8-sig")
        (root / "deploy" / ".env").write_text("PLATFORM_ENABLED_APPS=terminal-fun\n", encoding="utf-8")
        out[mode] = root
        body += f"""
& {{
  . {q(INSTALLER / 'lib-modelplan.ps1')}
  . {q(INSTALLER / 'lib-apply.ps1')}
  $plan = Get-ModelPlan -Rails @('terminal-fun') -VramMib 8192 -Installed @{{}}
  $plan.needs_media_venv = $true
  $acts = @(Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode {mode} -Root {q(root)})
  $envAct = $acts | Where-Object Id -eq 'env'
  & $envAct.Do
  $tok = [string](Get-DotEnvValue -Path {q(root / 'deploy' / '.env')} -Key 'BROKER_AUTH_TOKEN')
  $svc = $acts | Where-Object Id -eq 'services'
  $bind = Get-BrokerBindHost -Mode {mode}
  $spec = Get-BrokerServiceSpec -BindHost $bind -Token $tok -Root {q(root)}
  # The registry read the check makes, stubbed: the matching service, an old open one, none at all.
  # Get-Service too, answering "no such service", so whether THIS box has a platform-broker cannot
  # decide what a "the service exists" check would say.
  function global:Get-Service {{ $null }}
  $global:reg = @{{ AppParameters = $spec.AppParameters; AppEnvironmentExtra = [string[]]$spec.Environment }}
  function global:Get-ItemProperty {{ param($Path, $ErrorAction) if ($null -eq $global:reg) {{ throw 'no such key' }}; [pscustomobject]$global:reg }}
  $current = [bool](& $svc.Test)
  $global:reg = @{{ AppParameters = '-m uvicorn app.main:app --app-dir services\\broker --host 0.0.0.0 --port 11500'
                    AppEnvironmentExtra = [string[]]@('BROKER_MEDIA_ENABLED=false') }}
  $old = [bool](& $svc.Test)
  $global:reg = @{{ AppParameters = $spec.AppParameters; AppEnvironmentExtra = [string[]]@('BROKER_MEDIA_ENABLED=false', 'BROKER_AUTH_TOKEN=other') }}
  $wrongToken = [bool](& $svc.Test)
  $global:reg = $null
  $none = [bool](& $svc.Test)
  Remove-Item Function:\\Get-ItemProperty, Function:\\Get-Service
  & $svc.Do
  $r['{mode}'] = [ordered]@{{ ids = @($acts | ForEach-Object {{ $_.Id }}); token = $tok; current = $current; old = $old
                             wrong_token = $wrongToken; none = $none; label = $svc.Label; admin = $svc.NeedsAdmin }}
}}
"""
    body += "$r | ConvertTo-Json -Depth 4 -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    for mode, root in out.items():
        got[mode]["called"] = (root / "deploy" / "installer" / "called.txt").read_text(encoding="utf-8-sig").splitlines()
        got[mode]["root"] = str(root)
    return got


def test_the_services_step_runs_the_lean_installer_script_with_the_runtimes_bind(apply_steps):
    for mode, bind, fw in (("podman", "127.0.0.1", False), ("wsl", "0.0.0.0", True)):
        s = apply_steps[mode]
        assert s["called"] == [f"PlatformRoot={s['root']} SkipOllama=True BrokerHost={bind} OpenWslFirewall={fw}"], s
        assert s["admin"] is True and f"{bind}:11500" in s["label"], s


def test_the_env_step_writes_a_token_the_services_check_expects(apply_steps):
    for mode in ("podman", "wsl"):
        s = apply_steps[mode]
        assert HEX64.match(s["token"]), s
        assert s["current"] is True, s                       # the service registered with that token


def test_a_service_registered_open_or_with_another_token_is_not_done(apply_steps):
    for mode in ("podman", "wsl"):
        s = apply_steps[mode]
        assert s["old"] is False and s["wrong_token"] is False and s["none"] is False, s


def test_there_is_no_media_venv_step_even_when_the_plan_wants_one(apply_steps):
    for mode in ("podman", "wsl"):
        assert "media-venv" not in apply_steps[mode]["ids"], apply_steps[mode]["ids"]
        assert "services" in apply_steps[mode]["ids"]


# --- host-side callers send the token -------------------------------------------------------------

@pytest.fixture(scope="module")
def headers(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("hdr")
    (d / "with.env").write_text("BROKER_AUTH_TOKEN=\"abc def\"  # quoted\n", encoding="utf-8")
    (d / "without.env").write_text("OTHER=1\n", encoding="utf-8")
    body = f"""
. {q(INSTALLER / 'lib-runtime.ps1')}
@{{ with = (Get-BrokerAuthHeaders -EnvFile {q(d / 'with.env')}); without = (Get-BrokerAuthHeaders -EnvFile {q(d / 'without.env')}) }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_auth_header_is_the_env_token_as_compose_reads_it(headers):
    assert headers["with"] == {"Authorization": "Bearer abc def"}
    assert headers["without"] == {}


def test_every_host_side_call_to_a_broker_route_sends_headers():
    """activate-model-roles.ps1's go/no-go POSTed /v1/load with no token, so on any box whose broker
    enforces (the full stack's always did) it got 401 twenty times and aborted as though @embed did
    not resolve. A call to a broker /v1 route from a deploy script must pass -Headers."""
    offenders = []
    for path in sorted((REPO / "deploy").rglob("*.ps1")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        i = 0
        while i < len(lines):
            stmt = lines[i]
            while stmt.rstrip().endswith("`") and i + 1 < len(lines):   # backtick continuation
                i += 1
                stmt = stmt.rstrip()[:-1] + " " + lines[i]
            i += 1
            code = stmt.split(" #", 1)[0]
            if code.lstrip().startswith("#") or not re.search(r"Invoke-(RestMethod|WebRequest)\b", code):
                continue
            if re.search(r"(BrokerUrl|:11500)[^'\"]*/(v1|openai/v1)/", code) and "-Headers" not in code:
                offenders.append(f"{path.relative_to(REPO)}: {code.strip()}")
    assert offenders == [], offenders
