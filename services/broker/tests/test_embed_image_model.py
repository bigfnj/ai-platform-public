"""/v1/embed_image loads only an allowed image embedder.

`model` reached AutoModel.from_pretrained in the media worker unchecked, so any holder of a
broker token chose what the worker downloaded into the Hugging Face cache: an unbounded fetch
onto the system drive. Now a named model must be in BROKER_IMAGE_EMBED_MODELS (default: the one
SigLIP encoder bouquet's reference index was built with), and an unnamed one is that default.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import broker as broker_mod
from app.broker import Broker
from app.config import BrokerSettings

DEFAULT = "google/siglip2-base-patch16-384"


@pytest.fixture
def jobs(monkeypatch):
    sent: list[dict] = []

    async def fake_job(*, python_exe, spec, timeout):
        sent.append(spec)
        return {"embeddings": [[0.1]], "dim": 1, "model": spec.get("model")}

    monkeypatch.setattr(broker_mod.media, "run_media_job", fake_job)
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch, jobs):
    from app.main import app

    def build(**kw) -> TestClient:
        s = BrokerSettings(tokens_file=str(tmp_path / "tokens.json"), auth_token="",
                           media_enabled=True, **kw)
        monkeypatch.setattr(app.state, "settings", s, raising=False)
        monkeypatch.setattr(app.state, "broker", Broker(s), raising=False)
        return TestClient(app)

    return build


def test_an_unlisted_model_is_refused_before_the_worker(client, jobs):
    r = client().post("/v1/embed_image", json={"images": ["QUJD"], "model": "someone/huge-model"})
    assert r.status_code == 400, r.text
    assert "someone/huge-model" in r.json()["detail"]
    assert jobs == [], "the worker was asked to load an unlisted model"


def test_no_model_means_the_listed_default(client, jobs):
    r = client().post("/v1/embed_image", json={"images": ["QUJD"]})
    assert r.status_code == 200, r.text
    assert jobs[-1]["model"] == DEFAULT


def test_a_listed_model_is_passed_through(client, jobs):
    c = client(image_embed_models=f"{DEFAULT},google/siglip2-large-patch16-384")
    r = c.post("/v1/embed_image",
               json={"images": ["QUJD"], "model": "google/siglip2-large-patch16-384"})
    assert r.status_code == 200, r.text
    assert jobs[-1]["model"] == "google/siglip2-large-patch16-384"
