"""Speech ops may run in their OWN interpreter (BROKER_TTS_PYTHON).

Speech and image want different stacks: the image venv runs transformers 5.5.4 while
`coqui-tts` pulls 5.15.0, and coqui-tts declares no torch at all so its CUDA build is
chosen per-venv. Merging them puts the working image path (recipe-book, bouquet, CVC) at
risk to fix audio. The same reason the ai-voice rail gives each engine its own venv.

These tests pin the routing so a later refactor cannot quietly send speech back into the
image venv — which would fail at runtime, in a subprocess, as an opaque 502.
"""
from types import SimpleNamespace

from app.broker import Broker

IMAGE_PY = r"D:\image-venv\Scripts\python.exe"
TTS_PY = r"D:\tts-venv\Scripts\python.exe"


def _broker(tts_python: str) -> Broker:
    b = Broker.__new__(Broker)   # no I/O; only the interpreter-selection logic is exercised
    b.settings = SimpleNamespace(media_python=IMAGE_PY, tts_python=tts_python)
    return b


def test_speech_ops_use_the_dedicated_interpreter():
    b = _broker(TTS_PY)
    assert b._media_python_for("tts") == TTS_PY
    assert b._media_python_for("tts_batch") == TTS_PY


def test_image_ops_always_use_the_media_interpreter():
    b = _broker(TTS_PY)
    assert b._media_python_for("image") == IMAGE_PY
    assert b._media_python_for("embed_image") == IMAGE_PY


def test_unset_tts_python_falls_back_to_one_venv():
    """Single-venv installs (and every deployment predating this split) keep working."""
    b = _broker("")
    for op in ("tts", "tts_batch", "image", "embed_image"):
        assert b._media_python_for(op) == IMAGE_PY


def test_unknown_or_missing_op_uses_the_media_interpreter():
    b = _broker(TTS_PY)
    assert b._media_python_for(None) == IMAGE_PY
    assert b._media_python_for("something_new") == IMAGE_PY
