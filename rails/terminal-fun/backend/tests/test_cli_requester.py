"""Whose Claude subscription may answer the Terminal Fun assistant, and how long it waits.

The broker answers a buffered chat call from a platform owner's own Claude CLI subscription
when the call's `requester` names an owner and its @role is switched on there. Here the person
waiting is the viewer typing into the assistant panel, and the prompt holds their own question
over the public game catalogue, so the viewer is named. What these pin:

* `/api/chat` names the viewer, and standalone dev (no user) names nobody;
* the facade keeps a name only on the assistant's own role (`broker._cli_requester`);
* the broker wait stays under the gateway's read timeout and is the one the call uses. That
  it outlasts the CLI timeout plus a local fallback answer (so a hung CLI call reaches the
  viewer as gemma's answer) is not asserted here: the fleet smoke check (cli_client_timeouts;
  maintainer tooling, not in the public snapshot) checks it against the broker's LIVE
  cli.timeout_s on every sweep, from a registry its own tests hold equal to this rail's
  source, because BROKER_CLI_TIMEOUT lives only in the broker's service environment and a
  constant here could not fail when it changed;
* the assistant chip reads Claude for exactly the viewers whose question would.

Asserted on the body `httpx.post` would send. Offline, neutral account names.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from terminal_fun_app import broker
from terminal_fun_app.config import Settings, settings
from terminal_fun_app.main import app

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER, "X-Platform-Admin": "0"}
# Identity headers count only from the gateway's address (main.GatewayOnlyIdentity), so a request
# that carries them comes from where the gateway stands; conftest's `gateway` fixture resolves the
# gateway's name to this address.
GATEWAY_PEER = ("10.89.0.2", 50000)

# The gateway's buffered read timeout
# (apps/platform/backend/platform_gateway_app/main.py: httpx.AsyncClient(timeout=600.0)).
GATEWAY_READ_TIMEOUT = 600.0


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def wire(monkeypatch):
    """Every /v1/chat request the facade makes: (url, body, timeout). Nothing is sent."""
    sent: list[tuple[str, dict, float]] = []

    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002 - httpx's name
        sent.append((url, json, timeout))
        return _Resp({"message": {"content": '{"reply": "Press q to quit.", "set_params": null}'}})

    monkeypatch.setattr(broker.httpx, "post", fake_post)
    monkeypatch.setattr(settings, "llm_model", "@terminal-fun")
    return sent


def carried(sent) -> list:
    assert sent, "no call reached the facade, so nothing was asserted"
    return [body.get("requester", "ABSENT") for _, body, _ in sent]


# --- the facade --------------------------------------------------------------------------


def test_the_assistant_role_carries_the_requester(wire):
    broker.chat([{"role": "user", "content": "q"}], requester=OWNER)
    assert carried(wire) == [OWNER]


def test_no_requester_means_none_on_the_wire(wire):
    broker.chat([{"role": "user", "content": "q"}])
    assert carried(wire) == ["ABSENT"]


def test_chat_json_puts_the_requester_on_the_wire(wire):
    broker.chat_json([{"role": "user", "content": "q"}], requester=OWNER)
    assert carried(wire) == [OWNER]


def test_a_foreign_role_never_carries_the_name(wire):
    for model in ("@iep", "gemma4:12b"):
        broker.chat([{"role": "user", "content": "q"}], model=model, requester=OWNER)
    assert carried(wire) == ["ABSENT"] * 2


# --- the route ---------------------------------------------------------------------------


def test_the_assistant_names_the_viewer(wire, gateway):
    r = TestClient(app, client=GATEWAY_PEER).post("/api/chat", json={"message": "how do I quit?"},
                                                  headers=MINE)
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Press q to quit."
    assert carried(wire) == [OWNER]


def test_standalone_dev_names_nobody(wire, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    r = TestClient(app).post("/api/chat", json={"message": "how do I quit?"})
    assert r.status_code == 200, r.text
    assert carried(wire) == ["ABSENT"]


# --- the wait ----------------------------------------------------------------------------


def test_the_broker_wait_ends_before_the_gateways_and_is_the_one_the_call_uses(wire,
                                                                                monkeypatch):
    """The wait must end before the gateway's own read timeout, or the viewer gets a gateway
    error instead of this rail's message. Its LOWER bound (above the broker's CLI timeout plus
    a local answer, since every CLI failure falls back inside the same broker request) is
    checked live by the fleet smoke check (maintainer tooling), against the timeout the broker
    reports."""
    monkeypatch.delenv("TERMINAL_FUN_BROKER_TIMEOUT", raising=False)
    default = Settings(_env_file=None).broker_timeout
    assert default < GATEWAY_READ_TIMEOUT, default
    monkeypatch.setattr(settings, "broker_timeout", default)
    broker.chat([{"role": "user", "content": "q"}])
    assert wire[0][2] == default, "the call does not use the configured wait"


# --- the chip ----------------------------------------------------------------------------


@pytest.fixture()
def chips(monkeypatch, gateway):
    cli = {"ready": True, "owners": [OWNER], "roles": ["terminal-fun"], "active": []}
    monkeypatch.setattr(settings, "llm_model", "@terminal-fun")
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "terminal-fun",
                                                   "resolved": "gemma4:12b"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "gemma4:12b"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    client = TestClient(app, client=GATEWAY_PEER)

    def read(headers):
        r = client.get("/api/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        return {m["slot"]: m["backend"] for m in r.json()["models"]}

    read.cli = cli  # type: ignore[attr-defined]
    return read


def test_an_owner_sees_the_assistant_on_claude(chips):
    assert chips(MINE) == {"assistant": "claude"}


def test_a_non_owner_sees_ollama(chips):
    assert chips({"X-Platform-User": OTHER}) == {"assistant": "ollama"}


def test_a_role_off_the_allowlist_reads_ollama(chips):
    chips.cli["roles"] = ["iep"]
    assert chips(MINE) == {"assistant": "ollama"}
