"""BrokerClient's voice calls — the payload the gateway actually sends.

The gateway proxies /api/platform/{tts_light,transcribe} straight through this client, so
what it puts on the wire IS the contract. Optional fields must be OMITTED rather than sent as
null: the broker applies its own defaults (the platform voice, the whisper model), and an
explicit null would override them with None and fail inside the worker.

No network — the request method is stubbed, which is the seam worth testing.
"""
import asyncio

import pytest

from platform_core.broker_client import BrokerClient


@pytest.fixture
def client(monkeypatch):
    """A client whose _request records the call instead of making it."""
    c = BrokerClient.__new__(BrokerClient)
    seen: dict = {}

    async def fake_request(method, path, **kwargs):
        seen.update(method=method, path=path, json=kwargs.get("json"))
        return {"ok": True}

    monkeypatch.setattr(c, "_request", fake_request, raising=False)
    c.seen = seen  # type: ignore[attr-defined]
    return c


def test_tts_light_sends_only_the_text_by_default(client):
    asyncio.run(client.tts_light("hello"))
    assert client.seen["method"] == "POST"
    assert client.seen["path"] == "/v1/tts_light"
    # voice / lang_code / speed omitted => the broker applies the platform default
    # (BROKER_KOKORO_VOICE, af_heart). Sending nulls here would defeat that.
    assert client.seen["json"] == {"text": "hello"}


def test_tts_light_passes_the_knobs_when_given(client):
    asyncio.run(client.tts_light("hola", voice="ef_dora", lang_code="e", speed=1.1))
    assert client.seen["json"] == {
        "text": "hola", "voice": "ef_dora", "lang_code": "e", "speed": 1.1,
    }


def test_tts_light_keeps_an_explicit_zero_speed_distinguishable_from_unset(client):
    """`if speed is not None` rather than a truthiness check — 0.0 is a value, not absence."""
    asyncio.run(client.tts_light("hi", speed=0.0))
    assert client.seen["json"]["speed"] == 0.0


def test_transcribe_sends_only_the_audio_by_default(client):
    asyncio.run(client.transcribe("QUJD"))
    assert client.seen["method"] == "POST"
    assert client.seen["path"] == "/v1/transcribe"
    # No language => Whisper auto-detects, which is what makes bilingual dictation work
    # without the user telling it which language they are about to speak.
    assert client.seen["json"] == {"audio_b64": "QUJD"}


def test_transcribe_passes_the_container_hint_and_language(client):
    asyncio.run(client.transcribe("QUJD", suffix=".ogg", language="es"))
    assert client.seen["json"] == {
        "audio_b64": "QUJD", "suffix": ".ogg", "language": "es",
    }
