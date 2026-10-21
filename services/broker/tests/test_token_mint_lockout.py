"""Minting the FIRST named token must not lock every client out of the broker.

The store EXISTING is what turns token checks on (tokens_configured), deliberately, so that
revoking the last token means "nobody is authorised" rather than "authorisation is off". The
other side of that rule was a trap: on a broker with no BROKER_AUTH_TOKEN, the first mint from
Admin > Broker created tokens.json, enforcement switched on, and the gateway, every rail and
the console itself got 401 on their next call. The console could not reach /v1/tokens to undo
it; the only way back was deleting the file on the broker host. The audit's probe: mint 200,
next call 401.

Now that first mint is refused with a 409 that says what it would have done, and nothing is
written. With the shared secret set, minting works as before, because every existing client
already holds a token the broker accepts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.broker import Broker
from app.config import BrokerSettings


@pytest.fixture
def broker_app(tmp_path, monkeypatch):
    """The real broker app on a temp token store; returns (client, settings). app.state is set
    through monkeypatch, so nothing is left behind for the next test file."""
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES"):
        monkeypatch.delenv(key, raising=False)
    from app.main import app

    def build(auth_token: str = "") -> tuple[TestClient, BrokerSettings]:
        settings = BrokerSettings(
            roles_file=str(tmp_path / "roles.json"),
            disabled_file=str(tmp_path / "disabled.json"),
            tokens_file=str(tmp_path / "tokens.json"), auth_token=auth_token)
        monkeypatch.setattr(app.state, "settings", settings, raising=False)
        monkeypatch.setattr(app.state, "broker", Broker(settings), raising=False)
        monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
        return TestClient(app), settings

    return build


def test_the_first_mint_on_an_open_broker_is_refused_and_writes_nothing(broker_app, tmp_path):
    client, settings = broker_app()
    r = client.post("/v1/tokens", json={"label": "studio", "scope": "full"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "BROKER_AUTH_TOKEN" in detail and "401" in detail
    assert not (tmp_path / "tokens.json").exists(), "the refused mint still created the store"
    assert settings.tokens_configured() is False
    # And the broker is exactly as open as it was: the next call is not a 401.
    assert client.get("/v1/tokens").status_code == 200


def test_with_the_shared_secret_set_minting_works_and_the_secret_still_does(broker_app):
    client, _settings = broker_app(auth_token="shared-secret")
    auth = {"authorization": "Bearer shared-secret"}
    r = client.post("/v1/tokens", json={"label": "studio", "scope": "inference"}, headers=auth)
    assert r.status_code == 200, r.text
    minted = r.json()["token"]
    # The gateway's credential still opens the token manager after the mint...
    assert client.get("/v1/tokens", headers=auth).status_code == 200
    # ...and the new token works for what it is for.
    assert client.get("/v1/tokens", headers={"authorization": f"Bearer {minted}"}
                      ).status_code == 403  # inference scope: authenticated, not allowed here


def test_once_the_store_exists_a_full_token_may_mint_more(broker_app):
    """Enforcement already on, by named tokens alone: a full-scope holder minting another is
    not the lockout, because whoever is calling already holds a token the broker accepts."""
    client, settings = broker_app()
    plain, _row = settings.add_token("bootstrap", "full")
    r = client.post("/v1/tokens", json={"label": "studio", "scope": "inference"},
                    headers={"authorization": f"Bearer {plain}"})
    assert r.status_code == 200, r.text
