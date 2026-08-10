"""Shared Coqui XTTS v2 speech engine (bilingual EN / es_MX voice cloning).

Owns the single in-memory XTTS model, the ``weights_only`` monkey-patch,
reference-clip resolution, segment synthesis with retries, silence, and
WAV/base64 helpers. ``torch``, ``TTS`` and ``soundfile`` are imported lazily so
importing this module does not pull in CUDA.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

# Auto-accept the Coqui Public Model License so XTTS v2's first-run download
# doesn't hang on an interactive [y/n] prompt in a non-interactive shell.
os.environ.setdefault("COQUI_TOS_AGREED", "1")

SAMPLE_RATE = 24000  # XTTS v2 output sample rate
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
_RETRY_ATTEMPTS = 2
_RETRY_DELAY = 3.0

_tts = None  # loaded once, stays in memory for the full run


def voices_dir() -> Path:
    override = os.getenv("VOICES_DIR", "")
    if override:
        return Path(override)
    # edu-suite monorepo: <repo-root>/shared/voices
    return Path(__file__).resolve().parents[4] / "shared" / "voices"


def _reference_wav(lang: str) -> str:
    d = voices_dir()
    return str({"en": d / "english_reference.wav",
                "es": d / "spanish_reference.wav"}[lang])


def get_tts():
    """Load XTTS v2 once and cache it in memory."""
    global _tts
    if _tts is None:
        import torch
        # PyTorch 2.6+ defaults weights_only=True in torch.load, which breaks
        # Coqui's checkpoint loading (custom classes in the pickle). XTTS v2 is a
        # trusted public release, so weights_only=False is safe here.
        _original_load = torch.load
        torch.load = lambda *a, **kw: _original_load(*a, **{**kw, "weights_only": False})

        from TTS.api import TTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading XTTS v2 on {device}...")
        _tts = TTS(MODEL_NAME).to(device)

        torch.load = _original_load  # restore after model is loaded
        print("XTTS v2 ready.")
    return _tts


def synthesize_segment(text: str, lang: str) -> np.ndarray:
    """Synthesize one segment with the language's reference clip.

    ``lang`` must be "en" or "es". Retries up to ``_RETRY_ATTEMPTS`` times
    before raising. Returns a float32 array at ``SAMPLE_RATE``.
    """
    reference_wav = _reference_wav(lang)
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS + 1):
        try:
            wav = get_tts().tts(text=text, speaker_wav=reference_wav, language=lang)
            return np.array(wav, dtype=np.float32)
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                print(f"  [TTS] attempt {attempt + 1} failed — retrying in {_RETRY_DELAY}s: {exc}")
                time.sleep(_RETRY_DELAY)
    raise last_exc


def generate_silence(duration_seconds: float) -> np.ndarray:
    """Return a silence buffer of the given duration at ``SAMPLE_RATE``."""
    return np.zeros(int(SAMPLE_RATE * duration_seconds), dtype=np.float32)


def save_wav(wav: np.ndarray, path: str | Path) -> None:
    import soundfile as sf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), wav, SAMPLE_RATE)


def combine_and_save(segments: list[np.ndarray], output_path: str | Path) -> None:
    """Concatenate audio segments and write to a WAV file."""
    save_wav(np.concatenate(segments), output_path)


def wav_to_b64(wav: np.ndarray) -> str:
    """Encode a float32 audio array as a base64 WAV string (no data-URI prefix)."""
    import base64
    import io
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, wav, SAMPLE_RATE, format="WAV")
    return base64.b64encode(buf.getvalue()).decode()


def generate_timed_audio(script_segments: list[dict], output_path: str | Path) -> list[dict]:
    """Synthesize an ordered segment list and save the combined WAV.

    Each input segment is ``{lang, type, text, duration?}`` (as built by an
    app's script builder); ``lang == "pause"`` inserts silence of ``duration``
    seconds. Returns per-segment timing dicts with keys
    ``lang, type, text, start, end`` (seconds), so a player can sync highlights.
    """
    audio_parts: list[np.ndarray] = []
    timings: list[dict] = []
    cursor = 0.0
    total = len(script_segments)

    for i, seg in enumerate(script_segments, 1):
        lang = seg["lang"]

        if lang == "pause":
            duration = seg["duration"]
            audio_parts.append(generate_silence(duration))
            timings.append({"lang": "pause", "type": "pause", "text": "",
                            "start": cursor, "end": cursor + duration})
            print(f"  [{i}/{total}] [pause {duration}s]")
            cursor += duration
        else:
            text = seg["text"]
            print(f"  [{i}/{total}] [{lang.upper()}] {text}")
            wav = synthesize_segment(text, lang)
            duration = len(wav) / SAMPLE_RATE
            timings.append({"lang": lang, "type": seg.get("type", "sentence"),
                            "text": text, "start": cursor, "end": cursor + duration})
            audio_parts.append(wav)
            cursor += duration

    combine_and_save(audio_parts, output_path)
    print(f"  Saved → {output_path}")
    return timings
