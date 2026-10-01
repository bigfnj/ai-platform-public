"""Read aloud: the text cleanup, the backend seam, and the degradation rules.

voice.py is a seam rather than a dependency, and the reason is a VRAM argument rather than a
style preference: the broker's ``/v1/tts`` takes the full GPU gate and evicts every resident
heavy model per utterance, so pressing Read aloud on ``/v1/tts`` would evict this rail's answer
model every time. ``tts_light`` (Kokoro, ~350 MB) skips the gate, and the co-residency the whole
rail is designed around survives. The tests below pin the endpoint choice, the browser
fallback, and the cleanup that stops a synthesizer reading "bracket one" out loud.

``speakable()`` carries two fixes its docstring says were found by listening rather than by
reading regexes — list items needing a sentence boundary, and punctuation orphaned by citation
removal. Both are asserted here, because both are silent when they regress: the payload still
looks correct, it just sounds broken.
"""
from __future__ import annotations

import pytest

from gemini_cx import broker, config, voice


@pytest.fixture(autouse=True)
def _reset_probe(monkeypatch):
    """The media probe is cached in a module global for five minutes; a leak across tests
    would make the backend depend on execution order."""
    monkeypatch.setattr(voice, "_probe", None)
    monkeypatch.setattr(broker, "media_enabled",
                        lambda: pytest.fail("media probe should have been stubbed"))


@pytest.fixture()
def media(monkeypatch):
    """Stub the broker's media probe and count how often it is asked."""
    calls: list[int] = []

    def make(available: bool):
        def probe():
            calls.append(1)
            return available
        monkeypatch.setattr(broker, "media_enabled", probe)
        return calls
    return make


# --- speakable ------------------------------------------------------------------------------

def test_inline_citations_are_removed():
    """A synthesizer reads "[1]" as "bracket one". This rail's answers are dense with them."""
    assert voice.speakable("Pricing is unpublished [1] and unquotable [2, 3]") == \
        "Pricing is unpublished and unquotable"


def test_punctuation_orphaned_by_a_citation_is_pulled_back():
    """Text like "per session [1]." must not become "per session ." — the pause lands in
    the wrong place and the sentence reads as a stumble."""
    assert voice.speakable("It is metered per session [1].") == "It is metered per session."


def test_markdown_emphasis_and_code_are_flattened():
    assert voice.speakable("**GA** and *Preview* differ") == "GA and Preview differ"
    assert voice.speakable("Set `keep_alive` to 30m") == "Set keep_alive to 30m"


def test_fenced_code_blocks_are_dropped():
    spoken = voice.speakable("Before\n\n```\nPOST /v1/tts_light\n```\n\nAfter")
    assert "tts_light" not in spoken
    assert spoken.startswith("Before")
    assert spoken.endswith("After")


def test_list_items_get_a_sentence_boundary():
    """Deleting the marker and joining turns two bullets into one run-on clause."""
    assert voice.speakable("- not seat-priced\n- three component meters") == \
        "not seat-priced. three component meters."


def test_numbered_items_are_treated_as_list_items_too():
    assert voice.speakable("1. Scope it\n2) Test it") == "Scope it. Test it."


def test_a_heading_does_not_run_into_the_paragraph_beneath_it():
    assert voice.speakable("## Pricing\nIt is unpublished.") == "Pricing. It is unpublished."


def test_an_item_that_already_ends_in_punctuation_is_left_alone():
    assert voice.speakable("- GA?\n- Preview!") == "GA? Preview!"


def test_plain_prose_lines_are_joined_without_inventing_sentences():
    """Markdown wraps paragraphs across lines; adding a stop per line would break them up."""
    assert voice.speakable("GECX supports forty plus\nlanguages for text") == \
        "GECX supports forty plus languages for text"


def test_blank_lines_and_runs_of_whitespace_collapse():
    assert voice.speakable("  A\n\n\n   B   C  ") == "A B C"


def test_speakable_of_nothing_is_an_empty_string():
    assert voice.speakable("") == ""
    assert voice.speakable(None) == ""


# --- backend resolution -----------------------------------------------------------------------

@pytest.mark.parametrize("choice", ["browser", "broker", "off"])
def test_an_explicit_request_wins_without_probing(choice, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    assert voice.resolve_backend(choice) == choice


def test_auto_collapses_to_broker_when_the_media_worker_is_up(media, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    media(True)
    assert voice.resolve_backend() == "broker"


def test_auto_falls_back_to_the_browser_when_it_is_not(media, monkeypatch):
    """The media venv is optional. Read aloud must still work, on the client's own voice."""
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    media(False)
    assert voice.resolve_backend() == "browser"


def test_an_unknown_backend_name_degrades_to_auto_rather_than_off(media, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    media(False)
    assert voice.resolve_backend("kokoro-please") == "browser"


def test_the_configured_backend_is_used_when_nothing_is_requested(monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "off")
    assert voice.resolve_backend() == "off"


def test_the_media_probe_is_cached(media, monkeypatch):
    """It costs an HTTP round-trip on a path that has to feel instant, and the broker's media
    flag only changes when the broker restarts."""
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    calls = media(True)
    for _ in range(5):
        voice.resolve_backend()
    assert len(calls) == 1


# --- speak ------------------------------------------------------------------------------------

def test_browser_mode_returns_clean_text_and_no_audio(monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "browser")
    payload = voice.speak("It is metered per session [1].")
    assert payload == {"mode": "browser", "text": "It is metered per session.",
                       "lang": config.VOICE_LANG}


def test_off_mode_synthesises_nothing(monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "off")
    assert voice.speak("anything")["mode"] == "off"


def test_broker_mode_goes_through_tts_light_with_the_configured_speaker(monkeypatch):
    """NOT /v1/tts: that path evicts every heavy model, which is the co-residency this rail
    is built on. broker.tts_light is the only TTS call this rail may make."""
    seen = {}

    def fake(text, *, voice=None, lang_code=None, speed=None, timeout=None):
        seen.update(text=text, voice=voice, lang_code=lang_code)
        return {"audio_b64": "AAA", "sample_rate": 24000}

    monkeypatch.setattr(config, "VOICE_BACKEND", "broker")
    monkeypatch.setattr(broker, "tts_light", fake)
    payload = voice.speak("Pricing is unpublished [1].")
    assert payload["mode"] == "broker"
    assert payload["audio_b64"] == "AAA"
    assert payload["sample_rate"] == 24000
    assert seen["text"] == "Pricing is unpublished."
    assert seen["voice"] == config.VOICE_SPEAKER


@pytest.mark.parametrize("lang,code", [("en", "a"), ("en-GB", "b"), ("fr", "f"),
                                       ("kl", "a")])
def test_the_language_tag_maps_to_kokoros_single_letter_code(lang, code, monkeypatch):
    seen = {}

    def fake(text, *, voice=None, lang_code=None, speed=None, timeout=None):
        seen["lang_code"] = lang_code
        return {"audio_b64": "AAA"}

    monkeypatch.setattr(config, "VOICE_BACKEND", "broker")
    monkeypatch.setattr(broker, "tts_light", fake)
    voice.speak("Pricing is unpublished.", lang=lang)
    assert seen["lang_code"] == code


def test_a_broker_failure_degrades_to_the_browser_and_says_why(monkeypatch):
    """A media worker that is configured but not runnable must not take the button down."""
    def boom(*_a, **_kw):
        raise broker.BrokerError("media worker not installed")

    monkeypatch.setattr(config, "VOICE_BACKEND", "broker")
    monkeypatch.setattr(broker, "tts_light", boom)
    payload = voice.speak("Pricing is unpublished.")
    assert payload["mode"] == "browser"
    assert "media worker not installed" in payload["degraded"]
    assert "audio_b64" not in payload


def test_text_that_cleans_away_to_nothing_never_reaches_the_synthesizer(monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "broker")
    monkeypatch.setattr(broker, "tts_light",
                        lambda *a, **k: pytest.fail("nothing to speak"))
    assert voice.speak("[1] [2]")["text"] == ""


# --- describe ---------------------------------------------------------------------------------

def test_describe_tells_the_ui_what_is_configured_and_what_is_live(media, monkeypatch):
    monkeypatch.setattr(config, "VOICE_BACKEND", "auto")
    media(False)
    got = voice.describe()
    assert got["configured"] == "auto"
    assert got["effective"] == "browser"
    assert got["broker_media"] is False
    assert got["speaker"] == config.VOICE_SPEAKER
    assert "tts_light" in got["note"]


def test_the_default_speaker_is_pinned_rather_than_left_to_kokoro(monkeypatch):
    """Set explicitly so the voice does not change under us if Kokoro's default moves."""
    assert config.VOICE_SPEAKER.startswith("af_")
