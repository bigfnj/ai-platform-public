"""Media worker orchestration: spawn the short-lived torch worker, get JSON back.

The broker (Ollama-only venv, async) shells out to ``media_worker.py`` under
edu-suite's CUDA venv for each media job. Spec goes in via a temp file, the result
comes back via a temp file (b64 artifacts inline), and the worker process EXITS so
its VRAM is fully reclaimed. Callers must already hold the GPU gate and have
evicted resident heavy models before calling this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0

# media_worker.py sits next to this module and is executed by a DIFFERENT
# interpreter (edu-suite's), so it is referenced by path, never imported.
WORKER = str(Path(__file__).resolve().parent / "media_worker.py")


class MediaError(RuntimeError):
    """Raised when the media worker fails, times out, or returns an error."""


async def run_media_job(
    *,
    python_exe: str,
    spec: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Run one media job in a subprocess and return its parsed JSON result."""
    if not Path(python_exe).exists():
        raise MediaError(f"media python not found: {python_exe!r} "
                         "(set BROKER_MEDIA_PYTHON or BROKER_MEDIA_ENABLED=false)")

    tmp = Path(tempfile.mkdtemp(prefix="broker-media-"))
    in_path, out_path = tmp / "in.json", tmp / "out.json"
    try:
        in_path.write_text(json.dumps(spec), encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, WORKER, str(in_path), str(out_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise MediaError(f"could not spawn media worker: {exc}") from exc

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise MediaError(f"media job timed out after {timeout}s") from exc
        finally:
            # Reap the media worker on EVERY abandoned path, not just the timeout.
            # CancelledError is a BaseException, so `except asyncio.TimeoutError`
            # never saw it -- and both /v1/cancel (GpuGate.cancel -> task.cancel())
            # and a client disconnect (uvicorn cancels the handler) arrive exactly
            # that way. The media worker was then left running with its model resident
            # while the GPU gate released underneath it, so the next job loaded a
            # second heavy model onto a card the broker believed was empty.
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT)

        err_tail = (stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        if not out_path.exists():
            raise MediaError(f"media worker produced no result (exit {proc.returncode}). "
                             f"stderr:\n{err_tail}")
        result = json.loads(out_path.read_text(encoding="utf-8"))
        if "error" in result:
            raise MediaError(f"media worker error: {result['error']}\nstderr:\n{err_tail}")
        if proc.returncode != 0:
            raise MediaError(f"media worker exit {proc.returncode}. stderr:\n{err_tail}")
        return result
    finally:
        for p in (in_path, out_path):
            p.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
