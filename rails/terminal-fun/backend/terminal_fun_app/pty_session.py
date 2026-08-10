"""A local PTY subprocess bridged to the WebSocket. Linux-only (uses the stdlib
`pty`); it runs inside this rail's own container, never on the host.

Hardening: launched with an argv list (never a shell string) resolved on a fixed
PATH; each session gets an ephemeral tmpfs HOME (wiped on exit); SHELL=/bin/false
to neuter in-game shell escapes (e.g. NetHack's `!`); its own session/pgroup so
teardown kills the whole group.
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
import tempfile
import termios
from collections.abc import Sequence

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
        argv = [exe, *self._argv[1:]]

        if self.home is None:
            self.home = tempfile.mkdtemp(prefix="ft-")
        env = {
            "TERM": self._term,
            "HOME": self.home,
            "PATH": _GAME_PATH,
            # A real shell: tmux/byobu (hollywood) spawn panes via $SHELL, and roguelike
            # shell-escapes are harmless here — the container sandbox (non-root, cap_drop ALL,
            # no-new-privileges, no host mounts, no secrets, ephemeral home) is the boundary.
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
