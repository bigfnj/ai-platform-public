"""Per-word English + Spanish audio (asset caching + base64 for inlining).

The XTTS engine itself lives in edu_media_core.tts; this module only handles the
per-word file caching and base64 encoding the worksheet renderer needs.
Skip with --skip-audio if the torch/TTS stack is unavailable.
"""
from __future__ import annotations

import base64
from pathlib import Path

from edu_media_core import tts as core_tts

from .words import Word

AUDIO_DIR = Path(__file__).parent.parent.parent / "assets" / "audio"


def generate_word_audio(word: Word) -> tuple[str, str]:
    """
    Return (audio_en_b64, audio_es_b64) for the word.
    Saves WAV files to assets/audio/ for caching.
    """
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    en_path = AUDIO_DIR / f"{word.en}_en.wav"
    es_path = AUDIO_DIR / f"{word.en}_es.wav"

    if not en_path.exists():
        core_tts.save_wav(core_tts.synthesize_segment(word.en, "en"), en_path)

    if not es_path.exists() and word.es:
        core_tts.save_wav(core_tts.synthesize_segment(word.es, "es"), es_path)

    en_b64 = base64.b64encode(en_path.read_bytes()).decode() if en_path.exists() else ""
    es_b64 = base64.b64encode(es_path.read_bytes()).decode() if es_path.exists() else ""
    return en_b64, es_b64


def generate_all(words: list[Word], verbose: bool = True) -> None:
    """Generate audio for all words. Mutates word.audio_en_b64 / audio_es_b64 in-place."""
    for i, word in enumerate(words, 1):
        en_path = AUDIO_DIR / f"{word.en}_en.wav"
        es_path = AUDIO_DIR / f"{word.en}_es.wav"

        if en_path.exists() and es_path.exists():
            if verbose:
                print(f"  [{i}/{len(words)}] {word.en}: cached")
            word.audio_en_b64 = base64.b64encode(en_path.read_bytes()).decode()
            word.audio_es_b64 = base64.b64encode(es_path.read_bytes()).decode()
            continue

        if verbose:
            print(f"  [{i}/{len(words)}] {word.en}: generating...", end=" ", flush=True)

        if not word.es:
            if verbose:
                print("skipped (no Spanish word yet)")
            continue

        try:
            word.audio_en_b64, word.audio_es_b64 = generate_word_audio(word)
            if verbose:
                print("ok")
        except Exception as exc:
            if verbose:
                print(f"FAILED: {exc}")
