"""register-watchdog.ps1 says on the console why it refused, and its exit code says whether it worked.

Its refusals (no user profile to give the watchdog, a required file missing) and its catch wrote
ERROR and FAILED to C:\\Users\\Public\\register-watchdog.txt alone and then `return`ed, and the script
had no `exit`: run through RunAS Helper's /capture, which hands back the script's console and its
exit code, a refused registration exited 0 with nothing on screen (delta audit 2026-10-15).

Each case runs a scratch copy of the real registrar as its own process (`-File`, as /capture and an
elevated shell run it), under both engines. The copy differs from the script in two lines, each
asserted to match once: its log is the case's own file, not the shared one under C:\\Users\\Public,
and its nssm is a .cmd stand-in that records each call. Nothing is registered: the stand-in does
nothing, and the "done" case only reads the service table and the registry.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
REGISTRAR = INSTALLER / "register-watchdog.ps1"

LOG_LINE = "$out = 'C:\\Users\\Public\\register-watchdog.txt'\n"
NSSM_LINE = "    $NssmExe    = Join-Path $Root 'deploy\\bin\\nssm.exe'\n"
FAKE_NSSM = '@echo off\r\n>> "%~dp0calls.txt" echo %*\r\nexit /b 0\r\n'


def scratch_registrar(root: Path, log: Path, lib: bool = True) -> Path:
    """<root>\\deploy\\installer\\register-watchdog.ps1, with the two stand-in lines."""
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    text = REGISTRAR.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    assert text.count(LOG_LINE) == 1 and text.count(NSSM_LINE) == 1, "the registrar's log or nssm line moved"
    text = text.replace(LOG_LINE, f"$out = '{log}'\n").replace(NSSM_LINE, NSSM_LINE.replace("nssm.exe", "nssm.cmd"))
    (inst / "register-watchdog.ps1").write_text(text, encoding="utf-8-sig")
    if lib:
        (inst / "lib-runtime.ps1").write_bytes((INSTALLER / "lib-runtime.ps1").read_bytes())
    (inst / "platform-watchdog.ps1").write_text("# stand-in\n", encoding="utf-8")
    return inst / "register-watchdog.ps1"


@pytest.fixture(scope="module")
def registrations(shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("regexit")
    users = base / "Users"
    service_profile = base / "Windows" / "System32" / "config" / "systemprofile"   # a service account's, by shape
    service_profile.mkdir(parents=True)
    owner = users / "owner"
    owner.mkdir(parents=True)
    cases = {
        # Run as SYSTEM (through the gate) from a checkout outside any profile folder, no WATCHDOG_USER.
        "refused": dict(root=base / "D" / "projects" / "ai-platform", profile=service_profile, nssm=True),
        "no-nssm": dict(root=owner / "ai-platform-a", profile=owner, nssm=False),
        "throws": dict(root=owner / "ai-platform-b", profile=owner, nssm=True, lib=False),
        "done": dict(root=owner / "ai-platform-c", profile=owner, nssm=True),
    }
    out = {}
    for name, c in cases.items():
        log = base / f"{name}.txt"
        script = scratch_registrar(c["root"], log, lib=c.get("lib", True))
        bin_dir = c["root"] / "deploy" / "bin"
        bin_dir.mkdir(parents=True)
        if c["nssm"]:
            (bin_dir / "nssm.cmd").write_bytes(FAKE_NSSM.encode("ascii"))
        env = dict(os.environ, USERPROFILE=str(c["profile"]))
        for k in ("WATCHDOG_USER", "AIPLATFORM_DIR"):
            env.pop(k, None)
        r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                           capture_output=True, encoding="utf-8", errors="replace", env=env, cwd=base, timeout=180)
        calls = bin_dir / "calls.txt"
        out[name] = {"code": r.returncode, "console": r.stdout + r.stderr,
                     "log": log.read_text(encoding="utf-8-sig") if log.is_file() else "",
                     "calls": calls.read_text(encoding="ascii", errors="replace").splitlines() if calls.is_file() else [],
                     "root": c["root"]}
    return out


def _last_lines(text: str, n: int = 2) -> list[str]:
    return [ln.strip() for ln in text.strip().splitlines()][-n:]


def test_a_refused_profile_exits_1_and_says_why_on_the_console(registrations):
    got = registrations["refused"]
    assert got["code"] == 1, got
    assert "register-watchdog FAILED:" in got["console"] and "WATCHDOG_USER" in got["console"], got["console"]
    assert _last_lines(got["log"])[-1] == "FAILED" and "ERROR: USERPROFILE is" in got["log"], got["log"]
    assert got["calls"] == [], got["calls"]                     # refused before nssm was touched


def test_a_missing_nssm_exits_1_and_names_the_file(registrations):
    got = registrations["no-nssm"]
    nssm = got["root"] / "deploy" / "bin" / "nssm.cmd"
    assert got["code"] == 1, got
    assert f"Required file not found: {nssm}" in got["console"], got["console"]
    assert _last_lines(got["log"]) == [f"ERROR: Required file not found: {nssm}", "FAILED"], got["log"]


def test_an_error_inside_the_registration_exits_1_and_says_it(registrations):
    got = registrations["throws"]
    assert got["code"] == 1, got
    assert "register-watchdog FAILED:" in got["console"] and "lib-runtime.ps1" in got["console"], got["console"]
    assert _last_lines(got["log"])[-1] == "FAILED", got["log"]


def test_a_registration_that_ran_exits_0_after_done_and_says_so(registrations):
    got = registrations["done"]
    assert got["code"] == 0, got
    assert "register-watchdog DONE: platform-watchdog is registered" in got["console"], got["console"]
    assert "DONE" in _last_lines(got["log"]), got["log"]
    assert any(c.startswith("install platform-watchdog") for c in got["calls"]), got["calls"]
    assert any(c.startswith("start platform-watchdog") for c in got["calls"]), got["calls"]


# --- how the docs say to run it ---------------------------------------------------------------------
# WATCHDOG.md and the registrar's own header told the reader to run `RunAsHelper.exe`, the GUI
# launcher, which a shell does not wait for, so the exit code and the console above never reached
# anyone. And a shared kit must not depend on RunAS Helper at all: elevation goes through the
# standard UAC / BeyondTrust prompt. So each procedure leads with an elevated PowerShell, and RunAS
# Helper is the optional alternative, in its console form: `RunAsHelper /capture /timeout:N
# /ps:5 /as:system <the .ps1 itself>`.

WATCHDOG_MD = INSTALLER / "WATCHDOG.md"


def _header(script: Path) -> str:
    text = script.read_text(encoding="utf-8-sig")
    return text[: text.index("#>")]


def _sections(md: str) -> dict[str, str]:
    out, name = {}, None
    for line in md.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            out[name] = ""
        elif name:
            out[name] += line + "\n"
    return out


def _runas_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if re.search(r"\bRunAsHelper(\.exe|\.com)?\s+/", ln)]


def test_no_doc_runs_the_gui_launcher_and_every_runas_line_is_the_console_form():
    for name, text in (("WATCHDOG.md", WATCHDOG_MD.read_text(encoding="utf-8")), ("header", _header(REGISTRAR))):
        assert "RunAsHelper.exe" not in text, name
        lines = _runas_lines(text)
        assert lines, f"{name}: no RunAS Helper example left at all"
        for ln in lines:
            assert re.search(r"\bRunAsHelper /capture /timeout:\d+ ", ln), (name, ln)
            if "register-watchdog.ps1" in ln:
                assert "/ps:5 /as:system" in ln and "powershell.exe" not in ln and "-File" not in ln, (name, ln)


def test_each_procedure_leads_with_an_elevated_powershell_and_calls_runas_optional():
    sections = _sections(WATCHDOG_MD.read_text(encoding="utf-8"))
    for name in ("Installing on a new workstation", "Updating / re-registering", "Removing the service"):
        text = " ".join(sections[name].split())          # a phrase may wrap
        elevated = text.find("elevated PowerShell")
        runas = text.find("RunAsHelper /capture")
        assert 0 <= elevated < runas, (name, elevated, runas)
        assert "optional" in text[elevated:runas].lower(), name
    header = " ".join(_header(REGISTRAR).split())
    assert 0 <= header.find("elevated PowerShell") < header.find("RunAsHelper /capture"), header
    assert "optional" in header.lower(), header
