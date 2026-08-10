"""Control-plane token dependency (audit E1): open when unset, else Bearer/X-Broker-Token required."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import require_token


def _req(path: str, headers: dict, token: str):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers,
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(auth_token=token))),
    )


def test_open_when_token_unset():
    require_token(_req("/v1/status", {}, ""))  # no token configured => allowed


def test_healthz_always_open():
    require_token(_req("/healthz", {}, "secret"))  # liveness exempt even when enforcing


def test_missing_token_401():
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/status", {}, "secret"))
    assert e.value.status_code == 401


def test_wrong_token_401():
    with pytest.raises(HTTPException):
        require_token(_req("/v1/chat", {"authorization": "Bearer nope"}, "secret"))


def test_valid_bearer_ok():
    require_token(_req("/v1/status", {"authorization": "Bearer secret"}, "secret"))


def test_valid_bearer_case_insensitive_scheme():
    require_token(_req("/v1/status", {"authorization": "bearer secret"}, "secret"))


def test_valid_x_broker_token_ok():
    require_token(_req("/v1/embed", {"x-broker-token": "secret"}, "secret"))
