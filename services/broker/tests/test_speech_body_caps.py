"""The two platform-wide speech bodies are bounded.

WHY THIS EXISTS. `/v1/tts_light` and `/v1/transcribe` are the two capabilities every rail gets for
free, gated by `require_user` alone and by nothing else. Both bodies were unbounded at every hop.
Measured against the running broker before the cap: one `POST /v1/transcribe` carrying a 41.9 MB
JSON body was accepted, reached the media worker, and materialised a 30 MB temp file on the system
drive, 13.3 s round trip. `media.run_media_job` writes `json.dumps(spec)` to a second temp file on
the way, so the body exists several times over across gateway, broker and worker.

The caps are deliberately the same numbers the rails already chose for their own copies of these
bodies (smb-partner's `TranscribeBody` at 15 MB, both RAG rails' `SpeakBody` at 8000), so the
platform-wide route is not the loose one. This file pins the bound AND the headroom, because a cap
set too tight breaks dictation and read-aloud for everyone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BROKER = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def schemas():
    sys.path.insert(0, str(BROKER))
    try:
        from app import schemas as s
        return s
    finally:
        sys.path.pop(0)


def test_transcribe_rejects_an_oversized_body(schemas):
    from pydantic import ValidationError

    cap = 15_000_000
    with pytest.raises(ValidationError):
        schemas.TranscribeRequest(audio_b64="A" * (cap + 1))


def test_transcribe_still_accepts_a_real_utterance(schemas):
    """The measured reference point: the Kokoro probe clip is 200,748 bytes of wav, which is
    267,664 characters of base64. Two minutes of opus is comfortably inside the cap, and a
    regression that tightened this to a question-sized bound would break dictation silently."""
    assert schemas.TranscribeRequest(audio_b64="A" * 267_664).audio_b64
    assert schemas.TranscribeRequest(audio_b64="A" * 2_000_000).audio_b64


def test_tts_light_rejects_an_oversized_body(schemas):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.TtsLightRequest(text="x" * 8001)


def test_tts_light_still_accepts_a_long_read_aloud(schemas):
    """8000 characters is roughly ten minutes of speech. An IEP present-levels narrative, the
    longest thing this platform reads aloud, runs a few thousand characters."""
    assert schemas.TtsLightRequest(text="x" * 8000).text


def test_both_caps_are_declared_rather_than_implied(schemas):
    """Guards against the bound being removed while the tests above keep passing for some other
    reason: asserts the constraint exists on the field itself."""
    for model, field, want in (
        (schemas.TranscribeRequest, "audio_b64", 15_000_000),
        (schemas.TtsLightRequest, "text", 8000),
    ):
        meta = model.model_fields[field].metadata
        maxes = [getattr(m, "max_length", None) for m in meta]
        assert want in maxes, f"{model.__name__}.{field} has no max_length={want}: {meta}"


# --- A-7: the three GPU-gated speech bodies were still unbounded ---------------------------
# Left when tts_light and transcribe were capped, because their blast radius is different:
# these need the broker token and are GPU-gated, so they SERIALISE rather than fan out. The
# cost is still real (one oversized item ties up the card and writes a temp file), and
# "you need a token" is not a reason to accept an unbounded body.

_BODIES = [
    ("TtsRequest", lambda t: {"segments": [{"lang": "en", "text": t}]}),
    ("TtsBatchRequest", lambda t: {"items": [{"lang": "en", "text": t}]}),
    ("VoiceRequest", lambda t: {"voice_id": "v1", "text": t}),
]


@pytest.mark.parametrize("name,build", _BODIES)
def test_an_oversized_clip_is_refused(schemas, name, build):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        getattr(schemas, name)(**build("x" * 8001))


@pytest.mark.parametrize("name,build", _BODIES)
def test_a_clip_at_the_limit_is_accepted(schemas, name, build):
    """The headroom half. A cap one character too tight breaks a real read-aloud, and 8000
    characters is roughly ten minutes of speech."""
    getattr(schemas, name)(**build("x" * 8000))


def test_an_unbounded_batch_count_is_refused(schemas):
    """A batch is ONE XTTS load, so an unbounded list holds the card for as long as the list
    is long. Capping each item's text alone does not bound that."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.TtsBatchRequest(items=[{"lang": "en", "text": "hi"}] * 501)
    # And the limit is usable: a whole unit's vocabulary batch sits well under it.
    schemas.TtsBatchRequest(items=[{"lang": "en", "text": "hi"}] * 500)


def test_a_pause_segment_still_needs_no_text(schemas):
    """TtsSegment.text defaults to "" for lang == "pause", so adding max_length must not
    accidentally make it required."""
    schemas.TtsRequest(segments=[{"lang": "pause", "duration": 0.5}])
