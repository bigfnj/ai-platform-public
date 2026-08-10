"""Media worker orchestration: spawn the short-lived torch worker, get JSON back.

The broker (Ollama-only venv, async) shells out to ``media_worker.py`` under
edu-suite's CUDA venv for each media job. Spec goes in via a temp file, the result
comes back via a temp file (b64 artifacts inline), and the worker process EXITS so
its VRAM is fully reclaimed. Callers must already hold the GPU gate and have
evicted resident heavy models before calling this.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

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
            proc.kill()
            await proc.wait()
            raise MediaError(f"media job timed out after {timeout}s") from exc

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
