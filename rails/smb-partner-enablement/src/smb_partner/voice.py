"""Speech synthesis for the partner voice agent — a backend seam, not a hard dependency.

WHY THIS IS A SEAM. The broker can synthesize speech (XTTS v2, ``POST /v1/tts``), but that
path runs in a media WORKER process which takes the full GPU gate and evicts *every* resident
heavy model before it runs. On a single-GPU box that means each spoken answer costs:

    evict the RAG model -> load XTTS -> speak -> exit -> reload the RAG model

…which is the exact opposite of an "always-on" voice agent. Broker TTS is therefore one
backend among several rather than the assumed default:

  ``browser``  the client speaks via the Web Speech API. Zero GPU, zero latency, works on a
               phone. The RAG model is never disturbed, so it and the embedder stay
               co-resident. This is the default and what carries the mobile experience.
  ``broker``   server-side XTTS via the broker's media worker. Higher-quality, voice-cloned
               audio; costs a model swap per utterance. Requires BROKER_MEDIA_ENABLED=true
               and a torch venv on the broker box.
  ``auto``     probe the broker once; use ``broker`` when its media worker is actually
               available, else ``browser``.
  ``off``      text only.

Everything above the seam (``/api/ask``, the mobile client) is written against ``speak()``
and the ``mode`` it reports, so swapping backends is a config change, not a rewrite.
"""
from __future__ import annotations

import re
import time

from smb_partner import broker, config

BACKENDS = ("auto", "browser", "broker", "off")

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
    segment: dict = {"lang": payload["lang"], "text": clean}
    if config.VOICE_SPEAKER:
        segment["speaker"] = config.VOICE_SPEAKER
    try:
        result = broker.tts([segment])
    except broker.BrokerError as exc:
        # A media worker that is configured but not actually runnable must not take the
        # answer down with it — degrade to the browser and say so.
        payload["mode"] = "browser"
        payload["degraded"] = str(exc)
        return payload
    payload["audio_b64"] = result.get("audio_b64")
    payload["sample_rate"] = result.get("sample_rate")
    return payload


def describe() -> dict:
    """Voice capability for the UI: which backend is live and why."""
    configured = (config.VOICE_BACKEND or "auto").strip().lower()
    effective = resolve_backend()
    return {
        "configured": configured,
        "effective": effective,
        "broker_media": _broker_media_ready(),
        "note": (
            "Broker TTS evicts the resident RAG model per utterance; browser synthesis "
            "keeps both models warm."
        ),
    }
