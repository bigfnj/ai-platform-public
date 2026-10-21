"""deploy/provision-speech-venv.ps1 -VerifyOnly reaches its verdict under either spelling of TEMP.

The verify step writes a probe script to TEMP, runs it with the venv's python, removes it, and only
THEN reports. With TEMP in the 8.3 short form the removal threw (Remove-Item's terminating error on
a path through the short form of the user-profile folder, which -ErrorAction does not silence), and
under the script's EAP=Stop that ended the run with no verdict either way. conftest.py's
temp_dir_form has the detail.

The venv here is a real, EMPTY one (`python -m venv --without-pip` in tmp): its python runs the
probe, which fails at `import torch`, so the run must end in the script's own FAILED verdict.
Nothing is installed and nothing outside tmp is written.

The second half: the interpreter a NEW venv is built from. Its default was one box's own profile
Python, a literal path that is wrong on every other box (and shipped in public). It is found at
run time now, the py launcher's 3.11 first, then a python3.11 on PATH, and -VerifyOnly reports
which one -Recreate would use. Fakes stand in for both (a py.cmd and a python3.11.cmd that only
echo), first on PATH, so the real launcher on this box decides nothing. The one test that builds
nothing but runs the CREATE path does so with a PATH holding no Python at all, so even a broken
resolver has nothing to build with.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "provision-speech-venv.ps1"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


@pytest.fixture(scope="module")
def empty_venv(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("a Windows venv layout (Scripts\\python.exe)")
    v = tmp_path_factory.mktemp("speechvenv") / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(v)], check=True, timeout=180)
    assert (v / "Scripts" / "python.exe").is_file()
    return v


def test_verify_only_reaches_its_verdict_and_removes_its_probe(run_ps, shell, empty_venv,
                                                               temp_dir_form):
    given, long_dir = temp_dir_form
    body = (f"$env:TEMP = {q(given)}\n$env:TMP = {q(given)}\n"
            f"& {q(SCRIPT)} -VenvPath {q(empty_venv)} -VerifyOnly\nexit $LASTEXITCODE\n")
    r = run_ps(shell, body, timeout=180)
    out = r.stdout + r.stderr
    # Positive control: the probe really ran, from the venv, and failed where an empty venv must.
    assert "No module named 'torch'" in out, out
    assert "verification FAILED" in r.stdout, out
    assert r.returncode == 1, out
    assert not list(long_dir.glob("speech_venv_verify*.py")), "the probe script was left in TEMP"


# --- two runs at once: each probe is its run's own ------------------------------------------------

def test_a_run_never_touches_another_runs_probe(run_ps, shell, empty_venv, tmp_path):
    """Deterministic half: the probe a concurrent run is in the middle of using, at the name every
    run used to share. The old script overwrote it, ran it and deleted it."""
    temp = tmp_path / "shared temp"
    temp.mkdir()
    theirs = temp / "speech_venv_verify.py"
    theirs.write_text("# another run's probe\n", encoding="utf-8")
    body = (f"$env:TEMP = {q(temp)}\n$env:TMP = {q(temp)}\n"
            f"& {q(SCRIPT)} -VenvPath {q(empty_venv)} -VerifyOnly\nexit $LASTEXITCODE\n")
    r = run_ps(shell, body, timeout=180)
    out = r.stdout + r.stderr
    assert "No module named 'torch'" in out and r.returncode == 1, out
    assert theirs.read_text(encoding="utf-8") == "# another run's probe\n"
    assert sorted(p.name for p in temp.iterdir()) == ["speech_venv_verify.py"], list(temp.iterdir())


def test_two_concurrent_runs_both_reach_their_verdict(shell, empty_venv, tmp_path):
    """The failure the iep-render lane hit: two runs under one TEMP at the same moment."""
    temp = tmp_path / "shared temp"
    temp.mkdir()
    env = dict(__import__("os").environ, TEMP=str(temp), TMP=str(temp))
    argv = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-VenvPath", str(empty_venv), "-VerifyOnly"]
    procs = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                              encoding="utf-8", errors="replace") for _ in range(2)]
    outs = [p.communicate(timeout=180)[0] for p in procs]
    for p, out in zip(procs, outs):
        assert "No module named 'torch'" in out and "verification FAILED" in out, out
        assert p.returncode == 1, out
    assert not list(temp.glob("speech_venv_verify*.py")), list(temp.iterdir())


# --- the base interpreter: found at run time, never a literal path ---------------------------

def _fakes(tmp_path: Path, *, launcher_has_311: bool, on_path: bool) -> tuple[Path, Path, Path]:
    """(the fake bin dir, the file the launcher names, the python3.11 shim). The launcher answers
    only `-3.11`, the way py does, and exits 103 (py's "no suitable runtime") when it has none."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    named = tmp_path / "launcher311" / "python.exe"
    named.parent.mkdir()
    named.write_bytes(b"")                   # it only has to exist: nothing runs it under -VerifyOnly
    answer = (f"echo {named}\r\nexit /b 0" if launcher_has_311
              else "echo No suitable Python runtime found 1>&2\r\nexit /b 103")
    (bin_dir / "py.cmd").write_text(f'@echo off\r\nif not "%1"=="-3.11" exit /b 2\r\n{answer}\r\n',
                                    encoding="ascii")
    shim = bin_dir / "python3.11.cmd"
    if on_path:
        shim.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    return bin_dir, named, shim


def _verify(run_ps, shell, venv: Path, bin_dir: Path):
    body = (f"$env:PATH = {q(bin_dir)} + ';' + $env:PATH\n"
            f"& {q(SCRIPT)} -VenvPath {q(venv)} -VerifyOnly\nexit $LASTEXITCODE\n")
    return run_ps(shell, body, timeout=180)


def test_verify_only_names_the_launchers_3_11(run_ps, shell, empty_venv, tmp_path):
    bin_dir, named, _shim = _fakes(tmp_path, launcher_has_311=True, on_path=True)
    r = _verify(run_ps, shell, empty_venv, bin_dir)
    out = r.stdout + r.stderr
    assert "No module named 'torch'" in out, out            # the probe ran: the run got this far
    line = next((ln for ln in r.stdout.splitlines() if "base python" in ln), "")
    assert str(named) in line and "(py -3.11)" in line, out


def test_a_launcher_without_3_11_falls_back_to_python3_11_on_path(run_ps, shell, empty_venv,
                                                                  tmp_path):
    bin_dir, _named, shim = _fakes(tmp_path, launcher_has_311=False, on_path=True)
    r = _verify(run_ps, shell, empty_venv, bin_dir)
    out = r.stdout + r.stderr
    line = next((ln for ln in r.stdout.splitlines() if "base python" in ln), "")
    assert str(shim) in line and "(python3.11 on PATH)" in line, out


def test_with_no_python_3_11_building_a_venv_stops_and_says_how_to_fix_it(run_ps, shell, tmp_path):
    """The CREATE path, with a PATH that holds no interpreter at all: the fake launcher has no
    3.11 and nothing else is on PATH. It must stop before anything is created, and name both
    remedies."""
    bin_dir, _named, _shim = _fakes(tmp_path, launcher_has_311=False, on_path=False)
    venv = tmp_path / "never-built"
    body = (f"$env:PATH = {q(bin_dir)}\n"
            f"try {{ & {q(SCRIPT)} -VenvPath {q(venv)} }} catch {{ Write-Output \"THREW: $_\"; exit 7 }}\n"
            "exit $LASTEXITCODE\n")
    r = run_ps(shell, body, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 7, out
    assert "no Python 3.11 found" in out and "-BasePython" in out and "py -3.11" in out, out
    assert not venv.exists(), "a venv was started with no interpreter to build it from"


def test_no_box_path_is_a_default():
    """The literal this replaced was a user-profile path; no drive-letter profile path may come
    back as a default, or anywhere else in the script."""
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert not re.search(r"[A-Za-z]:\\Users\\", text, re.I), "a user-profile path is back in the script"
    assert re.search(r"\[string\]\$BasePython\s*=\s*''", text), "BasePython's default is no longer empty"
