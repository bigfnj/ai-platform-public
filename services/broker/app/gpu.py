"""GPU VRAM accounting via nvidia-smi (best-effort).

Degrades gracefully: if nvidia-smi is missing or fails, returns ``None`` rather
than raising, so the broker still works (just without a hardware VRAM view).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import shutil
import time
from typing import Any

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0

#: How long one nvidia-smi answer serves every reader of a VramCache, in seconds. /v1/status is
#: polled about 27 times a minute (the shell every 5 s, each rail header every 6 s, BrokerTray
#: every 15 s), and each poll spawned nvidia-smi, ~180 ms. VRAM figures two seconds old are what
#: every one of those pollers draws anyway: none of them polls faster than that.
VRAM_TTL_S = 2.0


@functools.lru_cache(maxsize=1)
def _nvidia_smi() -> str | None:
    """nvidia-smi's path, looked up ONCE per process.

    A filesystem fact, not a policy one, resolved once for the reason the Claude CLI's executable
    is (Broker._cli_exe): `shutil.which` walks every PATH directory, times every PATHEXT extension
    on Windows, and it ran on every status poll. A driver installed after the broker started is
    seen when the broker next starts. Tests replace this function, not `shutil.which`.
    """
    return shutil.which("nvidia-smi")


class VramCache:
    """`vram()` at most once per `ttl` seconds, and ONE nvidia-smi for callers that ask together.

    Every caller inside the window gets the last answer, None (no card) included. A caller that
    finds the answer stale while another caller's probe is still out waits for THAT probe rather
    than spawning its own, so five concurrent polls are one child, not five.

    The probe is a task of its own and each caller waits on it through a shield: one caller
    hanging up must not cancel the probe the others are waiting on. A probe nobody waits for any
    more still ends by itself, because `vram()` bounds its child at 10 s and reaps it on every
    path, so this leaves no orphan. `aclose()` cancels one still out, and its reap runs.
    """

    def __init__(self, ttl: float = VRAM_TTL_S) -> None:
        self._ttl = ttl
        self._at: float | None = None
        self._value: dict[str, Any] | None = None
        self._task: asyncio.Task[dict[str, Any] | None] | None = None

    async def read(self) -> dict[str, Any] | None:
        if self._at is not None and time.monotonic() - self._at < self._ttl:
            return self._value
        loop = asyncio.get_running_loop()
        task = self._task
        # A task from another event loop (each asyncio.run is a new one) cannot be awaited here.
        if task is None or task.done() or task.get_loop() is not loop:
            task = self._task = loop.create_task(self._refresh())
        return await asyncio.shield(task)

    async def _refresh(self) -> dict[str, Any] | None:
        # The module attribute, looked up per call, so a test that replaces gpu.vram is obeyed.
        value = await vram()
        self._value, self._at = value, time.monotonic()
        return value

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


async def vram() -> dict[str, Any] | None:
    """Return {total_mib, used_mib, free_mib, gpu_name} for GPU 0, or None.

    One nvidia-smi per call. Broker.status() reads it through a VramCache instead."""
    exe = _nvidia_smi()
    if exe is None:
        return None
    # The spawn and the wait are DELIBERATELY separate try blocks, matching media.py and
    # voice.py. Sharing one block means `proc` may be unbound when the finally runs, because
    # create_subprocess_exec itself can raise OSError.
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--query-gpu=memory.total,memory.used,memory.free,name",
            "--format=csv,noheader,nounits",
            "--id=0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    # OSError as well as the timeout: splitting the original single try narrowed this
    # to TimeoutError alone, which let a pipe error out of communicate() escape the
    # "returns None rather than raising" contract this module's docstring states, and
    # that Broker.status() and audit_roles() both depend on.
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        # Reap nvidia-smi on EVERY abandoned path, not just the timeout — the third instance
        # of the fix media.py and voice.py already carry. CancelledError is a BaseException,
        # so `except asyncio.TimeoutError` never saw it, and a client disconnect mid-poll
        # arrives exactly that way (a broker shutting down cancels a VramCache probe so too).
        #
        # This one is about process accumulation rather than VRAM. vram() backs
        # Broker.status(), which the shell polls every 5s, every rail header every 6s, and
        # BrokerTray every 15s — roughly 27 calls/min with one shell and one rail tab open.
        # When nvidia-smi hangs (a wedged driver, or a CUDA process stuck uninterruptible,
        # routine on a box running SDXL and XTTS workers) each poll abandoned a child after
        # 10s and never waited on it: ~10 orphaned processes and pipe pairs a minute, in a
        # service running as LocalSystem.
        if proc.returncode is None:
            proc.kill()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT)
    if proc.returncode != 0 or not stdout:
        return None

    line = stdout.decode("utf-8", "replace").splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        total, used, free = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
    return {
        "total_mib": total,
        "used_mib": used,
        "free_mib": free,
        "gpu_name": parts[3],
    }
