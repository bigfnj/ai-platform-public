"""A light-media job whose caller hangs up stops and gives its slot back.

`/v1/embed_image`, `/v1/transcribe` and `/openai/v1/audio/transcriptions` run an ungated CPU worker
(SigLIP, faster-whisper) in one of the BROKER_LIGHT_MEDIA_CONCURRENCY slots. They were plain routes,
so a caller who left (a rail that timed out; dictation itself never aborts) held its slot until the
worker finished, and a read-aloud or a dictation queued for a slot waited behind it. Each now hands
the broker the connection's `is_disconnected`, asked every _GONE_TICK_S (`_while_there`, as
/v1/tts_light's is): a hang-up cancels the job, which kills its worker (media.run_media_job's
finally; test_media_cancel.py), and the route answers 499 once the slot is back.

Through the real app and the real Broker; the media job is a stand-in that runs until it is
cancelled or told to answer, recording which.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import broker as broker_mod
from app.broker import Broker

BOUNDARY = "light-media-test"
CLIP = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.webm\"\r\n"
        f"Content-Type: audio/webm\r\n\r\n").encode() + b"ABCD" + (
        f"\r\n--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
        f"--{BOUNDARY}--\r\n").encode()
JSON = b"application/json"

#: route -> (path, content type, body). One per light-media route.
ROUTES = {
    "embed_image": ("/v1/embed_image", JSON, json.dumps({"images": ["QUJD"]}).encode()),
    "transcribe": ("/v1/transcribe", JSON, json.dumps({"audio_b64": "QUJD"}).encode()),
    "openai_transcription": ("/openai/v1/audio/transcriptions",
                             f"multipart/form-data; boundary={BOUNDARY}".encode(), CLIP),
}


@pytest.fixture
def served(monkeypatch):
    """The real app over a broker with media on, whose light-media job is a stand-in."""
    from app.main import app
    b = Broker.__new__(Broker)          # no I/O; the light-media paths read only what is set here
    b.settings = SimpleNamespace(
        media_enabled=True, media_python="py", kokoro_python="", whisper_python="",
        tts_python="", media_timeout=600.0, whisper_model="small", whisper_device="cpu",
        whisper_compute_type="int8", image_embed_model_list=lambda: [])
    b._light_media = asyncio.Semaphore(2)
    job = {"started": asyncio.Event(), "answer": asyncio.Event(), "cancelled": False}

    async def run_job(*, python_exe, spec, timeout):
        job["started"].set()
        try:
            await job["answer"].wait()
        except asyncio.CancelledError:
            job["cancelled"] = True
            raise
        return {"embeddings": [[0.0]], "text": "hello there", "language": "en",
                "duration": 1.0, "model": "small"}

    monkeypatch.setattr(broker_mod.media, "run_media_job", run_job)
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.1)
    open_gate = SimpleNamespace(auth_token="", tokens_configured=lambda: False)
    monkeypatch.setattr(app.state, "settings", open_gate, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    return SimpleNamespace(app=app, broker=b, job=job)


def _post(app, route: str, gone: asyncio.Event, sent: list) -> asyncio.Future:
    """POST ROUTES[route] through the raw ASGI interface as uvicorn delivers it (spec 2.3):
    receive() answers http.disconnect once `gone` is set and never before."""
    path, ctype, body = ROUTES[route]
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
             "headers": [(b"content-type", ctype),
                         (b"content-length", str(len(body)).encode())]}
    return asyncio.ensure_future(app(scope, receive, send))


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_light_media_job_whose_caller_hangs_up_stops_and_frees_its_slot(served, route):
    s = served

    async def scenario():
        gone, sent = asyncio.Event(), []
        call = _post(s.app, route, gone, sent)
        await asyncio.wait_for(s.job["started"].wait(), 5)
        assert not call.done(), f"the job ended before its caller left: {sent}"
        gone.set()
        try:
            await asyncio.wait_for(asyncio.shield(call), 3)
        except asyncio.TimeoutError:
            s.job["answer"].set()       # let the old route end, so nothing outlives the test
            await asyncio.wait_for(call, 5)
            return None
        return sent

    sent = asyncio.run(scenario())
    assert sent is not None, f"{route}: the job was still running 3 s after its caller hung up"
    assert s.job["cancelled"], f"{route}: the request ended, but its job was never stopped"
    assert sent[0]["status"] == 499, sent[:1]
    assert s.broker._light_media._value == 2, f"{route}: the light-media slot did not come back"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_light_media_job_whose_caller_stays_is_answered(served, route):
    """The witness: asking must not cost a caller who waits for the result."""
    s = served

    async def scenario():
        sent = []
        call = _post(s.app, route, asyncio.Event(), sent)
        await asyncio.wait_for(s.job["started"].wait(), 5)
        await asyncio.sleep(0.35)       # several ticks asked, and answered "still there"
        s.job["answer"].set()
        await asyncio.wait_for(call, 5)
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 200 and not s.job["cancelled"], sent[:2]
