"""The two SHORT-LIVED probes must not leave their child running either.

test_media_cancel.py covers the heavy workers, where an orphan holds ~12 GB of VRAM. These
two are cheaper per incident and far more frequent, which is what makes them worth pinning:

  gpu.vram()      -- called from Broker.status(), which the shell polls every 5s, every rail
                     header every 6s, and BrokerTray every 15s: roughly 27 calls/min with one
                     shell and one rail tab open. When nvidia-smi hangs (a wedged driver, or a
                     CUDA process stuck uninterruptible -- routine on a box running SDXL and
                     XTTS) each poll abandoned a child after 10s and never waited on it. That
                     is ~10 orphaned processes and pipe pairs a minute, as LocalSystem.

  Ollama.stop()   -- called from _evict_other_heavy() before EVERY media/image/tts/voice job,
                     and its whole purpose is to free VRAM. This is the one that reproduces
                     media.py's own failure story: a CancelledError during /v1/cancel orphans
                     an `ollama stop` while the GPU gate releases underneath it, so the next
                     job loads a second heavy model onto a card the broker believes is empty.

Same regression shape as the media/voice pair: `except (OSError, asyncio.TimeoutError)` was
the only cleanup path, and asyncio.CancelledError is a BaseException, so it went straight
past. Both real cancel routes arrive that way.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import httpx
import pytest

from app import gpu, ollama


class FakeProc:
    """An async subprocess that never finishes until it is killed."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

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
    """Make both probes spawn a FakeProc and believe their executable is on PATH."""
    proc = FakeProc()

    async def fake_exec(*_a: object, **_k: object) -> FakeProc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # Both gate on shutil.which, not Path.exists (unlike media/voice).
    monkeypatch.setattr(gpu.shutil, "which", lambda _n: "nvidia-smi")
    monkeypatch.setattr(ollama.shutil, "which", lambda _n: "ollama")
    return proc


def stub_http(client: "ollama.OllamaClient") -> list[dict]:
    """Replace the client's httpx transport and return the list it records into.

    Every stop() path that does NOT return early falls through to the API unload, so a
    test using a real OllamaClient would POST keep_alive=0 to whatever is listening on
    127.0.0.1:11434 and evict a model out of the live broker. This keeps the suite
    offline, and makes the fallback assertable rather than invisible.
    """
    calls: list[dict] = []

    class _NoNetwork:
        async def post(self, url: str, **kw: object) -> None:
            calls.append({"url": url, **kw})
            raise httpx.ConnectError("offline in tests")

    client._client = _NoNetwork()  # type: ignore[assignment]
    return calls


@pytest.fixture()
def impatient(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten every asyncio.wait_for so the 10s / 30s ceilings do not slow the suite.
    monkeypatch restores it, unlike assigning to the module attribute by hand."""
    real = asyncio.wait_for

    async def short(coro, timeout=None):  # noqa: ANN001 - test shim
        return await real(coro, 0.01)

    monkeypatch.setattr(asyncio, "wait_for", short)


async def test_a_timed_out_vram_probe_kills_nvidia_smi(spawned: FakeProc, impatient: None) -> None:
    # vram() swallows the timeout and returns None rather than raising, so the assertion is
    # on the child, not on an exception.
    assert await gpu.vram() is None
    assert spawned.killed, "a timed-out vram probe left nvidia-smi running"


async def test_a_timed_out_model_stop_kills_the_child(spawned: FakeProc, impatient: None) -> None:
    client = ollama.OllamaClient("http://127.0.0.1:11434")
    fell_back = stub_http(client)
    await client.stop("mistral-small3.2:24b")   # best-effort, never raises
    assert fell_back, "a timed-out CLI eviction must still try the API unload"
    assert spawned.killed, "a timed-out eviction left `ollama stop` holding the card"


async def test_a_cancelled_vram_probe_kills_nvidia_smi(spawned: FakeProc) -> None:
    task = asyncio.create_task(gpu.vram())
    await asyncio.sleep(0)  # let it reach communicate()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawned.killed, "cancelling a status poll left nvidia-smi running"


async def test_a_cancelled_model_stop_kills_the_child(spawned: FakeProc) -> None:
    """The important one: stop() runs with the GPU gate held, before every media job."""
    client = ollama.OllamaClient("http://127.0.0.1:11434")
    stub_http(client)
    task = asyncio.create_task(client.stop("mistral-small3.2:24b"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawned.killed, "cancelling an eviction left `ollama stop` holding the card"


class UnkillableProc:
    """A child that is sent SIGKILL and does not die.

    This is not a hypothetical. On Windows TerminateProcess is ASYNCHRONOUS, and a process
    sitting in a non-alertable kernel wait is not signalled until that wait returns, which
    is exactly the state gpu.py's own comment names as its motivation: a wedged driver, or
    a CUDA process stuck uninterruptible on a box running SDXL and XTTS.

    FakeProc above cannot express this, and that is why the first version of this fix
    shipped a regression: its kill() sets returncode, so `if proc.returncode is None`
    was already false and the reap body never ran. A fake whose kill() always works can
    only ever test the happy path of a kill.
    """

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        # As if the caller's wait_for had already fired, so the test reaches the reap
        # immediately without having to shorten any real timeout.
        raise asyncio.TimeoutError

    def kill(self) -> None:
        self.killed = True          # deliberately does NOT set returncode

    async def wait(self) -> int:
        await asyncio.sleep(3600)   # the kill never lands
        raise AssertionError("unreachable")


@pytest.fixture()
def unkillable(monkeypatch: pytest.MonkeyPatch) -> UnkillableProc:
    proc = UnkillableProc()

    async def fake_exec(*_a: object, **_k: object) -> UnkillableProc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(gpu.shutil, "which", lambda _n: "nvidia-smi")
    monkeypatch.setattr(ollama.shutil, "which", lambda _n: "ollama")
    # Shorten only the REAP ceiling, not asyncio.wait_for globally, so the test's own
    # outer bound stays meaningful.
    monkeypatch.setattr(gpu, "_REAP_TIMEOUT", 0.05)
    monkeypatch.setattr(ollama, "_REAP_TIMEOUT", 0.05)
    return proc


# The assertion below is on ELAPSED TIME, not on the return value, and that is not a
# stylistic choice. The obvious version of these two tests
#
#     assert await asyncio.wait_for(gpu.vram(), timeout=2.0) is None
#
# SURVIVED mutation: the reap runs inside contextlib.suppress(BaseException), so it
# swallows the very cancellation wait_for sends, the function proceeds to its own
# `return None`, and wait_for returns that instead of raising TimeoutError. The test
# passed against the unbounded code it was written to catch. The outer wait_for is kept
# only so a regression fails in 2s instead of wedging the suite for an hour.
_REAP_BUDGET = 1.0


async def test_an_unkillable_child_does_not_hang_the_vram_probe(
        unkillable: UnkillableProc) -> None:
    """vram() promises to return rather than raise, and it used to promise it within 10s.
    An unbounded `await proc.wait()` in the reap silently removed that ceiling on the
    broker's most-polled path: status() is hit ~27 times a minute."""
    t0 = time.perf_counter()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(gpu.vram(), timeout=2.0)
    elapsed = time.perf_counter() - t0

    assert elapsed < _REAP_BUDGET, (
        f"the vram probe took {elapsed:.2f}s to give up on a child that ignored its kill; "
        f"the reap has no ceiling of its own, so the 10s contract is conditional on the "
        f"kill working")
    assert unkillable.killed, "the probe did not even try to kill the child"


async def test_an_unkillable_child_does_not_hang_an_eviction(
        unkillable: UnkillableProc) -> None:
    """The consequential one. stop() runs INSIDE `async with self.gate.hold(...)`, so a
    reap that cannot return holds the single GPU gate for ever and every later chat,
    image, tts and voice job queues behind it permanently."""
    client = ollama.OllamaClient("http://127.0.0.1:11434")
    stub_http(client)
    t0 = time.perf_counter()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(client.stop("mistral-small3.2:24b"), timeout=2.0)
    elapsed = time.perf_counter() - t0

    assert elapsed < _REAP_BUDGET, (
        f"an eviction took {elapsed:.2f}s to give up on a child that ignored its kill, "
        f"and it holds the single GPU gate for all of it")
    assert unkillable.killed, "the eviction did not even try to kill the child"


class ExplodingProc(FakeProc):
    """communicate() raises OSError rather than timing out: a pipe error, not a hang."""

    async def communicate(self) -> tuple[bytes, bytes]:
        raise ConnectionResetError("the pipe went away")


@pytest.fixture()
def exploding(monkeypatch: pytest.MonkeyPatch) -> ExplodingProc:
    proc = ExplodingProc()

    async def fake_exec(*_a: object, **_k: object) -> ExplodingProc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(gpu.shutil, "which", lambda _n: "nvidia-smi")
    monkeypatch.setattr(ollama.shutil, "which", lambda _n: "ollama")
    return proc


async def test_a_pipe_error_from_communicate_does_not_escape_vram(
        exploding: ExplodingProc) -> None:
    """The module docstring says vram() "returns None rather than raising, so the broker
    still works". Broker.status() depends on it (its own guard does not cover this call)
    and so does audit_roles(), which runs unguarded in the lifespan: a raise there means
    the NSSM service hangs in startup instead of serving."""
    assert await gpu.vram() is None


async def test_a_pipe_error_from_communicate_does_not_escape_stop(
        exploding: ExplodingProc) -> None:
    """stop()'s docstring says "Best-effort: never raises". A raise here turns a VRAM
    reclaim into a 502 for the whole media job, and skips the HTTP-unload fallback."""
    client = ollama.OllamaClient("http://127.0.0.1:11434")
    fell_back = stub_http(client)
    await client.stop("mistral-small3.2:24b")
    assert fell_back, "a pipe error must still reach the documented API unload"


async def test_a_failed_spawn_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_subprocess_exec itself can raise OSError, which is why the spawn and the wait
    are separate try blocks — one shared block would leave `proc` unbound in the finally."""

    async def boom(*_a: object, **_k: object) -> None:
        raise OSError("no such executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    monkeypatch.setattr(gpu.shutil, "which", lambda _n: "nvidia-smi")
    monkeypatch.setattr(ollama.shutil, "which", lambda _n: "ollama")

    assert await gpu.vram() is None
    # stop() falls through to the HTTP unload, which has its own guard; it must not raise.
    client = ollama.OllamaClient("http://127.0.0.1:11434")
    stub_http(client)
    await client.stop("anything")
