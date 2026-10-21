"""A GPU-gated job whose caller hangs up stops, frees the gate and leaves nothing behind.

/v1/image, /v1/tts, /v1/tts_batch and /v1/voice/synthesize take the GPU gate and evict every
resident model, then run a worker that holds the whole card. They were plain routes, and a plain
route never hears a disconnect (neither uvicorn nor Starlette cancels its handler), so a caller
who left (a rail that timed out, a closed tab) held the card for the rest of its job, or until
BROKER_MEDIA_TIMEOUT / BROKER_VOICE_TIMEOUT, and every job queued behind it waited too. Each
route now hands the broker the connection's own `is_disconnected`, asked every _GONE_TICK_S
while the job waits or runs (Broker._while_there, as /v1/tts_light's is), and answers 499.

Through the real app, with the REAL job runners (media.run_media_job, voice.run_voice_job):
only the worker process is a stand-in, spawned in place of the interpreter, which runs until it
is killed or told to answer. So what is asserted is the whole cleanup a hang-up has to reach:
the worker killed, the job's directory gone, the gate's slot and its queue entry released.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import broker as broker_mod
from app.broker import Broker, GpuGate

#: route -> (path, body). One per gated route, the OpenAI dialect's images route included.
ROUTES = {
    "image": ("/v1/image", {"prompts": ["a lighthouse at dusk"]}),
    "openai_image": ("/openai/v1/images/generations", {"prompt": "a lighthouse at dusk"}),
    "tts": ("/v1/tts", {"segments": [{"lang": "en", "text": "hello"}]}),
    "tts_batch": ("/v1/tts_batch", {"items": [{"lang": "en", "text": "hello"}]}),
    "voice": ("/v1/voice/synthesize", {"voice_id": "narrator", "text": "hello"}),
}


class Worker:
    """The stand-in for a worker or engine process: it runs until killed, or until `answer` is
    set, when it writes what the real one would (a media worker's result JSON, an engine's wav)
    and exits 0. `job_dir` is the job's directory, read from its arguments."""

    def __init__(self, args: tuple, answer: asyncio.Event) -> None:
        self.args = [str(a) for a in args]
        self.answer = answer
        self.returncode: int | None = None
        self.killed = False

    @property
    def job_dir(self) -> Path:
        if "--text" in self.args:                       # voice: --text @<dir>/text.txt
            return Path(self.args[self.args.index("--text") + 1][1:]).parent
        return Path(self.args[2]).parent                # media: <py> <worker> <in> <out>

    async def communicate(self) -> tuple[bytes, bytes]:
        await self.answer.wait()
        if "--out" in self.args:
            Path(self.args[self.args.index("--out") + 1]).write_bytes(b"RIFF" + b"\0" * 40)
        else:
            Path(self.args[3]).write_text(json.dumps(
                {"images": ["AAA="], "errors": [], "audio_b64": "AAA=", "audios": ["AAA="]}),
                encoding="utf-8")
        self.returncode = 0
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else -9


@pytest.fixture
def served(monkeypatch, tmp_path):
    """The real app over a broker with media and voice on, whose eviction is a no-op and whose
    workers are Worker stand-ins (`spawned`, in order). `answer` lets them finish."""
    from app.main import app
    entry = tmp_path / "engine_cli.py"
    entry.write_text("# a stand-in: never run\n", encoding="utf-8")
    b = Broker.__new__(Broker)          # no I/O; the gated paths read only what is set here
    b.settings = SimpleNamespace(
        media_enabled=True, media_python=sys.executable, tts_python="", kokoro_python="",
        whisper_python="", media_core_src="", media_voices_dir="", media_timeout=600.0,
        voice_enabled=True, voice_registry_path=lambda: "unused", voice_timeout=600.0,
        voice_engines=lambda: {"xtts": {"python": sys.executable, "entry": str(entry)}},
        roles=lambda: {})
    b.gate = GpuGate()
    b._media_active = None
    spawned: list[Worker] = []
    answer = asyncio.Event()

    async def spawn(*args, **_kw):
        w = Worker(args, answer)
        spawned.append(w)
        return w

    async def no_eviction(*_a, **_kw):
        return []

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(Broker, "_evict_other_heavy", no_eviction)
    monkeypatch.setattr(broker_mod.voice, "load_registry",
                        lambda _path: {"voices": [{"voice_id": "narrator", "engine": "xtts"}]})
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.1)
    open_gate = SimpleNamespace(auth_token="", tokens_configured=lambda: False)
    monkeypatch.setattr(app.state, "settings", open_gate, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    return SimpleNamespace(app=app, broker=b, spawned=spawned, answer=answer)


def _post(app, route: str, gone: asyncio.Event, sent: list) -> asyncio.Future:
    """POST ROUTES[route] through the raw ASGI interface as uvicorn delivers it (spec 2.3):
    receive() answers http.disconnect once `gone` is set and never before."""
    path, payload = ROUTES[route]
    body = json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 11500),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    return asyncio.ensure_future(app(scope, receive, send))


async def _until(cond, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_gated_job_whose_caller_hangs_up_stops_and_frees_the_gate(served, route):
    s = served

    async def scenario():
        gone, sent = asyncio.Event(), []
        call = _post(s.app, route, gone, sent)
        assert await _until(lambda: s.spawned), "the job never started its worker"
        assert s.broker.gate.depth() == {"active": 1, "waiting": 0}
        job_dir = s.spawned[0].job_dir
        assert job_dir.is_dir()
        gone.set()
        try:
            await asyncio.wait_for(asyncio.shield(call), 3)
        except asyncio.TimeoutError:
            s.answer.set()              # let the old route end, so nothing outlives the test
            await asyncio.wait_for(call, 10)
            return None, job_dir
        return sent, job_dir

    sent, job_dir = asyncio.run(scenario())
    assert sent is not None, f"{route}: the job was still running 3 s after its caller hung up"
    assert s.spawned[0].killed, f"{route}: the worker was not killed"
    assert not os.path.isdir(job_dir), f"{route}: the job's directory outlived the hang-up"
    assert s.broker.gate.depth() == {"active": 0, "waiting": 0} and s.broker.gate.jobs() == []
    assert s.broker._media_active is None
    assert sent[0]["status"] == 499, sent[:1]


def test_a_queued_job_whose_caller_hangs_up_leaves_the_queue(served):
    """Waiting for the gate is part of the job: a caller queued behind another job who leaves
    is dropped from the queue (the gate's `waiting` and its job list), not run later for
    nobody."""
    s = served

    async def scenario():
        first_gone, second_gone, first, second = asyncio.Event(), asyncio.Event(), [], []
        running = _post(s.app, "image", first_gone, first)
        assert await _until(lambda: s.spawned)
        queued = _post(s.app, "tts", second_gone, second)
        assert await _until(lambda: s.broker.gate.depth()["waiting"] == 1)
        second_gone.set()
        try:
            await asyncio.wait_for(asyncio.shield(queued), 3)
            dropped = True
        except asyncio.TimeoutError:
            dropped = False
        depth = s.broker.gate.depth()
        s.answer.set()
        await asyncio.wait_for(running, 10)
        if not dropped:
            await asyncio.wait_for(queued, 10)
        return dropped, depth, second, len(s.spawned)

    dropped, depth, second, workers = asyncio.run(scenario())
    assert dropped, "the queued caller's job was still waiting 3 s after it hung up"
    assert depth == {"active": 1, "waiting": 0}, depth
    assert second[0]["status"] == 499
    assert workers == 1, "the dropped job started a worker anyway"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_gated_job_whose_caller_stays_is_answered(served, route):
    """The witness: asking must not cost a caller who waits for the job."""
    s = served

    async def scenario():
        sent = []
        call = _post(s.app, route, asyncio.Event(), sent)
        assert await _until(lambda: s.spawned)
        await asyncio.sleep(0.35)       # several ticks asked, and answered "still there"
        s.answer.set()
        await asyncio.wait_for(call, 10)
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 200, sent[:2]
    assert not s.spawned[0].killed
    assert not os.path.isdir(s.spawned[0].job_dir)
    assert s.broker.gate.depth() == {"active": 0, "waiting": 0}


def test_an_admins_cancel_still_stops_a_gated_job(served):
    """A guard for the move, true before it too: the gate records the task a job runs in, and
    /v1/cancel cancels that task. The job now runs in a task of its own (_while_there), which is
    the one recorded, so the admin's cancel still kills the worker, removes its directory and
    frees the gate, and the request ends."""
    s = served

    async def scenario():
        sent = []
        call = _post(s.app, "image", asyncio.Event(), sent)
        assert await _until(lambda: s.spawned)
        [job] = s.broker.gate.jobs()
        assert s.broker.cancel_job(job["seq"])
        try:
            await asyncio.wait_for(call, 5)
        except (asyncio.CancelledError, Exception):     # noqa: BLE001 -- how it ends is not the point
            pass
        return s.spawned[0]

    worker = asyncio.run(scenario())
    assert worker.killed and not os.path.isdir(worker.job_dir)
    assert s.broker.gate.depth() == {"active": 0, "waiting": 0} and s.broker.gate.jobs() == []
