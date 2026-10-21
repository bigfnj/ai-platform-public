"""Kokoro read-aloud (`/v1/tts_light`) must never disturb the resident model.

That is the whole reason this endpoint exists as a separate path from `tts()`. Read-aloud is
offered platform-wide, so it gets called mid-conversation from any rail; if it took the GPU
gate it would queue behind chat, and if it evicted like the other media ops it would drop the
model someone is talking to in order to speak one sentence, then reload it. Either behaviour
makes the feature unshippable.

The property is structural — "does not call gate.hold / _evict_other_heavy" — so these tests
assert it directly rather than through a live synthesis, which would need the ONNX weights and
a working media venv. A refactor that routes this through `_run_media` for tidiness would pass
a round-trip test and fail these.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app import broker as broker_mod
from app.broker import Broker

MODEL = r"D:\kokoro\kokoro-v1.0.onnx"
VOICES = r"D:\kokoro\voices-v1.0.bin"
MEDIA_PY = r"D:\media-venv\Scripts\python.exe"
KOKORO_PY = r"D:\kokoro-venv\Scripts\python.exe"
TTS_PY = r"D:\tts-venv\Scripts\python.exe"


def _broker(**over) -> Broker:
    b = Broker.__new__(Broker)      # no I/O; only the dispatch logic is exercised
    fields = dict(media_enabled=True, media_python=MEDIA_PY, tts_python="",
                  kokoro_python=KOKORO_PY, media_timeout=1200.0,
                  kokoro_model_path=MODEL, kokoro_voices_path=VOICES,
                  kokoro_voice="af_heart", kokoro_lang_code="a")
    fields.update(over)             # merged, so an override may replace a default
    b.settings = SimpleNamespace(**fields)
    # __init__ is skipped, so the ungated-job bound it builds is supplied here.
    b._light_media = asyncio.Semaphore(2)
    return b


@pytest.fixture
def captured(monkeypatch):
    """Capture the job the broker would have run, instead of spawning a worker."""
    seen: dict = {}

    async def fake_run(*, python_exe, spec, timeout):
        seen.update(python_exe=python_exe, spec=spec, timeout=timeout)
        return {"audio_b64": "AAA=", "sample_rate": 24000}

    monkeypatch.setattr(broker_mod.media, "run_media_job", fake_run)
    return seen


def test_builds_a_kokoro_job_with_the_configured_asset_paths(captured):
    out = asyncio.run(_broker().tts_light("hello there"))
    assert out["sample_rate"] == 24000
    spec = captured["spec"]
    assert spec["op"] == "kokoro_tts"
    assert spec["text"] == "hello there"
    # The worker cannot read broker settings (different interpreter), so the paths must
    # travel in the spec. Omitting them is a 'model not found' deep inside a subprocess.
    assert spec["model_path"] == MODEL
    assert spec["voices_path"] == VOICES
    assert captured["python_exe"] == KOKORO_PY


def test_defaults_to_the_platform_voice_af_heart(captured):
    """The shipped platform voice is American English female, and it is resolved from broker
    SETTINGS rather than the worker's literal so it can be changed for every rail at once
    (BROKER_KOKORO_VOICE) without rebuilding a frontend."""
    asyncio.run(_broker().tts_light("hi"))
    spec = captured["spec"]
    assert spec["voice"] == "af_heart"
    assert spec["lang_code"] == "a"


def test_voice_and_language_always_travel_together(captured):
    """A Spanish voice under lang 'a' produces garbled audio, so the pair is never split:
    both come from settings, or both from the caller."""
    asyncio.run(_broker(kokoro_voice="ef_dora", kokoro_lang_code="e").tts_light("hola"))
    spec = captured["spec"]
    assert (spec["voice"], spec["lang_code"]) == ("ef_dora", "e")


def test_speed_is_omitted_rather_than_sent_as_none(captured):
    """Unset speed must not become an explicit null — kokoro-onnx would choke on it."""
    asyncio.run(_broker().tts_light("hi"))
    assert "speed" not in captured["spec"]


def test_knobs_pass_through_when_given(captured):
    asyncio.run(_broker().tts_light("hola", voice="ef_dora", lang_code="e", speed=1.1))
    spec = captured["spec"]
    assert (spec["voice"], spec["lang_code"], spec["speed"]) == ("ef_dora", "e", 1.1)


def test_does_not_take_the_gpu_gate_or_evict(monkeypatch, captured):
    """The load-bearing assertion. A resident chat model must still be resident afterwards."""
    b = _broker()
    b.gate = SimpleNamespace(hold=lambda **_kw: pytest.fail("tts_light took the GPU gate"))

    async def no_evict(*_a, **_kw):
        pytest.fail("tts_light evicted the resident model")

    monkeypatch.setattr(Broker, "_evict_other_heavy", no_evict)
    asyncio.run(b.tts_light("still resident afterwards"))
    assert captured["spec"]["op"] == "kokoro_tts"


def test_unconfigured_kokoro_fails_with_the_variable_names():
    """A missing asset path is a deployment mistake; the error has to say which var to set
    rather than surfacing as an opaque subprocess failure."""
    b = _broker(kokoro_model_path="", kokoro_voices_path="")
    with pytest.raises(RuntimeError, match="BROKER_KOKORO_MODEL_PATH"):
        asyncio.run(b.tts_light("hi"))


def test_disabled_media_is_reported_as_such():
    b = _broker(media_enabled=False)
    with pytest.raises(RuntimeError, match="media is disabled"):
        asyncio.run(b.tts_light("hi"))


def test_three_interpreters_route_by_op():
    """Kokoro cannot share the media venv: kokoro-onnx requires numpy>=2.0.2 and
    simple-lama-inpainting requires numpy<2.0.0, so installing it there silently upgrades
    numpy underneath the whole image path. Routing it back would resurrect that."""
    b = _broker(tts_python=TTS_PY)
    assert b._media_python_for("kokoro_tts") == KOKORO_PY
    assert b._media_python_for("tts") == TTS_PY
    assert b._media_python_for("tts_batch") == TTS_PY
    assert b._media_python_for("image") == MEDIA_PY
    assert b._media_python_for("embed_image") == MEDIA_PY


def test_unset_kokoro_python_falls_back_to_the_media_venv():
    """Single-venv installs (and any deployment predating this split) keep working — the
    caller gets a real interpreter rather than an empty string."""
    b = _broker(kokoro_python="")
    assert b._media_python_for("kokoro_tts") == MEDIA_PY


# --- a read whose caller hangs up stops its job (2026-10-15) -----------------------------------
#
# Both routes onto Broker.tts_light: the platform's /v1/tts_light, and the OpenAI dialect's
# /openai/v1/audio/speech (an external client's; it watches its caller since 2026-10-15 too).
READS = {"v1": ("/v1/tts_light", b'{"text": "read this aloud"}'),
         "openai": ("/openai/v1/audio/speech", b'{"model": "tts-1", "input": "read this aloud"}')}


def _asgi_read(app, gone: asyncio.Event, sent: list, route: str = "v1") -> "asyncio.Future":
    """POST one read (READS[route]) through the raw ASGI interface as uvicorn delivers it (spec
    2.3): receive() answers http.disconnect once `gone` is set and never before."""
    path, body = READS[route]
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


@pytest.fixture
def served(monkeypatch):
    """The real app over a broker whose media job is a stand-in that runs until it is told to
    answer (`answer`), recording that it started and whether it was cancelled. Its cancellation
    is what kills a real worker (media.run_media_job's finally; test_media_cancel.py)."""
    from app.main import app
    b = _broker()
    job = {"started": asyncio.Event(), "answer": asyncio.Event(), "cancelled": False}

    async def run_job(*, python_exe, spec, timeout):
        job["started"].set()
        try:
            await job["answer"].wait()
        except asyncio.CancelledError:
            job["cancelled"] = True
            raise
        return {"audio_b64": "AAA=", "sample_rate": 24000}

    monkeypatch.setattr(broker_mod.media, "run_media_job", run_job)
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.1)
    open_gate = SimpleNamespace(auth_token="", tokens_configured=lambda: False)
    monkeypatch.setattr(app.state, "settings", open_gate, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    return app, b, job


@pytest.mark.parametrize("route", sorted(READS))
def test_a_read_whose_caller_hangs_up_stops_its_job(served, route):
    """A plain route never hears a disconnect, so the job ran on for a caller who had left (the
    gateway lets go when the browser stops a read; an SDK times out): its worker synthesised to
    the end, holding a light-media slot. Now each route hands the broker the connection's own
    `is_disconnected`, and the job is cancelled within a tick or two of the hang-up, its slot
    back."""
    app, b, job = served

    async def scenario():
        gone, sent = asyncio.Event(), []
        call = _asgi_read(app, gone, sent, route)
        await asyncio.wait_for(job["started"].wait(), 5)
        assert not call.done(), f"the read ended before its caller left: {sent}"
        gone.set()
        try:
            await asyncio.wait_for(asyncio.shield(call), 3)
        except asyncio.TimeoutError:
            job["answer"].set()         # let the old route end, so nothing outlives the test
            await asyncio.wait_for(call, 5)
            return None
        return sent

    sent = asyncio.run(scenario())
    assert sent is not None, "the job was still running 3 s after its caller hung up"
    assert job["cancelled"], "the read ended, but its job was never stopped"
    assert sent[0]["status"] == 499
    assert b._light_media._value == 2, "the read's light-media slot did not come back"


@pytest.mark.parametrize("route", sorted(READS))
def test_a_read_whose_caller_stays_is_answered(served, route):
    """The witness: asking must not cost a caller who waits for the audio."""
    app, b, job = served

    async def scenario():
        sent = []
        call = _asgi_read(app, asyncio.Event(), sent, route)
        await asyncio.wait_for(job["started"].wait(), 5)
        await asyncio.sleep(0.35)       # several ticks asked, and answered "still there"
        job["answer"].set()
        await asyncio.wait_for(call, 5)
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 200 and not job["cancelled"]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    if route == "v1":
        assert b'"sample_rate":24000' in body
    else:
        assert (b"content-type", b"audio/wav") in sent[0]["headers"]
        assert body == b"\x00\x00"         # "AAA=", the stand-in's audio, decoded
