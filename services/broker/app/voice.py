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

# Ceiling on reaping a killed child. A kill is not a guarantee: on Windows
# TerminateProcess is asynchronous, and a process in a non-alertable kernel wait is not
# signalled until that wait returns, so `await proc.wait()` on its own can never return.
# Without this the reap silently removed the timeout the caller was promised.
_REAP_TIMEOUT = 5.0


class VoiceError(RuntimeError):
    """Raised when a voice engine fails, times out, or produces no audio."""


def load_registry(path: "str | Path | None") -> dict[str, Any]:
    """The voice registry (voice_id -> engine + asset paths + provenance).

    None means no engines root is configured, which is the state of a platform with the
    ai-voice rail removed. An empty catalog is the honest answer there; the alternative was
    a TypeError deep in a synth job.
    """
    if path is None:
        return {"voices": []}
    return json.loads(Path(path).read_text(encoding="utf-8"))


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

    tmp = Path(tempfile.mkdtemp(prefix="broker-voice-"))
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
            # never saw it -- and both /v1/cancel (GpuGate.cancel -> task.cancel())
            # and a client disconnect (uvicorn cancels the handler) arrive exactly
            # that way. The voice engine was then left running with its model resident
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
        for p in (txt_path, out_path):
            p.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
