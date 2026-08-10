"""Media path + eviction policy (no GPU / no real worker needed)."""

from __future__ import annotations

from typing import Any

import pytest

from app import media
from app.broker import Broker
from app.config import BrokerSettings


class FakeOllama:
    """Stand-in for OllamaClient: records stops and simulates eviction."""

    def __init__(self, loaded: list[str]):
        self._loaded = list(loaded)
        self.stopped: list[str] = []

    async def ps(self) -> list[dict[str, Any]]:
        return [{"name": n} for n in self._loaded]

    async def stop(self, model: str) -> None:
        self.stopped.append(model)
        self._loaded = [n for n in self._loaded if n != model]

    async def aclose(self) -> None:
        pass


def make_broker(ollama: FakeOllama, **overrides: Any) -> Broker:
    b = Broker(BrokerSettings(**overrides))
    b.ollama = ollama  # type: ignore[assignment]
    return b


async def test_evict_all_heavy_uses_stop_and_keeps_embedder():
    b = make_broker(FakeOllama(["llama3.1:8b", "bge-m3", "qwen2.5:32b-instruct-q3_K_M"]))
    evicted = await b._evict_other_heavy()  # keep=None -> evict all heavy
    assert set(evicted) == {"llama3.1:8b", "qwen2.5:32b-instruct-q3_K_M"}
    assert "bge-m3" not in b.ollama.stopped  # the embedder is allowed to stay
    await b.aclose()


async def test_evict_keeps_named_model():
    b = make_broker(FakeOllama(["llama3.1:8b", "mistral-small3.1:24b"]))
    evicted = await b._evict_other_heavy(keep="llama3.1:8b")
    assert evicted == ["mistral-small3.1:24b"]
    assert b.ollama.stopped == ["mistral-small3.1:24b"]
    await b.aclose()


async def test_media_disabled_raises():
    b = make_broker(FakeOllama([]), media_enabled=False)
    with pytest.raises(RuntimeError):
        await b.image(["a cat"])
    await b.aclose()


async def test_image_gates_evicts_and_returns(monkeypatch):
    b = make_broker(FakeOllama(["llama3.1:8b"]), media_enabled=True)
    captured: dict[str, Any] = {}

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        captured["spec"] = spec
        captured["python_exe"] = python_exe
        return {"images": ["b64data"], "errors": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    result = await b.image(["a red circle"], steps=3, size=256)

    assert result["images"] == ["b64data"]
    assert result["evicted"] == ["llama3.1:8b"]  # heavy model evicted before media ran
    assert captured["spec"]["op"] == "image"
    assert captured["spec"]["prompts"] == ["a red circle"]
    assert captured["spec"]["steps"] == 3 and captured["spec"]["size"] == 256
    await b.aclose()


async def test_tts_builds_spec(monkeypatch):
    b = make_broker(FakeOllama([]), media_enabled=True)
    captured: dict[str, Any] = {}

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        captured["spec"] = spec
        return {"audio_b64": "x", "sample_rate": 24000, "timings": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    segs = [{"lang": "en", "text": "hello", "type": None, "duration": None}]
    result = await b.tts(segs)

    assert result["sample_rate"] == 24000
    assert captured["spec"]["op"] == "tts"
    assert captured["spec"]["segments"] == segs
    assert "voices_dir" in captured["spec"]
    await b.aclose()


async def test_tts_batch_builds_spec(monkeypatch):
    b = make_broker(FakeOllama([]), media_enabled=True)
    captured: dict[str, Any] = {}

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        captured["spec"] = spec
        return {"audios": ["a", "b"], "sample_rate": 24000}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    items = [{"lang": "en", "text": "cat"}, {"lang": "es", "text": "gato"}]
    result = await b.tts_batch(items)

    assert result["audios"] == ["a", "b"]
    assert captured["spec"]["op"] == "tts_batch"
    assert captured["spec"]["items"] == items
    assert "voices_dir" in captured["spec"]
    await b.aclose()


async def test_run_media_job_missing_python_raises():
    with pytest.raises(media.MediaError):
        await media.run_media_job(
            python_exe=r"C:\does\not\exist\python.exe",
            spec={"op": "image", "prompts": ["x"]},
            timeout=5.0,
        )
