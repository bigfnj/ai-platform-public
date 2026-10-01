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
