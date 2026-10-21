"""Media worker orchestration: spawn the short-lived torch worker, get JSON back.

The broker (Ollama-only venv, async) shells out to ``media_worker.py`` under
edu-suite's CUDA venv for each media job. Spec goes in via a temp file, the result
comes back via a temp file (b64 artifacts inline), and the worker process EXITS so
its VRAM is fully reclaimed. Both files, and the worker's own temp files, are in one
directory of the job's, removed when the job ends however it ends; one a broker that
died mid-job left is removed at the next start (sweep_job_dirs). Callers must
already hold the GPU gate and have evicted resident heavy models before calling this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.cli_backend import settle, sweep_call_temps

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0

# A job's directory is removed in tries, the pause doubling from the first: a worker that was
# just killed can still hold its files for a moment (on Windows the venv's python.exe is a
# launcher, and the interpreter it started dies with its job object a little after it), and a
# file filter scanning what it wrote can refuse a delete as briefly. 1.5 s in all; read at call
# time.
_REMOVE_TRIES = 5
_REMOVE_PAUSE = 0.1

# A job's directory is `tempfile.mkdtemp(prefix=JOB_DIR_PREFIX)` in the system temp: the prefix
# and the eight characters tempfile draws from [a-z0-9_]. The startup sweep (sweep_job_dirs)
# removes only names of that shape, since the system temp is every program's. A voice job's
# directory is the same shape under a prefix of its own (voice.JOB_DIR_PREFIX).
JOB_DIR_PREFIX = "broker-media-"


def job_dir_name(prefix: str) -> re.Pattern[str]:
    """The names `tempfile.mkdtemp(prefix=prefix)` makes, for the sweep to match whole."""
    return re.compile(re.escape(prefix) + r"[a-z0-9_]{8}")


JOB_DIR_NAME = job_dir_name(JOB_DIR_PREFIX)

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
    """Run one media job in a subprocess and return its parsed JSON result.

    The job's own directory holds the spec, the result and `scratch`, which the spec names
    (`scratch_dir`) and the worker makes its temp files in, so removing the directory once the
    job ends removes everything the job wrote, a worker killed half way included: its own
    `finally` never runs then, and a transcribe's clip (up to 11 MB) used to stay in the
    system temp as `broker-stt-*`. The spec and the result are written and read in a thread:
    a transcribe's spec is up to 15 MB of base64 and an image batch's result several MB, and
    either one stalled every route while it went to disk."""
    if not Path(python_exe).exists():
        raise MediaError(f"media python not found: {python_exe!r} "
                         "(set BROKER_MEDIA_PYTHON or BROKER_MEDIA_ENABLED=false)")

    tmp = Path(tempfile.mkdtemp(prefix=JOB_DIR_PREFIX))
    in_path, out_path, scratch = tmp / "in.json", tmp / "out.json", tmp / "scratch"
    threads: list[asyncio.Future[Any]] = []
    try:
        scratch.mkdir()
        await _in_thread(threads, _write_json, in_path, {**spec, "scratch_dir": str(scratch)})
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
            # never saw it -- and every cancel arrives exactly that way: /v1/cancel
            # (GpuGate.cancel -> task.cancel()), a caller who hung up (Broker.tts_light
            # and, for the gated jobs, Broker._run_media ask the connection through
            # _while_there and cancel the job), a broker shutting down. A client's
            # disconnect alone cancels nothing: uvicorn and Starlette leave a plain
            # route's handler running, so only a route that asks its connection stops a
            # job for a caller who left.
            # The media worker was then left running with its model resident
            # while the GPU gate released underneath it, so the next job loaded a
            # second heavy model onto a card the broker believed was empty.
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT)

        err_tail = (stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        result = await _in_thread(threads, _read_result, out_path)
        if result is None:
            raise MediaError(f"media worker produced no result (exit {proc.returncode}). "
                             f"stderr:\n{err_tail}")
        if "error" in result:
            raise MediaError(f"media worker error: {result['error']}\nstderr:\n{err_tail}")
        if proc.returncode != 0:
            raise MediaError(f"media worker exit {proc.returncode}. stderr:\n{err_tail}")
        return result
    finally:
        # Waited for through a cancellation (settle, as a CLI call's reap is), and only after
        # every thread this job started has ended: a cancelled wait does not stop a thread, and
        # one still writing the spec holds the file the removal has to delete.
        await settle(asyncio.ensure_future(_clean_up(threads, tmp)))


async def _in_thread(threads: list[asyncio.Future[Any]], fn: Callable[..., Any],
                     *args: Any) -> Any:
    """`fn(*args)` in a thread, the thread's future kept in `threads` for the job's cleanup.
    A cancellation ends this wait, not the thread."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    threads.append(fut)
    return await asyncio.shield(fut)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_result(path: Path) -> Any:
    """The worker's result, or None when it wrote none."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(text)


async def _clean_up(threads: list[asyncio.Future[Any]], tmp: Path) -> None:
    for fut in threads:
        with contextlib.suppress(BaseException):    # its failure was the job's to report
            await fut
    await asyncio.to_thread(_remove_job_dir, tmp)


def _remove_job_dir(path: Path) -> bool:
    """Remove a job's directory and everything in it, in tries (`_REMOVE_TRIES`); True once it
    is gone. One that outlasts them is logged once, by name and error, and left. A media job's
    or a voice job's (voice.run_voice_job), so the log line names neither kind but the path."""
    pause = _REMOVE_PAUSE
    for attempt in range(_REMOVE_TRIES):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            failure = exc
        if attempt + 1 < _REMOVE_TRIES:
            time.sleep(pause)
            pause *= 2
    print(f"[broker] job directory {path} could not be removed after {_REMOVE_TRIES} "
          f"tries ({failure.__class__.__name__}: {failure.strerror or failure}); the next "
          "startup's sweep removes it", file=sys.stderr, flush=True)
    return False


def sweep_job_dirs(root: str, *, older_than: float,
                   name: re.Pattern[str] = JOB_DIR_NAME) -> tuple[int, int]:
    """Remove the job directories under `root` (the system temp) in which nothing has changed
    for `older_than` seconds: what a broker that died mid-job left (stopped by NSSM or the
    watchdog, or crashed: the job's `finally` never ran), or a removal a file filter outlasted
    (`_remove_job_dir`). The CLI calls' rule (`sweep_call_temps`: real directories only, nothing
    followed, aged by the newest time of anything inside) over this module's names, or over
    `name`, another job's (`voice.JOB_DIR_NAME`). `(removed, left)`."""
    return sweep_call_temps(root, older_than=older_than, name=name)
