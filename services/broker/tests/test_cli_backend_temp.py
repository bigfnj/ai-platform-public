"""The Claude CLI backend: each call's own TEMP, removed once its tree is dead.

Split out of test_cli_backend.py by concern on 2026-10-19 (its docstring says what the
backend's tests assert, and lists the files); the fakes and shared helpers are
cli_backend_fakes.py's.
"""
import asyncio
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest

from app import cli_backend
from app.cli_backend import (
    CliUnavailable,
    call_temp_root,
)

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    _FakeProc,
    _Q,
    _after_first_delta,
    _cancel_again_and_again,
    _client,
    _left_under,
    _lines,
    _partial,
    _patch,
    _result,
    _said,
    _streaming_proc,
    _system_temp,
    broker_temp,
    group_kills,
    real_group_kill,
)


# --- each call's own TEMP, removed once its tree is dead (added 2026-10-14) ------------------
#
# The CLI writes under TEMP on every call, and while the child inherited the broker's TEMP it
# left one `claude\<workdir slug>\<session id>\` there per call, for as long as the broker ran.
# Every call now has a directory of its own under the temp root, and `_reap` removes it once the
# tree is dead, on every exit; the startup sweep removes what a removal could not.

_CALL_TEMP = re.compile(r"call-[0-9a-f]{32}")


def _age(path, seconds: float) -> None:
    """Make `path` and everything under it look untouched for `seconds`."""
    then = time.time() - seconds
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for name in (*filenames, *dirnames):
            os.utime(os.path.join(dirpath, name), (then, then))
    os.utime(path, (then, then))


@pytest.mark.parametrize("posix", [False, True], ids=["windows", "posix"])
def test_the_child_gets_the_calls_own_temp_in_place_of_the_brokers(monkeypatch, posix):
    """TEMP and TMP, and TMPDIR on POSIX (which Node reads first there), are the call's own
    directory, never the broker's: whatever the child writes under them goes with the call. A
    TMPDIR the broker's environment carries on Windows (Git Bash sets one) is replaced too."""
    monkeypatch.setattr(cli_backend, "_POSIX", posix)
    monkeypatch.setenv("TEMP", "C:\\broker-temp")
    monkeypatch.setenv("TMP", "C:\\broker-temp")
    monkeypatch.delenv("TMPDIR", raising=False)
    env = _client().build_env("D:\\calls\\call-1")
    assert env["TEMP"] == env["TMP"] == "D:\\calls\\call-1"
    assert env.get("TMPDIR") == ("D:\\calls\\call-1" if posix else None)
    monkeypatch.setenv("TMPDIR", "/broker-tmp")
    assert _client().build_env("D:\\calls\\call-1")["TMPDIR"] == "D:\\calls\\call-1"


@pytest.mark.parametrize("outcome", ["served", "failed", "timeout", "cancelled",
                                     "cancelled-again-and-again"])
async def test_a_buffered_call_leaves_no_temp_behind(monkeypatch, tmp_path, broker_temp,
                                                     outcome):
    """A fake CLI that writes into its TEMP as it starts, as the real one does. However the call
    ends, served, refused, timed out or cancelled (once, or the anyio way, again at every await:
    how Starlette cancels a stream that asked for a format), the directory and everything in it
    is gone by the time the call has ended, and the child had it as TEMP and TMP: one of its
    own, directly under the configured root, never the broker's TEMP."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)
    root = tmp_path / "tmp"
    hang = outcome in ("timeout", "cancelled", "cancelled-again-and-again")
    proc = _FakeProc(_result("ok"), b"Error: boom" if outcome == "failed" else b"",
                     1 if outcome == "failed" else 0, hang=hang)
    seen = _patch(monkeypatch, proc, spawn_yields=True, scribble=True)
    client = _client(temp_root=str(root), timeout=0.2 if outcome == "timeout" else 30)
    call = asyncio.ensure_future(client.chat([{"role": "user", "content": "q"}]))
    if outcome.startswith("cancelled"):
        for _ in range(200):
            if "temps" in seen:
                break
            await asyncio.sleep(0.01)
        assert seen.get("temps"), "the fake CLI never started"
        if outcome == "cancelled":
            call.cancel()
        else:
            await _cancel_again_and_again(call)
    try:
        await call
    except (CliUnavailable, asyncio.CancelledError):
        pass
    assert call.cancelled() is outcome.startswith("cancelled")
    [temp] = seen["temps"]
    assert os.path.dirname(temp) == str(root) and _CALL_TEMP.fullmatch(os.path.basename(temp))
    assert seen["env"]["TMP"] == temp
    assert _left_under(root) == [], "the call's temp directory outlived it"
    assert _left_under(broker_temp) == [], "the child wrote into the broker's own TEMP"


@pytest.mark.parametrize("outcome", ["served", "failed", "closed", "cancelled-again-and-again"])
async def test_a_streamed_call_leaves_no_temp_behind(monkeypatch, tmp_path, broker_temp,
                                                     outcome):
    """The streamed client the same way: served to the end, refused mid-stream, closed by a
    consumer that stopped reading after the first frame, and cancelled again and again (how
    Starlette cancels a disconnected text stream under ASGI 2.3)."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)
    root = tmp_path / "tmp"
    records = _partial("Hel", "lo")
    head = [_lines(records[:_after_first_delta(records)])]
    proc = (_streaming_proc([_lines(records)]) if outcome == "served"
            else _streaming_proc(head, code=1, err=b"Error: boom") if outcome == "failed"
            else _streaming_proc(head, hold=True))
    seen = _patch(monkeypatch, proc, spawn_yields=True, scribble=True)
    agen = _client(temp_root=str(root)).chat_stream(_Q)
    got: list = []

    async def consume():
        async for frame in agen:
            got.append(frame)

    if outcome == "closed":
        got.append(await agen.__anext__())
        await agen.aclose()
    elif outcome == "cancelled-again-and-again":
        task = asyncio.ensure_future(consume())
        for _ in range(200):
            if got:
                break
            await asyncio.sleep(0.01)
        await _cancel_again_and_again(task)
        assert task.cancelled()
    else:
        try:
            await consume()
        except CliUnavailable:
            assert outcome == "failed"
    assert _said(got)[:1] == ["Hel"]
    [temp] = seen["temps"]
    assert os.path.dirname(temp) == str(root) and seen["env"]["TMP"] == temp
    assert _left_under(root) == [], "the call's temp directory outlived it"
    assert _left_under(broker_temp) == [], "the child wrote into the broker's own TEMP"


@pytest.mark.parametrize("hang", [False, True], ids=["served", "cancelled"])
async def test_a_real_cli_that_writes_into_its_temp_leaves_nothing_behind(
        tmp_path, monkeypatch, broker_temp, real_group_kill, hang):
    """REAL processes. The stand-in CLI writes the real one's scratchpad under its TEMP and says
    which TEMP and TMP it was given, then answers, or hangs holding a file open there and
    writing another every 50 ms until its caller is cancelled. Either way the directory is gone
    when the call has ended, and the workdir is as empty as it began. The hanging one can be
    cleared only once its tree is dead: before, it writes the directory back (and on Windows
    its open file cannot be deleted at all). Cancelled once it has written, rather than timed
    out, so a slow start on a busy host cannot end the call before there is anything to
    clear. The hanging one stops by itself after two minutes: a mutation that removes the kill
    left a `while True` stand-in writing every 50 ms for as long as the box stayed up."""
    work, root, said = tmp_path / "work", tmp_path / "tmp", tmp_path / "temp.txt"
    work.mkdir()
    answer = json.dumps({"type": "result", "is_error": False, "result": "ok"})
    script = ("import os, sys, time\n"
              "t = os.environ['TEMP']\n"
              f"open({str(said)!r}, 'w').write(t + '|' + os.environ['TMP'])\n"
              "d = os.path.join(t, 'claude', 'C--work', 'session', 'scratchpad')\n"
              "os.makedirs(d)\n"
              "held = open(os.path.join(d, 'note.txt'), 'w')\n"
              "held.write('x')\n"
              "held.flush()\n"
              + ("for _ in range(2400):\n"
                 "    os.makedirs(d, exist_ok=True)\n"
                 "    open(os.path.join(d, 'tick.txt'), 'w').write('x')\n"
                 "    time.sleep(0.05)\n" if hang else f"sys.stdout.write({answer!r})\n"))
    client = _client(timeout=60, workdir=str(work), temp_root=str(root))
    monkeypatch.setattr(client, "build_argv", lambda *a, **k: [sys.executable, "-c", script])
    if hang:
        call = asyncio.ensure_future(client.chat([{"role": "user", "content": "q"}]))

        def written() -> bool:
            text = said.read_text() if said.exists() else ""
            return "|" in text and (pathlib.Path(text.split("|")[0]) / "claude").exists()

        for _ in range(600):
            if call.done() or written():
                break
            await asyncio.sleep(0.05)
        assert not call.done(), "the stand-in CLI ended on its own"
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        out = await client.chat([{"role": "user", "content": "q"}])
        assert out["message"]["content"] == "ok"
    temp, tmp = said.read_text().split("|")
    assert temp == tmp and os.path.dirname(temp) == str(root)
    assert _left_under(root) == [], "the call's temp directory outlived it"
    assert os.listdir(work) == [] and _left_under(broker_temp) == []


async def test_a_spawn_that_fails_leaves_no_temp_behind(monkeypatch, tmp_path):
    """The directory is made before the child starts; a child that never starts (a missing
    binary, a shim) takes it away again, and the call still falls back as `not_installed`."""
    root = tmp_path / "tmp"
    made: list = []

    async def boom(*a, **kw):
        made.append(os.path.isdir(kw["env"]["TEMP"]))
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    with pytest.raises(CliUnavailable) as exc:
        await _client(temp_root=str(root)).chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "not_installed" and made == [True]
    assert _left_under(root) == []


async def test_a_temp_root_that_cannot_be_made_is_a_fallback_and_nothing_starts(monkeypatch,
                                                                                tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    seen = _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client(temp_root=str(blocker / "tmp")).chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "failed" and str(blocker / "tmp") in exc.value.detail
    assert "argv" not in seen, "the CLI started with no temp directory of its own"


def test_the_temp_root_is_the_setting_else_beside_the_workdir_never_inside_it(tmp_path):
    work = tmp_path / "var" / "cli-work"
    assert call_temp_root("", str(work)) == str(tmp_path / "var" / "cli-tmp")
    assert call_temp_root("", f"{work}{os.sep}") == str(tmp_path / "var" / "cli-tmp")
    assert call_temp_root(str(tmp_path / "elsewhere"), str(work)) == str(tmp_path / "elsewhere")
    assert call_temp_root("", "") == os.path.join(tempfile.gettempdir(), "claude-cli-tmp")


async def test_with_no_temp_root_a_calls_temp_goes_beside_the_workdir(monkeypatch, tmp_path,
                                                                     broker_temp):
    """The broker's default: BROKER_CLI_TEMP_ROOT unset. The workdir stays empty."""
    work = tmp_path / "cli-work"
    work.mkdir()
    seen = _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0), scribble=True)
    await _client(workdir=str(work)).chat([{"role": "user", "content": "q"}])
    assert os.path.dirname(seen["temps"][0]) == str(tmp_path / "cli-tmp")
    assert os.listdir(work) == [] and _left_under(tmp_path / "cli-tmp") == []


def test_a_calls_temp_directory_is_the_brokers_account_alone(tmp_path):
    """Made with mode 0o700, as tempfile.mkdtemp makes one, so the root's own permissions (a
    Public folder, a shared drive) never reach it: on POSIX the owner's alone; on Windows
    (Python 3.12.4 and later) a protected ACL, the account, SYSTEM and Administrators, that
    inherits none of the root's entries."""
    path = _client(temp_root=str(tmp_path / "tmp"))._new_call_temp()
    try:
        if sys.platform == "win32":
            if sys.version_info < (3, 12, 4):
                pytest.skip("os.mkdir applies mode 0o700 as an ACL from Python 3.12.4")
            acl = subprocess.run(["icacls", path], capture_output=True, text=True).stdout
            assert acl.strip() and "(I)" not in acl, acl
        else:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    finally:
        os.rmdir(path)


async def test_a_removal_a_file_filter_refuses_for_a_moment_is_retried(monkeypatch, tmp_path,
                                                                       capsys):
    path = tmp_path / ("call-" + "a" * 32)
    (path / "claude").mkdir(parents=True)
    real, refused = shutil.rmtree, []

    def flaky(p, *a, **kw):
        if len(refused) < 2:
            refused.append(p)
            raise PermissionError(32, "The process cannot access the file because it is being "
                                      "used by another process")
        return real(p, *a, **kw)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    monkeypatch.setattr(cli_backend, "_TEMP_REMOVE_PAUSE", 0.01)
    assert await cli_backend._remove_call_temp(str(path)) is True
    assert not path.exists() and len(refused) == 2
    assert "could not be removed" not in capsys.readouterr().err


async def test_a_removal_that_never_succeeds_is_logged_once_and_left_for_the_sweep(
        monkeypatch, tmp_path, capsys):
    root = tmp_path / "tmp"
    path = root / ("call-" + "a" * 32)
    (path / "claude").mkdir(parents=True)
    real, tries = shutil.rmtree, []

    def refuse(p, *a, **kw):
        tries.append(p)
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    monkeypatch.setattr(cli_backend, "_TEMP_REMOVE_PAUSE", 0.01)
    assert await cli_backend._remove_call_temp(str(path)) is False
    assert len(tries) == cli_backend._TEMP_REMOVE_TRIES and path.exists()
    lines = [ln for ln in capsys.readouterr().err.splitlines() if "could not be removed" in ln]
    assert len(lines) == 1 and str(path) in lines[0] and "sweep" in lines[0], lines
    monkeypatch.setattr(shutil, "rmtree", real)
    assert cli_backend.sweep_call_temps(str(root), older_than=0) == (1, 0)
    assert _left_under(root) == []


def test_the_sweep_removes_stale_call_temps_and_leaves_fresh_ones_and_anything_else(tmp_path):
    """Only a call's own name, only a real directory, only once nothing in it has changed for
    the threshold: a directory whose own time is old but which holds a file written a moment
    ago is a call still writing, and an operator's own folder under the root is never touched."""
    root = tmp_path / "tmp"
    stale = root / ("call-" + "a" * 32)
    (stale / "claude" / "session" / "scratchpad").mkdir(parents=True)
    (stale / "claude" / "note.txt").write_text("x", encoding="utf-8")
    fresh = root / ("call-" + "b" * 32)
    fresh.mkdir()
    writing = root / ("call-" + "c" * 32)
    (writing / "claude").mkdir(parents=True)
    keep = root / "operator-notes"
    keep.mkdir()
    lookalike = root / ("call-" + "d" * 32 + ".old")
    lookalike.mkdir()
    a_file = root / ("call-" + "e" * 32)
    a_file.write_text("a file with a call's name", encoding="utf-8")
    for p in (stale, writing, keep, lookalike, a_file):
        _age(p, 7200)
    (writing / "claude" / "late.txt").write_text("written a moment ago", encoding="utf-8")
    os.utime(writing / "claude", (time.time() - 7200,) * 2)     # only the new file is recent
    assert cli_backend.sweep_call_temps(str(root), older_than=3600) == (1, 0)
    assert _left_under(root) == sorted(p.name for p in (fresh, writing, keep, lookalike, a_file))
    assert cli_backend.sweep_call_temps(str(tmp_path / "never-made")) == (0, 0)


def _sweep_settings(tmp_path, monkeypatch, **kw):
    from app.config import BrokerSettings
    monkeypatch.delenv("BROKER_CLI_TEMP_ROOT", raising=False)
    monkeypatch.delenv("BROKER_CLI_WORKDIR", raising=False)
    monkeypatch.delenv("BROKER_CLI_TIMEOUT", raising=False)
    return BrokerSettings(roles_file=str(tmp_path / "roles.json"),
                          disabled_file=str(tmp_path / "disabled.json"),
                          tokens_file=str(tmp_path / "tokens.json"),
                          upstreams_file=str(tmp_path / "upstreams.json"), auth_token="", **kw)


@pytest.mark.parametrize("timeout,kept", [(180.0, False), (3600.0, True)],
                         ids=["an-hour", "three-timeouts"])
def test_the_startup_sweep_waits_an_hour_or_three_call_timeouts_if_longer(
        tmp_path, monkeypatch, capsys, timeout, kept):
    """A directory two hours old: stale beside a 180 s BROKER_CLI_TIMEOUT, but possibly a call
    still running beside an hour-long one (a call's whole life is within two timeouts)."""
    from app.broker import Broker
    work = tmp_path / "work"
    work.mkdir()
    stale = tmp_path / "cli-tmp" / ("call-" + "a" * 32)
    stale.mkdir(parents=True)
    _age(stale, 2 * 3600)
    b = Broker(_sweep_settings(tmp_path, monkeypatch, cli_workdir=str(work),
                               cli_timeout=timeout))
    assert b.sweep_cli_temp() == ((0, 0) if kept else (1, 0))
    assert stale.exists() is kept
    log = capsys.readouterr().err
    assert ("removed 1 stale" in log and str(tmp_path / "cli-tmp") in log) is not kept, log


def test_with_no_workdir_and_no_temp_root_the_startup_sweep_touches_nothing(tmp_path,
                                                                            monkeypatch):
    from app.broker import Broker
    elsewhere = pathlib.Path(call_temp_root("", "")) / ("call-" + "a" * 32)
    elsewhere.mkdir(parents=True)
    _age(elsewhere, 7200)
    try:
        assert Broker(_sweep_settings(tmp_path, monkeypatch)).sweep_cli_temp() == (0, 0)
        assert elsewhere.exists()
    finally:
        shutil.rmtree(elsewhere)


def test_the_broker_sweeps_stale_call_temps_at_startup(tmp_path, monkeypatch):
    """The real lifespan, before any call of the new run can start."""
    from app import main
    from app.broker import Broker
    work = tmp_path / "work"
    work.mkdir()
    stale = tmp_path / "cli-tmp" / ("call-" + "a" * 32)
    (stale / "claude").mkdir(parents=True)
    _age(stale, 2 * 3600)
    settings = _sweep_settings(tmp_path, monkeypatch, cli_workdir=str(work))
    monkeypatch.setattr(main, "BrokerSettings", lambda: settings)

    async def answers(_broker):
        return True

    async def no_findings(_self):
        return []

    monkeypatch.setattr(main, "_ollama_answers", answers)
    monkeypatch.setattr(Broker, "audit_roles", no_findings)
    for name in ("settings", "broker", "token_seen"):
        monkeypatch.setattr(main.app.state, name, None, raising=False)

    async def boot() -> bool:
        async with main.lifespan(main.app):
            return stale.exists()

    assert asyncio.run(boot()) is False, "the startup left a stale call temp directory"
