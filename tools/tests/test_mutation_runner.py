"""The shared mutation-harness runner (`mutation_runner.py`): its time limit and its
byte-exact restore.

A harness that hangs gives no verdict at all, which is worse than a wrong one: nobody can tell
it from a slow run. These pin the two ways the limit could fail to hold. The second is the one
`subprocess.run(timeout=...)` gets wrong on Windows, and the reason the helper exists.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

import mutation_runner as runner

PY = sys.executable


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_finished_suite_reports_its_exit_code_and_output(tmp_path):
    run = runner.run_bounded([PY, "-c", "import sys; print('hello'); sys.exit(3)"],
                             cwd=tmp_path, timeout=60)
    assert run.timed_out is False
    assert run.code == 3
    assert "hello" in run.out


# A killed run is judged on two steps, each timed from its own start by wrapping the runner's tree
# kill, as test_platform_startup_sh.py does. The old bound, 20 s from the run's start, held the
# taskkill, the step a busy host stalls (one took ~33 s in that test's run on 2026-10-14).
#: From the run's start to the kill being issued: the run's limit plus a margin for a host slow
#: to start or schedule anything. The child sleeps 60 s, so a run that never cut it off issues
#: no kill at all.
CUT_OFF_MARGIN = 15
#: From the tree kill's return to the run's: the read after the kill. Nothing holds the pipe once
#: the tree is dead, so it takes milliseconds; a runner that left a grandchild holding it waits
#: out REAP_TIMEOUT here, which the grandchild tests raise to 30 s.
AFTER_KILL = 10
#: The grandchild tests' limit: the child must have started its grandchild by then, or the tree
#: kill has nothing to find. That took 0.3-1.1 s idle and 1.4-3.7 s under a CPU hog plus eight
#: process-spawning loops (2026-10-14), so the old 3 s raced it; this is about twice the slowest.
GRANDCHILD_LIMIT = 8


@pytest.fixture
def kills(monkeypatch):
    """[(issued, returned)] in monotonic seconds, one per call of the runner's tree kill."""
    seen: list[tuple[float, float]] = []
    real = runner._kill_tree

    def timed(proc):
        issued = time.monotonic()
        try:
            real(proc)
        finally:
            seen.append((issued, time.monotonic()))

    monkeypatch.setattr(runner, "_kill_tree", timed)
    return seen


def _judge_kill(kills, t0: float, back: float, limit: float, who: str) -> None:
    assert len(kills) == 1, f"the run timed out without the runner's tree kill: {kills}"
    issued, returned = kills[0]
    assert issued - t0 < limit + CUT_OFF_MARGIN, (
        f"the kill came {issued - t0:.1f} s into a run limited to {limit:g} s")
    assert back - returned < AFTER_KILL, (
        f"{who} waited {back - returned:.1f} s after the tree kill on a pipe something still "
        f"held (bound {AFTER_KILL} s)")


def test_a_hung_suite_is_killed_at_the_timeout(tmp_path, kills):
    t0 = time.monotonic()
    run = runner.run_bounded([PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, timeout=1)
    back = time.monotonic()
    assert run.timed_out is True
    assert run.code != 0
    assert "TIMEOUT" in run.out
    _judge_kill(kills, t0, back, 1, "the runner")


def test_a_grandchild_holding_the_pipe_cannot_keep_the_runner_waiting(tmp_path, monkeypatch, kills):
    """The Windows trap. The child starts a grandchild, which inherits the child's stdout (the
    runner's pipe), and then hangs. Killing only the child leaves the grandchild holding the
    pipe, and the read that follows waits for it. With the reap made long, a runner that kills
    only the child waits ~30 s after its kill and leaves the grandchild alive; one that kills
    the tree returns at once with both gone."""
    monkeypatch.setattr(runner, "REAP_TIMEOUT", 30.0)
    pidfile = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"
    )
    gc_pid = None
    try:
        t0 = time.monotonic()
        run = runner.run_bounded([PY, "-c", child], cwd=tmp_path, timeout=GRANDCHILD_LIMIT)
        back = time.monotonic()
        assert pidfile.is_file(), "the grandchild never started, so this proved nothing"
        gc_pid = int(pidfile.read_text())
        assert run.timed_out is True
        _judge_kill(kills, t0, back, GRANDCHILD_LIMIT, "the runner")
        deadline = time.monotonic() + 10
        while _alive(gc_pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(gc_pid), "the grandchild outlived the kill"
    finally:
        if gc_pid is None and pidfile.exists():
            gc_pid = int(pidfile.read_text())
        if gc_pid is not None and _alive(gc_pid):
            _kill(gc_pid)


# --- byte-exact mutation and restore ----------------------------------------------------------
#
# Every harness here used read_text/write_text, which translate line endings on Windows: a
# "restore" rewrote an LF file as CRLF (the broker's _suite_runner.py, this module's first home, came back CRLF
# that way), the tree read as changed, and a pattern written for one spelling stopped matching.

PATTERN_OLD, PATTERN_NEW = "a = 1\nb = 2\n", "a = 9\nb = 2\n"


def test_a_crlf_file_is_mutated_in_its_own_line_endings():
    n, out = runner.mutate_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n", PATTERN_OLD, PATTERN_NEW)
    assert n == 1
    assert out == b"a = 9\r\nb = 2\r\nc = 3\r\n"


def test_an_lf_file_stays_lf():
    n, out = runner.mutate_bytes(b"a = 1\nb = 2\nc = 3\n", PATTERN_OLD, PATTERN_NEW)
    assert n == 1
    assert out == b"a = 9\nb = 2\nc = 3\n"


def test_a_mixed_file_changes_only_the_matched_span():
    n, out = runner.mutate_bytes(b"x\r\na = 1\nb = 2\nz\r\n", PATTERN_OLD, PATTERN_NEW)
    assert n == 1
    assert out == b"x\r\na = 9\nb = 2\nz\r\n"


def test_a_pattern_found_twice_in_either_spelling_is_not_applied():
    raw = b"a = 1\nb = 2\na = 1\r\nb = 2\r\n"
    assert runner.mutate_bytes(raw, PATTERN_OLD, PATTERN_NEW) == (2, raw)


# A pattern that STARTS at a line break. In a CRLF file its LF spelling also matches the "\n"
# inside the "\r\n", so the one real occurrence used to be counted twice (once per spelling)
# and every harness pattern of that shape read as PATTERN x2 on a CRLF checkout.
LEADING_OLD, LEADING_NEW = "\nfoo = 1", "\nfoo = 2"


def test_a_pattern_starting_at_a_line_break_is_found_once_in_a_crlf_file():
    n, out = runner.mutate_bytes(b"x = 0\r\nfoo = 1\r\n", LEADING_OLD, LEADING_NEW)
    assert n == 1
    assert out == b"x = 0\r\nfoo = 2\r\n"


def test_a_pattern_starting_at_a_line_break_is_found_once_in_an_lf_file():
    n, out = runner.mutate_bytes(b"x = 0\nfoo = 1\n", LEADING_OLD, LEADING_NEW)
    assert n == 1
    assert out == b"x = 0\nfoo = 2\n"


def test_a_leading_break_pattern_present_twice_in_a_mixed_file_is_still_refused():
    raw = b"x = 0\r\nfoo = 1\ny = 0\nfoo = 1\n"
    assert runner.mutate_bytes(raw, LEADING_OLD, LEADING_NEW) == (2, raw)


def test_a_restore_is_proven_byte_for_byte(tmp_path):
    f = tmp_path / "f.py"
    f.write_bytes(b"x = 1\n")
    runner.restore_exact(f, b"y = 2\r\n")
    assert f.read_bytes() == b"y = 2\r\n"

    class _Translating:
        """Writes what a text-mode round trip would: LF turned into CRLF."""
        def __init__(self):
            self.data = b""

        def write_bytes(self, data):
            self.data = data.replace(b"\n", b"\r\n")

        def read_bytes(self):
            return self.data

    with pytest.raises(RuntimeError, match="TREE IS DIRTY"):
        runner.restore_exact(_Translating(), b"x = 1\n")


def test_a_changed_real_file_is_named_as_a_dirty_tree(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"x\n")
    b.write_bytes(b"y\n")
    snap = runner.snapshot([a, b])
    runner.assert_unchanged(snap)
    b.write_bytes(b"y\r\n")                 # one line ending is enough
    with pytest.raises(RuntimeError, match="b.py.*TREE IS DIRTY"):
        runner.assert_unchanged(snap)


_SHOW_LIVE = ("import os; print('|'.join(os.environ.get(k, '<unset>') "
              "for k in ('CLI_LIVE', 'JOB_AID_LIVE_TESTS')))")


def test_no_child_ever_inherits_a_live_opt_in(tmp_path, monkeypatch):
    """A harness runs its suite dozens of times; CLI_LIVE left set in the shell would turn each
    run into real Claude calls. Stripped whether it comes from the environment or is passed."""
    monkeypatch.setenv("CLI_LIVE", "1")
    monkeypatch.setenv("JOB_AID_LIVE_TESTS", "1")
    bounded = runner.run_bounded([PY, "-c", _SHOW_LIVE], cwd=tmp_path, timeout=60)
    assert "<unset>|<unset>" in bounded.out, bounded.out
    passed = dict(os.environ, CLI_LIVE="1")
    tree = runner.run_tree([PY, "-c", _SHOW_LIVE], capture_output=True, text=True,
                           cwd=tmp_path, env=passed, timeout=60)
    assert tree.stdout.strip() == "<unset>|<unset>", tree.stdout


def test_write_like_keeps_each_files_own_line_endings(tmp_path):
    f = tmp_path / "f.sh"
    runner.write_like(f, "a\nb\n", b"x\r\ny\r\n")
    assert f.read_bytes() == b"a\r\nb\r\n"
    runner.write_like(f, "a\nb\n", b"x\ny\n")
    assert f.read_bytes() == b"a\nb\n"


# --- run_tree: subprocess.run with a tree-killing timeout ---------------------------------------
#
# For harnesses whose verdict logic is built on subprocess.run's own shapes (CompletedProcess,
# TimeoutExpired). Only the kill differs, so their logic can stay exactly as it was.

def test_run_tree_returns_what_subprocess_run_would(tmp_path):
    p = runner.run_tree([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); "
                                   "sys.exit(4)"], capture_output=True, text=True, cwd=tmp_path,
                        timeout=60)
    assert (p.returncode, p.stdout.strip(), p.stderr.strip()) == (4, "out", "err")


def test_run_tree_decodes_text_as_utf8_with_replacement(tmp_path):
    """text=True with no encoding used to decode in the console code page, where byte 0x8f has
    no character: the reader thread raised, stdout came back None, and a harness died on a
    TypeError instead of a verdict. U+03CF is CF 8F in UTF-8."""
    child = ("import sys; sys.stdout.buffer.write('\\u03cf ok\\n'.encode('utf-8')); "
             "sys.stdout.buffer.write(b'\\xff\\n'); sys.stdout.flush()")
    p = runner.run_tree([PY, "-c", child], capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "\u03cf ok\n\ufffd\n", repr(p.stdout)


def test_run_tree_raises_timeout_expired_promptly(tmp_path, kills):
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_tree([PY, "-c", "import time; time.sleep(60)"], capture_output=True,
                        cwd=tmp_path, timeout=1)
    _judge_kill(kills, t0, time.monotonic(), 1, "run_tree")


def test_run_tree_kills_a_grandchild_holding_the_pipe(tmp_path, monkeypatch, kills):
    """The same Windows trap as run_bounded's, through the drop-in."""
    monkeypatch.setattr(runner, "REAP_TIMEOUT", 30.0)
    pidfile = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"
    )
    gc_pid = None
    try:
        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run_tree([PY, "-c", child], capture_output=True, cwd=tmp_path,
                            timeout=GRANDCHILD_LIMIT)
        back = time.monotonic()
        assert pidfile.is_file(), "the grandchild never started, so this proved nothing"
        gc_pid = int(pidfile.read_text())
        _judge_kill(kills, t0, back, GRANDCHILD_LIMIT, "run_tree")
        deadline = time.monotonic() + 10
        while _alive(gc_pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(gc_pid), "the grandchild outlived the kill"
    finally:
        if gc_pid is None and pidfile.exists():
            gc_pid = int(pidfile.read_text())
        if gc_pid is not None and _alive(gc_pid):
            _kill(gc_pid)


def test_run_tree_without_a_timeout_is_still_bounded(tmp_path, monkeypatch):
    """`timeout=None` means the default limit, never "wait for ever"."""
    monkeypatch.setattr(runner, "DEFAULT_TIMEOUT", 1.0)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_tree([PY, "-c", "import time; time.sleep(60)"], capture_output=True,
                        cwd=tmp_path)


# --- parse_harness_args: the command line, read before anything is touched ----------------------

CASES = [("a case that fires", "file.py", "old", "new", "test_x"),
         ("Another Case", "file.py", "old", "new", "test_y")]
CONTROLS = [("a control that must survive", "file.py", "old", "new")]


def _parse(monkeypatch, *argv, cases=CASES, **kw):
    monkeypatch.setattr(sys, "argv", ["mutate_demo.py", *argv])
    return runner.parse_harness_args(cases, kw.pop("controls", CONTROLS), **kw)


def test_list_names_every_case_and_control_and_exits_before_returning(monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        _parse(monkeypatch, "--list")
    assert stop.value.code == 0
    assert capsys.readouterr().out.splitlines() == [
        "case\ta case that fires", "case\tAnother Case", "control\ta control that must survive",
        "2 mutations, 1 controls; listed, nothing run"]


def test_a_label_is_found_in_each_shape_a_harness_keeps_its_cases_in(monkeypatch, capsys):
    class Case:
        label = "an attribute"
    with pytest.raises(SystemExit):
        _parse(monkeypatch, "--list", cases=["a string", ("a tuple", 1), {"label": "a dict"}, Case()],
               controls=())
    rows = [ln.split("\t")[-1] for ln in capsys.readouterr().out.splitlines() if "\t" in ln]
    assert rows == ["a string", "a tuple", "a dict", "an attribute"]


def test_list_with_a_filter_names_only_what_the_filter_selects(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _parse(monkeypatch, "--list", "Case", filter=True)
    assert [ln for ln in capsys.readouterr().out.splitlines() if "\t" in ln] == ["case\tAnother Case"]
    with pytest.raises(SystemExit):
        _parse(monkeypatch, "--list", "case", filter=True, ignore_case=True)
    assert [ln for ln in capsys.readouterr().out.splitlines() if "\t" in ln] == [
        "case\ta case that fires", "case\tAnother Case"]


@pytest.mark.parametrize("argv", [["--no-such-flag"], ["--list", "--lsit"], ["-x"]])
def test_an_unknown_option_is_refused_with_a_usage_line(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as stop:
        _parse(monkeypatch, *argv, filter=True)
    err = capsys.readouterr()
    assert stop.value.code == 2 and err.out == ""
    assert "unknown option" in err.err and "usage: mutate_demo.py [--list] [LABEL-FILTER ...]" in err.err


def test_a_positional_argument_to_a_harness_with_no_filter_is_refused(monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        _parse(monkeypatch, "fires")
    assert stop.value.code == 2
    assert "takes no label filter" in capsys.readouterr().err


def test_the_filter_and_a_harness_flag_come_back_to_the_harness(monkeypatch):
    got = _parse(monkeypatch, "fires", "--whole-file", "Another", filter=True, flags=("--whole-file",))
    assert got == runner.HarnessArgs(False, ("fires", "Another"), frozenset({"--whole-file"}))
    assert _parse(monkeypatch) == runner.HarnessArgs(False, (), frozenset())


def test_a_harness_that_lists_itself_gets_list_back_and_unknown_options_still_refused(monkeypatch):
    assert _parse(monkeypatch, "--list", "om-", cases=None, filter=True) == \
        runner.HarnessArgs(True, ("om-",), frozenset())
    with pytest.raises(SystemExit) as stop:
        _parse(monkeypatch, "--list", "--bogus", cases=None, filter=True)
    assert stop.value.code == 2


def test_help_prints_the_usage_and_exits_0(monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        _parse(monkeypatch, "--help", flags=("--whole-file",))
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("usage: mutate_demo.py [--list] [--whole-file]\n")


# --- named_failed: FIRED means pytest reported THAT test red ------------------------------------
#
# About thirty harnesses judged FIRED by `expected in out`, which a traceback, an assertion message
# or a log line naming the test satisfies, and several by `expected in` a FAILED line, where
# `test_x` is also found in `test_x_more`.

_TRACEBACK = (
    "F.                                                                       [100%]\n"
    "=================================== FAILURES ===================================\n"
    "_________________________________ test_other __________________________________\n"
    "    def test_other():\n"
    ">       assert seen == 1, 'see test_refuses_a_bad_token, which pins the same read'\n"
    "E       AssertionError: see test_refuses_a_bad_token, which pins the same read\n"
    "tests/test_x.py:12: AssertionError\n"
    "----------------------------- Captured log call ------------------------------\n"
    "ERROR    broker:broker.py:88 tests/test_x.py::test_refuses_a_bad_token - not yet run\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_x.py::test_other - AssertionError: see test_refuses_a_bad_token, wh...\n"
    "1 failed, 1 passed in 0.12s\n")


def test_a_failed_line_for_the_expected_test_counts():
    out = _TRACEBACK.replace("FAILED tests/test_x.py::test_other",
                             "FAILED tests/test_x.py::test_refuses_a_bad_token")
    assert runner.named_failed(out, "test_refuses_a_bad_token")
    assert runner.named_failed(out, "test_x.py::test_refuses_a_bad_token")


def test_a_traceback_or_a_log_line_that_only_mentions_the_name_does_not():
    """test_other failed and its message, its source line and a captured ERROR record all name
    test_refuses_a_bad_token, which passed: `expected in out` called this FIRED."""
    assert "test_refuses_a_bad_token" in _TRACEBACK
    assert runner.failed_ids(_TRACEBACK) == ["tests/test_x.py::test_other"]
    assert not runner.named_failed(_TRACEBACK, "test_refuses_a_bad_token")


def test_a_failed_line_for_another_test_with_the_same_prefix_or_suffix_does_not():
    out = ("FAILED tests/test_x.py::test_refuses_a_bad_token_twice - assert 2 == 1\n"
           "FAILED tests/test_x.py::also_test_refuses_a_bad_token\n")
    assert not runner.named_failed(out, "test_refuses_a_bad_token")
    assert runner.named_failed(out, "test_refuses_a_bad_token_twice")


@pytest.mark.parametrize("line, expected, hit", [
    # a parametrized test: a bare name is any id, an id is that id only
    ("FAILED tests/test_x.py::test_a[chat-switched] - x", "test_a", True),
    ("FAILED tests/test_x.py::test_a[chat-switched] - x", "test_a[chat-switched]", True),
    ("FAILED tests/test_x.py::test_a[chat-switched] - x", "test_a[chat_stream-switched]", False),
    ("FAILED tests/test_x.py::test_a[chat-switched] - x", "test_a[chat]", False),
    # an id is free text: ` - `, `::`, a `]` of its own and a line ending's \r are not where the
    # test or its id ends
    ("FAILED tests/test_x.py::test_a[k]2] - x", "test_a[k]", False),
    ("FAILED tests/test_x.py::test_a[a - b] - x", "test_a[a - b]", True),
    ("FAILED tests/test_x.py::test_a[a::b] - x", "test_a", True),
    ("FAILED tests/test_x.py::test_a[a::b] - x", "b]", False),
    ("FAILED tests/test_x.py::test_a[k]\r", "test_a[k]", True),
    # a method under its class, and a file-qualified name under directories, Windows' too
    ("FAILED tests/test_x.py::TestK::test_a - x", "test_a", True),
    ("FAILED tests/test_x.py::TestK::test_a - x", "TestK::test_a", True),
    ("FAILED tests/test_x.py::TestK::test_a - x", "TestJ::test_a", False),
    ("FAILED apps\\gw\\tests\\test_x.py::test_a - x", "test_x.py::test_a", True),
    ("FAILED apps\\gw\\tests\\test_x.py::test_a - x", "tests/test_x.py::test_a", True),
    ("FAILED tests/test_y.py::test_a - x", "test_x.py::test_a", False),
    ("FAILED tests/old_test_x.py::test_a - x", "test_x.py::test_a", False),
    # no message, an error in setup, a pytest 9 subtest with a free-text description
    ("FAILED tests/test_x.py::test_a", "test_a", True),
    ("ERROR tests/test_x.py::test_a - RuntimeError: fixture broke", "test_a", True),
    ("SUBFAILED[the cap, then a byte over] (size=26) tests/test_x.py::test_a - x", "test_a", True),
    # not summary lines: a collection error names no test, a -v progress line, an indented copy
    ("ERROR tests/test_a.py - ImportError: no module named test_a", "test_a", False),
    ("tests/test_x.py::test_a FAILED                                    [ 50%]", "test_a", False),
    ("    FAILED tests/test_x.py::test_a - x", "test_a", False),
])
def test_each_shape_a_harness_names_a_test_in(line, expected, hit):
    assert runner.named_failed(line + "\n", expected) is hit


def test_an_id_that_quotes_another_test_id_names_only_its_own_test():
    """A test parametrized over summary lines (the shape test above is one) carries ` - `, `::`
    and brackets in its id. Cut at the first ` - ` and split on `::`, its line read as the test
    quoted inside the id and never as itself, so a harness naming it saw the wrong test fail."""
    own = "test_shape[FAILED tests/test_x.py::test_a[k] - x-test_a-True]"
    out = f"FAILED tools/tests/test_m.py::{own} - AssertionError: assert False is True\n"
    assert runner.failed_ids(out) == [f"tools/tests/test_m.py::{own}"]
    assert runner.named_failed(out, "test_shape")
    assert runner.named_failed(out, own)
    assert runner.named_failed(out, f"test_m.py::{own}")
    assert not runner.named_failed(out, "test_a")
    assert not runner.named_failed(out, "test_a[k]")


def test_real_pytest_output_reads_as_the_verdict_needs(tmp_path):
    """A real run, so the summary's actual shape (Windows' backslashed ids included) is what is
    read: test_target passes, a test that shares its prefix fails, and another fails whose
    message names it; only those two are reported."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text(
        "import logging\n\n"
        "def test_target():\n    pass\n\n"
        "def test_target_more():\n    assert False\n\n"
        "def test_other():\n"
        "    logging.getLogger('x').error('test_target saw this')\n"
        "    assert 0, 'test_target is named here'\n", encoding="utf-8")
    p = runner.run_tree([PY, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider"],
                        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 1 and "test_target is named here" in out, out
    assert sorted(i.replace("\\", "/") for i in runner.failed_ids(out)) == [
        "tests/test_demo.py::test_other", "tests/test_demo.py::test_target_more"], out
    assert not runner.named_failed(out, "test_target")
    assert runner.named_failed(out, "test_target_more")
    assert runner.named_failed(out, "test_demo.py::test_other")
