"""Voice-studio worker orchestration for the ai-voice rail.

Like media.py, the broker shells out to a short-lived GPU worker that EXITS to
reclaim VRAM. Unlike media (one edu-suite torch venv), each voice engine has its
OWN incompatible venv (Chatterbox py3.11/torch2.6, RVC py3.10/torch2.1+fairseq,
GPT-SoVITS py3.9, ...), so the engine is selected per voice and invoked by its
own interpreter against a stable CLI contract:

    <engine-python> <entry> --voice <id> --text @<textfile> --out <wav>

Callers must already hold the GPU gate and have evicted resident heavy models
(the engine loads its own model on the freed card), exactly like run_media_job.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

from app import media
from app.cli_backend import settle

# A voice job's directory is `tempfile.mkdtemp(prefix=JOB_DIR_PREFIX)` in the system temp, the
# media jobs' shape under a prefix of its own. It holds the text being read aloud and the wav,
# and only the job's own `finally` removes it, so one a broker that died mid-job left is removed
# at the next start (Broker.sweep_media_temp), which matches JOB_DIR_NAME whole and nothing else.
JOB_DIR_PREFIX = "broker-voice-"
JOB_DIR_NAME = media.job_dir_name(JOB_DIR_PREFIX)

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0


class VoiceError(RuntimeError):
    """Raised when a voice engine fails, times out, or produces no audio."""


class VoiceRegistryError(RuntimeError):
    """The registry file is missing, unreadable, or not a registry. Deliberately NOT a
    ValueError: /v1/voice/synthesize maps ValueError to 404 "no such voice", and a broken
    registry is a configuration fault, not a voice the caller got wrong."""


def load_registry(path: "str | Path | None") -> dict[str, Any]:
    """The voice registry (voice_id -> engine + asset paths + provenance).

    None means no engines root is configured, which is the state of a platform with the
    ai-voice rail removed. An empty catalog is the honest answer there; the alternative was
    a TypeError deep in a synth job.

    A root that IS configured but whose registry cannot be read raises VoiceRegistryError
    naming the file. It used to escape as a bare FileNotFoundError or JSONDecodeError, which
    /v1/voice/catalog (a read view) turned into an unexplained 500. So did a registry nested
    too deeply to parse (RecursionError).

    utf-8-SIG, like every hand-edited JSON the broker reads (config.overlay_roles says why): a
    registry saved by PowerShell's Set-Content carries a BOM, and read as plain utf-8 that was
    a 503 for a file with nothing wrong in it.
    """
    if path is None:
        return {"voices": []}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError) as exc:
        raise VoiceRegistryError(f"voice registry {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("voices", []), list):
        raise VoiceRegistryError(f"voice registry {path} is not a registry "
                                 "(expected an object with a 'voices' list)")
    return data


async def run_voice_job(
    *,
    python_exe: str,
    entry: str,
    cwd: str | None,
    voice_id: str,
    text: str,
    timeout: float,
) -> bytes:
    """Run one synthesis in the engine's own venv; return the wav bytes."""
    if not Path(python_exe).exists():
        raise VoiceError(f"voice engine python not found: {python_exe!r}")
    if not Path(entry).exists():
        raise VoiceError(f"voice engine entry not found: {entry!r}")

    tmp = Path(tempfile.mkdtemp(prefix=JOB_DIR_PREFIX))
    txt_path, out_path = tmp / "text.txt", tmp / "out.wav"
    try:
        txt_path.write_text(text, encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, entry,
                "--voice", voice_id,
                "--text", f"@{txt_path}",
                "--out", str(out_path),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise VoiceError(f"could not spawn voice engine: {exc}") from exc

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise VoiceError(f"voice job timed out after {timeout}s") from exc
        finally:
            # Reap the voice engine on EVERY abandoned path, not just the timeout.
            # CancelledError is a BaseException, so `except asyncio.TimeoutError`
            # never saw it -- and every cancel arrives exactly that way: /v1/cancel
            # (GpuGate.cancel -> task.cancel()), a caller who hung up (since
            # 2026-10-15 Broker.voice_synthesize asks the connection through
            # _while_there and cancels its job), a broker shutting down. A client's
            # disconnect alone cancels nothing: uvicorn and Starlette leave a plain
            # route's handler running, so only a route that asks its connection stops.
            # The voice engine was then left running with its model resident
            # while the GPU gate released underneath it, so the next job loaded a
            # second heavy model onto a card the broker believed was empty.
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT)

        err_tail = (stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        if not out_path.exists() or proc.returncode != 0:
            raise VoiceError(
                f"voice engine failed (exit {proc.returncode}). stderr:\n{err_tail}")
        return out_path.read_bytes()
    finally:
        # The whole directory, in tries and in a thread (media._remove_job_dir): a just-killed
        # engine can hold its files a moment after the kill (its venv's python.exe is a launcher,
        # and the interpreter it started dies a little after it), and a file filter scanning the
        # wav can refuse a delete as briefly. One that outlasts the tries is logged and left to
        # the next start's sweep. It never raises, so a refused delete can no longer replace the
        # job's own VoiceError or CancelledError, as an unlink here once could. Waited for
        # through a cancellation (settle), as a media job's removal is.
        await settle(asyncio.ensure_future(asyncio.to_thread(media._remove_job_dir, tmp)))
