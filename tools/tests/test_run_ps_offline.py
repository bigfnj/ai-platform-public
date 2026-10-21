"""No network from a test's PowerShell: conftest's PS_OFFLINE stand-ins, run_ps, offline_script.

offline_guard.py watches the pytest process; a script a test starts through run_ps is a process of
its own. The offline-guard lane's read of every tools test that starts PowerShell (2026-10-15)
found one real call, the apply self-test's GETs to the live Ollama, and three runs that stayed
offline only because their script exits before its web call. Every run_ps body now starts with
throwing functions for the web cmdlets, and a script started as a process of its own goes
through offline_script, which carries them in. These pin both, and hold the scripts the tests run
to calls the stand-ins can catch.

Where a broken stand-in would let a call through, it goes nowhere: 192.0.2.1 is TEST-NET-1
(RFC 5737), never routed. The three proofs inject the script's own next call at its early exit
and show it meets the stand-in; past that exit each script would also start containers or
install software, which no test here does.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
SAYS = "no network in a test"
STAND_INS = ("Invoke-WebRequest", "Invoke-RestMethod", "Start-BitsTransfer", "Test-NetConnection",
             "Test-Connection", "Resolve-DnsName")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def test_every_stand_in_throws_naming_its_call(run_ps, shell):
    body = "".join(f"try {{ {n} 'http://192.0.2.1:9/x' -TimeoutSec 1 }} catch {{ '{n}|' + $_ }}\n"
                   for n in STAND_INS)
    body += ("try { iwr 'http://192.0.2.1:9/alias' } catch { 'iwr|' + $_ }\n"
             "try { irm 'http://192.0.2.1:9/alias' } catch { 'irm|' + $_ }\n")
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = dict(line.split("|", 1) for line in r.stdout.splitlines() if "|" in line)
    for n in STAND_INS:
        assert SAYS in got[n] and f"{n} http://192.0.2.1:9/x -TimeoutSec 1" in got[n], got
    assert "Invoke-WebRequest http://192.0.2.1:9/alias" in got["iwr"], got
    assert "Invoke-RestMethod http://192.0.2.1:9/alias" in got["irm"], got


def test_a_tests_own_stand_in_replaces_one(run_ps, shell):
    r = run_ps(shell, "function global:Invoke-RestMethod { 'mine' }\nInvoke-RestMethod 'http://192.0.2.1:9/'\n")
    assert r.returncode == 0 and r.stdout.strip() == "mine", r.stdout + r.stderr


def test_offline_false_leaves_the_cmdlets_alone(run_ps, shell):
    r = run_ps(shell, "(Get-Command Invoke-WebRequest).CommandType\n(Get-Command Invoke-RestMethod).CommandType\n",
               offline=False)
    assert r.returncode == 0 and r.stdout.split() == ["Cmdlet", "Cmdlet"], r.stdout + r.stderr


def test_a_script_started_as_its_own_process_needs_offline_script(run_ps, offline_script, shell, tmp_path):
    """A function of the parent does not reach a child process: the bare -File child resolves the
    cmdlet; the same script through offline_script meets the stand-in."""
    probe = tmp_path / "probe.ps1"
    probe.write_text("(Get-Command Invoke-WebRequest).CommandType\n"
                     "Invoke-WebRequest 'http://192.0.2.1:9/probe' -TimeoutSec 1 -UseBasicParsing\n",
                     encoding="utf-8-sig")
    wrapped = offline_script(probe)
    r = run_ps(shell, f"""
'<<<bare>>>'
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -Command "(Get-Command Invoke-WebRequest).CommandType"
'<<<wrapped>>>'
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(wrapped)} *>&1 | ForEach-Object {{ [string]$_ }}
"EXIT=$LASTEXITCODE"
""")
    assert r.returncode == 0, r.stdout + r.stderr
    bare, wrapped_out = r.stdout.split("<<<bare>>>", 1)[1].split("<<<wrapped>>>", 1)
    assert bare.split() == ["Cmdlet"], bare
    assert "Function" in wrapped_out and SAYS in wrapped_out, wrapped_out
    assert "Invoke-WebRequest http://192.0.2.1:9/probe" in wrapped_out, wrapped_out
    assert "EXIT=1" in wrapped_out, wrapped_out


@pytest.mark.parametrize("script,code", [
    ("'ran'\n", 0),
    ("cmd /c exit 5\n'ran on'\n", 0),          # a failed native command, then the end: -File says 0
    ("'x'\nexit 3\n", 3),
    ("cmd /c exit 5\nexit 0\n", 0),
    ("throw 'boom'\n", 1),
])
def test_offline_script_exits_as_file_would(run_ps, offline_script, shell, tmp_path, script, code):
    target = tmp_path / "target.ps1"
    target.write_text(script, encoding="utf-8-sig")
    r = run_ps(shell, f"""
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(target)} *>&1 | Out-Null
"DIRECT=$LASTEXITCODE"
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(offline_script(target))} *>&1 | Out-Null
"WRAPPED=$LASTEXITCODE"
""")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"DIRECT={code}" in r.stdout and f"WRAPPED={code}" in r.stdout, r.stdout


# --- the three runs that were offline only by an early exit -----------------------------------
# Each copies its script as its test does, puts the script's own next web call right before the
# exit, and runs it the way its test runs it.

def _inject(path: Path, anchor: str, call: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    assert text.count(anchor) == 1, f"{anchor!r} is not once in {path.name}: re-anchor this proof"
    path.write_text(text.replace(anchor, call + anchor), encoding="utf-8-sig")


def test_platform_startup_past_its_missing_env_exit_meets_the_stand_in(run_ps, offline_script, shell,
                                                                       tmp_path):
    """test_startup_lock.py and test_docker_config_env.py's startup run: a -File child with no
    deploy\\.env, which exits before the front-door wait calls the gateway on :1111."""
    from test_startup_lock import tree
    root = tree(tmp_path, None)
    script = root / "deploy" / "installer" / "platform-startup.ps1"
    line = next(ln for ln in script.read_text(encoding="utf-8-sig").splitlines()
                if ln.startswith("  Invoke-WebRequest 'http://localhost:1111/"))
    _inject(script, "if (-not (Test-Path $envFile)) {", line.strip() + "\n")
    prof = tmp_path / "profile"
    prof.mkdir()
    r = run_ps(shell, f"""
$env:USERPROFILE = {q(prof)}
& {q(shell)} -NoProfile -ExecutionPolicy Bypass -File {q(offline_script(script, '-RuntimeMode desktop'))} *>&1 | Out-Null
"EXIT=$LASTEXITCODE"
""", timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    log = (root / "deploy" / "logs" / "startup.log").read_text(encoding="utf-8-sig")
    assert "EXIT=0" not in r.stdout, (r.stdout, log)
    assert SAYS in log and "Invoke-WebRequest http://localhost:1111/api/platform/healthz" in log, log
    assert "aborting" not in log, log                 # it met the call, not the early exit


def test_install_provision_past_its_first_pull_meets_the_stand_in(run_ps, shell, tmp_path):
    """test_wsl_ollama_reach.py's provision run: its own Invoke-WebRequest stand-in, and none for
    Invoke-RestMethod, ended by a fake `ollama pull` that fails. Ollama's API, asked as
    lib-modelplan asks it, at that pull."""
    import test_wsl_ollama_reach as wsl
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ollama.cmd").write_bytes(wsl.OLLAMA.encode("ascii"))
    (fake / "podman.cmd").write_bytes(wsl.PODMAN.encode("ascii"))
    root = tmp_path / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    for f in ("install.ps1", "env.lean.example", "roles.lean.json"):
        shutil.copy2(INSTALLER / f, inst / f)
    (inst / "lib-runtime.ps1").write_text(wsl.scratch_lib(), encoding="utf-8-sig")
    (inst / "install-native.ps1").write_text(wsl.RECORDER, encoding="utf-8-sig")
    (root / "services" / "broker").mkdir(parents=True)
    prof = tmp_path / "profile"
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
    local = tmp_path / "localappdata"
    (local / "Programs" / "Ollama").mkdir(parents=True)
    (local / "Programs" / "Ollama" / "ollama app.exe").write_bytes(b"")
    _inject(inst / "install.ps1", "      & ollama pull $m",
            "      Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 | Out-Null\n")
    body = wsl.PROVISION_STUBS + f"""
$ErrorActionPreference = 'Continue'
$env:Path = {q(fake)} + ';' + {q(wsl.SYSTEM32)} + ';' + {q(wsl.WINPS)}
$env:USERPROFILE = {q(prof)}; $env:LOCALAPPDATA = {q(local)}
& {q(inst / 'install.ps1')} -Provision -RuntimeMode podman -AdminUser admin -AdminPass pw -EnabledApps terminal-fun *>&1 | Out-Null
"PULLED=" + (Test-Path {q(fake / 'ollama-calls.txt')})
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    log = (root / "deploy" / "logs" / "install.log").read_text(encoding="utf-8-sig")
    assert SAYS in log and "Invoke-RestMethod -Uri http://127.0.0.1:11434/api/tags" in log, log
    assert "PULLED=False" in r.stdout, r.stdout       # it met the call before the pull


def test_install_native_past_its_plan_only_exit_meets_the_stand_in(run_ps, shell, tmp_path):
    """test_lean_broker_files.py's and the lean-broker tests' -PlanOnly runs: they exit before
    install-native's first real step, the NSSM download."""
    from test_lean_broker_files import broker_tree
    root = broker_tree(tmp_path / "repo")
    (root / "deploy" / "installer").mkdir()
    script = root / "deploy" / "installer" / "install-native.ps1"
    shutil.copy2(INSTALLER / "install-native.ps1", script)
    (root / "deploy" / "installer" / "lib-runtime.ps1").write_text(
        (INSTALLER / "lib-runtime.ps1").read_text(encoding="utf-8")
        + "\nfunction Get-InstalledBrokerEnv { param([string]$Key) @() }\n", encoding="utf-8-sig")
    (root / "deploy" / ".env").write_text("PLATFORM_RUNTIME=podman\n", encoding="utf-8")
    text = script.read_text(encoding="utf-8-sig")
    m = re.search(r"\n  \} \| ConvertTo-Json -Depth \d+ -Compress\n  exit 0\n", text)
    assert m and text.count(m.group(0)) == 1, "the -PlanOnly block moved: re-anchor this proof"
    script.write_text(text.replace(m.group(0), m.group(0).replace(
        "  exit 0\n", "  $null = Install-NssmExe -Dest (Join-Path $PlatformRoot 'deploy\\bin\\nssm.exe')\n"
                      "  exit 0\n")), encoding="utf-8-sig")
    r = run_ps(shell, f"""
try {{ & {q(script)} -PlatformRoot {q(root)} -SkipOllama -PlanOnly 3>$null *>&1 | Out-Null; "EXIT=$LASTEXITCODE" }}
catch {{ "THROWN: $($_.Exception.Message)" }}
""")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "THROWN:" in r.stdout and SAYS in r.stdout, r.stdout
    assert "Invoke-WebRequest https://nssm.cc/" in r.stdout, r.stdout
    assert not (root / "deploy" / "bin" / "nssm.exe").exists()


# --- the scripts the tests run reach the network only through what PS_OFFLINE stands in for -------

# What a function cannot stand in for: a .NET client, or a cmdlet called by its module's name.
_UNCATCHABLE = re.compile(r"Net\.WebClient|Net\.Http\.HttpClient|Net\.Sockets\.|Net\.Dns\]|"
                          r"Net\.WebRequest\]|HttpWebRequest|Microsoft\.PowerShell\.Utility\\Invoke-|"
                          r"BitsTransfer\\", re.I)


def _scripts() -> list[Path]:
    """Every script a tools test starts: deploy/'s, and the two at the root."""
    return sorted([*REPO.joinpath("deploy").rglob("*.ps1"), REPO / "get.ps1", REPO / "run-tests.ps1"])


def test_the_scripts_tests_run_call_out_only_through_the_stand_ins():
    found = _scripts()
    assert len(found) > 10, found
    bad = []
    for p in found:
        if not p.is_file():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.lstrip().startswith("#") and _UNCATCHABLE.search(line):
                bad.append(f"{p.relative_to(REPO).as_posix()}:{n}: {line.strip()}")
    assert not bad, ("a script the tests run reaches the network in a way conftest's PS_OFFLINE "
                     "cannot stand in for (use Invoke-WebRequest / Invoke-RestMethod, or add a "
                     "stand-in):\n  " + "\n  ".join(bad))
