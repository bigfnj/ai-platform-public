"""GPU VRAM accounting via nvidia-smi (best-effort).

Degrades gracefully: if nvidia-smi is missing or fails, returns ``None`` rather
than raising, so the broker still works (just without a hardware VRAM view).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from typing import Any

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0


async def vram() -> dict[str, Any] | None:
    """Return {total_mib, used_mib, free_mib, gpu_name} for GPU 0, or None."""
    exe = shutil.which("nvidia-smi")
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
        # arrives exactly that way.
        #
        # This one is about process accumulation rather than VRAM. vram() is called from
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
