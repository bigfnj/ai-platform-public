"""install-services.ps1 run whole, from a scratch tree, with every outside call a stand-in.

A lean box whose deploy\\local was wiped (`git clean -fdx`) could not re-register its broker: the
script carried the installed service's BROKER_ROLES_FILE (deploy\\local\\roles.json) into -RolesFile
and refused it as missing ("-RolesFile ... does not exist") before Initialize-LeanBrokerFiles, further
down, could seed it (delta audit 2026-10-15). Now the stack is decided first, a lean box carries
neither deploy\\local key and seeds a missing file before anything checks it, and the full stack
carries and checks as it always did.

And the broker is given AI_VOICE_FFMPEG, the full path of the ffmpeg the installing shell finds (a
toolbox shim resolved to the exe it runs): ai-voice's RVC engine runs ffmpeg in the broker's
environment, and the LocalSystem service sees the machine PATH only.

The script refuses to run unelevated (#Requires -RunAsAdministrator), so each run is a scratch copy
whose three box-touching lines are stand-ins, each asserted to match once: no #Requires, the venv's
python a .cmd that answers the import probe, and nssm a .cmd that records each call (so the
registered environment is read from its `set ... AppEnvironmentExtra` call). Get-ItemProperty (the
installed service's environment, the profiles folder, the hook key), Get-Service and Start-Sleep are
functions. Nothing is registered, started or read from the box's own registry. Both engines.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICES = REPO / "deploy" / "install-services.ps1"
LIB = REPO / "deploy" / "installer" / "lib-runtime.ps1"
# Where the script looks for the voice engines, read from its own line (that rail is withheld from
# the public snapshot, so this file names no path into it).
VOICE_DIR = re.search(r"^\$VoiceEnginesDir = Join-Path \$PlatformRoot '([^']+)'\s*$",
                      SERVICES.read_text(encoding="utf-8-sig"), re.M).group(1)

REQUIRES = "#Requires -RunAsAdministrator\n"
PY_LINE = '$BrokerPy     = "$PlatformRoot\\.venv\\Scripts\\python.exe"\n'
NSSM_LINE = '$Nssm         = "$BinDir\\nssm.exe"\n'
RECORDER = '@echo off\r\n>> "%~dp0calls.txt" echo %*\r\nexit /b 0\r\n'
TRACKED_ROLES = '{"chat": "tracked-24gb-model"}\n'

STUBS = r"""
$ErrorActionPreference = 'Continue'
function global:Get-ItemProperty {
  param([Parameter(Position = 0)][string]$Path, [string]$LiteralPath, [Parameter(Position = 1)]$Name)
  $p = if ($LiteralPath) { $LiteralPath } else { $Path }
  if ($p -eq 'HKLM:\SYSTEM\CurrentControlSet\Services\platform-broker\Parameters') {
    return [pscustomobject]@{ AppEnvironmentExtra = [string[]]$global:installed }
  }
}
function global:Get-Service { param([Parameter(Position = 0)][string[]]$Name) }
function global:Start-Sleep { }
"""


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def ps_list(items) -> str:
    return "@(" + ", ".join(q(i) for i in items) + ")"


def scratch_tree(root: Path) -> Path:
    deploy = root / "deploy"
    (deploy / "installer").mkdir(parents=True)
    text = SERVICES.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    for line in (REQUIRES, PY_LINE, NSSM_LINE):
        assert text.count(line) == 1, f"install-services.ps1 moved its stand-in line {line!r}"
    text = (text.replace(REQUIRES, "").replace(PY_LINE, PY_LINE.replace("python.exe", "python.cmd"))
            .replace(NSSM_LINE, NSSM_LINE.replace("nssm.exe", "nssm.cmd")))
    (deploy / "install-services.ps1").write_text(text, encoding="utf-8-sig")
    (deploy / "installer" / "lib-runtime.ps1").write_bytes(LIB.read_bytes())
    (deploy / ".env").write_text("PLATFORM_RUNTIME=podman\nBROKER_AUTH_TOKEN=tok\n", encoding="utf-8")
    broker = root / "services" / "broker"
    broker.mkdir(parents=True)
    (broker / "roles.json").write_text(TRACKED_ROLES, encoding="utf-8")
    (broker / "disabled.json").write_text("[]\n", encoding="utf-8")
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.cmd").write_bytes(RECORDER.encode("ascii"))
    (deploy / "bin").mkdir()
    (deploy / "bin" / "nssm.cmd").write_bytes(RECORDER.encode("ascii"))
    return deploy / "install-services.ps1"


@pytest.fixture(scope="module")
def runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("svcrun")
    upstreams = base / "box" / "upstreams.json"
    upstreams.parent.mkdir()
    upstreams.write_text("{}\n", encoding="utf-8")
    prof = base / "profile"
    prof.mkdir()
    # ffmpeg as the DevToolbox has it: a .cmd shim on the user's PATH naming the real exe.
    ffmpeg_exe = base / "ffmpeg-pkg" / "bin" / "ffmpeg.exe"
    ffmpeg_exe.parent.mkdir(parents=True)
    ffmpeg_exe.write_bytes(b"MZ stand-in")
    toolbox = base / "toolbox-bin"
    toolbox.mkdir()
    (toolbox / "ffmpeg.cmd").write_bytes(f'@echo off\r\n"{ffmpeg_exe}" %*\r\n'.encode("ascii"))
    system32 = str(Path(__import__("os").environ.get("SystemRoot", r"C:\Windows")) / "System32")
    cases = {}
    body = STUBS
    for name in ("lean-wiped", "full-missing", "full"):
        root = base / name / "repo"
        script = scratch_tree(root)
        (root / VOICE_DIR).mkdir(parents=True)                       # the voice engines are here
        local = root / "deploy" / "local"
        # The lean run's shell finds ffmpeg through the toolbox shim; the full run's finds none.
        path = f"{toolbox};{system32}" if name == "lean-wiped" else system32
        body += f"$env:Path = {q(path)}\n"
        if name == "lean-wiped":     # a lean broker whose deploy\local maps were wiped
            installed = ["BROKER_MEDIA_ENABLED=false", "BROKER_AUTH_TOKEN=old", "BROKER_CLI_ENABLED=true",
                         f"BROKER_ROLES_FILE={local / 'roles.json'}", f"BROKER_DISABLED_FILE={local / 'disabled.json'}",
                         f"BROKER_UPSTREAMS_FILE={upstreams}", ""]
        elif name == "full-missing":  # a full-stack broker whose own map is gone: still refused
            installed = ["HF_HOME=C:\\work\\cache\\hf-cache", f"BROKER_ROLES_FILE={base / 'gone' / 'roles.json'}"]
        else:
            installed = ["HF_HOME=C:\\work\\cache\\hf-cache", "BROKER_AUTH_TOKEN=tok"]
        cases[name] = root
        body += f"""
$global:installed = {ps_list(installed)}
Write-Output '<<<{name}>>>'
try {{ & {q(script)} -SkipOllama -SkipCourseBuilder -UserProfile {q(prof)} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        root = cases[name]
        calls_file = root / "deploy" / "bin" / "calls.txt"
        calls = calls_file.read_text(encoding="utf-8", errors="replace").splitlines() if calls_file.is_file() else []
        env = next((c for c in calls if c.startswith("set platform-broker AppEnvironmentExtra ")), "")
        out[name] = {"text": text, "calls": calls, "env": env, "root": root}
    assert set(out) == set(cases), r.stdout + r.stderr
    out["upstreams"] = upstreams
    out["gone"] = base / "gone" / "roles.json"
    out["ffmpeg"] = ffmpeg_exe
    return out


def test_a_lean_box_with_its_deploy_local_maps_wiped_reregisters_and_seeds_them(runs):
    got = runs["lean-wiped"]
    text, local = got["text"], got["root"] / "deploy" / "local"
    assert "THROWN" not in text and "does not exist" not in text, text
    assert "broker: the lean stack's" in text, text
    # Seeded from the tracked map, and said to be that, not this box's own.
    assert (local / "roles.json").read_text(encoding="utf-8") == TRACKED_ROLES, text
    assert (local / "disabled.json").is_file(), text
    seeded = [ln for ln in text.splitlines() if "seeded from the tracked map" in ln]
    assert len(seeded) == 2 and any(str(local / "roles.json") in ln for ln in seeded), text
    # Neither deploy\local key is carried as a flag; the upstreams file still is.
    assert "keeping BROKER_ROLES_FILE" not in text and "keeping BROKER_DISABLED_FILE" not in text, text
    assert f"keeping BROKER_UPSTREAMS_FILE={runs['upstreams']}" in text, text
    env = got["env"]
    assert f"BROKER_ROLES_FILE={local / 'roles.json'}" in env and f"BROKER_DISABLED_FILE={local / 'disabled.json'}" in env, got["calls"]
    assert f"BROKER_UPSTREAMS_FILE={runs['upstreams']}" in env and "BROKER_CLI_ENABLED=true" in env, env
    assert any(c.startswith("start platform-broker") for c in got["calls"]), got["calls"]


def test_the_full_stack_still_carries_and_checks_its_own_map(runs):
    """The control: a full-stack broker's own map outside the checkout is carried, and a missing
    one refused before anything is torn down, as before."""
    got = runs["full-missing"]
    assert f"THROWN: -RolesFile '{runs['gone']}' does not exist" in got["text"], got["text"]
    assert got["calls"] == [], got["calls"]
    assert not (got["root"] / "deploy" / "local").exists()


def test_a_full_stack_run_registers_the_full_brokers_command_line(runs):
    got = runs["full"]
    assert "THROWN" not in got["text"] and "broker: the full stack's" in got["text"], got["text"]
    params = [c for c in got["calls"] if c.startswith("set platform-broker AppParameters ")]
    assert params and "--host 0.0.0.0 --port 11500" in params[0], got["calls"]
    assert "BROKER_MEDIA_ENABLED=false" not in got["env"] and not (got["root"] / "deploy" / "local").exists(), got


# --- AI_VOICE_FFMPEG: the voice engines' ffmpeg, for a service that sees the machine PATH only -----

def test_the_broker_is_given_the_ffmpeg_its_installing_shell_finds(runs):
    """ai-voice's RVC engine runs ffmpeg in the broker's environment, AI_VOICE_FFMPEG or PATH, and
    the LocalSystem service sees the machine PATH only. The lean run's shell finds ffmpeg through a
    toolbox shim, and the service is given the exe the shim runs."""
    got = runs["lean-wiped"]
    assert f"AI_VOICE_FFMPEG={runs['ffmpeg']}" in got["env"], got["env"]
    assert f"voice engines' ffmpeg: AI_VOICE_FFMPEG={runs['ffmpeg']}" in got["text"], got["text"]


def test_no_ffmpeg_in_the_installing_shell_sets_nothing_and_says_so(runs):
    got = runs["full"]
    assert "BROKER_VOICE_ENGINES_DIR=" in got["env"] and "AI_VOICE_FFMPEG" not in got["env"], got["env"]
    assert "AI_VOICE_FFMPEG is left unset" in got["text"] and "MACHINE PATH" in got["text"], got["text"]


FAKE_GET_COMMAND = r"""
function Get-Command {
  [CmdletBinding()] param([Parameter(Position = 0)][string]$Name, [string]$CommandType)
  if ($Name -eq 'ffmpeg' -and $global:ffSource) { [pscustomobject]@{ Name = (Split-Path $global:ffSource -Leaf); Source = $global:ffSource } }
}
"""


@pytest.fixture(scope="module")
def ffmpeg_picks(run_ps, shell, tmp_path_factory):
    """lib-runtime's Resolve-FfmpegExe with Get-Command a function: what each shape of `ffmpeg` on
    PATH gives the service."""
    d = tmp_path_factory.mktemp("ffpick")
    real = d / "pkg dir" / "bin" / "ffmpeg.exe"           # a blank in the path, as Program Files has
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ")
    bare = d / "plain" / "ffmpeg.exe"                      # none, so a shim may name it unquoted
    bare.parent.mkdir()
    bare.write_bytes(b"MZ")
    shims = {
        "quoted": f'@echo off\r\n"{real}" %*\r\n',
        "dp0": '@echo off\r\n"%~dp0..\\pkg dir\\bin\\ffmpeg.exe" %*\r\n',
        "envvar": '@echo off\r\n"%FFPICK_ROOT%\\pkg dir\\bin\\ffmpeg.exe" %*\r\n',
        "bare": f"@echo off\r\n{bare} %*\r\n",
        "missing": f'@echo off\r\n"{d / "gone" / "ffmpeg.exe"}" %*\r\n',
    }
    shim_dir = d / "shims"
    shim_dir.mkdir()
    for n, text in shims.items():
        (shim_dir / f"{n}.cmd").write_bytes(text.encode("ascii"))
    cases = {"exe": str(real), "none": "", **{n: str(shim_dir / f"{n}.cmd") for n in shims}}
    body = (f"$ErrorActionPreference = 'Stop'\n. {q(LIB)}\n{FAKE_GET_COMMAND}\n$env:FFPICK_ROOT = {q(d)}\n"
            "$out = [ordered]@{}\n")
    for n, src in cases.items():
        body += f"$global:ffSource = {q(src)}\n$out['{n}'] = Resolve-FfmpegExe\n"
    body += "$out | ConvertTo-Json -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return __import__("json").loads(r.stdout.strip().splitlines()[-1]), real, bare, shim_dir


def test_each_shape_of_ffmpeg_on_path_resolves_to_the_exe_it_runs(ffmpeg_picks):
    got, real, bare, shim_dir = ffmpeg_picks
    for n in ("exe", "quoted", "dp0", "envvar"):
        assert got[n]["Path"] == str(real), (n, got[n])
    assert got["bare"]["Path"] == str(bare), got["bare"]
    assert got["quoted"]["Note"] == f"the ffmpeg.exe the shim {shim_dir / 'quoted.cmd'} runs", got["quoted"]


def test_no_ffmpeg_or_a_shim_naming_nothing_gives_no_path_and_says_why(ffmpeg_picks):
    got, _, _, shim_dir = ffmpeg_picks
    assert got["none"] == {"Path": "", "Note": "no ffmpeg on PATH in this shell"}, got["none"]
    assert got["missing"]["Path"] == "" and str(shim_dir / "missing.cmd") in got["missing"]["Note"], got["missing"]
