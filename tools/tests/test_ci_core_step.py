"""Private CI's core step runs the core target as tools/test-targets.json gives it.

private-suite.yml's "Core tests" step named four directories itself (services/broker/tests,
apps/platform/backend/tests, packages/platform_core/tests, tools/tests), so the table's fifth,
packages/edu-media-core/tests, ran in no CI job (found by the pypath lane, 2026-10-19). The step
now takes core's directories, PYTHONPATH and pytest arguments from the table, which run-tests.ps1
and the public CI driver read as well.

These run the step's own text under bash the way the runner runs a `shell: bash` step (`bash
--noprofile --norc -eo pipefail`), with `python -m pytest` stood in for by a script that records
what it was handed. Every other `python` is the real interpreter, so the step's reading of the
table is real. A step that restates a directory or drops one, builds another PYTHONPATH or loses
the table's arguments fails the first test; one that stops reading the table fails the second,
which hands it a table of its own. The skips summary after it is run on what it wrote.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "private-suite.yml"
TABLE = "tools/test-targets.json"
if not WORKFLOW.is_file():
    pytest.skip(".github/ is not published; the workflow is checked in the private repo",
                allow_module_level=True)

yaml = pytest.importorskip("yaml")
sys.path.insert(0, str(HERE))
import mutation_runner as mr  # noqa: E402  (a timeout kills the whole process tree)

#: What the step adds to the table's arguments: run-tests.ps1's `-q -p no:cacheprovider`, -ra so
#: every skip says why (the next step counts them by reason), and --durations=15.
OWN_ARGS = ["-q", "-p", "no:cacheprovider", "-ra", "--durations=15"]
#: The skip the stand-in pytest reports, and the line the skips summary makes of it.
SKIP_LINE = "SKIPPED [2] tools/tests/test_x.py:3: needs Windows"
SUMMARY_LINE = "    2  needs Windows"


def _bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    # Git's bash, never System32\bash.exe (that one is WSL's, and would run a real distro).
    git = shutil.which("git")
    for anc in list(Path(git).resolve().parents)[:3] if git else []:
        cand = anc / "usr" / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash (Git for Windows' on Windows)")

# `python -m pytest` records PYTHONPATH and its arguments, one per line, reports one skip and
# exits $PYTEST_EXIT; any other `python` is the real one. A Windows python ends its lines with
# CRLF, where a runner's does not, so the CR is dropped from what it prints.
PYTHON_STUB = r"""#!/usr/bin/env bash
set -o pipefail
if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then
  shift 2
  { printf 'PYTHONPATH=%s\n' "${PYTHONPATH-<unset>}"; printf '%s\n' "$@"; } > "$RECORD"
  echo "SKIPPED [2] tools/tests/test_x.py:3: needs Windows"
  echo "1 passed, 2 skipped in 0.01s"
  exit "${PYTEST_EXIT:-0}"
fi
"$REAL_PY" "$@" | tr -d '\r'
"""

# Runs one step's text: the stand-in first on PATH, then the system's tools, as POSIX paths. Under
# Git's bash a C:/ path on PATH splits at the drive's colon, so every path is converted first.
DRIVER = r"""set -u
B="$1"; S="$2"
if [ -x /usr/bin/cygpath ]; then
  B=$(/usr/bin/cygpath -u "$B"); S=$(/usr/bin/cygpath -u "$S")
  REAL_PY=$(/usr/bin/cygpath -u "$REAL_PY"); RECORD=$(/usr/bin/cygpath -u "$RECORD")
  RUNNER_TEMP=$(/usr/bin/cygpath -u "$RUNNER_TEMP")
  GITHUB_STEP_SUMMARY=$(/usr/bin/cygpath -u "$GITHUB_STEP_SUMMARY")
fi
export REAL_PY RECORD RUNNER_TEMP GITHUB_STEP_SUMMARY
export PATH="$B:/usr/bin:/bin"
exec bash --noprofile --norc -eo pipefail "$S"
"""


def _step(name: str) -> dict:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["core-tests"]
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, [s.get("name") for s in job["steps"]]
    return found[0]


def run_steps(tmp: Path, tree: Path, pytest_exit: int = 0) -> dict:
    """Run "Core tests", then "Skipped tests by reason", in `tree` as the runner runs them."""
    core, skips = _step("Core tests"), _step("Skipped tests by reason")
    # The runner's bash runs with pipefail, so the tee after pytest does not turn its failure
    # into a pass; `shell: bash` is what selects it.
    assert core.get("shell") == "bash" and skips.get("shell") == "bash", (core, skips)
    tmp.mkdir(parents=True, exist_ok=True)
    bin_ = tmp / "bin"
    bin_.mkdir()
    (bin_ / "python").write_bytes(PYTHON_STUB.encode("utf-8"))
    (bin_ / "python").chmod(0o755)
    (tmp / "driver.sh").write_bytes(DRIVER.encode("utf-8"))
    record, runner_temp, summary = tmp / "record.txt", tmp / "runner-temp", tmp / "summary.md"
    runner_temp.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(REAL_PY=Path(sys.executable).as_posix(), RECORD=record.as_posix(),
               RUNNER_TEMP=runner_temp.as_posix(), GITHUB_STEP_SUMMARY=summary.as_posix(),
               PYTEST_EXIT=str(pytest_exit))
    got = {}
    for key, step in (("core", core), ("skips", skips)):
        script = tmp / f"{key}.sh"
        script.write_bytes(step["run"].encode("utf-8"))
        got[key] = mr.run_tree([BASH, str(tmp / "driver.sh"), bin_.as_posix(), script.as_posix()],
                               cwd=tree, env=env, capture_output=True, text=True, timeout=60)
    lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
    got["ran_pytest"] = bool(lines)
    got["pythonpath"] = lines[0].partition("=")[2].split(":") if lines else None
    got["argv"] = lines[1:]
    out = runner_temp / "core-tests.out"
    got["tee"] = out.read_text(encoding="utf-8") if out.is_file() else None
    got["summary"] = summary.read_text(encoding="utf-8") if summary.is_file() else ""
    return got


def _said(r) -> str:
    return f"exit {r.returncode}\n{r.stdout}{r.stderr}"


@pytest.fixture(scope="module")
def this_tree(tmp_path_factory):
    return run_steps(tmp_path_factory.mktemp("core-step"), REPO)


def test_the_core_step_runs_the_tables_core_target(this_tree):
    core = json.loads((REPO / TABLE).read_text(encoding="utf-8"))["core"]
    assert this_tree["core"].returncode == 0, _said(this_tree["core"])
    assert this_tree["argv"] == [*core["paths"], *core["pytest_args"], *OWN_ARGS], this_tree["argv"]
    assert this_tree["pythonpath"] == core["pythonpath"], this_tree["pythonpath"]
    # The directory no CI job ran until 2026-10-20, and the root that makes it importable.
    assert "packages/edu-media-core/tests" in this_tree["argv"]
    assert "packages/edu-media-core/src" in this_tree["pythonpath"]


def test_the_core_step_pins_asyncio_mode(this_tree):
    """pytest takes its config from the first path's pyproject.toml when the root has none, and
    only services/broker's sets asyncio_mode = "auto" (test_core_asyncio_pin.py)."""
    argv = this_tree["argv"]
    assert any(argv[i:i + 2] == ["-o", "asyncio_mode=auto"] for i in range(len(argv))), argv


def test_the_core_step_follows_the_table_it_finds(tmp_path):
    """A step that names the right five directories itself passes the test above today and
    drifts with the next change to the table. Handed a table of its own, it must follow it."""
    tree = tmp_path / "tree"
    (tree / "tools").mkdir(parents=True)
    own = {"core": {"paths": ["b/tests", "a/tests", "c/tests"], "pythonpath": ["b", "a/src"],
                    "pytest_args": ["-o", "x=y", "-p", "z"]}}
    (tree / TABLE).write_text(json.dumps(own), encoding="utf-8")
    got = run_steps(tmp_path / "run", tree)
    assert got["core"].returncode == 0, _said(got["core"])
    assert got["argv"] == ["b/tests", "a/tests", "c/tests", "-o", "x=y", "-p", "z", *OWN_ARGS], got["argv"]
    assert got["pythonpath"] == ["b", "a/src"], got["pythonpath"]


def test_the_skips_step_counts_what_the_core_step_wrote_and_a_red_run_stays_red(tmp_path):
    got = run_steps(tmp_path, REPO, pytest_exit=1)
    assert got["ran_pytest"], _said(got["core"])
    assert got["core"].returncode != 0, "a failing pytest passed the step: the tee hid its exit"
    assert got["tee"] is not None and SKIP_LINE in got["tee"], got["tee"]
    assert got["skips"].returncode == 0, _said(got["skips"])
    assert SUMMARY_LINE in got["summary"].splitlines(), got["summary"]


@pytest.mark.parametrize("table", ["absent", "no core paths"])
def test_a_table_with_no_core_paths_fails_the_step_before_pytest(tmp_path, table):
    """pytest given no path collects every test under its working directory, every rail's
    included, so a table the step cannot read, or one with no core directories, must stop it."""
    tree = tmp_path / "tree"
    (tree / "tools").mkdir(parents=True)
    if table != "absent":
        (tree / TABLE).write_text(json.dumps(
            {"core": {"paths": [], "pythonpath": ["a"], "pytest_args": []}}), encoding="utf-8")
    got = run_steps(tmp_path / "run", tree)
    assert got["core"].returncode != 0, _said(got["core"])
    assert not got["ran_pytest"], got["argv"]
