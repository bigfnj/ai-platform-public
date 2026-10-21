"""A bare `pytest services/broker/tests` cannot reach a real Claude CLI.

It once could. The broker's LiveTests were guarded only by run-tests.ps1's `-k "not LiveTests"`,
so running pytest directly, on a box where `claude` resolves, made real CLI calls on the plan of
whoever the CLI was signed in as. They now skip unless CLI_LIVE=1.

This runs the broker's whole suite that way (plain pytest, CLI_LIVE unset) with a
FAKE `claude` first on PATH, where resolve_cli_exe will find it, and a pytest plugin that records
every process start whose program is a claude or codex binary. The count must be zero. The fake
is an empty file, so even the witness below, which opts in on purpose, cannot reach a real CLI:
its spawn is recorded and then fails.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

RECORDER = '''\
"""pytest plugin: record every process start whose program is a claude or codex binary."""
import os
import subprocess

_LOG = os.environ["LIVE_GATE_LOG"]
_original = subprocess.Popen.__init__


def _recording(self, args, *a, **kw):
    first = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
    if os.path.basename(str(first)).lower().startswith(("claude", "codex")):
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(repr(args) + "\\n")
    return _original(self, args, *a, **kw)


subprocess.Popen.__init__ = _recording
'''


def _run_broker_suite(tmp_path: Path, *extra: str, opt_in: bool) -> tuple[int, str, list[str]]:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "live_gate_recorder.py").write_text(RECORDER, encoding="utf-8")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    fake = fakebin / ("claude.exe" if sys.platform == "win32" else "claude")
    fake.write_bytes(b"")
    fake.chmod(0o755)
    log = tmp_path / f"spawns-{'on' if opt_in else 'off'}.log"
    env = {k: v for k, v in os.environ.items() if k not in ("CLI_LIVE", "JOB_AID_LIVE_TESTS")}
    if opt_in:
        env["CLI_LIVE"] = "1"
    env["LIVE_GATE_LOG"] = str(log)
    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in (
        plugin_dir, REPO / "services" / "broker", REPO / "packages" / "platform_core"))
    p = subprocess.run([sys.executable, "-m", "pytest", "services/broker/tests", "-q",
                        "-p", "no:cacheprovider", "-p", "live_gate_recorder", *extra],
                       cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    spawns = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return p.returncode, p.stdout + p.stderr, spawns


def test_a_bare_pytest_of_the_broker_suite_makes_no_cli_call(tmp_path):
    code, out, spawns = _run_broker_suite(tmp_path, opt_in=False)
    assert spawns == [], f"real-CLI spawns without the opt-in: {spawns}"
    assert code == 0, out[-3000:]
    assert "CLI_LIVE" in out or "skipped" in out, "the live tests should report their skip"


_JOB_AID_PROBE = """
import test_source_adzuna as a, test_source_usajobs as u
print('SKIPPED' if a.LiveFetchTests.__unittest_skip__ and u.LiveTests.__unittest_skip__
      else 'RUNS')
"""


def test_job_aids_keyed_boards_need_the_opt_in_even_with_their_keys():
    """Adzuna and USAJOBS used to run on credentials alone, so a box with the keys exported
    spent their quota on every bare pytest. Adzuna's class is not even named LiveTests, so
    run-tests' `-k "not LiveTests"` never caught it. Keys present (fake ones), opt-in absent:
    both must skip. The opt-in is checked first, so no network probe runs either."""
    if not (REPO / "rails" / "job-aid" / "tests").is_dir():
        pytest.skip("job-aid is not in this tree")
    env = {k: v for k, v in os.environ.items() if k != "JOB_AID_LIVE_TESTS"}
    env.update(ADZUNA_APP_ID="x", ADZUNA_APP_KEY="x", USAJOBS_API_KEY="x", USAJOBS_EMAIL="x")
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / "rails" / "job-aid" / p)
                                        for p in ("tests", "src"))
    p = subprocess.run([sys.executable, "-c", _JOB_AID_PROBE], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.stdout.strip() == "SKIPPED", p.stdout + p.stderr


@pytest.mark.skipif(sys.platform != "win32",
                    reason="the broker's LiveTests run on Windows only, so off Windows there is "
                           "no call for this witness to see")
def test_the_recorder_does_see_a_cli_call_when_the_opt_in_is_given(tmp_path):
    """Witness: a recorder that saw nothing above proves nothing unless it CAN see a call."""
    _code, out, spawns = _run_broker_suite(tmp_path, "-k", "LiveTests", opt_in=True)
    assert spawns, f"the recorder missed an opted-in call:\n{out[-2000:]}"
    assert all("fakebin" in s for s in spawns), f"a spawn reached something but the fake: {spawns}"
