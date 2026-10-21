"""The lean installer's broker venv holds every dependency the broker declares.

install-native.ps1 used to install the broker's dependencies from a list written into the script,
and the copy had fallen behind services/broker/pyproject.toml: no python-multipart, without which
FastAPI raises while the broker is imported, so a lean install's broker service died at start and
NSSM restarted it forever; and no anyio, which the broker imports by name. It now installs from the
pyproject (lib-runtime's Get-BrokerPipInstalls), and these tests hold it there:

  * every pip run in install-native.ps1 is either pip's own upgrade or one of
    Get-BrokerPipInstalls' entries, so the dry run reports exactly what a real run installs;
  * that install set, read from the dry run (-PlanOnly) under both engines, covers every
    dependency the broker's pyproject declares, and a dependency that is one of the repo's own
    packages (not on PyPI) is installed from the repo before the install that needs it;
  * the dry run lists the pyproject's dependencies as the pyproject has them;
  * Test-BrokerImports, which decides whether an existing venv is reinstalled, tells a venv the
    broker imports with from one it does not.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
NATIVE = INSTALLER / "install-native.ps1"
BROKER_PYPROJECT = REPO / "services" / "broker" / "pyproject.toml"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def _local_projects() -> dict[str, Path]:
    """The repo's own Python packages, by normalized name: none of them is on PyPI."""
    found = {}
    for f in sorted((REPO / "packages").glob("*/pyproject.toml")):
        name = tomllib.loads(f.read_text(encoding="utf-8")).get("project", {}).get("name")
        if name:
            found[norm(name)] = f.parent
    return found


def coverage_gaps(installs: list[list[str]], root: Path) -> list[str]:
    """What the broker declares that `installs` (pip argument lists, in order, their paths under
    `root`) would not put in the venv. A requirement is covered when an install names it, or
    installs a project that declares it; one of the repo's own packages only by an editable
    install of that package that comes BEFORE the install that needs it, because pip cannot fetch
    it from an index."""
    local = _local_projects()
    brings: dict[str, int] = {}       # normalized name -> the first install that brings it
    editable: dict[str, int] = {}     # normalized project name -> the install that installs it
    needs: dict[str, int] = {}        # local package -> the first install whose project needs it
    for i, args in enumerate(installs):
        assert args and args[0] == "install", args
        rest = iter(args[1:])
        for a in rest:
            if a == "-e":
                proj_dir = REPO / Path(next(rest)).relative_to(root)
                project = _project(proj_dir / "pyproject.toml")
                name = norm(project["name"])
                editable.setdefault(name, i)
                brings.setdefault(name, i)
                for dep in project.get("dependencies", []):
                    dn = norm(Requirement(dep).name)
                    brings.setdefault(dn, i)
                    if dn in local:
                        needs.setdefault(dn, i)
            elif not a.startswith("-"):
                brings.setdefault(norm(Requirement(a).name), i)
    gaps = []
    for dep in _project(BROKER_PYPROJECT)["dependencies"]:
        dn = norm(Requirement(dep).name)
        if dn in local:
            if dn not in editable:
                gaps.append(f"{dep} (the repo's own package, never installed from the repo)")
            elif dn in needs and editable[dn] > needs[dn]:
                gaps.append(f"{dep} (installed from the repo only after the install that needs it)")
        elif dn not in brings:
            gaps.append(dep)
    return gaps


# --- static: install-native.ps1 restates no requirement ------------------------------------------

_PIP_RUN = re.compile(r"-m\s+pip\b(.*)$")
_ALLOWED = (re.compile(r"^\s+install\s+--upgrade\s+pip\s+--quiet\s*$"),
            re.compile(r"^\s+@a\s*$"))


def test_every_pip_run_in_the_lean_installer_comes_from_the_broker_install_set():
    """A pip run with its own arguments is a list nobody keeps in step with the pyproject, and one
    the dry run does not report. Only pip's own upgrade and Get-BrokerPipInstalls' entries."""
    lines = NATIVE.read_text(encoding="utf-8").splitlines()
    runs = [(n, m.group(1)) for n, line in enumerate(lines, 1)
            if (m := _PIP_RUN.search(line)) and not line.lstrip().startswith("#")]
    assert runs, "install-native.ps1 runs pip nowhere: this scan is reading the wrong thing"
    stray = [f"line {n}: pip{rest}" for n, rest in runs
             if not any(p.match(rest) for p in _ALLOWED)]
    assert not stray, ("install-native.ps1 runs pip with arguments of its own:\n  "
                       + "\n  ".join(stray))
    assert any(_ALLOWED[1].match(rest) for _n, rest in runs), runs


# --- the dry run, under both engines -------------------------------------------------------------

@pytest.fixture(scope="module")
def plan(run_ps, shell, tmp_path_factory):
    """install-native.ps1 -PlanOnly on a copy of the installer, with the broker's real pyproject at
    the copy's services\\broker. (It writes a token into the copy's deploy\\.env, as a real run
    does.)"""
    root = tmp_path_factory.mktemp("native-deps")
    (root / "deploy" / "installer").mkdir(parents=True)
    for f in ("install-native.ps1", "lib-runtime.ps1"):
        shutil.copy2(INSTALLER / f, root / "deploy" / "installer" / f)
    (root / "deploy" / ".env").write_text("PLATFORM_RUNTIME=podman\n", encoding="utf-8")
    (root / "services" / "broker").mkdir(parents=True)
    shutil.copy2(BROKER_PYPROJECT, root / "services" / "broker" / "pyproject.toml")
    script = root / "deploy" / "installer" / "install-native.ps1"
    r = run_ps(shell, f"$ErrorActionPreference = 'Stop'\n"
                      f"& {q(script)} -PlatformRoot {q(root)} -SkipOllama -PlanOnly 3>$null | "
                      f"Select-Object -Last 1\n")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), root


def test_the_install_set_covers_every_dependency_the_broker_declares(plan):
    p, root = plan
    installs = p["BrokerPipInstalls"]
    assert installs, p
    gaps = coverage_gaps(installs, root)
    assert not gaps, ("the lean broker venv would lack what services/broker/pyproject.toml "
                      f"declares: {gaps}\ninstalls: {installs}")


def test_the_dry_run_lists_what_the_broker_declares(plan):
    p, _root = plan
    declared = _project(BROKER_PYPROJECT)["dependencies"]
    assert p["BrokerDependencies"] == declared
    names = {norm(Requirement(d).name) for d in p["BrokerDependencies"]}
    assert {"python-multipart", "anyio"} <= names, names


def test_the_coverage_check_names_what_a_hand_written_list_misses():
    """The check itself, on the list the installer used to carry: it must name python-multipart,
    which that list never had, and anyio, which the broker declared after it was written. And on
    the right installs in the wrong order: pip cannot install the broker before platform-core."""
    old = [["install", "--quiet", "fastapi>=0.111", "uvicorn[standard]>=0.30", "httpx>=0.27",
            "pydantic>=2.7", "pydantic-settings>=2.2"],
           ["install", "--quiet", "-e", str(REPO / "packages" / "platform_core")]]
    assert [norm(Requirement(g).name) for g in coverage_gaps(old, REPO)] == [
        "anyio", "python-multipart"]
    swapped = [["install", "--quiet", "-e", str(REPO / "services" / "broker")],
               ["install", "--quiet", "-e", str(REPO / "packages" / "platform_core")]]
    assert coverage_gaps(swapped, REPO) == [
        "platform-core (installed from the repo only after the install that needs it)"]


# --- Test-BrokerImports, under both engines ------------------------------------------------------

@pytest.fixture(scope="module")
def bare_python(tmp_path_factory):
    """A venv with nothing installed, so the broker cannot import with it."""
    d = tmp_path_factory.mktemp("bare-venv") / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(d)], check=True,
                   capture_output=True, timeout=120)
    return d / "Scripts" / "python.exe"


def test_the_import_probe_tells_a_complete_venv_from_one_that_is_not(run_ps, shell, bare_python,
                                                                      tmp_path):
    """Whether an existing venv is reinstalled hangs on this: a yes for a venv the broker does not
    import with would leave a box that ran the old installer crash-looping, re-run or not. Run
    under EAP Stop, as the installer runs, where 5.1 turns the failing import's traceback on
    stderr into a throw unless the probe relaxes it."""
    lib = INSTALLER / "lib-runtime.ps1"
    body = f"""$ErrorActionPreference = 'Stop'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
. {q(lib)}
[ordered]@{{
  complete = Test-BrokerImports -Python {q(sys.executable)} -Root {q(REPO)}
  bare = Test-BrokerImports -Python {q(bare_python)} -Root {q(REPO)}
  missing = Test-BrokerImports -Python {q(tmp_path / 'nope' / 'python.exe')} -Root {q(REPO)}
  eap = [string]$ErrorActionPreference
}} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {"complete": True, "bare": False, "missing": False, "eap": "Stop"}, got
