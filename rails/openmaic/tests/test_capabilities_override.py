"""The reasoning chip while OPENMAIC_LLM_BASE_URL points generation at an external endpoint.

THE DEFECT. Every chip entry carries `slot`, `label`, `role`, `model`, `state`, `upstream`,
`backend` and `busy` (docs/RAIL_CONTRACT.md, the model chip envelope and "Who answers a slot"),
and the shell reads all of them (web/src/model_chip_view.ts). modelstate.resolve() builds the
entries for every slot it resolves; the override's entry is built in api/app.py instead, and it
carried the first five only. The shell then rendered the slot from defaults: the rail's guessed
`cold` shown as residency, as if a local model sat idle on this box.

THE FIX. The override's entry carries every key the broker's entries do, with the values that say
the broker is not in the path: `backend` and `upstream` "external" (the contract's word for it),
`busy` false. No test here reaches the far side: the chip never calls it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openmaic_app import broker
from openmaic_app.api import app as app_module
from openmaic_app.config import settings

OVERRIDE = "http://gpu-box.lan:11434/v1?api_key=a-synthetic-secret"
# The contract's keys, spelled out once here; the test below also holds the override's entry to
# whatever the broker's own entries carry, so a key the template adds later is caught too.
CHIP_KEYS = {"slot", "label", "role", "model", "state", "upstream", "backend", "busy"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", OVERRIDE)
    monkeypatch.setattr(settings, "cli_requester", "")
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "openmaic", "resolved": "gemma3:4b"},
                                                  {"role": "embed", "resolved": "bge-m3"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "gemma3:4b"}, {"name": "bge-m3"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [{"name": "bge-m3"}], "jobs": []})

    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(app_module, "app_reachable", unreachable)


def chips(gateway) -> list[dict]:
    c = TestClient(app_module.app, client=(gateway.address, 50000))
    r = c.get("/api/capabilities", headers={"X-Platform-User": "owner-one"})
    assert r.status_code == 200, r.text
    return r.json()["models"]


def test_the_override_entry_carries_every_key_the_brokers_entries_do(gateway):
    models = chips(gateway)
    assert [m["slot"] for m in models] == ["reasoning", "embed"]
    reasoning, embed = models
    assert set(embed) == CHIP_KEYS, "the template's own entry changed shape; recheck CHIP_KEYS"
    assert set(reasoning) == set(embed)


def test_the_override_says_the_broker_is_not_in_the_path(gateway):
    reasoning = chips(gateway)[0]
    assert reasoning["backend"] == "external"
    assert reasoning["upstream"] == "external"
    assert reasoning["busy"] is False
    # Unchanged: the rail cannot see residency there, and the chip names the host only, never
    # the URL, whose query string can carry a key.
    assert reasoning["state"] == "cold"
    assert reasoning["model"] == "gpu-box.lan:11434"
    assert "a-synthetic-secret" not in str(reasoning)


def test_the_override_entry_is_whole_when_the_broker_is_down(gateway, monkeypatch):
    """The unreachable branch keeps every key for the broker's slots; the override's entry must
    not be the one that loses them on that day."""
    def down():
        raise broker.BrokerError("broker unreachable")

    monkeypatch.setattr(broker, "roles", down)
    monkeypatch.setattr(broker, "models", down)
    monkeypatch.setattr(broker, "status", down)
    reasoning, embed = chips(gateway)
    assert embed["state"] == "missing" and set(embed) == CHIP_KEYS
    assert set(reasoning) == CHIP_KEYS
    assert (reasoning["backend"], reasoning["upstream"], reasoning["busy"]) == ("external",
                                                                             "external", False)


def test_without_the_override_the_broker_answers_for_the_slot(gateway, monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "")
    reasoning = chips(gateway)[0]
    assert set(reasoning) == CHIP_KEYS
    assert reasoning["backend"] == "ollama" and reasoning["upstream"] == "local"
