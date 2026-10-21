"""The voice seam.

``voice.py`` exists so that speech is a config choice rather than a dependency: the broker's
Kokoro path when the media worker is up, the browser's own synthesizer when it is not, and
never a failed answer either way. Two behaviours carry that promise and are what this file
guards — ``speak()`` degrading to ``browser`` when synthesis raises instead of taking the
answer down with it, and ``speakable()`` stripping the markdown a synthesizer would otherwise
read out as "asterisk asterisk".
"""
from __future__ import annotations

import pytest

from smb_partner import broker, config, voice


# --- backend resolution -------------------------------------------------------------------

def test_auto_uses_the_broker_when_the_media_worker_is_up(fake_broker):
    fake_broker.media = True
    assert voice.resolve_backend("auto") == "broker"


def test_auto_falls_back_to_the_browser_when_it_is_not(fake_broker):
    """Zero GPU, zero latency, works on a phone — and it is what actually carries the mobile
    experience today."""
    fake_broker.media = False
    assert voice.resolve_backend("auto") == "browser"


def test_an_explicit_request_wins_over_the_configured_backend(fake_broker, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "broker")
    assert voice.resolve_backend("browser") == "browser"
    assert voice.resolve_backend("off") == "off"


def test_the_configured_backend_is_used_when_nothing_is_requested(fake_broker, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "off")
    assert voice.resolve_backend() == "off"
    assert voice.resolve_backend(None) == "off"


def test_an_unrecognised_backend_falls_back_to_probing(fake_broker):
    """A client sending nonsense must not be able to pick a backend that does not exist."""
    fake_broker.media = False
    assert voice.resolve_backend("kokoro-please") == "browser"
    assert voice.resolve_backend("") == "browser"


def test_the_media_probe_is_cached(fake_broker, monkeypatch):
    """The flag changes only when the broker restarts, and probing costs a round-trip on a
    path that has to feel instant."""
    calls = []
    real_status = fake_broker.status
    monkeypatch.setattr(broker, "status", lambda: calls.append(1) or real_status())
    voice.resolve_backend("auto")
    voice.resolve_backend("auto")
    voice.describe()
    assert len(calls) == 1


def test_a_broker_that_is_down_probes_as_no_media(monkeypatch, fake_broker):
    def down():
        raise broker.BrokerError("connection refused")

    monkeypatch.setattr(broker, "status", down)
    assert voice.resolve_backend("auto") == "browser"
    # Asserted through describe(), which is what the UI actually reads, rather than through
    # the removed can_transcribe() alias. That was a one-line delegate to
    # _broker_media_ready() with no caller outside this test, so the assertion was checking
    # a path production never took. Same property, live route.
    assert voice.describe()["stt"] == "browser"


# --- text flattening ------------------------------------------------------------------------

@pytest.mark.parametrize("markdown,expected", [
    ("**Business Premium** is the fit", "Business Premium is the fit"),
    ("*emphasis* matters", "emphasis matters"),
    ("Use `Partner Center` today", "Use Partner Center today"),
    ("## Your next move", "Your next move"),
    ("- first point", "first point"),
    ("* bullet\n* another", "bullet\nanother"),
])
def test_speakable_removes_markdown_scaffolding(markdown, expected):
    assert voice.speakable(markdown) == expected


def test_speakable_removes_inline_citations():
    """Nobody wants to hear "bracket one"."""
    assert "[1]" not in voice.speakable("Business Premium fits [1] and scales [12].")
    assert "[12]" not in voice.speakable("Business Premium fits [1] and scales [12].")


def test_speakable_drops_code_fences_entirely():
    spoken = voice.speakable("Run this:\n\n```\nGet-MsolUser\n```\n\nThen check the portal.")
    assert "Get-MsolUser" not in spoken
    assert "Then check the portal." in spoken


def test_speakable_tolerates_empty_input():
    assert voice.speakable("") == ""
    assert voice.speakable(None) == ""


# --- speak ------------------------------------------------------------------------------------

def test_broker_mode_returns_audio_alongside_the_text(fake_broker):
    payload = voice.speak("**Lead** with frontline [1].", backend="broker")
    assert payload["mode"] == "broker"
    assert payload["audio_b64"] == "QUJD"
    assert payload["sample_rate"] == 24000
    assert "**" not in payload["text"]


def test_browser_mode_carries_text_only_so_the_gpu_stays_free(fake_broker):
    payload = voice.speak("Lead with frontline.", backend="browser")
    assert payload == {"mode": "browser", "text": "Lead with frontline.",
                       "lang": config.VOICE_LANG}
    assert fake_broker.spoken == []


def test_off_mode_synthesizes_nothing(fake_broker):
    payload = voice.speak("Lead with frontline.", backend="off")
    assert payload["mode"] == "off"
    assert "audio_b64" not in payload
    assert fake_broker.spoken == []


def test_an_empty_answer_is_not_sent_for_synthesis(fake_broker):
    payload = voice.speak("   ", backend="broker")
    assert payload["text"] == ""
    assert "audio_b64" not in payload
    assert fake_broker.spoken == []


def test_synthesis_failure_degrades_to_the_browser_and_says_why(fake_broker):
    """A media worker that is configured but not actually runnable must not take the answer
    down with it — this is the single most important behaviour in the module."""
    fake_broker.tts_error = broker.BrokerError("kokoro-onnx not installed")
    payload = voice.speak("Lead with frontline.", backend="broker")
    assert payload["mode"] == "browser"
    assert "kokoro-onnx not installed" in payload["degraded"]
    assert payload["text"] == "Lead with frontline."
    assert "audio_b64" not in payload


@pytest.mark.parametrize("lang,code", [("en", "a"), ("en-GB", "b"), ("fr", "f"),
                                       ("ja", "j"), ("kl", "a")])
def test_the_language_tag_maps_to_a_kokoro_code(fake_broker, lang, code):
    """Unrecognised tags fall back to English rather than failing the utterance."""
    voice.speak("Lead with frontline.", backend="broker", lang=lang)
    assert fake_broker.spoken[-1]["lang_code"] == code


def test_an_unset_speaker_means_the_default_voice(fake_broker, monkeypatch):
    monkeypatch.setattr(config, "VOICE_SPEAKER", "")
    voice.speak("Lead with frontline.", backend="broker")
    assert fake_broker.spoken[-1]["voice"] is None

    monkeypatch.setattr(config, "VOICE_SPEAKER", "af_heart")
    voice.speak("Lead with frontline.", backend="broker")
    assert fake_broker.spoken[-1]["voice"] == "af_heart"


# --- transcribe --------------------------------------------------------------------------------

def test_transcribe_normalises_the_result(fake_broker):
    assert voice.transcribe("QUJD", suffix=".webm", language="en") == {
        "text": "how does the copilot trial work", "language": "en", "duration": 2.5}
    assert fake_broker.transcribed == ["QUJD"]


def test_transcribe_raises_voice_unavailable_rather_than_failing_silently(fake_broker):
    """The caller needs to be able to say so — a silent empty transcript reads to a user as
    "the microphone did not work", which sends them to the wrong problem."""
    fake_broker.transcribe_error = broker.BrokerError("media worker disabled")
    with pytest.raises(voice.VoiceUnavailable, match="speech-to-text unavailable"):
        voice.transcribe("QUJD")


def test_a_missing_field_in_the_broker_reply_does_not_crash(monkeypatch, fake_broker):
    monkeypatch.setattr(broker, "transcribe",
                        lambda audio_b64, **kw: {})
    assert voice.transcribe("QUJD") == {"text": "", "language": "", "duration": 0.0}


# --- describe ------------------------------------------------------------------------------------

def test_describe_reports_configured_and_effective_separately(fake_broker, monkeypatch):
    """The UI renders from this rather than assuming, so "auto" collapsing to "browser" has to
    be visible as a fallback rather than looking like the configuration."""
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    fake_broker.media = False
    described = voice.describe()
    assert described["configured"] == "auto"
    assert described["effective"] == "browser"
    assert described["broker_media"] is False
    assert described["stt"] == "browser"


def test_describe_reports_server_side_stt_when_the_media_worker_is_up(fake_broker):
    fake_broker.media = True
    described = voice.describe()
    assert described["broker_media"] is True
    assert described["stt"] == "broker"
    assert described["effective"] == "broker"


def test_stt_follows_the_media_worker_not_the_tts_choice(fake_broker, monkeypatch):
    """Recording is a separate decision from synthesis: a partner who turned voice output off
    can still dictate a question, and Web Speech could not honour their microphone choice."""
    monkeypatch.setattr(config, "VOICE_BACKEND", "off")
    fake_broker.media = True
    described = voice.describe()
    assert described["effective"] == "off"
    assert described["stt"] == "broker"
