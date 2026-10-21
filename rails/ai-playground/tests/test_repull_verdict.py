"""The weekly "Embedding model update" says when it failed.

``POST /api/bench/refresh/repull-all`` is what the platform scheduler fires every week for this
rail. It answered 200 whatever happened, and the scheduler records any 2xx as ok, so with Ollama
unreachable from the container (every pull failing) Admin > Schedule read "ok (200) count=3"
(reproduced by passing the response through the scheduler's own summariser).

Now any failed pull is a 502 carrying the counts and the first reason, which the scheduler
keeps as the run's error. Hermetic: pull.pull is a stub; nothing dials Ollama.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ai_playground import broker, config
from ai_playground.bench import pull, registry

ROUTE = "/api/bench/refresh/repull-all"
SYSTEM = {"X-Platform-User": "system", "X-Platform-Admin": "1"}
BROKER_SEEDS = [m["broker_model"] for m in registry.SEED_MODELS if m["provider"] == "broker"]


def _unreachable(model):
    raise RuntimeError(f"could not pull '{model}' via http://host.docker.internal:11434 "
                       f"(URLError). Run `ollama pull {model}` on the broker box instead.")


@pytest.fixture
def client(api, monkeypatch):
    """The verdict tests run OPTED IN to re-pulling the platform embedder, so they see every
    seed; the default skip has its own tests below."""
    monkeypatch.setattr(config, "REPULL_EMBEDDER", True)
    return TestClient(api)


@pytest.fixture
def default_client(api, monkeypatch):
    """The shipped default: not opted in, and @embed resolving to bge-m3 (tagged, as Ollama
    reports it, against the registry's untagged spec)."""
    monkeypatch.setattr(config, "REPULL_EMBEDDER", False)
    monkeypatch.setattr(config, "EMBED_MODEL", "@embed")
    monkeypatch.setattr(broker, "resolved_model",
                        lambda name: "bge-m3:latest" if name == "@embed" else name)
    pulled: list[str] = []

    def ok(model):
        pulled.append(model)
        return {"ok": True, "model": model, "status": "success"}
    monkeypatch.setattr(pull, "pull", ok)
    return TestClient(api), pulled


def test_every_pull_failing_is_a_failed_run(client, monkeypatch):
    """The audit's case. A 200 here is a green Schedule row over an update that never ran."""
    monkeypatch.setattr(pull, "pull", _unreachable)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["count"] == detail["failed"] == len(BROKER_SEEDS) >= 1
    assert "could not pull" in detail["first_error"]


def test_one_failed_pull_fails_the_run_too(client, monkeypatch):
    """Partial is not ok: the task exists to keep EVERY registered embedder current."""
    def one_bad(model):
        if model == BROKER_SEEDS[0]:
            return _unreachable(model)
        return {"ok": True, "model": model, "status": "success"}
    monkeypatch.setattr(pull, "pull", one_bad)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["failed"] == 1


def test_a_clean_run_is_still_ok_and_pulls_every_model(client, monkeypatch):
    """Guards the over-correction, and that one bad pull still does not stop the rest."""
    pulled = []

    def ok(model):
        pulled.append(model)
        return {"ok": True, "model": model, "status": "success"}
    monkeypatch.setattr(pull, "pull", ok)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed"] == 0 and body["count"] == len(BROKER_SEEDS)
    assert sorted(pulled) == sorted(BROKER_SEEDS)


def test_a_failure_does_not_stop_the_remaining_pulls(client, monkeypatch):
    pulled = []

    def first_fails(model):
        pulled.append(model)
        if len(pulled) == 1:
            return _unreachable(model)
        return {"ok": True}
    monkeypatch.setattr(pull, "pull", first_fails)
    client.post(ROUTE, headers=SYSTEM)
    assert len(pulled) == len(BROKER_SEEDS)


# --- the platform embedder is not re-pulled by default ------------------------------------
#
# @embed (bge-m3 today) is the retrieval embedder EVERY rail's stored vectors were made with.
# A new upstream digest for that tag, pulled by this weekly task, changes the weights under all
# of them with nothing re-embedding anything, so retrieval degrades platform-wide and silently.
# So it is skipped unless AI_PLAYGROUND_REPULL_EMBEDDER opts in (a default set 2026-10-08, and
# reversible), and the run record says so.

def test_the_platform_embedder_is_not_repulled_by_default(default_client):
    client, pulled = default_client
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 200, resp.text
    assert "bge-m3" not in pulled, "the platform embedder was re-pulled without an opt-in"
    others = [m for m in BROKER_SEEDS if m != "bge-m3"]
    assert sorted(pulled) == sorted(others) and others, "the other embedders must still update"
    body = resp.json()
    assert body["count"] == len(others)
    # Scalars, so the scheduler's k=v summary carries them into the Schedule tab's run record.
    assert body["skipped"] == "bge-m3"
    assert "AI_PLAYGROUND_REPULL_EMBEDDER=1" in body["skipped_why"]


def test_opting_in_repulls_the_embedder_too(default_client, monkeypatch):
    client, pulled = default_client
    monkeypatch.setattr(config, "REPULL_EMBEDDER", True)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 200, resp.text
    assert sorted(pulled) == sorted(BROKER_SEEDS)
    assert "skipped" not in resp.json()


def test_an_unresolvable_embedder_refuses_the_run_and_pulls_nothing(default_client, monkeypatch):
    """Broker down or role unknown: resolved_model hands back the role literal. A run that
    cannot tell which model is the platform's embedder must not risk re-pulling it."""
    client, pulled = default_client
    monkeypatch.setattr(broker, "resolved_model", lambda name: name)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 502, resp.text
    assert "could not tell which model @embed resolves to" in resp.json()["detail"]["error"]
    assert pulled == []


def test_the_skip_survives_into_a_failed_runs_record(default_client, monkeypatch):
    client, _ = default_client
    monkeypatch.setattr(pull, "pull", _unreachable)
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 502
    assert resp.json()["detail"]["skipped"] == "bge-m3"


def test_the_setting_reads_as_off_unless_set(monkeypatch):
    import importlib
    monkeypatch.delenv("AI_PLAYGROUND_REPULL_EMBEDDER", raising=False)
    fresh = importlib.reload(config)
    try:
        assert fresh.REPULL_EMBEDDER is False
        monkeypatch.setenv("AI_PLAYGROUND_REPULL_EMBEDDER", "1")
        assert importlib.reload(config).REPULL_EMBEDDER is True
    finally:
        monkeypatch.delenv("AI_PLAYGROUND_REPULL_EMBEDDER", raising=False)
        importlib.reload(config)
