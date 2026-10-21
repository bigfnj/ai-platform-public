"""backup-platform.ps1's designed native-failure paths are reachable under Windows PowerShell 5.1.

The script runs under EAP=Stop, and in 5.1 a REDIRECTED native stderr line (2>&1, *>) is a
terminating error, so the "daemon is not reachable" message, the automatic pull of the capture
image and the exit-75 retry with a writable mount could never run there: the first stderr line
ended the backup with a NativeCommandError instead (record, 2026-09-11). Driven with REAL child
processes (fake docker/age .cmd files on a PATH holding nothing else but System32), because a
PowerShell function stub cannot produce native stderr.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "deploy" / "backup-platform.ps1"
# The public snapshot withholds the backup scripts, so there is nothing to drive there.
pytestmark = pytest.mark.skipif(not BACKUP.is_file(), reason="the backup script is withheld from this tree")

DOCKER = r"""@echo off
>> "%~dp0env.txt" echo %TEMP%^|%TMP%^|%USERPROFILE%^|%DOCKER_CONFIG%
>> "%~dp0argv.txt" echo docker %*
if "%1"=="version" if exist "%~dp0daemon-down" ( echo error during connect: the daemon is not running 1>&2 & exit /b 1 )
if "%1"=="version" ( echo 27.0.0& exit /b 0 )
if "%1"=="volume" ( echo platform_recipe_book_data& exit /b 0 )
if "%1"=="image" ( echo Error: No such image: python:3.11-slim 1>&2 & exit /b 1 )
if "%1"=="pull" ( echo pulled& exit /b 0 )
if "%1"=="run" if not exist "%~dp0ran-once" ( type nul > "%~dp0ran-once" & echo sqlite3.OperationalError: unable to open database file 1>&2 & exit /b 75 )
if "%1"=="run" ( echo still failing 1>&2 & exit /b 3 )
exit /b 0
"""
OK = "@echo off\r\nexit /b 0\r\n"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# --- a profile of each run's own ---------------------------------------------------------------------
# backup-platform.ps1 stages in, logs to and makes its empty docker config under TEMP, and reads
# ~/.docker/config.json and its age key under USERPROFILE. Under the user's own profile these runs
# pointed DOCKER_CONFIG at <the real Temp>\platform-docker-config, and wrote that folder when it was
# missing: ~/.docker/config.json is a directory on the box they ran on (Repair-DockerConfigEnv), and
# one dated 2026-10-13 was in its Temp. So every run here gets a scratch profile, as
# test_disabled_rails.py's warned_run does.

# What the script writes under TEMP: the empty docker config, its log, the staging default with no D:.
TEMP_NAMES = ("platform-docker-config", "ai-platform-backup.log", "platform-backup-stage")


def scratch_profile(base: Path) -> tuple[Path, Path, str]:
    """(profile, its Temp, the PowerShell that makes them the run's own) under `base`."""
    prof = base / "scratch-profile"
    temp = prof / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    return prof, temp, (f"$env:USERPROFILE = {q(prof)}; $env:TEMP = {q(temp)}; $env:TMP = {q(temp)}\n"
                        "Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue\n")


def temp_state(temp: Path) -> dict:
    """Each of TEMP_NAMES in `temp`: what is there (a folder's files too) and when it was written."""
    out = {}
    for n in TEMP_NAMES:
        p = temp / n
        found = [p, *sorted(p.rglob("*"))] if p.is_dir() else [p]
        out[n] = [(str(f.relative_to(temp)), f.stat().st_mtime_ns) for f in found if f.exists()]
    return out


@pytest.fixture(scope="module")
def runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("bk")
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    script = deploy / BACKUP.name
    shutil.copy2(BACKUP, script)
    shutil.copy2(REPO / "deploy" / "installer" / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    fake = base / "bin"
    fake.mkdir()
    (fake / "docker.cmd").write_bytes(DOCKER.replace("\n", "\r\n").encode("ascii"))
    for n in ("age.cmd", "age-keygen.cmd"):
        (fake / n).write_bytes(OK.encode("ascii"))
    # A podman beside docker for the "guess" run, listing nothing, in a folder of its own.
    pod = base / "podman-bin"
    pod.mkdir()
    (pod / "podman.cmd").write_bytes(b'@echo off\r\n>> "' + str(fake / "argv.txt").encode("ascii") +
                                     b'" echo podman %*\r\nexit /b 0\r\n')
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    # -RuntimeMode desktop: the fake docker lists no gateway, and with podman beside it an undeclared
    # runtime is a guess, which the script refuses (the "guess" run below). docker alone is Docker
    # Desktop (the "docker-alone" run, which then meets the stopped daemon).
    args = (f"-Only platform_recipe_book_data -Target {q(base / 'target')} -StagingRoot {q(base / 'stage')} "
            "-AgeRecipient age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq -SkipRestoreTest -AcknowledgeKeyEscrow")
    prof, temp, body = scratch_profile(base)
    # A stray ~/.docker/config.json directory, as on the box these ran on, so the script does write
    # its empty docker config, and where it lands is seen.
    (prof / ".docker" / "config.json").mkdir(parents=True)
    real_temp = Path(os.environ.get("TEMP") or tempfile.gettempdir()).resolve()
    before = temp_state(real_temp)
    for name in ("guess", "docker-alone", "daemon-down", "capture"):
        if name == "guess":
            body += f"$env:Path = {q(fake)} + ';' + {q(pod)} + ';' + {q(system32)}\n"
            body += f"New-Item -ItemType File -Path {q(fake / 'argv.txt')} -Force | Out-Null\n"
        elif name == "docker-alone":
            body += f"$env:Path = {q(fake)} + ';' + {q(system32)}\n"
            body += f"Copy-Item -LiteralPath {q(fake / 'argv.txt')} -Destination {q(base / 'argv-guess.txt')}\n"
            body += f"New-Item -ItemType File -Path {q(fake / 'daemon-down')} | Out-Null\n"
        elif name == "capture":
            body += f"Remove-Item -LiteralPath {q(fake / 'daemon-down')}\n"
        mode = "" if name in ("guess", "docker-alone") else "-RuntimeMode desktop "
        body += f"""
Write-Output '<<<{name}>>>'
try {{ & {q(script)} {mode}{args} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)"; Write-Output "ERRID: $($_.FullyQualifiedErrorId)" }}
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = text
    out["argv"] = (fake / "argv.txt").read_text(encoding="ascii", errors="replace").splitlines()
    out["argv-guess"] = (base / "argv-guess.txt").read_text(encoding="ascii", errors="replace").splitlines()
    out["recipient_written"] = (deploy / "backup-age-recipient.txt").exists()
    out["env"] = (fake / "env.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    out["profile"], out["temp"] = prof, temp
    out["real_temp"], out["real_before"], out["real_after"] = real_temp, before, temp_state(real_temp)
    return out


def test_the_runs_keep_their_temp_files_in_a_profile_of_their_own(runs):
    """Every docker call saw the fixture's own TEMP, TMP and USERPROFILE, and DOCKER_CONFIG in that
    Temp, where the empty config the script made for the stray directory is. The user's real Temp
    gained and changed nothing the script writes there (ops-e follow-up, 2026-10-15)."""
    prof, temp = runs["profile"], runs["temp"]
    ours = temp / "platform-docker-config"
    assert runs["env"] and set(runs["env"]) == {f"{temp}|{temp}|{prof}|{ours}"}, set(runs["env"])
    assert (ours / "config.json").is_file()
    assert runs["real_after"] == runs["real_before"], (runs["real_temp"], runs["real_before"], runs["real_after"])


def test_an_undeclared_runtime_no_cli_proves_is_refused(runs):
    """No PLATFORM_RUNTIME and no CLI listing the gateway: the first CLI on PATH was asked for
    volumes, podman on a box carrying both. Refused before any volume is read."""
    out = runs["guess"]
    assert "THROWN: the runtime is a guess" in out and "-RuntimeMode" in out, out
    asked = runs["argv-guess"]
    assert asked and all(a.startswith(("docker ps ", "podman ps ")) for a in asked), asked


def test_docker_as_the_only_engine_cli_is_docker_desktop_with_the_stack_down(runs):
    """A full-stack box declares no runtime, and with its stack down no CLI lists the gateway: the
    backup was refused as a guess. docker alone is Docker Desktop (delta audit 2026-10-15), so it goes
    on, to the daemon check here (the fake's daemon is down)."""
    out = runs["docker-alone"]
    assert "runtime     desktop (" in out and "docker is the only engine CLI on PATH" in out, out
    assert "the runtime is a guess" not in out and "THROWN: the Docker daemon is not reachable" in out, out


def test_a_stopped_daemon_gets_the_designed_message(runs):
    out = runs["daemon-down"]
    assert "THROWN: the Docker daemon is not reachable" in out, out
    assert "NativeCommandError" not in out, out


def test_a_missing_capture_image_is_pulled_and_the_exit_75_retry_runs(runs):
    out = runs["capture"]
    assert "pulling python:3.11-slim" in out, out
    assert "no -shm index; re-reading with a writable mount" in out, out
    assert "THROWN: capture of platform_recipe_book_data failed (exit 3)" in out, out
    assert "NativeCommandError" not in out, out
    runs_seen = [a for a in runs["argv"] if a.startswith("docker run ")]
    assert len(runs_seen) == 2 and ":/src:ro " in runs_seen[0] and ":/src:ro " not in runs_seen[1], runs_seen


def test_a_supplied_recipient_is_not_written_into_the_checkout(runs):
    """-AgeRecipient was rewritten into deploy/backup-age-recipient.txt, a TRACKED file, on every run
    that got past the preflight, leaving a live checkout dirty."""
    assert runs["recipient_written"] is False


# --- -DryRun's size probe reaches docker as ONE argument, under both engines ----------------------

# A fake docker that RECORDS the argv it was given, parsed by the C runtime's rules (the ones docker,
# a Go program, also follows on Windows), and answers the three calls -DryRun makes. The .cmd only
# forwards, so what the .py records is what a real docker.exe would have received.
FAKE_DOCKER_PY = r'''import json
import os
import sys
from pathlib import Path

with (Path(__file__).parent / "argv-dry.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
with (Path(__file__).parent / "env-dry.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({k: os.environ.get(k, "") for k in ("TEMP", "USERPROFILE", "DOCKER_CONFIG")}) + "\n")
a = sys.argv[1:]
if a[:1] == ["version"]:
    print("27.0.0")
elif a[:2] == ["volume", "ls"]:
    print("platform_recipe_book_data")
elif a[:1] == ["run"]:
    print("123 4 platform_recipe_book_data")
'''


def _probe_literal() -> str:
    """The sh script -DryRun hands to `docker run ... alpine:3 sh -c`, as the .ps1 spells it."""
    import re
    src = BACKUP.read_text(encoding="utf-8")
    m = re.search(r"'sh', '-c',\s*'((?:[^']|'')*)'", src)
    assert m, "the -DryRun size probe is no longer an <image>, 'sh', '-c', '<script>' literal"
    return m.group(1).replace("''", "'")


@pytest.fixture(scope="module")
def dry_run(run_ps, shell, tmp_path_factory):
    import json
    import sys
    base = tmp_path_factory.mktemp("bkdry")
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    script = deploy / BACKUP.name
    shutil.copy2(BACKUP, script)
    shutil.copy2(REPO / "deploy" / "installer" / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    fake = base / "bin"
    fake.mkdir()
    (fake / "fake_docker.py").write_text(FAKE_DOCKER_PY, encoding="utf-8")
    (fake / "docker.cmd").write_bytes(f'@"{sys.executable}" "%~dp0fake_docker.py" %*\r\n'.encode("ascii"))
    for n in ("age.cmd", "age-keygen.cmd"):
        (fake / n).write_bytes(OK.encode("ascii"))
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    args = (f"-DryRun -RuntimeMode desktop -Only platform_recipe_book_data -Target {q(base / 'target')} -StagingRoot {q(base / 'stage')} "
            "-AgeRecipient age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq -AcknowledgeKeyEscrow")
    prof, temp, own = scratch_profile(base)
    body = (own + f"$env:Path = {q(fake)} + ';' + {q(system32)}\n"
            f"try {{ & {q(script)} {args} *>&1 | ForEach-Object {{ [string]$_ }} }}\n"
            "catch { Write-Output \"THROWN: $($_.Exception.Message)\" }\n")
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = fake / "argv-dry.jsonl"
    calls = [json.loads(ln) for ln in rec.read_text(encoding="utf-8").splitlines()] if rec.is_file() else []
    envs = fake / "env-dry.jsonl"
    seen = [json.loads(ln) for ln in envs.read_text(encoding="utf-8").splitlines()] if envs.is_file() else []
    return r.stdout, [c for c in calls if c[:1] == ["run"]], {"profile": prof, "temp": temp, "seen": seen}


def test_the_dry_run_size_probe_reaches_docker_as_one_argument(dry_run):
    """Windows PowerShell 5.1 does not escape a double quote inside a native argument, so a probe
    script carrying one reached docker in pieces: sh ran half a script ("unexpected end of file")
    and -DryRun reported every volume as 0 bytes, 0 files, a 1.4 GB backup as 24 KB (2026-10-08).
    The script docker receives must be the one the .ps1 wrote, whole, and its answer must be read."""
    out, runs, _ = dry_run
    assert len(runs) == 1, (runs, out)
    argv = runs[0]
    i = argv.index("-c")
    assert argv[i - 1] == "sh" and argv[i + 1:] == [_probe_literal()], argv
    import re
    assert re.search(r"platform_recipe_book_data\s+volume\s+\d+\s+123\s+4\b", out), out


# --- a podman box on the lean stack ---------------------------------------------------------------
# The backup called docker everywhere and took the full stack's member list, so on a podman box
# (no docker CLI, the lean stack) it could not even start. Real child processes again: podman and
# docker-compose are Python fakes behind .cmd shims (the dry-run probe carries a `|`, which a .cmd
# body cannot echo), and docker.cmd only records that it was called.

FAKE_PODMAN_PY = r'''import json
import os
import sys
from pathlib import Path

here = Path(__file__).parent
a = sys.argv[1:]
with (here / "podman.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(a) + "\n")
with (here / "podman-env.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": a, **{k: os.environ.get(k, "") for k in ("DOCKER_CONFIG", "TEMP", "USERPROFILE")}}) + "\n")
if a[:1] == ["inspect"]:
    print((here / "labels.json").read_text(encoding="utf-8"))
elif a[:1] == ["version"]:
    print("5.6.0")
elif a[:2] == ["volume", "ls"]:
    print("\n".join(["platform_gateway_data", "platform_ai_playground_data", "platform_terminal_fun_data",
                     "platform_recipe_book_data", "unrelated_volume"]))
elif a[:2] == ["image", "inspect"]:
    sys.stderr.write("Error: docker.io/library/python:3.11-slim: image not known\n")
    sys.exit(1)
elif a[:1] == ["pull"]:
    print("pulled")
elif a[:1] == ["run"] and "sh" in a:
    for arg in a:
        if ":/m/" in arg:
            print("2048 3 " + arg.split(":/m/")[1].split(":")[0])
elif a[:1] == ["run"]:
    flag = here / "ran-once"
    if not flag.exists():
        flag.write_text("x")
        sys.stderr.write("sqlite3.OperationalError: unable to open database file\n")
        sys.exit(75)
    sys.stderr.write("still failing\n")
    sys.exit(3)
'''

FAKE_COMPOSE_PY = r'''import json
import os
import sys
from pathlib import Path

here = Path(__file__).parent
with (here / "compose.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "DOCKER_HOST": os.environ.get("DOCKER_HOST", "")}) + "\n")
sys.stderr.write("WARN[0000] a compose warning on stderr must not reach the JSON\n")
print((here / "render.json").read_text(encoding="utf-8"))
'''

LEAN_RENDER = {
    "services": {
        "gateway": {"volumes": [{"type": "volume", "source": "gateway_data", "target": "/data"}]},
        "ai-playground": {"volumes": [{"type": "volume", "source": "ai_playground_data", "target": "/srv/var"}]},
        "terminal-fun": {"volumes": [{"type": "volume", "source": "terminal_fun_data", "target": "/data"}]},
        "openmaic-app": {"volumes": [{"type": "volume", "source": "openmaic_app_data", "target": "/app/data"}]},
    },
    "volumes": {k: {"name": f"platform_{k}"} for k in
                ("gateway_data", "ai_playground_data", "terminal_fun_data", "openmaic_app_data")},
}


def _lean_podman_box(base: Path):
    """A scratch checkout of the backup script on a lean podman box, with podman, docker-compose,
    docker and age as fakes on a PATH holding nothing else but System32. Returns the script, the
    fakes' directory, deploy/, the installer compose file, and the PowerShell that sets PATH and
    gives the run a profile of its own (scratch_profile)."""
    import json
    import sys
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    script = deploy / BACKUP.name
    shutil.copy2(BACKUP, script)
    shutil.copy2(REPO / "deploy" / "installer" / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    (deploy / ".env").write_text("PLATFORM_ENABLED_APPS=ai-playground\n", encoding="utf-8")
    fake = base / "bin"
    fake.mkdir()
    for name, src in (("podman", FAKE_PODMAN_PY), ("docker-compose", FAKE_COMPOSE_PY)):
        (fake / f"fake_{name}.py").write_text(src, encoding="utf-8")
        (fake / f"{name}.cmd").write_bytes(f'@"{sys.executable}" "%~dp0fake_{name}.py" %*\r\n'.encode("ascii"))
    (fake / "docker.cmd").write_bytes(b'@echo off\r\n>> "%~dp0docker-called.txt" echo docker\r\nexit /b 1\r\n')
    for n in ("age.cmd", "age-keygen.cmd"):
        (fake / n).write_bytes(OK.encode("ascii"))
    installer_yml = deploy / "installer" / "docker-compose.installer.yml"
    (fake / "labels.json").write_text(json.dumps(
        {"com.docker.compose.project.config_files": str(installer_yml)}), encoding="utf-8")
    (fake / "render.json").write_text(json.dumps(LEAN_RENDER), encoding="utf-8")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    _prof, _temp, own = scratch_profile(base)
    path_ps = own + (f"$env:Path = {q(fake)} + ';' + {q(system32)}\n"
                     "Remove-Item Env:DOCKER_HOST, Env:PLATFORM_ENABLED_APPS -ErrorAction SilentlyContinue\n")
    return script, fake, deploy, installer_yml, path_ps


LEAN_ARGS = "-AgeRecipient age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq -AcknowledgeKeyEscrow"


@pytest.fixture(scope="module")
def podman_runs(run_ps, shell, tmp_path_factory):
    import json
    base = tmp_path_factory.mktemp("bkpod")
    script, fake, deploy, installer_yml, body = _lean_podman_box(base)
    common = (f"-RuntimeMode podman -Target {q(base / 'target')} -StagingRoot {q(base / 'stage')} "
              + LEAN_ARGS)
    # No -Stack: it is read off the gateway container's compose label.
    for name, args in (("dry", f"-DryRun {common}"),
                       ("capture", f"-Only platform_gateway_data -SkipRestoreTest {common}")):
        body += f"""
Write-Output '<<<{name}>>>'
try {{ & {q(script)} {args} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = text
    out["podman"] = [json.loads(ln) for ln in (fake / "podman.jsonl").read_text(encoding="utf-8").splitlines()]
    out["compose"] = [json.loads(ln) for ln in (fake / "compose.jsonl").read_text(encoding="utf-8").splitlines()]
    out["docker_called"] = (fake / "docker-called.txt").exists()
    out["recipient_written"] = (deploy / "backup-age-recipient.txt").exists()
    out["deploy"], out["installer_yml"] = deploy, installer_yml
    out["podman-env"] = [json.loads(ln) for ln in (fake / "podman-env.jsonl").read_text(encoding="utf-8").splitlines()]
    out["profile"] = base / "scratch-profile"
    return out


def test_the_dry_run_and_the_lean_runs_have_a_profile_of_their_own(dry_run, podman_runs):
    """Every CLI call of the -DryRun fixture and of the lean podman box's runs saw a scratch TEMP
    and USERPROFILE, and no DOCKER_CONFIG: neither profile holds a stray ~/.docker/config.json, so
    the script made no empty config, in the user's Temp or anywhere (ops-e follow-up, 2026-10-15)."""
    _, _, dry = dry_run
    want = {"TEMP": str(dry["temp"]), "USERPROFILE": str(dry["profile"]), "DOCKER_CONFIG": ""}
    assert dry["seen"] and all(s == want for s in dry["seen"]), dry["seen"]
    prof = podman_runs["profile"]
    want = {"TEMP": str(prof / "AppData" / "Local" / "Temp"), "USERPROFILE": str(prof), "DOCKER_CONFIG": ""}
    seen = [{k: c[k] for k in want} for c in podman_runs["podman-env"]]
    assert seen and all(s == want for s in seen), seen


def test_a_lean_podman_box_sizes_the_volumes_its_compose_declares_through_podman(podman_runs):
    import re
    out = podman_runs["dry"]
    assert "stack       lean (platform-gateway-1 was created from" in out, out
    rows = {m.group(1): m.group(2) for m in re.finditer(r"(?m)^(\S+)\s+(volume|file)\s+\d\s", out)}
    assert set(rows) == {"deploy-env", "platform_gateway_data", "platform_ai_playground_data",
                         "platform_terminal_fun_data"}, out
    assert re.search(r"platform_gateway_data\s+volume\s+2\s+2,?048\s+3\b", out), out
    assert "not on this box  platform_openmaic_app_data: declared, but no such volume yet" in out, out
    assert "not on this box  platform_iep_goals_db: not declared by this box's compose" in out, out
    probe = [a for a in podman_runs["podman"] if a[:1] == ["run"] and "sh" in a]
    assert len(probe) == 1 and "docker.io/library/alpine:3" in probe[0], probe
    assert "platform_gateway_data:/m/platform_gateway_data:ro" in probe[0], probe
    first = podman_runs["compose"][0]
    d = podman_runs["deploy"]
    assert first["argv"] == ["--env-file", str(d / ".env"), "-f", str(podman_runs["installer_yml"]),
                             "--profile", "ai-playground", "config", "--format", "json"], first
    assert first["DOCKER_HOST"] == "npipe:////./pipe/podman-machine-default", first


def test_a_lean_podman_box_captures_through_podman_with_its_retry_and_never_calls_docker(podman_runs):
    out = podman_runs["capture"]
    assert "pulling docker.io/library/python:3.11-slim" in out, out
    assert "no -shm index; re-reading with a writable mount" in out, out
    assert "THROWN: capture of platform_gateway_data failed (exit 3)" in out, out
    runs_seen = [" ".join(a) for a in podman_runs["podman"] if a[:1] == ["run"] and "sh" not in a]
    assert len(runs_seen) == 2, runs_seen
    assert "platform_gateway_data:/src:ro " in runs_seen[0] and ":/src:ro " not in runs_seen[1], runs_seen
    assert podman_runs["docker_called"] is False
    assert podman_runs["recipient_written"] is False


# --- a ~/.docker/config.json that is a DIRECTORY ---------------------------------------------------
# podman run / pull read that file for registry auth and fail with exit 125 when it is a directory,
# so every volume read failed (tools/tests/test_docker_config_env.py has the measurement). The
# backup now points DOCKER_CONFIG at an empty config of its own first, as smoke-test does.

@pytest.fixture(scope="module")
def stray_config_run(run_ps, shell, tmp_path_factory):
    import json
    base = tmp_path_factory.mktemp("bkcfg")
    script, fake, _deploy, _yml, body = _lean_podman_box(base)
    prof, temp = base / "profile", base / "temp"
    (prof / ".docker" / "config.json").mkdir(parents=True)     # the stray directory
    temp.mkdir()
    args = (f"-Only platform_gateway_data -SkipRestoreTest -RuntimeMode podman -Target {q(base / 'target')} "
            f"-StagingRoot {q(base / 'stage')} " + LEAN_ARGS)
    body += f"""
$env:USERPROFILE = {q(prof)}; $env:TEMP = {q(temp)}; $env:TMP = {q(temp)}
Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
try {{ & {q(script)} {args} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = [json.loads(ln) for ln in (fake / "podman-env.jsonl").read_text(encoding="utf-8").splitlines()]
    return r.stdout, calls, prof, temp


# --- an 8.3 TEMP on a box with no D: ---------------------------------------------------------------
# Staging fell back to %TEMP% as given, and Remove-Item throws on a path through the 8.3 form of
# the profile folder (conftest's temp_dir_form), so the first member's cleanup ended the backup and
# the finally's cleanup threw too: the member's PLAINTEXT archive stayed in %TEMP%. Here the capture
# succeeds (a fake podman stages an archive and its meta.json as the capture container does, and a
# fake age copies it to the target), so every cleanup is reached.

FAKE_CAPTURE_PY = r'''import hashlib
import json
import sys
from pathlib import Path

here = Path(__file__).parent
a = sys.argv[1:]
if a[:1] == ["inspect"]:
    print((here / "labels.json").read_text(encoding="utf-8"))
elif a[:1] == ["version"]:
    print("5.6.0")
elif a[:2] == ["volume", "ls"]:
    print("platform_gateway_data")
elif a[:2] == ["image", "inspect"]:
    print("[]")
elif a[:1] == ["run"]:
    stage = Path(next(v for v in a if v.endswith(":/out"))[:-len(":/out")])
    name = a[-1]
    data = b"plaintext of " + name.encode()
    (stage / (name + ".tar.gz")).write_bytes(data)
    (stage / (name + ".meta.json")).write_text(json.dumps({
        "tar_gz_sha256": hashlib.sha256(data).hexdigest(), "tar_gz_bytes": len(data),
        "file_count": 1, "raw_bytes": len(data), "sqlite_opened_via": None,
        "sqlite_tables": None, "members": []}), encoding="utf-8")
    with (here / "staged.txt").open("a", encoding="utf-8") as fh:
        fh.write(str(stage) + "\n")
    print("captured " + name)
'''
# age -r <recipient> -o <out> <in>: the "ciphertext" is a copy; %~ drops PowerShell's quotes.
FAKE_AGE = '@echo off\r\ncopy /y "%~5" "%~4" >nul\r\nexit /b %errorlevel%\r\n'


def test_an_8_3_temp_leaves_no_plaintext_staging_behind(run_ps, shell, tmp_path, temp_dir_form):
    if Path("D:\\").exists():
        pytest.skip("this box has a D:, so staging defaults to D:\\tmp, not %TEMP%")
    import sys
    temp_as_given, temp_long = temp_dir_form
    script, fake, _deploy, _yml, body = _lean_podman_box(tmp_path / "box")
    (fake / "fake_podman.py").write_text(FAKE_CAPTURE_PY, encoding="utf-8")
    (fake / "podman.cmd").write_bytes(f'@"{sys.executable}" "%~dp0fake_podman.py" %*\r\n'.encode("ascii"))
    (fake / "age.cmd").write_bytes(FAKE_AGE.encode("ascii"))
    target = tmp_path / "target"
    # No -StagingRoot: the default is what is under test.
    args = f"-Only platform_gateway_data -SkipRestoreTest -RuntimeMode podman -Target {q(target)} " + LEAN_ARGS
    body += f"""
$env:TEMP = {q(temp_as_given)}; $env:TMP = {q(temp_as_given)}
try {{ & {q(script)} {args} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    staged = (fake / "staged.txt").read_text(encoding="utf-8").splitlines() if (fake / "staged.txt").is_file() else []
    assert staged, f"the capture never staged an archive, so this proved nothing: {r.stdout}"
    assert "THROWN" not in r.stdout, r.stdout
    left = [str(p) for p in (temp_long / "platform-backup-stage").rglob("*") if p.is_file()] \
        if (temp_long / "platform-backup-stage").exists() else []
    assert left == [], f"plaintext left in TEMP: {left}"
    assert list(target.glob("*/platform_gateway_data*")) or list(target.glob("*/**/platform_gateway_data*")), \
        f"the backup did not finish: {r.stdout}"


# --- a podman restore hands OpenMAIC's volume to the app's user ------------------------------------
# RESTORE.md's refill of platform_openmaic_app_data ran in a root alpine container, which under
# podman is the volume's first mount, so the volume came back root-owned and OpenMAIC v1.1.3 (uid
# 1001) could not write it. The real script, run to the end on a lean podman box with the capture
# faked, writes a RESTORE.md whose openmaic refill chowns after the untar and whose other refills
# do not.

def test_a_podman_restore_plan_chowns_the_openmaic_volume_and_nothing_else(run_ps, shell, tmp_path):
    import sys
    script, fake, _deploy, _yml, body = _lean_podman_box(tmp_path / "box")
    both = FAKE_CAPTURE_PY.replace('    print("platform_gateway_data")\n',
                                   '    print("platform_gateway_data\\nplatform_openmaic_app_data")\n')
    assert both != FAKE_CAPTURE_PY
    (fake / "fake_podman.py").write_text(both, encoding="utf-8")
    (fake / "podman.cmd").write_bytes(f'@"{sys.executable}" "%~dp0fake_podman.py" %*\r\n'.encode("ascii"))
    (fake / "age.cmd").write_bytes(FAKE_AGE.encode("ascii"))
    target = tmp_path / "target"
    args = (f"-Only platform_gateway_data,platform_openmaic_app_data -SkipRestoreTest -RuntimeMode podman "
            f"-Target {q(target)} -StagingRoot {q(tmp_path / 'stage')} " + LEAN_ARGS)
    body += f"""
try {{ & {q(script)} {args} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $($_.Exception.Message)" }}
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "THROWN" not in r.stdout, r.stdout
    restore = list(target.glob("*/RESTORE.md"))
    assert len(restore) == 1, (restore, r.stdout)
    lines = restore[0].read_text(encoding="utf-8-sig").splitlines()
    refills = [ln for ln in lines if ln.startswith("podman run --rm -v ") and ":/dst " in ln]
    assert len(refills) == 2, lines                     # both volumes were captured and get a refill
    om = [ln for ln in refills if "platform_openmaic_app_data:/dst" in ln]
    gw = [ln for ln in refills if "platform_gateway_data:/dst" in ln]
    assert len(om) == 1 and om[0].endswith(
        "tar -xzf /in/platform_openmaic_app_data.tar.gz -C /dst && chown -R 1001:65533 /dst\""), om
    assert len(gw) == 1 and gw[0].endswith("tar -xzf /in/platform_gateway_data.tar.gz -C /dst\""), gw


def test_a_config_json_directory_does_not_reach_the_backups_containers(stray_config_run):
    out, calls, prof, temp = stray_config_run
    ours = temp / "platform-docker-config"
    containers = [c for c in calls if c["argv"][:1] in (["run"], ["pull"])]
    assert {c["argv"][0] for c in containers} == {"run", "pull"}, (calls, out)   # both were reached
    assert all(c["DOCKER_CONFIG"] == str(ours) for c in containers), containers
    assert json.loads((ours / "config.json").read_text(encoding="utf-8")) == {}
    assert str(prof / ".docker" / "config.json") in out and "is a DIRECTORY" in out, out
    assert (prof / ".docker" / "config.json").is_dir()                     # never touched
