"""Speech synthesis for the partner voice agent — a backend seam, not a hard dependency.

WHY THIS IS A SEAM. The broker exposes two TTS paths:

  * ``POST /v1/tts``       XTTS v2 — takes the full GPU gate and evicts every resident heavy
                           model before the worker runs. One model swap per utterance.
  * ``POST /v1/tts_light`` Kokoro-82M ONNX — NO gate, NO eviction. Kokoro (~350 MB) coexists
                           with the RAG LLM on the same card. This is the path this rail uses.

This rail's backends:

  ``browser``  the client speaks via the Web Speech API. Zero GPU, zero latency, works on a
               phone. The RAG model is never disturbed. Useful when the media venv isn't set up.
  ``broker``   Kokoro-82M via ``tts_light``. Both models stay GPU-resident simultaneously —
               the original prototype's "GPU · ready" status for both. Requires the media venv
               (kokoro-onnx + onnxruntime-directml) and BROKER_MEDIA_ENABLED=true.
  ``auto``     probe the broker once; use ``broker`` when its media worker is available, else
               ``browser``.
  ``off``      text only.

Everything above the seam (``/api/ask``, the mobile client) is written against ``speak()``
and the ``mode`` it reports, so swapping backends is a config change, not a rewrite.
"""
from __future__ import annotations

import re
import time

from smb_partner import broker, config

BACKENDS = ("auto", "browser", "broker", "off")

# Maps the BCP-47 lang tag from config/client to Kokoro's single-letter lang_code.
_KOKORO_LANG = {
    "en": "a", "en-us": "a", "en-gb": "b",
    "es": "e", "fr": "f", "ja": "j", "ko": "z", "zh": "z", "pt": "p",
}

# Probe result cache: the broker's media flag changes only when the broker restarts, and
# probing costs an HTTP round-trip on a path that wants to feel instant.
_probe: tuple[float, bool] | None = None
_PROBE_TTL = 300.0


class VoiceUnavailable(RuntimeError):
    """Raised when server-side synthesis was requested but cannot be served."""


def _broker_media_ready() -> bool:
    global _probe
    now = time.monotonic()
    if _probe is not None and now - _probe[0] < _PROBE_TTL:
        return _probe[1]
    ready = broker.media_enabled()
    _probe = (now, ready)
    return ready


def resolve_backend(requested: str | None = None) -> str:
    """The concrete backend to use: an explicit request wins, else the configured one,
    with ``auto`` collapsed to ``broker`` or ``browser`` by probing the broker."""
    choice = (requested or config.VOICE_BACKEND or "auto").strip().lower()
    if choice not in BACKENDS:
        choice = "auto"
    if choice == "auto":
        return "broker" if _broker_media_ready() else "browser"
    return choice


# Spoken text should not contain markdown scaffolding or bracket citations — a synthesizer
# will happily read "asterisk asterisk" and "bracket one" out loud.
_STRIP = (
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[\d+\]"), ""),
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"\*\*?([^*]+)\*\*?"), r"\1"),
    (re.compile(r"\s{2,}"), " "),
)


def speakable(text: str) -> str:
    """Flatten an answer into something worth reading aloud."""
    out = text or ""
    for pattern, repl in _STRIP:
        out = pattern.sub(repl, out)
    return out.strip()


def speak(text: str, *, backend: str | None = None, lang: str | None = None) -> dict:
    """Produce the voice payload for an answer.

    Always returns a dict carrying ``mode`` and the cleaned ``text``. Only the ``broker``
    mode also carries ``audio_b64``; in ``browser`` mode the client is expected to speak
    ``text`` itself, which keeps the GPU free for the two resident models.
    """
    mode = resolve_backend(backend)
    clean = speakable(text)
    payload: dict = {"mode": mode, "text": clean, "lang": lang or config.VOICE_LANG}
    if mode in ("off", "browser") or not clean:
        return payload
    lang_key = payload["lang"].lower()
    kokoro_lang = _KOKORO_LANG.get(lang_key, "a")
    voice_id = config.VOICE_SPEAKER or None  # e.g. "af_heart"; empty string → None → default
    try:
        result = broker.tts_light(clean, voice=voice_id, lang_code=kokoro_lang)
    except broker.BrokerError as exc:
        # A media worker that is configured but not actually runnable must not take the
        # answer down with it — degrade to the browser and say so.
        payload["mode"] = "browser"
        payload["degraded"] = str(exc)
        return payload
    payload["audio_b64"] = result.get("audio_b64")
    payload["sample_rate"] = result.get("sample_rate")
    return payload


def transcribe(audio_b64: str, *, suffix: str | None = None,
               language: str | None = None) -> dict:
    """Turn a recorded utterance into text via the broker's faster-whisper op.

    WHY THIS EXISTS. Speech *input* used to be the browser's ``SpeechRecognition``, which has
    two defects this rail could not live with: it ships the audio to Google, and it offers no
    way to choose an input device — it always uses the OS default. A user who picked a headset
    in our own device picker got silence and a ``no-speech`` error. Recording with
    ``getUserMedia({deviceId})`` and transcribing here honours the picker, keeps the audio on
    this box, and works in Firefox.

    Returns {"text", "language", "duration"}; raises VoiceUnavailable when the media worker
    cannot serve it, so the caller can say so rather than failing silently.
    """
    try:
        result = broker.transcribe(audio_b64, suffix=suffix, language=language)
    except broker.BrokerError as exc:
        raise VoiceUnavailable(f"speech-to-text unavailable: {exc}") from exc
    return {
        "text": (result.get("text") or "").strip(),
        "language": result.get("language") or "",
        "duration": result.get("duration") or 0.0,
    }


def can_transcribe() -> bool:
    """Whether server-side STT is actually available (same media-worker probe as TTS)."""
    return _broker_media_ready()


def describe() -> dict:
    """Voice capability for the UI: which backend is live and why."""
    configured = (config.VOICE_BACKEND or "auto").strip().lower()
    effective = resolve_backend()
    media = _broker_media_ready()
    return {
        "configured": configured,
        "effective": effective,
        "broker_media": media,
        # The UI uses this to decide between server-side recording and the browser's
        # recognizer, so it must reflect the media worker, not the TTS backend choice.
        "stt": "broker" if media else "browser",
        "note": (
            "Broker TTS uses Kokoro-82M via tts_light — no eviction, both models GPU-resident. "
            "Speech input uses faster-whisper (CPU) via /v1/transcribe, which honours the "
            "browser's selected microphone. Browser fallback uses Web Speech API."
        ),
    }
