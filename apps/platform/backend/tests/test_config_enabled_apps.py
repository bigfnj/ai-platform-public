"""PLATFORM_ENABLED_APPS must accept the plain comma form, not only a JSON array.

Regression guard for a shipped fault: `enabled_apps` is a complex field, so pydantic-settings
JSON-decodes the env value inside EnvSettingsSource *before* any validator runs, and raised
SettingsError on anything that was not a JSON array. Every override actually shipped writes the
comma form — Dockerfile.gateway.bundled, the installer compose default, and env.lean.example (which
install.ps1 fills with `$enabled -join ','`) — and GatewaySettings() is constructed at import time
by main._mount_app_remotes(), so the lean installer's gateway died before uvicorn could serve.
Even a single bare value failed, since `terminal-fun` is not valid JSON. The full stack never hit
it only because deploy/.env sets no such line and the code default applies.
"""
from __future__ import annotations

import pytest

from platform_gateway_app.config import GatewaySettings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("terminal-fun", ("terminal-fun",)),                              # installer compose default
        ("terminal-fun,recipe-book", ("terminal-fun", "recipe-book")),    # Dockerfile / env.lean
        (" terminal-fun , recipe-book ", ("terminal-fun", "recipe-book")),  # tolerant of spaces
        ('["terminal-fun","recipe-book"]', ("terminal-fun", "recipe-book")),  # JSON still works
    ],
)
def test_enabled_apps_env_forms(monkeypatch, raw: str, expected: tuple[str, ...]) -> None:
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", raw)
    assert GatewaySettings().enabled_apps == expected


def test_enabled_apps_default_is_the_full_set(monkeypatch) -> None:
    """No env override = every integrated rail, unchanged."""
    apps = GatewaySettings().enabled_apps
    assert "gemini-cx" in apps and "terminal-fun" in apps
    assert len(apps) >= 14


def test_enabled_apps_drives_the_derived_maps(monkeypatch) -> None:
    """The point of the field: it filters the backend + dist registries the proxy routes on."""
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "terminal-fun,recipe-book")
    s = GatewaySettings()
    assert set(s.app_backends()) == {"terminal-fun", "recipe-book"}
