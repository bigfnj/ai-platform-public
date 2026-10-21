"""The gateway's speech bodies hold the broker's bounds, and agree with them case for case.

/api/platform/tts_light and /api/platform/transcribe are open to any logged-in user. Their
bodies had no bounds at all, so an oversized text or upload was read whole and relayed, and a
bad suffix came back as a relayed 502 instead of a 422 naming the field. The broker's models
are the authority; this file proves the gateway's copies refuse exactly what they refuse.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_BROKER_DIR = Path(__file__).resolve().parents[4] / "services" / "broker"
if str(_BROKER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROKER_DIR))

from app.schemas import TranscribeRequest, TtsLightRequest  # noqa: E402
from platform_gateway_app import main as m  # noqa: E402


def _ok(model, **kw) -> bool:
    try:
        model(**kw)
    except ValidationError:
        return False
    return True


TTS_CASES = [
    {"text": "x" * 8000}, {"text": "x" * 8001},
    {"text": "hi", "speed": 0.5}, {"text": "hi", "speed": 2.0},
    {"text": "hi", "speed": 0.49}, {"text": "hi", "speed": 2.01},
]
TRANSCRIBE_CASES = [
    {"audio_b64": "QUJD"}, {"audio_b64": "A" * 15_000_000}, {"audio_b64": "A" * 15_000_001},
    {"audio_b64": "QUJD", "suffix": ".webm"}, {"audio_b64": "QUJD", "suffix": ".wav"},
    {"audio_b64": "QUJD", "suffix": "/../../x"}, {"audio_b64": "QUJD", "suffix": ".toolong12"},
    {"audio_b64": "QUJD", "suffix": "webm"},
]


@pytest.mark.parametrize("case", TTS_CASES, ids=range(len(TTS_CASES)))
def test_tts_light_body_refuses_what_the_broker_refuses(case):
    assert _ok(m.TtsLightBody, **case) == _ok(TtsLightRequest, **case)


@pytest.mark.parametrize("case", TRANSCRIBE_CASES, ids=range(len(TRANSCRIBE_CASES)))
def test_transcribe_body_refuses_what_the_broker_refuses(case):
    assert _ok(m.TranscribeBody, **case) == _ok(TranscribeRequest, **case)


def test_the_grid_is_not_degenerate():
    """Every case agreeing would also be true of two models that accept everything. Each grid
    must hold both outcomes, or a dropped bound on BOTH sides could pass unseen."""
    for model, cases in ((m.TtsLightBody, TTS_CASES), (m.TranscribeBody, TRANSCRIBE_CASES)):
        outcomes = {_ok(model, **c) for c in cases}
        assert outcomes == {True, False}, model.__name__


def test_the_unsafe_suffix_is_refused_at_the_gateway():
    with pytest.raises(ValidationError) as exc:
        m.TranscribeBody(audio_b64="QUJD", suffix="/../../x")
    assert "suffix" in str(exc.value)
