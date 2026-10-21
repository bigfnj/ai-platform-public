"""PtySession owns nothing after a failed launch, and cannot hold a size it cannot encode.

The 2026-09-10/11 audits recorded both and the 2026-10-08 audit reproduced them: a resize
frame of {"cols": 70000} was stored unclamped, struct.pack("HHHH") raised struct.error on the
next start() AFTER openpty, and nothing closed the two pty fds or removed the session HOME.
Any exec failure (a catalog binary missing from the image) leaked the same way, one fd and one
/tmp/ft-* per click.

pty / fcntl / termios are Linux-only, so they are stubbed: openpty() hands out a real os.pipe()
pair, which is what lets these tests see whether each fd was closed afterwards.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types

import pytest

from terminal_fun_app import dims


def _fd_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


@pytest.fixture
def pty_mod(monkeypatch, tmp_path):
    """terminal_fun_app.pty_session imported over stub Linux modules, plus a record of every fd
    openpty handed out and every HOME the session made."""
    opened: list[int] = []
    homes: list[str] = []

    def fake_openpty():
        r, w = os.pipe()
        opened.extend([r, w])
        return r, w

    winsizes: list[bytes] = []
    monkeypatch.setitem(sys.modules, "pty", types.SimpleNamespace(openpty=fake_openpty))
    monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(
        ioctl=lambda fd, req, packed: winsizes.append(packed)))
    monkeypatch.setitem(sys.modules, "termios", types.SimpleNamespace(TIOCSWINSZ=0x5414))
    monkeypatch.delitem(sys.modules, "terminal_fun_app.pty_session", raising=False)
    mod = importlib.import_module("terminal_fun_app.pty_session")

    def tracking_home(prefix):
        h = tmp_path / f"{prefix}{len(homes)}"
        h.mkdir()
        homes.append(str(h))
        return str(h)

    monkeypatch.setattr(mod, "make_session_home", tracking_home)
    monkeypatch.setattr(mod, "game_argv", lambda argv: argv)
    yield mod, opened, homes, winsizes
    for fd in opened:
        if _fd_open(fd):
            os.close(fd)
    # The stub-backed module must not outlive these tests.
    sys.modules.pop("terminal_fun_app.pty_session", None)


async def _refuse(*a, **k):
    raise FileNotFoundError("no such game in this image")


def test_an_exec_failure_releases_both_fds_and_the_home(pty_mod, monkeypatch):
    mod, opened, homes, _ = pty_mod
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _refuse)
    s = mod.PtySession(["missing-game"], "xterm", 80, 24)
    with pytest.raises(FileNotFoundError):
        asyncio.run(s.start())
    assert len(opened) == 2, "openpty never ran, so this proves nothing"
    assert [_fd_open(fd) for fd in opened] == [False, False], "a pty fd leaked"
    assert [os.path.isdir(h) for h in homes] == [False], "the session HOME leaked"
    assert s.home is None and s.master_fd is None


def test_a_failure_before_the_pty_still_removes_the_home(pty_mod, monkeypatch):
    """openpty itself can fail (the fd limit this leak used to reach)."""
    mod, _, homes, _ = pty_mod

    def no_pty():
        raise OSError(24, "Too many open files")
    monkeypatch.setattr(mod.pty, "openpty", no_pty)
    s = mod.PtySession(["cmatrix"], "xterm", 80, 24)
    with pytest.raises(OSError):
        asyncio.run(s.start())
    assert [os.path.isdir(h) for h in homes] == [False]


def test_a_caller_owned_home_is_left_for_the_caller(pty_mod, monkeypatch, tmp_path):
    """Saveable games pass their own HOME (seeded with the save) and capture it afterwards, so a
    failed launch must not delete it; main.py removes it after capture."""
    mod, _, _, _ = pty_mod
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _refuse)
    mine = tmp_path / "save-home"
    mine.mkdir()
    s = mod.PtySession(["nethack"], "xterm", 80, 24, home=str(mine))
    with pytest.raises(FileNotFoundError):
        asyncio.run(s.start())
    assert mine.is_dir()


@pytest.mark.parametrize("cols, rows, want", [
    (70000, 24, (500, 24)), (0, -3, (1, 1)), ("80", "24", (80, 24)), ("x", None, (80, 24)),
])
def test_a_session_cannot_hold_a_size_the_ioctl_cannot_encode(pty_mod, cols, rows, want):
    mod, _, _, _ = pty_mod
    s = mod.PtySession(["cmatrix"], "xterm", cols, rows)
    assert (s.cols, s.rows) == want


def test_resize_is_clamped_too_and_packs(pty_mod):
    """The poison itself: before the clamp, 70000 columns made struct.pack raise here."""
    mod, _, _, winsizes = pty_mod
    s = mod.PtySession(["cmatrix"], "xterm", 80, 24)
    s.master_fd = 99                         # pretend it is running; ioctl is a recorder
    s.resize(70000, 70000)
    assert (s.cols, s.rows) == (500, 500)
    assert len(winsizes) == 1


def test_the_clamp_bounds():
    assert dims.clamp_dim(70000, 80) == dims.MAX_DIM == 500
    assert dims.clamp_dim(-1, 80) == 1
    assert dims.clamp_dim(float("inf"), 80) == 80
    assert dims.clamp_dim(None, 24) == 24
