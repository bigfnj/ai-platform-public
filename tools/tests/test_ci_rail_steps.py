"""Every rail's run-tests.ps1 target runs in CI, on ubuntu, as run-tests.ps1 runs it.

private-suite.yml's core job ran the broker, gateway, platform_core and tools suites and nothing
of any rail's (terminal-fun's saves alone, from 2026-10-16), so a rail's suite ran only where
somebody ran run-tests.ps1 by hand, on Windows. Its rail-tests job now holds one step per rail,
named `rail: <id>`, running that rail's target: its test directories, with PYTHONPATH as
run-tests.ps1 builds it from tools/test-targets.json (the rail's source root, src/ else backend/
else the rail dir, edu-suite's apps/dashboard/src, then the two shared libraries), and
run-tests.ps1's pytest arguments plus -ra.

The rails, their test directories and their PYTHONPATH are run-tests.ps1's as the public CI driver
builds them (.github/scripts/run_public_suite.py rail_targets(), from the same table, which
test_public_suite_plan.py holds to `run-tests.ps1 -List -Json`); where pwsh can run the script on
Windows, this compares with -List itself too. So a rail added without a step fails here, and so
does a step left behind by a rail that went, a step that runs other directories, or one that
builds another PYTHONPATH.
"""
from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "private-suite.yml"
DRIVER = REPO / ".github" / "scripts" / "run_public_suite.py"
if not WORKFLOW.is_file() or not DRIVER.is_file():
    pytest.skip(".github/ is not published; the workflow is checked in the private repo",
                allow_module_level=True)

yaml = pytest.importorskip("yaml")

#: run-tests.ps1's pytest arguments for a rail target (`-q -p no:cacheprovider @kArgs`), plus -ra
#: so every skip prints its reason in the log, as the core step and the public driver do.
ARGS = ["-q", "-p", "no:cacheprovider", "-ra", "-k", "not LiveTests"]
#: Every rail step runs after a failed one (each rail is its own verdict), never after a failed
#: install, where every step would fail for the install's reason.
WHEN = "${{ !cancelled() && steps.deps.outcome == 'success' }}"


def _driver():
    spec = importlib.util.spec_from_file_location("_run_public_suite_ci", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _targets() -> dict[str, tuple[list[str], list[str]]]:
    """{rail id: (its test directories, its PYTHONPATH)}, run-tests.ps1's rail targets."""
    return {rid: (dirs, pp) for rid, dirs, pp in _driver().rail_targets(REPO)}


def _steps() -> dict[str, dict]:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["rail-tests"]
    assert job["runs-on"] == "ubuntu-latest", job["runs-on"]
    names = [s.get("name", "") for s in job["steps"]]
    rails = [n[len("rail: "):] for n in names if n.startswith("rail: ")]
    assert len(rails) == len(set(rails)), f"a rail has two steps: {rails}"
    assert "deps" in [s.get("id") for s in job["steps"]], "no install step with id `deps`"
    return {n[len("rail: "):]: s for n, s in zip(names, job["steps"]) if n.startswith("rail: ")}


def test_run_tests_builds_a_rail_s_pythonpath_from_the_table_the_driver_reads():
    """The steps are held to the driver's rail_targets(), which reads tools/test-targets.json.
    test_public_suite_plan.py holds run-tests.ps1's own plan to the driver, but only where pwsh
    runs on Windows, so the lines of run-tests.ps1 that build a rail's PYTHONPATH from that table
    are checked here as well, wherever this runs."""
    text = (REPO / "run-tests.ps1").read_text(encoding="utf-8")
    assert ("$TargetTable = Get-Content -Raw -LiteralPath (Join-Path $Root 'tools\\test-targets.json')"
            " | ConvertFrom-Json") in text
    assert "$shared = @($TargetTable.rail.shared | ForEach-Object { Get-TreePath $_ })" in text
    assert "$pyPath = @(Get-RailRoot $dir.FullName) + $shared" in text
    assert "$own = $TargetTable.rail.source_root_of.PSObject.Properties[(Split-Path $RailDir -Leaf)]" in text
    assert "foreach ($s in @($TargetTable.rail.source_roots)) {" in text
    assert _driver().TABLE == "tools/test-targets.json"
    assert "@('-k', 'not LiveTests')" in text
    assert "& $Py -m pytest @($t.Paths) -q -p no:cacheprovider @kArgs @pyArgs" in text


def test_every_rail_target_has_a_ci_step_and_every_step_a_rail():
    targets, steps = _targets(), _steps()
    assert len(targets) >= 15, targets                              # witness: discovery worked
    missing = sorted(set(targets) - set(steps))
    stale = sorted(set(steps) - set(targets))
    assert missing == [], f"rails whose suite CI never runs (add a `rail: <id>` step): {missing}"
    assert stale == [], f"steps for no rail target: {stale}"


@pytest.mark.parametrize("rail", sorted(_targets()))
def test_each_rail_step_runs_its_target_as_run_tests_does(rail):
    step = _steps().get(rail)
    if step is None:
        pytest.fail(f"no `rail: {rail}` step (the test above names every one)")
    assert step.get("if") == WHEN, step.get("if")
    dirs, pythonpath = _targets()[rail]
    path = step.get("env", {}).get("PYTHONPATH", "")
    assert path.split(":") == pythonpath, path
    argv = shlex.split(step["run"])
    assert argv == ["python", "-m", "pytest", *dirs, *ARGS], argv


_NEEDS_PWSH = pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="needs pwsh on Windows: run-tests.ps1 -List is the other half of the comparison")


@_NEEDS_PWSH
def test_the_steps_match_run_tests_list_itself():
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(REPO / "run-tests.ps1"), "-List"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    rails = {p.name for p in (REPO / "rails").iterdir() if p.is_dir()}
    listed = {}
    for line in r.stdout.splitlines():
        name, _, paths = line.partition(" ")
        if name in rails:
            listed[name] = [p.strip().replace("\\", "/") for p in paths.split(",") if p.strip()]
    assert len(listed) >= 15, r.stdout                               # the parse itself worked
    steps = _steps()
    assert sorted(listed) == sorted(steps)
    for rail, dirs in listed.items():
        assert shlex.split(steps[rail]["run"])[3:3 + len(dirs)] == dirs, rail
