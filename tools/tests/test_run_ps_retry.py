"""The run_ps fixture (conftest.py) tells a PowerShell child that never started from a verdict.

The 2026-10-14 gate failed one test_service_env.py case under powershell.exe with exit 1 and
NOTHING on stdout or stderr, while five suites ran at once; the file passed 18/18 alone straight
after. A script that runs prints its own failure, so a silent non-zero exit is the child failing
to start (desktop heap, or an endpoint file filter holding the launch under load). run_ps launches
such a child once more, with a warning, and fails naming that cause if it is silent again. A
child that printed anything, or exited 0, is its own verdict and is never run twice.

No process starts here: each case replaces subprocess.run with a fake that answers from a list,
so the cases cost nothing and cannot add to the load they are about. A fake asked for more
launches than its list holds raises, which fails the case.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SHELL = "powershell.exe"
BODY = "Write-Output 'ok'\n"


class FakeRun:
    """subprocess.run as run_ps calls it: records each argv and answers (code, stdout, stderr)."""

    def __init__(self, answers: list[tuple[int, str, str]]) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kw):
        self.calls.append(list(argv))
        code, out, err = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, code, out, err)


@pytest.fixture()
def launches(monkeypatch):
    def install(*answers: tuple[int, str, str]) -> FakeRun:
        fake = FakeRun(list(answers))
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return install


def test_a_child_silent_once_is_launched_again_and_its_second_result_returned(run_ps, launches):
    fake = launches((1, "", ""), (0, "ok\n", ""))
    with pytest.warns(UserWarning, match=r"powershell\.exe exited 1 with no output at all "
                                         r"\(harness-\d+\.ps1\)\..* Launching it once more\."):
        r = run_ps(SHELL, BODY)
    assert (r.returncode, r.stdout, r.stderr) == (0, "ok\n", "")
    # The same script, launched again: not a new one, and not a different command.
    assert len(fake.calls) == 2 and fake.calls[0] == fake.calls[1]
    script = Path(fake.calls[0][-1])
    assert fake.calls[0][:-1] == [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                  "Bypass", "-File"]
    assert script.read_text(encoding="utf-8-sig").endswith(BODY)


def test_a_child_silent_twice_fails_as_a_launch_failure(run_ps, launches):
    fake = launches((1, "", ""), (1, "", ""))
    with pytest.warns(UserWarning, match="Launching it once more"), \
            pytest.raises(pytest.fail.Exception) as failed:
        run_ps(SHELL, BODY)
    msg = str(failed.value)
    assert "powershell.exe exited 1 with no output at all, twice" in msg, msg
    assert "the child failing to start, not a script verdict" in msg, msg
    assert "desktop heap" in msg and "endpoint file filter" in msg, msg
    assert len(fake.calls) == 2


@pytest.mark.parametrize("out,err", [("FAIL: the check said no\n", ""),
                                     ("", "Write-Error: the check said no\n")],
                         ids=["stdout", "stderr"])
def test_a_child_that_printed_anything_is_its_own_verdict(run_ps, launches, recwarn, out, err):
    fake = launches((1, out, err))
    r = run_ps(SHELL, BODY)
    assert (r.returncode, r.stdout, r.stderr) == (1, out, err)
    assert len(fake.calls) == 1
    assert not [w for w in recwarn if "run_ps" in str(w.message)]


def test_a_clean_exit_with_no_output_is_not_launched_again(run_ps, launches, recwarn):
    fake = launches((0, "", ""))
    assert run_ps(SHELL, BODY).returncode == 0
    assert len(fake.calls) == 1
    assert not [w for w in recwarn if "run_ps" in str(w.message)]
