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


async def test_evict_keeps_an_untagged_role_s_model_that_ollama_lists_as_latest():
    """A role pinned untagged resolves to `qwen3` while Ollama reports it resident as
    `qwen3:latest`. A plain != unloaded the model the call was about to serve, every call."""
    b = make_broker(FakeOllama(["qwen3:latest", "mistral-small3.1:24b"]))
    evicted = await b._evict_other_heavy(keep="qwen3")
    assert evicted == ["mistral-small3.1:24b"]
    await b.aclose()


async def test_evict_still_tells_two_tags_of_one_family_apart():
    """Only the implicit :latest is dropped. qwen3:8b is not the qwen3:4b being kept."""
    b = make_broker(FakeOllama(["qwen3:8b"]))
    assert await b._evict_other_heavy(keep="qwen3:4b") == ["qwen3:8b"]
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


@pytest.mark.parametrize("model", ["@chat", "dall-e-3", "qwen3.6:27b"])
async def test_an_image_call_for_a_model_that_is_not_an_image_backend_is_refused(
        monkeypatch, model):
    """`/v1/image` resolved any model, and the worker draws SDXL for every name that is not
    FLUX, so `@chat` rendered an image the queue labelled with the chat model (audit
    2026-10-14). Refused before the gate: nothing evicted, no worker started."""
    from app.broker import NotAnImageModel

    b = make_broker(FakeOllama(["llama3.1:8b"]), media_enabled=True)
    started: list[dict] = []

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        started.append(spec)
        return {"images": ["b64data"], "errors": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    with pytest.raises(NotAnImageModel) as exc:
        await b.image(["a red circle"], model=model)
    assert model in str(exc.value) and "sdxl-turbo" in str(exc.value)
    assert started == [] and b.ollama.stopped == [], "an image job ran for a model that draws none"
    await b.aclose()


async def test_an_image_role_and_both_backends_still_render(monkeypatch):
    b = make_broker(FakeOllama([]), media_enabled=True)
    started: list[str] = []

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        started.append(spec["model"])
        return {"images": ["b64data"], "errors": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    for model in ("sdxl-turbo", "flux-schnell", "@recipe-icon"):
        await b.image(["a red circle"], model=model)
    assert started == ["sdxl-turbo", "flux-schnell", "flux-schnell"]
    await b.aclose()


@pytest.mark.parametrize("spelled,backend", [("SDXL-Turbo", "sdxl-turbo"),
                                             ("FLUX-Schnell", "flux-schnell"),
                                             ("Flux-schnell", "flux-schnell")])
async def test_an_image_backend_named_in_any_case_renders_under_its_own_name(
        monkeypatch, spelled, backend):
    """The worker lowercases the name, so `FLUX-Schnell` always drew FLUX, until the refusal of
    a model that draws no images compared exactly and made it a 400 (2026-10-14). Matched
    without case, and handed on as the backend spells itself, so the worker, the job queue and
    the answer name it one way."""
    b = make_broker(FakeOllama([]), media_enabled=True)
    started: list[str] = []

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        started.append(spec["model"])
        return {"images": ["b64data"], "errors": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    await b.image(["a red circle"], model=spelled)
    assert started == [backend]
    await b.aclose()


def test_the_image_route_answers_400_for_a_chat_role(tmp_path, monkeypatch):
    """The backlog's check, through /v1/image with the media job stubbed."""
    from fastapi.testclient import TestClient

    from app.main import app

    started: list[dict] = []

    async def fake_run(*, python_exe: str, spec: dict, timeout: float) -> dict:
        started.append(spec)
        return {"images": ["b64data"], "errors": []}

    monkeypatch.setattr(media, "run_media_job", fake_run)
    b = make_broker(FakeOllama([]), media_enabled=True,
                    roles_file=str(tmp_path / "roles.json"),
                    disabled_file=str(tmp_path / "disabled.json"),
                    tokens_file=str(tmp_path / "tokens.json"), auth_token="")
    app.state.settings = b.settings
    app.state.broker = b
    r = TestClient(app).post("/v1/image", json={"prompts": ["a cat"], "model": "@chat"})
    assert r.status_code == 400, r.text
    assert "@chat" in r.json()["detail"]
    assert started == []


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
