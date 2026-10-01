"""Audio and image endpoints on the /openai/v1 surface.

These differ in kind from chat and embeddings: those are forwarded to Ollama, which
owns their shapes, while these translate onto the broker's OWN Kokoro /
faster-whisper / SDXL workers. So what needs asserting is different too. Not
"did the body survive the hop" but:

  * the translation is right in both directions (OpenAI in, worker out)
  * a capability the workers cannot deliver is REFUSED, not approximated -- the
    failure this guards against is a wav returned as `audio/mpeg`, which no test
    catches by looking at a status code
  * the no-gate promise holds: read-aloud and dictation must not queue behind or
    evict a resident chat model, which is what makes them offerable at all

The workers are stubbed at the broker method, not at the subprocess: these tests
must never spawn a media venv or touch the card.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.config import BrokerSettings

# A 44-byte canonical RIFF header followed by four sample bytes, so the pcm path has
# something real to strip.
WAV = b"RIFF" + b"\x00" * 40 + b"\xde\xad\xbe\xef"
WAV_B64 = base64.b64encode(WAV).decode()
PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()


class _StubBroker:
    """Only the three media methods the compat surface calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.tts_result = {"audio_b64": WAV_B64, "sample_rate": 24000,
                           "voice": "af_heart", "lang": "a"}
        self.transcribe_result = {"text": "hello there", "language": "en",
                                  "duration": 1.25, "model": "small"}
        self.image_result = {"images": [PNG_B64], "errors": []}
        self.raises: Exception | None = None

    async def tts_light(self, text, *, voice=None, lang_code=None, speed=None):
        self.calls.append(("tts_light", {"text": text, "voice": voice,
                                         "lang_code": lang_code, "speed": speed}))
        if self.raises:
            raise self.raises
        return self.tts_result

    async def transcribe(self, audio_b64, *, suffix=None, language=None):
        self.calls.append(("transcribe", {"len": len(audio_b64), "suffix": suffix,
                                          "language": language}))
        if self.raises:
            raise self.raises
        return self.transcribe_result

    async def image(self, prompts, *, negative_prompt=None, steps=4, size=512, model="sdxl-turbo"):
        self.calls.append(("image", {"prompts": prompts, "size": size, "model": model,
                                     "negative_prompt": negative_prompt}))
        if self.raises:
            raise self.raises
        return self.image_result


@pytest.fixture
def client(tmp_path):
    from app.main import app

    disabled = tmp_path / "disabled.json"
    disabled.write_text("[]", encoding="utf-8")
    # tokens_file isolated too, or BrokerSettings falls back to the operator's real
    # services/broker/tokens.json and these tests 401 the moment a token exists on the box.
    app.state.settings = BrokerSettings(disabled_file=str(disabled), auth_token="",
                                        tokens_file=str(tmp_path / "tokens.json"))
    app.state.broker = _StubBroker()
    return TestClient(app)


def stub(client) -> _StubBroker:
    return client.app.state.broker


# --- /audio/speech --------------------------------------------------------

def test_speech_returns_raw_wav_bytes_not_json(client):
    """OpenAI's contract here is a body a client writes straight to a file."""
    r = client.post("/openai/v1/audio/speech", json={"model": "tts-1", "input": "hello"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == WAV


def test_speech_defaults_to_wav_when_format_omitted(client):
    """OpenAI's own default is mp3. Refusing an unset field would fail every
    default-constructed client call, so absent means wav -- deliberately."""
    r = client.post("/openai/v1/audio/speech", json={"input": "hello"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"


@pytest.mark.parametrize("fmt", ["mp3", "opus", "aac", "flac"])
def test_speech_refuses_formats_it_cannot_encode(client, fmt):
    """The bug this exists for: returning wav bytes labelled audio/mpeg. A strict
    client decodes by declared type and gets a corrupt file with no error anywhere."""
    r = client.post("/openai/v1/audio/speech", json={"input": "hi", "response_format": fmt})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"
    assert "wav" in r.json()["error"]["message"]


def test_pcm_strips_the_riff_header(client):
    """OpenAI's pcm is headerless samples. Passing the 44-byte header through as if
    it were sample data is an audible click on any device that plays it raw."""
    r = client.post("/openai/v1/audio/speech", json={"input": "hi", "response_format": "pcm"})
    assert r.status_code == 200
    assert r.content == b"\xde\xad\xbe\xef"
    assert r.headers["content-type"] == "audio/pcm"


def test_openai_voice_names_fall_back_to_the_configured_default(client):
    """A client hardcoding "alloy" should get speech, not a stack trace: the name is
    dropped so the operator's BROKER_KOKORO_VOICE applies."""
    client.post("/openai/v1/audio/speech", json={"input": "hi", "voice": "alloy"})
    assert stub(client).calls[-1][1]["voice"] is None


def test_kokoro_voice_ids_are_passed_through(client):
    """The other half: a real Kokoro id must survive, or Spanish read-aloud breaks."""
    client.post("/openai/v1/audio/speech", json={"input": "hola", "voice": "ef_dora"})
    assert stub(client).calls[-1][1]["voice"] == "ef_dora"


def test_speed_outside_kokoro_range_is_refused_not_clamped(client):
    """OpenAI allows 0.25-4.0, Kokoro 0.5-2.0. Clamping would return audio at a speed
    the caller did not ask for and cannot detect."""
    r = client.post("/openai/v1/audio/speech", json={"input": "hi", "speed": 3.0})
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "speed"


def test_speech_requires_input(client):
    r = client.post("/openai/v1/audio/speech", json={"model": "tts-1"})
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "input"


# --- /audio/transcriptions ------------------------------------------------

def _upload(client, name="clip.webm", data=b"\x00\x01\x02\x03", **form):
    return client.post("/openai/v1/audio/transcriptions",
                       files={"file": (name, io.BytesIO(data), "audio/webm")},
                       data={"model": "whisper-1", **form})


def test_transcription_returns_openai_json(client):
    r = _upload(client)
    assert r.status_code == 200
    assert r.json() == {"text": "hello there"}


def test_transcription_text_format_is_plain_text(client):
    r = _upload(client, response_format="text")
    assert r.status_code == 200
    assert r.text == "hello there"
    assert r.headers["content-type"].startswith("text/plain")


def test_verbose_json_carries_language_and_an_empty_segments_key(client):
    """`segments` is present and empty rather than omitted: it is part of the shape,
    and a client that iterates it should get nothing, not a KeyError."""
    body = _upload(client, response_format="verbose_json").json()
    assert body["language"] == "en" and body["duration"] == 1.25
    assert body["segments"] == []


@pytest.mark.parametrize("fmt", ["srt", "vtt"])
def test_subtitle_formats_are_refused(client, fmt):
    """The worker returns no per-segment timings, so these cannot be synthesised
    honestly -- a subtitle file with one cue spanning the whole clip is worse than none."""
    r = _upload(client, response_format=fmt)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_suffix_is_taken_from_the_filename(client):
    _upload(client, name="recording.ogg")
    assert stub(client).calls[-1][1]["suffix"] == ".ogg"


def test_unsafe_filename_yields_no_suffix_rather_than_a_repaired_one(client):
    """This value reaches a filename in a LocalSystem subprocess, and a '/../..' once
    escaped the temp directory entirely. Dropped, not sanitised: a repaired suffix is
    a guess about the container format."""
    _upload(client, name="evil/../../etc/passwd")
    assert stub(client).calls[-1][1]["suffix"] is None


def test_empty_upload_is_refused(client):
    r = _upload(client, data=b"")
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "file"


def test_language_is_forwarded(client):
    _upload(client, language="es")
    assert stub(client).calls[-1][1]["language"] == "es"


# --- /images/generations --------------------------------------------------

def test_image_returns_openai_envelope(client):
    r = client.post("/openai/v1/images/generations",
                    json={"prompt": "a cat", "size": "512x512"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["created"], int)
    assert body["data"][0]["b64_json"] == PNG_B64


def test_url_response_format_is_refused(client):
    """OpenAI's DEFAULT is url and this server hosts nothing. A data: URI in a field a
    client hands to an <img src> or a downloader fails later and somewhere else."""
    r = client.post("/openai/v1/images/generations",
                    json={"prompt": "a cat", "response_format": "url"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_n_repeats_the_prompt(client):
    client.post("/openai/v1/images/generations", json={"prompt": "a cat", "n": 3})
    assert stub(client).calls[-1][1]["prompts"] == ["a cat"] * 3


def test_n_is_capped(client):
    """Each image evicts every heavy model and holds the card. Ten is ten serialised
    generations behind one HTTP call, long past any client timeout."""
    r = client.post("/openai/v1/images/generations", json={"prompt": "a cat", "n": 10})
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "n"


def test_non_square_size_is_refused(client):
    r = client.post("/openai/v1/images/generations",
                    json={"prompt": "a cat", "size": "1024x512"})
    assert r.status_code == 400
    assert "square" in r.json()["error"]["message"]


def test_size_is_parsed_to_the_broker_integer(client):
    client.post("/openai/v1/images/generations", json={"prompt": "a cat", "size": "1024x1024"})
    assert stub(client).calls[-1][1]["size"] == 1024


def test_unknown_openai_model_falls_back_rather_than_failing(client):
    """"dall-e-3" means nothing here, but failing on it breaks every stock client."""
    client.post("/openai/v1/images/generations", json={"prompt": "a cat", "model": "dall-e-3"})
    assert stub(client).calls[-1][1]["model"] == "sdxl-turbo"


def test_media_role_is_honoured(client):
    client.post("/openai/v1/images/generations", json={"prompt": "a cat", "model": "@recipe-icon"})
    assert stub(client).calls[-1][1]["model"] == "@recipe-icon"


def test_all_images_failing_is_a_502_not_an_empty_success(client):
    """An empty data array reads as success to a client and produces no file and no
    error. The worker's own error text is what makes it diagnosable."""
    stub(client).image_result = {"images": [None], "errors": ["CUDA out of memory"]}
    r = client.post("/openai/v1/images/generations", json={"prompt": "a cat"})
    assert r.status_code == 502
    assert "CUDA out of memory" in r.json()["error"]["message"]


# --- failures -------------------------------------------------------------

def test_worker_failure_is_an_openai_shaped_502(client):
    stub(client).raises = RuntimeError("media is disabled")
    r = client.post("/openai/v1/audio/speech", json={"input": "hi"})
    assert r.status_code == 502
    assert "media is disabled" in r.json()["error"]["message"]
