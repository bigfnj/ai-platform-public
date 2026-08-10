"""GPU VRAM accounting via nvidia-smi (best-effort).

Degrades gracefully: if nvidia-smi is missing or fails, returns ``None`` rather
than raising, so the broker still works (just without a hardware VRAM view).
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any


async def vram() -> dict[str, Any] | None:
    """Return {total_mib, used_mib, free_mib, gpu_name} for GPU 0, or None."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--query-gpu=memory.total,memory.used,memory.free,name",
            "--format=csv,noheader,nounits",
            "--id=0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (OSError, asyncio.TimeoutError):
        return None
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
