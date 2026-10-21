"""PLATFORM_ALLOWED_WS_ORIGINS can be set, in the forms the app lists take.

config.py documented the setting (pin the WebSocket proxy's Origin allowlist instead of
same-origin), but it could not be used. A complex field without NoDecode, its env value was
JSON-decoded before any validator ran, so a bare origin, or a blank line, raised SettingsError
when GatewaySettings() was built at import and the gateway never served; only a JSON array
worked. And neither compose file passed it to the container. Now it takes the app lists' forms
(a comma list or a JSON array, blank meaning the default, same-origin), and both compose files
pass it with a default of `[]`, which a gateway image built before this change also reads as
same-origin, where a blank would have stopped it.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_gateway_app import main as m
from platform_gateway_app.config import GatewaySettings

_REPO = Path(__file__).resolve().parents[4]
_VAR = "PLATFORM_ALLOWED_WS_ORIGINS"


@pytest.mark.parametrize(("raw", "expected"), [
    ("https://platform.example.com", ("https://platform.example.com",)),
    ("https://a.example.com, http://localhost:1111", ("https://a.example.com",
                                                      "http://localhost:1111")),
    ('["https://a.example.com", "http://localhost:1111"]', ("https://a.example.com",
                                                            "http://localhost:1111")),
])
def test_a_bare_origin_a_comma_list_and_a_json_list_all_load(monkeypatch, raw, expected):
    monkeypatch.setenv(_VAR, raw)
    assert GatewaySettings().allowed_ws_origins == expected


@pytest.mark.parametrize("raw", ["", "   ", "[]"])
def test_blank_or_an_empty_list_keeps_the_same_origin_default(monkeypatch, raw):
    monkeypatch.setenv(_VAR, raw)
    assert GatewaySettings().allowed_ws_origins == ()


def test_unset_is_the_same_origin_default(monkeypatch):
    monkeypatch.delenv(_VAR, raising=False)
    assert GatewaySettings().allowed_ws_origins == ()


def test_a_pinned_origin_is_the_one_the_websocket_proxy_admits(monkeypatch):
    monkeypatch.setenv(_VAR, "https://platform.example.com")
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)

    def ws(origin: str):
        return SimpleNamespace(headers={"origin": origin, "host": "platform.example.com"})

    assert m._ws_origin_ok(ws("https://platform.example.com"))
    assert not m._ws_origin_ok(ws("https://other.example.org"))


@pytest.mark.parametrize("compose_file", ["deploy/docker-compose.yml",
                                          "deploy/installer/docker-compose.installer.yml"])
def test_both_compose_files_pass_it_to_the_gateway_with_an_empty_list_default(compose_file):
    """`[]`, not a blank: compose passes the default on every box that sets nothing, and a
    gateway image from before this change JSON-decodes the value, so a blank stops it at import
    while `[]` is the same-origin default to old and new alike."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((_REPO / compose_file).read_text(encoding="utf-8"))
    env = compose["services"]["gateway"]["environment"]
    assert env.get(_VAR) == "${PLATFORM_ALLOWED_WS_ORIGINS:-[]}"


def test_the_env_template_documents_it_without_pinning_it():
    text = (_REPO / "deploy/.env.example").read_text(encoding="utf-8")
    assert re.search(rf"^#[ \t]*{_VAR}=", text, re.M)
    assert not re.search(rf"^[ \t]*{_VAR}=", text, re.M)
