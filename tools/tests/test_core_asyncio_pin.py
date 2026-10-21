"""Every runner of the core target pins pytest-asyncio's mode, so the order of its paths cannot
choose it.

The repo root has no pytest config, so pytest takes the first path's pyproject.toml, and only
services/broker's sets asyncio_mode = "auto". With packages/platform_core/tests first, pytest took
that package's config (strict), and 304 tests in the 13 core files with async tests failed
(measured 2026-10-14). run-tests.ps1, the public CI driver and the private CI workflow each pass
`-o asyncio_mode=auto` to the core target now, all three read from tools/test-targets.json, the core
entry's pytest_args; these keep the first two from losing it. The workflow's core step is held by
tools/tests/test_ci_core_step.py (test_the_core_step_pins_asyncio_mode), which runs the step's own
text and reads what it hands pytest: the step restated the pin until 2026-10-20, and a check of its
text for the pin could no longer see it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PIN = ["-o", "asyncio_mode=auto"]


def test_run_tests_pins_the_core_target():
    table = json.loads((REPO / "tools" / "test-targets.json").read_text(encoding="utf-8"))
    assert table["core"]["pytest_args"] == PIN, table["core"]
    text = (REPO / "run-tests.ps1").read_text(encoding="utf-8")
    start = text.index("@{ Name = 'core'")
    core = text[start:text.index("\n)", start)]
    assert "PytestArgs = @($TargetTable.core.pytest_args)" in core, core
    # ...and the pytest call hands a target's PytestArgs on.
    assert "@kArgs @pyArgs }" in text and "$pyArgs = if ($t.PytestArgs)" in text


def test_the_public_driver_pins_the_core_target():
    driver = REPO / ".github" / "scripts" / "run_public_suite.py"
    if not driver.is_file():
        pytest.skip(".github/ is not published; the driver is checked in the private repo")
    spec = importlib.util.spec_from_file_location("_run_public_suite_pin", driver)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = next(t for t in mod.plan(REPO) if t["name"] == "core")
    assert core["args"] == PIN
    assert '*PYTEST_ARGS, *t.get("args", [])]' in driver.read_text(encoding="utf-8")
