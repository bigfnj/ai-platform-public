"""Dictation (`/v1/transcribe`) must not queue behind the GPU or evict anything.

Sharper version of the tts_light constraint: the user has just stopped talking and is
watching a spinner. Taking the GPU gate would make dictation wait behind whatever chat is
running, and evicting would drop the model they are about to ask a question — so the answer
arrives late AND the next answer is slow. CPU/int8 keeps it off the card entirely.

Structural assertions again, not a round trip: a refactor that routed this through
`_run_media()` would pass an end-to-end test and fail these.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app import broker as broker_mod
from app.broker import Broker

MEDIA_PY = r"D:\media-venv\Scripts\python.exe"
KOKORO_PY = r"D:\kokoro-venv\Scripts\python.exe"
WHISPER_PY = r"D:\whisper-venv\Scripts\python.exe"
TTS_PY = r"D:\tts-venv\Scripts\python.exe"


def _broker(**over) -> Broker:
    b = Broker.__new__(Broker)
    fields = dict(media_enabled=True, media_python=MEDIA_PY, tts_python="",
                  kokoro_python=KOKORO_PY, whisper_python="", media_timeout=1200.0,
                  whisper_model="small", whisper_device="cpu", whisper_compute_type="int8")
    fields.update(over)
    b.settings = SimpleNamespace(**fields)
    return b


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    async def fake_run(*, python_exe, spec, timeout):
        seen.update(python_exe=python_exe, spec=spec, timeout=timeout)
        return {"text": "hello there", "language": "en", "duration": 1.2, "model": "small"}

    monkeypatch.setattr(broker_mod.media, "run_media_job", fake_run)
    return seen


def test_builds_a_transcribe_job_carrying_the_model_settings(captured):
    out = asyncio.run(_broker().transcribe("QUJD"))
    assert out["text"] == "hello there"
    spec = captured["spec"]
    assert spec["op"] == "transcribe"
    assert spec["audio_b64"] == "QUJD"
    # The worker runs under a different interpreter and cannot read broker settings, so the
    # model choice has to travel in the spec.
    assert (spec["model"], spec["device"], spec["compute_type"]) == ("small", "cpu", "int8")


def test_cpu_int8_is_the_default_because_speech_input_must_not_touch_the_card(captured):
    asyncio.run(_broker().transcribe("QUJD"))
    assert captured["spec"]["device"] == "cpu"
    assert captured["spec"]["compute_type"] == "int8"


def test_the_default_model_is_multilingual_not_english_only():
    """`.en` variants are English-ONLY: they ignore the language parameter entirely (the
    library warns "using 'en' instead") and render Spanish as nonsense. Measured on this
    box, small.en turned "El estudiante identificara la idea central de un texto de su
    nivel" into "de estudiante ... de cunibel", while multilingual small returned it
    verbatim. This platform is bilingual EN/es_MX by design, so an .en default is a bug.
    """
    from app.config import BrokerSettings

    default = BrokerSettings.model_fields["whisper_model"].default
    assert not default.endswith(".en"), (
        f"whisper_model default {default!r} is English-only; Spanish dictation would break"
    )


def test_container_hint_and_language_pass_through_only_when_given(captured):
    asyncio.run(_broker().transcribe("QUJD"))
    assert "suffix" not in captured["spec"]     # worker defaults to .webm
    assert "language" not in captured["spec"]   # None => Whisper auto-detects

    asyncio.run(_broker().transcribe("QUJD", suffix=".ogg", language="es"))
    assert captured["spec"]["suffix"] == ".ogg"
    assert captured["spec"]["language"] == "es"


def test_does_not_take_the_gpu_gate_or_evict(monkeypatch, captured):
    b = _broker()
    b.gate = SimpleNamespace(hold=lambda **_kw: pytest.fail("transcribe took the GPU gate"))

    async def no_evict(*_a, **_kw):
        pytest.fail("transcribe evicted the resident model")

    monkeypatch.setattr(Broker, "_evict_other_heavy", no_evict)
    asyncio.run(b.transcribe("QUJD"))
    assert captured["spec"]["op"] == "transcribe"


def test_disabled_media_is_reported_as_such():
    with pytest.raises(RuntimeError, match="media is disabled"):
        asyncio.run(_broker(media_enabled=False).transcribe("QUJD"))


def test_routes_to_the_light_speech_venv_not_the_torch_one(captured):
    """Whisper is CTranslate2, not torch — it shares Kokoro's venv. Sending it to the media
    venv would be a ModuleNotFoundError inside a subprocess, surfacing as an opaque 502."""
    asyncio.run(_broker().transcribe("QUJD"))
    assert captured["python_exe"] == KOKORO_PY


def test_a_dedicated_whisper_interpreter_wins_when_set():
    """The split is available without touching call sites, if the two ever conflict."""
    b = _broker(whisper_python=WHISPER_PY)
    assert b._media_python_for("transcribe") == WHISPER_PY


def test_falls_all_the_way_back_to_the_media_venv_when_nothing_is_set():
    b = _broker(whisper_python="", kokoro_python="")
    assert b._media_python_for("transcribe") == MEDIA_PY


def test_the_four_op_families_do_not_cross_wires():
    b = _broker(tts_python=TTS_PY, whisper_python=WHISPER_PY)
    assert b._media_python_for("transcribe") == WHISPER_PY
    assert b._media_python_for("kokoro_tts") == KOKORO_PY
    assert b._media_python_for("tts") == TTS_PY
    assert b._media_python_for("image") == MEDIA_PY
