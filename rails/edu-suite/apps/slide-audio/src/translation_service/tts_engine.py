"""Thin shim — the XTTS engine now lives in edu_media_core.tts.

Kept so existing imports keep working. `generate_slide_audio` is the previous
name for `edu_media_core.tts.generate_timed_audio`.
"""
from edu_media_core.tts import (  # noqa: F401
    SAMPLE_RATE,
    MODEL_NAME,
    voices_dir,
    get_tts,
    synthesize_segment,
    generate_silence,
    combine_and_save,
    wav_to_b64,
    generate_timed_audio as generate_slide_audio,
)

# Backwards-compatible private alias (old module exposed _SAMPLE_RATE)
_SAMPLE_RATE = SAMPLE_RATE
