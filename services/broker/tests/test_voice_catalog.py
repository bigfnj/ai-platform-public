"""The voice catalog must report availability, not hide it.

Filtering unrunnable voices out is what created the drift this replaces: a registered voice
with no configured engine simply vanished, so the rail kept a second hand-maintained list
purely to have something that mentioned it — and that list then went stale in both directions
(it showed the voice that does NOT work and omitted one that does).

The rail is containerized and cannot read the registry itself (host path, half gitignored), so
this endpoint is the only thing that can tell the whole truth. These tests pin that it does.
"""
import json
from types import SimpleNamespace

import pytest

from app.broker import Broker

REGISTRY = {
    "project_root": r"D:\fake\native",
    "voices": [
        {"voice_id": "trained", "display_name": "Trained", "engine": "chatterbox",
         "kind": "trained-native", "language": "en", "source": "local",
         "model": {"adapter": "a"}, "reference_wav": "r.wav"},
        {"voice_id": "converted", "display_name": "Converted", "engine": "rvc",
         "kind": "conversion", "language": "en", "source": "hf",
         "model": {"pth": "p", "index": "i"}, "params": {"pitch": 0}},
        {"voice_id": "unwired", "display_name": "Unwired", "engine": "piper",
         "kind": "fixed-tts", "language": "en", "source": "hf",
         "model": {"onnx": "o"}},
    ],
}


@pytest.fixture
def broker(tmp_path, monkeypatch):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    b = Broker.__new__(Broker)          # no I/O; only catalog projection is exercised
    # voice_registry_path(), not a bare voice_registry string: the engines root is deployment
    # configuration now (BROKER_VOICE_ENGINES_DIR) so the broker stops naming a rail, and the
    # registry is resolved from it. None is a legitimate return — no root configured — which
    # the last test in this file exercises.
    b.settings = SimpleNamespace(
        voice_registry_path=lambda: reg,
        voice_engines=lambda: {"chatterbox": {}, "rvc": {}},   # piper deliberately absent
        voice_enabled=True,
    )
    return b


def test_returns_every_registered_voice_not_just_the_runnable_ones(broker):
    ids = [v["voice_id"] for v in broker.voice_catalog()["voices"]]
    assert ids == ["trained", "converted", "unwired"], (
        "a registered voice must never silently vanish — that is what made a second, "
        "hand-maintained voice list seem necessary in the first place"
    )


def test_runnable_reflects_whether_the_engine_is_configured(broker):
    got = {v["voice_id"]: v["runnable"] for v in broker.voice_catalog()["voices"]}
    assert got == {"trained": True, "converted": True, "unwired": False}


def test_unavailable_voices_carry_a_reason_naming_the_engine(broker):
    by_id = {v["voice_id"]: v for v in broker.voice_catalog()["voices"]}
    assert by_id["unwired"]["unavailable_reason"] == (
        "engine 'piper' is not installed on this broker"
    )
    # A runnable voice must not carry a reason — the UI keys off None to decide whether to
    # grey the row, so an empty string here would grey every voice.
    assert by_id["trained"]["unavailable_reason"] is None


def test_runtime_asset_fields_stay_out_of_the_catalog(broker):
    """model / params / reference_wav are the broker's business. They are absolute-ish paths
    into a host tree the rail cannot see, so shipping them to a browser is noise at best."""
    for v in broker.voice_catalog()["voices"]:
        assert not {"model", "params", "reference_wav", "base_voice"} & set(v)


def test_an_empty_engine_set_reports_everything_unavailable_rather_than_an_empty_list(broker):
    """A broker with no voice engines configured should still describe the catalog. Returning
    [] would render as 'no voices exist', which is a different and wrong statement."""
    broker.settings.voice_engines = lambda: {}
    voices = broker.voice_catalog()["voices"]
    assert len(voices) == 3
    assert not any(v["runnable"] for v in voices)


def test_no_engines_root_configured_yields_an_empty_catalog(monkeypatch):
    """A platform with the ai-voice rail removed must report no voices, not crash.

    The broker used to resolve the ai-voice rail's native/ directly, which made one rail
    load-bearing for a platform service. The location is deployment configuration now
    (BROKER_VOICE_ENGINES_DIR), and unset has to be a first-class state: load_registry(None)
    returns an empty catalog rather than raising inside a synth job.
    """
    from app import voice
    assert voice.load_registry(None) == {"voices": []}


def test_unset_engines_dir_resolves_to_no_root_and_no_registry(monkeypatch):
    """The default ships with nothing wired, so the broker names no rail out of the box."""
    monkeypatch.delenv("BROKER_VOICE_ENGINES_DIR", raising=False)
    monkeypatch.delenv("BROKER_VOICE_REGISTRY", raising=False)
    from app.config import BrokerSettings
    s = BrokerSettings()
    assert s.voice_engines_root() is None
    assert s.voice_registry_path() is None
    assert s.voice_engines() == {}


# --- a configured registry that cannot be read ------------------------------------------
# It used to escape as a bare FileNotFoundError / JSONDecodeError, which this read view turned
# into an unexplained 500.

def _broken_registry_client(tmp_path, monkeypatch, content):
    from fastapi.testclient import TestClient

    from app.main import app
    reg = tmp_path / "registry.json"
    if content is not None:
        reg.write_text(content, encoding="utf-8")
    b = Broker.__new__(Broker)
    b.settings = SimpleNamespace(voice_registry_path=lambda: reg,
                                 voice_engines=lambda: {}, voice_enabled=True)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "settings", SimpleNamespace(
        auth_token="", tokens=lambda: [], tokens_configured=lambda: False), raising=False)
    return TestClient(app)


BROKEN = [None, "{not json", "[1, 2]", '{"voices": "x"}']


@pytest.mark.parametrize("content", BROKEN)
def test_an_unreadable_registry_is_a_503_naming_the_file(tmp_path, monkeypatch, content):
    r = _broken_registry_client(tmp_path, monkeypatch, content).get("/v1/voice/catalog")
    assert r.status_code == 503, r.text
    assert "registry.json" in r.json()["detail"]


@pytest.mark.parametrize("content", BROKEN)
def test_synthesize_answers_an_unreadable_registry_as_the_catalog_does(tmp_path, monkeypatch,
                                                                       content):
    """One status for one fault. Synthesize answered it with its 502 "voice synth failed", which
    says the engine ran and broke, while the catalog said 503 naming the file."""
    r = _broken_registry_client(tmp_path, monkeypatch, content).post(
        "/v1/voice/synthesize", json={"voice_id": "trained", "text": "hello"})
    assert r.status_code == 503, r.text
    assert "registry.json" in r.json()["detail"]


def test_a_broken_registry_is_not_mistaken_for_an_unknown_voice(tmp_path):
    """synthesize maps ValueError to 404 "no such voice". A broken registry must not be one."""
    from app import voice
    reg = tmp_path / "registry.json"
    reg.write_text("{not json", encoding="utf-8")
    with pytest.raises(voice.VoiceRegistryError) as exc:
        voice.load_registry(reg)
    assert not isinstance(exc.value, ValueError)
