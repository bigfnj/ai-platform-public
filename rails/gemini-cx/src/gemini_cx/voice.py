"""Speech synthesis for "Read aloud" — a backend seam, not a hard dependency.

Lifted from the smb-partner-enablement rail deliberately: same seam, same payload shape, same
degradation rules, so the two rails behave identically and there is one pattern to learn.

WHY THIS IS A SEAM. The broker exposes two TTS paths:

  * ``POST /v1/tts``       XTTS v2 — takes the full GPU gate and evicts every resident heavy
                           model before the worker runs. One model swap per utterance.
  * ``POST /v1/tts_light`` Kokoro-82M ONNX — NO gate, NO eviction. Kokoro (~350 MB) coexists
                           with the RAG LLM and the embedder. This is the path this rail uses.

Using ``/v1/tts`` here would evict this rail's answer model every time someone pressed the
button, which is exactly the co-residency the rail is built around. That is not a performance
detail; it is the reason this file picks the endpoint it picks.

Backends:

  ``browser``  the client speaks via the Web Speech API. Zero GPU, works anywhere, never
               disturbs the resident models. The fallback when the media venv is not set up.
  ``broker``   Kokoro-82M via ``tts_light``. Requires the media venv and
               BROKER_MEDIA_ENABLED=true on the broker.
  ``auto``     probe the broker once; use ``broker`` when its media worker is available,
               else ``browser``.
  ``off``      text only.

Everything above the seam (``/api/speak``, the client) is written against ``speak()`` and the
``mode`` it reports, so swapping backends is a config change rather than a rewrite.
"""
from __future__ import annotations

import re
import time

from gemini_cx import broker, config

BACKENDS = ("auto", "browser", "broker", "off")

# Maps the BCP-47 lang tag from config to Kokoro's single-letter lang_code.
_KOKORO_LANG = {
    "en": "a", "en-us": "a", "en-gb": "b",
    "es": "e", "fr": "f", "ja": "j", "ko": "z", "zh": "z", "pt": "p",
}

# Probe result cache: the broker's media flag changes only when the broker restarts, and
# probing costs an HTTP round-trip on a path that wants to feel instant.
_probe: tuple[float, bool] | None = None
_PROBE_TTL = 300.0


def _broker_media_ready() -> bool:
    global _probe
    now = time.monotonic()
    if _probe is not None and now - _probe[0] < _PROBE_TTL:
        return _probe[1]
    ready = broker.media_enabled()
    _probe = (now, ready)
    return ready


def resolve_backend(requested: str | None = None) -> str:
    """The concrete backend to use: an explicit request wins, else the configured one, with
    ``auto`` collapsed to ``broker`` or ``browser`` by probing the broker."""
    choice = (requested or config.VOICE_BACKEND or "auto").strip().lower()
    if choice not in BACKENDS:
        choice = "auto"
    if choice == "auto":
        return "broker" if _broker_media_ready() else "browser"
    return choice


# Spoken text must not contain markdown scaffolding or bracket citations — a synthesizer will
# happily read "asterisk asterisk" and "bracket one" out loud. This rail's answers are dense
# with both, so this is load-bearing rather than cosmetic.
_STRIP = (
    (re.compile(r"```.*?```", re.S), " "),        # fenced code blocks
    (re.compile(r"`([^`]*)`"), r"\1"),             # inline code
    (re.compile(r"\[\d+(?:,\s*\d+)*\]"), ""),      # [1] and [1, 2] citations
    (re.compile(r"\*\*?([^*]+)\*\*?"), r"\1"),     # bold / italic
)

# Headings and list items are matched per line, BEFORE their markers are removed, because both
# need a sentence boundary appended and that is impossible to detect once the marker is gone.
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")
# Removing an inline citation leaves the punctuation stranded: "per session [1]." -> "per
# session ." Pull it back onto the word, or the synthesizer pauses in the wrong place.
_ORPHAN_PUNCT = re.compile(r"\s+([.,;:!?])")
_RUNS = re.compile(r"\s{2,}")
_SENTENCE_END = ".!?:;,"


def speakable(text: str) -> str:
    """Flatten an answer into something worth reading aloud.

    Two fixes here beyond stripping markup, both found by listening to real output rather than
    by reading the regexes:

    * **List items and headings get a sentence boundary.** Simply deleting the marker and
      joining lines turns "- not seat-priced" + "- three component meters" into the run-on "not
      seat-priced three component meters", which sounds like one broken clause; a heading runs
      straight into the paragraph beneath it the same way. Both now end with a full stop unless
      they already end in punctuation. Plain prose lines are left alone, because markdown wraps
      paragraphs across lines and adding stops there would invent sentence breaks.
    * **Punctuation orphaned by citation removal is reattached**, so "per session [1]." does
      not become "per session ." and get read with a stumble before the pause.
    """
    out = text or ""
    for pattern, repl in _STRIP:
        out = pattern.sub(repl, out)

    lines: list[str] = []
    for raw in out.splitlines():
        match = _BULLET.match(raw) or _HEADING.match(raw)
        item = (match.group(1) if match else raw).strip()
        if not item:
            continue
        if match and item[-1] not in _SENTENCE_END:
            item += "."
        lines.append(item)

    out = " ".join(lines)
    out = _ORPHAN_PUNCT.sub(r"\1", out)
    return _RUNS.sub(" ", out).strip()


def speak(text: str, *, backend: str | None = None, lang: str | None = None) -> dict:
    """Produce the voice payload for a block of text.

    Always returns a dict carrying ``mode`` and the cleaned ``text``. Only ``broker`` mode also
    carries ``audio_b64``; in ``browser`` mode the client speaks ``text`` itself, which keeps the
    GPU free for the two resident models.
    """
    mode = resolve_backend(backend)
    clean = speakable(text)
    payload: dict = {"mode": mode, "text": clean, "lang": lang or config.VOICE_LANG}
    if mode in ("off", "browser") or not clean:
        return payload
    kokoro_lang = _KOKORO_LANG.get(payload["lang"].lower(), "a")
    voice_id = config.VOICE_SPEAKER or None  # empty string → None → Kokoro's own default
    try:
        result = broker.tts_light(clean, voice=voice_id, lang_code=kokoro_lang)
    except broker.BrokerError as exc:
        # A media worker that is configured but not actually runnable must not take the button
        # down with it — degrade to the browser and say why.
        payload["mode"] = "browser"
        payload["degraded"] = str(exc)
        return payload
    payload["audio_b64"] = result.get("audio_b64")
    payload["sample_rate"] = result.get("sample_rate")
    return payload


def describe() -> dict:
    """Voice capability for the UI: which backend is live, and why."""
    configured = (config.VOICE_BACKEND or "auto").strip().lower()
    return {
        "configured": configured,
        "effective": resolve_backend(),
        "broker_media": _broker_media_ready(),
        "speaker": config.VOICE_SPEAKER,
        "note": (
            "Broker voice is Kokoro-82M via tts_light — no GPU gate, no eviction, so the answer "
            "model and embedder stay resident. Browser fallback uses the Web Speech API."
        ),
    }
