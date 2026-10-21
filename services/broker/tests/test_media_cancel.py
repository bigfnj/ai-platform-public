"""A cancelled or timed-out media/voice job must not leave its worker running.

The worker holds the card. run_media_job / run_voice_job are called with the GPU gate
held and the heavy models already evicted, and the gate releases as soon as they return
-- so a worker that survives the call is a process holding ~12 GB of VRAM that the broker
believes is free. The next job then loads a second heavy model onto that card.

The regression this pins: `except asyncio.TimeoutError` was the only cleanup path, and
asyncio.CancelledError is a BaseException, so it went straight past. Every real cancel
arrives that way: /v1/cancel (GpuGate.cancel -> task.cancel()), a caller who hung up
(Broker.tts_light, _run_media and voice_synthesize ask the connection through _while_there
and cancel the job; the tests are test_tts_light.py's and test_gated_hang_up.py's), and a
broker shutting down. A client's disconnect alone cancels nothing: neither uvicorn nor
Starlette cancels a plain route's handler when its client goes, so only a route that asks
its connection stops a job for a caller who left.
"""

from __future__ import annotations

import asyncio

import pytest

from app import media, voice


class FakeProc:
    """An async subprocess that never finishes until it is killed."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.started = asyncio.Event()  # set when the job spawns it

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(3600)  # never returns on its own
        raise AssertionError("unreachable")

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or -9


@pytest.fixture()
def spawned(monkeypatch: pytest.MonkeyPatch) -> FakeProc:
    """Make both modules spawn a FakeProc and treat every path as present."""
    proc = FakeProc()

    async def fake_exec(*_a: object, **_k: object) -> FakeProc:
        proc.started.set()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(media.Path, "exists", lambda _self: True)
    monkeypatch.setattr(voice.Path, "exists", lambda _self: True)
    return proc


async def test_cancelled_media_job_kills_the_worker(spawned: FakeProc) -> None:
    task = asyncio.create_task(
        media.run_media_job(python_exe="py.exe", spec={"op": "image"}, timeout=600)
    )
    # Until the worker is spawned, not one loop turn: the spec is written in a thread first.
    await asyncio.wait_for(spawned.started.wait(), 10)
    await asyncio.sleep(0)  # let it reach communicate()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawned.killed, "cancelling the job left the media worker holding the GPU"


async def test_timed_out_media_job_kills_the_worker(spawned: FakeProc) -> None:
    with pytest.raises(media.MediaError, match="timed out"):
        await media.run_media_job(python_exe="py.exe", spec={"op": "image"}, timeout=0.01)

    assert spawned.killed, "the timeout path left the media worker holding the GPU"


async def test_cancelled_voice_job_kills_the_worker(spawned: FakeProc) -> None:
    task = asyncio.create_task(
        voice.run_voice_job(python_exe="py.exe", entry="cli.py", cwd=None,
                            voice_id="slj", text="hello", timeout=600)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawned.killed, "cancelling the job left the voice engine holding the GPU"


async def test_timed_out_voice_job_kills_the_worker(spawned: FakeProc) -> None:
    with pytest.raises(voice.VoiceError, match="timed out"):
        await voice.run_voice_job(python_exe="py.exe", entry="cli.py", cwd=None,
                                  voice_id="slj", text="hello", timeout=0.01)

    assert spawned.killed, "the timeout path left the voice engine holding the GPU"
