"""Shared loading for the tools suite.

The tools are SCRIPTS, not a package: `tools/` has no __init__.py and each file is run as
`python tools/<name>.py`. So they are loaded by path rather than imported by name, which also
keeps the suite runnable from run-tests.ps1's `core` target with an empty PYTHONPATH.

One non-obvious detail: the loaded module MUST be registered in sys.modules BEFORE
exec_module. rail_conformance.py defines @dataclass classes, and dataclasses resolves
`cls.__module__` through sys.modules while processing the class — an unregistered module
raises `AttributeError: 'NoneType' object has no attribute '__dict__'` at import. The two
loaders inside the tools themselves (rail_conformance._rail_template, and the publish
tooling's own loader) skip this step and get away with it only because neither module they
load happens to use a dataclass.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent


# --- PowerShell scripts ---------------------------------------------------------------------
# The deploy scripts run under BOTH engines in real use: Windows PowerShell 5.1 for the logon
# startup and the watchdog, PowerShell 7 for CI-style gates. They differ in ways that have
# shipped bugs here (a BOM from Set-Content, ConvertFrom-Json -AsHashtable, native stderr under
# EAP=Stop), so a script test that runs under one engine only has certified half the product.
#
# Module scope, so a test module can run one engine process for several scenarios and share it.
@pytest.fixture(scope="module", params=["powershell.exe", "pwsh"])
def shell(request):
    exe = shutil.which(request.param)
    if sys.platform != "win32" or exe is None:
        pytest.skip(f"needs {request.param} on Windows")
    return exe


# --- no network from a PowerShell child -----------------------------------------------------
# offline_guard.py (imported at the end of this file) watches THIS process. A script run_ps starts
# is a process of its own, and the scripts these tests drive call the platform by URL: the gateway
# on :1111, the broker on :11500, Ollama on :11434, NSSM's download. The apply self-test GETted
# the live Ollama 12 times a run (2026-10-15), and three runs were offline only because their
# script exits before its web call. So every run_ps body runs with these defined first: functions
# win command resolution over cmdlets (and aliases such as iwr and irm resolve to the names), and
# each throws, naming its call. A test's own stand-in, defined later in its body, replaces one.
# A body that calls a server the test started itself passes run_ps(..., offline=False) and says
# why. A script started as a process of its own (`& $shell -File x.ps1`) does not see a function
# of its parent: offline_script() wraps it. test_run_ps_offline.py holds every script to calls
# these can catch (no .NET web client, no module-qualified cmdlet).
PS_OFFLINE = """\
function global:Invoke-WebRequest { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Invoke-WebRequest $args" }
function global:Invoke-RestMethod { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Invoke-RestMethod $args" }
function global:Start-BitsTransfer { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Start-BitsTransfer $args" }
function global:Test-NetConnection { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Test-NetConnection $args" }
function global:Test-Connection { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Test-Connection $args" }
function global:Resolve-DnsName { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Resolve-DnsName $args" }
"""


def _ps_quote(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


@pytest.fixture(scope="module")
def offline_script(tmp_path_factory):
    """offline_script(script, args="") -> the path of a .ps1 to start with `-File` IN PLACE of
    `script`: it defines PS_OFFLINE, runs `script` with `args` (PowerShell source text), and exits
    as `-File script` would have. `$?` after the call tells an `exit N` (N > 0) from a script that
    ran to its end, so a script that finishes after a failed native command still exits 0, as it
    does under -File, under both engines. A terminating error ends the wrapper with 1, as it ends
    -File."""
    here = tmp_path_factory.mktemp("ps-offline")
    counter = iter(range(10_000))

    def make(script, args: str = "") -> Path:
        f = here / f"offline-{next(counter)}.ps1"
        f.write_text(PS_OFFLINE + f"$global:LASTEXITCODE = 0\n& {_ps_quote(script)} {args}\n"
                     "if ($?) { exit 0 }\nexit $LASTEXITCODE\n", encoding="utf-8-sig")
        return f
    return make


@pytest.fixture(scope="module")
def run_ps(tmp_path_factory):
    """Run a PowerShell script body under one engine; returns the CompletedProcess.

    Written with a BOM because 5.1 reads a BOM-less .ps1 as ANSI, and with console output forced
    to UTF-8 so a non-ASCII temp path does not come back in the OEM code page. PS_OFFLINE comes
    first unless the caller passes offline=False (see above).

    A child that exits non-zero having printed NOTHING, on stdout or on stderr, is launched once
    more with a warning, and a second silent exit fails the test as a launch failure. A script
    that runs prints its own failure (an uncaught error writes its record to stderr), so a silent
    exit is the child failing to start: the 2026-10-14 gate lost a test_service_env.py case that
    way, exit 1 and no output while five suites ran at once, and the file passed 18/18 alone
    straight after. A child that printed anything, or exited 0, is its own verdict and is never
    run twice (test_run_ps_retry.py)."""
    import warnings

    here = tmp_path_factory.mktemp("ps")
    counter = iter(range(10_000))
    cause = ("A script that runs prints its own failure, so this is the child failing to start, "
             "not a script verdict: most likely desktop heap or an endpoint file filter under load "
             "(many processes starting at once).")

    def silent_failure(p: subprocess.CompletedProcess) -> bool:
        return p.returncode != 0 and not p.stdout and not p.stderr

    def run(shell: str, body: str, *, timeout: int = 120,
            offline: bool = True) -> subprocess.CompletedProcess:
        f = here / f"harness-{next(counter)}.ps1"
        f.write_text("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                     + (PS_OFFLINE if offline else "") + body, encoding="utf-8-sig")
        argv = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(f)]

        def launch() -> subprocess.CompletedProcess:
            return subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                                  timeout=timeout)

        r = launch()
        if silent_failure(r):
            warnings.warn(f"run_ps: {Path(shell).name} exited {r.returncode} with no output at all "
                          f"({f.name}). {cause} Launching it once more.", stacklevel=2)
            r = launch()
            if silent_failure(r):
                pytest.fail(f"run_ps: {Path(shell).name} exited {r.returncode} with no output at "
                            f"all, twice ({f.name}). {cause} Rerun it with less running.",
                            pytrace=False)
        return r
    return run


# --- TEMP in both spellings -------------------------------------------------------------------
# Windows sets $env:TEMP to the 8.3 SHORT form of the profile path on a box whose user folder is
# not an 8.3 name (C:\Users\YOURNA~1.ME\AppData\Local\Temp), and the long form elsewhere. Under
# both engines Remove-Item throws a terminating error on a path through the short form of the
# user-profile folder, one -ErrorAction SilentlyContinue does not silence, so a script's temp
# cleanup passed or killed the script depending on the box. A test that hands a script a TEMP runs
# it in both spellings. The short case reproduces that defect when the directory sits under the
# profile folder, which is where pytest puts tmp_path unless --basetemp moves it.
def short_path(path) -> str:
    """The 8.3 short form of an existing path, or '' when the volume keeps no short names."""
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.kernel32.GetShortPathNameW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    fn.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(32768)
    n = fn(str(path), buf, len(buf))
    return buf.value if 0 < n < len(buf) and "~" in buf.value else ""


@pytest.fixture(params=["long-temp", "short-temp"])
def temp_dir_form(request, tmp_path):
    """(the spelling to hand the script as TEMP, the same directory in the long form)."""
    d = tmp_path / "Temp dir.long name"     # not an 8.3 name, so the volume gives it a short one
    d.mkdir()
    if request.param == "long-temp":
        return str(d), d
    if sys.platform != "win32":
        pytest.skip("8.3 short names are a Windows thing")
    s = short_path(d)
    if not s:
        pytest.skip("this volume generates no 8.3 short names")
    return s, d


def load_tool(name: str):
    """Load tools/<name>.py as a module, once per session."""
    mod_name = f"_tools_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = TOOLS / f"{name}.py"
    assert path.is_file(), f"expected a tool at {path}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # see the module docstring: dataclasses needs this
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def smoke():
    return load_tool("rail_smoke")


@pytest.fixture(scope="session")
def publish():
    return load_tool("publish")


@pytest.fixture(scope="session")
def template():
    return load_tool("rail_template")


@pytest.fixture(scope="session")
def conformance():
    return load_tool("rail_conformance")


@pytest.fixture(scope="session")
def venv_doctor():
    return load_tool("venv_doctor")


@pytest.fixture(scope="session")
def real_baseline() -> dict:
    """tools/smoke-baseline.json as recorded. Read-only; nothing here writes it.

    The baseline is withheld from the public snapshot (publish.json exclude_why), and so is the
    one test that requests this fixture; the guard keeps any future user honest there."""
    import json
    baseline = TOOLS / "smoke-baseline.json"
    if not baseline.is_file():
        pytest.skip("tools/smoke-baseline.json is withheld from the public snapshot")
    return json.loads(baseline.read_text(encoding="utf-8"))


# No test outside a live class reaches the network: offline_guard.py beside this file, which
# every suite's conftest takes (its docstring has the rules and their reasons).
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: E402,F401
