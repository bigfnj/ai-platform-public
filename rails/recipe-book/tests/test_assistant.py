"""The client does not pick the model.

``POST /api/assistant`` passed ``req.model or broker.ASSISTANT_MODEL`` straight into
``broker.chat``, so whatever name the caller sent is what the shared 4090 was asked to load —
the hole finance closed on 2026-08-06 (audit finding E3). Combined with the missing identity
gate on this same route, the caller did not even have to be signed in.

Offline: the broker is replaced by a recorder, so these assert which model the rail ASKED for.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import broker
from recipe_book.api.routers import assistant as assistant_router

USER = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}

INSTALLED = ["gemma4:26b", "mistral-small3.2:24b"]
ROLES = ["recipe", "recipe-vision", "chat"]


@pytest.fixture()
def client(monkeypatch):
    """Records the model each broker.chat call asked for. picker_models/roles answer as a
    reachable broker would; the chat itself never leaves the process."""
    asked: list[str] = []

    def fake_chat(model, messages, **kw):
        asked.append(model)
        return "## ok"

    monkeypatch.setattr(broker, "chat", fake_chat)
    monkeypatch.setattr(broker, "picker_models",
                        lambda: {"broker_up": True,
                                 "models": [{"name": n} for n in INSTALLED]})
    monkeypatch.setattr(broker, "roles", lambda: [{"role": r, "resolved": "gemma4:26b"}
                                                  for r in ROLES])
    monkeypatch.setattr(broker, "ASSISTANT_MODEL", "@recipe")
    app = FastAPI()
    app.include_router(assistant_router.router)
    c = TestClient(app)
    c.asked = asked  # type: ignore[attr-defined]
    return c


def _ask(client, **body):
    return client.post("/api/assistant", json={"mode": "ask", "prompt": "hi", **body},
                       headers=USER)


def test_uninstalled_model_is_refused(client):
    """The one that mattered: an arbitrary name used to be forwarded verbatim, so a caller
    chose what the shared GPU pulls into VRAM."""
    r = _ask(client, model="llama3.1:405b")
    assert r.status_code == 400
    assert "not installed" in r.json()["detail"]
    assert client.asked == []          # and it never reached the broker


def test_installed_model_is_honoured(client):
    assert _ask(client, model="mistral-small3.2:24b").status_code == 200
    assert client.asked == ["mistral-small3.2:24b"]


def test_role_reference_is_accepted(client):
    """finance validates a @role against the INSTALLED MODEL list, where a role can never
    appear, so its own default would 400 against a reachable broker. A role is validated
    against the role table instead."""
    assert _ask(client, model="@recipe-vision").status_code == 200
    assert client.asked == ["@recipe-vision"]


def test_unknown_role_is_refused(client):
    r = _ask(client, model="@no-such-role")
    assert r.status_code == 400
    assert client.asked == []


def test_default_is_the_rails_own_model(client):
    """No model in the body -> the server's configured default, which is a @role by default
    (``@recipe``). If roles were validated as model names this would be the first thing to
    break, which is the finance bug."""
    assert _ask(client).status_code == 200
    assert client.asked == ["@recipe"]


def test_blank_model_falls_back_to_the_default(client):
    assert _ask(client, model="   ").status_code == 200
    assert client.asked == ["@recipe"]


def test_unreachable_broker_skips_validation(client, monkeypatch):
    """Nothing to validate against is not a reason to refuse the request; the chat call
    reports the outage itself (502). picker_models() swallows the error and returns no
    models, so the allowlist must treat 'empty' as 'unknown', not as 'nothing is allowed'."""
    monkeypatch.setattr(broker, "picker_models",
                        lambda: {"broker_up": False, "models": [], "error": "unreachable"})
    assert _ask(client, model="gemma4:26b").status_code == 200
    assert client.asked == ["gemma4:26b"]
