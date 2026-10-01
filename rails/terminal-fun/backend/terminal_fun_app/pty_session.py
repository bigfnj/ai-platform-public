"""A local PTY subprocess bridged to the WebSocket. Linux-only (uses the stdlib
`pty`); it runs inside this rail's own container, never on the host.

Hardening: launched with an argv list (never a shell string) resolved on a fixed
PATH; each session gets an ephemeral tmpfs HOME (wiped on exit); its own
session/pgroup so teardown kills the whole group; and — where the deployment
grants it — a SECOND, lower-privileged uid, so the shell a roguelike will happily
hand a player is not the shell the backend runs under.

THE SHELL ESCAPE IS REAL, AND THE OLD REASONING FOR ALLOWING IT WAS WRONG.
`SHELL` below is a real bash on purpose (tmux/byobu panes need one), and NetHack's
`!` will use it. That was accepted because "the container holds no secrets". It
held one: uvicorn's environment carries BROKER_AUTH_TOKEN, the single credential
compose hands all nine rails, and the child env here is a full replacement but the
PID namespace is shared — so `tr '\\0' '\\n' < /proc/1/environ` read it straight
out of the backend. The same shell could read and delete every other player's
saves on /data. Both were found by the 2026-09-10 rail audit (docs/BACKLOG.md).

Two changes close it, and they are independent on purpose. The token can now be
delivered as a file (broker.py), which takes it out of every process environment.
And the game runs as `gameuser` while the backend runs as `funuser`, which is what
makes /proc/1 and /data — including that file — unreadable rather than merely
inconvenient. The uid half lives in sandbox.py (privilege, not terminals, and
importable off-Linux where this module is not); deploy/Dockerfile has the layout.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import shutil
import signal
import struct
import termios
from collections.abc import Sequence

from terminal_fun_app.sandbox import game_argv, make_session_home

_CHUNK = 65536
# Fixed launch PATH: /opt/fun/bin (our wrapper scripts) + the usual game dirs.
_GAME_PATH = "/opt/fun/bin:/usr/local/bin:/usr/bin:/bin:/usr/games:/usr/local/games"


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PtySession:
    def __init__(self, argv: Sequence[str], term: str, cols: int, rows: int,
                 env_extra: dict[str, str] | None = None, home: str | None = None) -> None:
        self._argv = list(argv)
        self._term = term
        self._env_extra = dict(env_extra or {})
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.master_fd: int | None = None
        self.proc: asyncio.subprocess.Process | None = None
        # A caller may supply the HOME (already seeded with a restored save) and own its
        # lifecycle — then close() does NOT wipe it, so the caller can capture the save first.
        # With no home given we mkdtemp our own ephemeral one and wipe it on close (default).
        self.home: str | None = home
        self._own_home = home is None

    async def start(self) -> None:
        exe = self._argv[0]
        if "/" not in exe:
            resolved = shutil.which(exe, path=_GAME_PATH)
            if resolved:
                exe = resolved
        # Resolve the game on the fixed PATH FIRST, then wrap. The wrapper execs whatever it
        # is handed, and it runs as a uid whose own PATH we do not control — resolving after
        # wrapping would move the lookup inside the sandbox and out of this fixed list.
        argv = game_argv([exe, *self._argv[1:]])

        if self.home is None:
            self.home = make_session_home("ft-")
        env = {
            "TERM": self._term,
            "HOME": self.home,
            "PATH": _GAME_PATH,
            # A real shell: tmux/byobu (hollywood) spawn panes via $SHELL. Assume a player
            # WILL reach it — NetHack's `!` is one keystroke — and make that boring rather
            # than forbidden. The boundary is the container (cap_drop ALL, no-new-privileges,
            # no host mounts, ephemeral home) plus, where the deployment grants it, the second
            # uid: /proc/1/environ and /data belong to funuser, and this is gameuser.
            # "No secrets in the container" was the old claim here and it was false; see the
            # module docstring. Do not restore it.
            "SHELL": "/bin/bash",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERMINFO": "/usr/share/terminfo",
            "COLUMNS": str(self.cols),
            "LINES": str(self.rows),
        }
        # Per-item tuning overrides (e.g. cowsay's COW_FILE/COW_MOOD). Validated upstream;
        # values are printable strings from a fixed schema, never raw shell.
        env.update(self._env_extra)

        master_fd, slave_fd = pty.openpty()
        _set_winsize(master_fd, self.cols, self.rows)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,  # own session+pgroup so teardown can killpg
                close_fds=True,
                env=env,
                cwd=self.home,
            )
        finally:
            os.close(slave_fd)  # the child holds it now; parent only needs the master
        os.set_blocking(master_fd, False)
        self.master_fd = master_fd

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(1, cols), max(1, rows)
        if self.master_fd is not None:
            _set_winsize(self.master_fd, self.cols, self.rows)

    def write(self, data: bytes) -> None:
        if self.master_fd is not None:
            with contextlib.suppress(OSError):
                os.write(self.master_fd, data)

    async def read(self) -> bytes:
        """Next chunk of PTY output; b'' on EOF (child exited) or a closed fd."""
        fd = self.master_fd
        if fd is None:
            return b""
        loop = asyncio.get_running_loop()
        while True:
            try:
                return os.read(fd, _CHUNK)  # b'' == EOF
            except BlockingIOError:
                pass
            except OSError:
                return b""
            ev = asyncio.Event()
            try:
                loop.add_reader(fd, ev.set)
            except (OSError, ValueError):
                return b""
            try:
                await ev.wait()
            finally:
                with contextlib.suppress(OSError, ValueError):
                    loop.remove_reader(fd)

    async def close(self) -> None:
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=2)
        if self.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
            self.master_fd = None
        if self._own_home and self.home:
            shutil.rmtree(self.home, ignore_errors=True)
            self.home = None
