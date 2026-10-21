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

import re
from pathlib import Path

import pytest

from platform_gateway_app.catalog import APP_CATALOG, APP_IDS
from platform_gateway_app.config import GatewaySettings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)


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
    """No env override = every integrated rail: exactly the catalog's ready tiles.

    Asserted against the catalog, not a count. This used to say `len(apps) >= 14`, a fact about
    the full private fleet: publishing de-wires the withheld rails out of BOTH this default and
    APP_CATALOG, which leaves 9, so the public snapshot failed here on every build since
    2026-09-24. The catalog is the other half of the same registry and is de-wired in the same
    pass, so the comparison holds in either tree. It is also the stronger check: `>= 14` let up
    to four rails fall out of the default unnoticed, and this lets none.
    """
    apps = GatewaySettings().enabled_apps
    assert "gemini-cx" in apps and "terminal-fun" in apps
    assert len(apps) == len(set(apps)), "an app is enabled twice"
    ready = {e["id"] for e in APP_CATALOG if e.get("status", "ready") == "ready"}
    assert ready - set(apps) == set(), "a ready tile is missing from the default enabled_apps"
    assert set(apps) - set(APP_IDS) == set(), "the default enables an app the catalog has no tile for"


def test_enabled_apps_drives_the_derived_maps(monkeypatch) -> None:
    """The point of the field: it filters the backend + dist registries the proxy routes on."""
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "terminal-fun,recipe-book")
    s = GatewaySettings()
    assert set(s.app_backends()) == {"terminal-fun", "recipe-book"}


# --- PLATFORM_DISABLED_APPS: a box declaring a rail it deliberately does not run -------------
#
# Subtracted from enabled_apps, so the default "every rail" survives and a box names only the
# exception. The same two input forms as PLATFORM_ENABLED_APPS, because the same validator
# parses both: an operator copying one line's format into the other must not get a gateway that
# dies at import, which is exactly what the JSON-only parse once did to the lean installer.

@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("recipe-book", {"recipe-book"}),                                  # one bare value
        ("recipe-book,gemini-cx", {"recipe-book", "gemini-cx"}),           # comma form
        (" recipe-book , gemini-cx ", {"recipe-book", "gemini-cx"}),       # spaces
        ('["recipe-book","gemini-cx"]', {"recipe-book", "gemini-cx"}),     # JSON form
    ],
)
def test_disabled_apps_env_forms_subtract_from_the_default(monkeypatch, raw, gone) -> None:
    full = GatewaySettings().enabled_apps
    monkeypatch.setenv("PLATFORM_DISABLED_APPS", raw)
    s = GatewaySettings()
    assert set(s.enabled_apps) == set(full) - gone
    assert set(s.disabled_apps) == gone


def test_disabled_apps_subtracts_from_an_explicit_enabled_list_in_order(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "terminal-fun,recipe-book,gemini-cx")
    monkeypatch.setenv("PLATFORM_DISABLED_APPS", "recipe-book")
    assert GatewaySettings().enabled_apps == ("terminal-fun", "gemini-cx")


def test_a_blank_disabled_list_disables_nothing(monkeypatch) -> None:
    """The full compose stack passes `${PLATFORM_DISABLED_APPS:-}`, i.e. an EMPTY string, on
    every box that sets nothing. That must mean "nothing disabled"."""
    full = GatewaySettings().enabled_apps
    monkeypatch.setenv("PLATFORM_DISABLED_APPS", "")
    assert GatewaySettings().enabled_apps == full


@pytest.mark.parametrize("blank", ["", "   ", "\n", " \t "])
def test_a_blank_enabled_list_means_every_rail_not_none(monkeypatch, blank) -> None:
    """A blank PLATFORM_ENABLED_APPS is "not set", so the default (every rail) applies. It used
    to parse to (), i.e. NO rails: a `PLATFORM_ENABLED_APPS=` line in deploy/.env emptied the
    launcher. This is what makes the variable safe to pass through on the full stack."""
    # The field default itself, not a count: the public snapshot ships fewer rails than this
    # repo, and a hard-coded "at least 14" is exactly the assertion that fails there.
    default = GatewaySettings.model_fields["enabled_apps"].default
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", blank)
    assert GatewaySettings().enabled_apps == default
    assert default, "the default must name at least one rail, or blank-means-default is vacuous"


def test_an_explicit_empty_json_list_still_means_no_rails(monkeypatch) -> None:
    """`[]` is a list someone wrote, not a value someone left out."""
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "[]")
    assert GatewaySettings().enabled_apps == ()


def test_the_full_stack_passes_enabled_apps_through_with_no_default() -> None:
    """No `${...:-}` default, deliberately: unset in deploy/.env must stay unset in the
    container, so even a gateway image built before the blank rule above keeps every rail. A
    blank default would reach such an image as "" and switch every rail off. The lean installer
    keeps its own default (terminal-fun), which its chooser always fills."""
    yaml = pytest.importorskip("yaml")
    full = yaml.safe_load((_REPO / "deploy/docker-compose.yml").read_text(encoding="utf-8"))
    env = full["services"]["gateway"]["environment"]
    assert "PLATFORM_ENABLED_APPS" in env and env["PLATFORM_ENABLED_APPS"] is None
    lean = yaml.safe_load((_REPO / "deploy/installer/docker-compose.installer.yml")
                          .read_text(encoding="utf-8"))
    assert (lean["services"]["gateway"]["environment"]["PLATFORM_ENABLED_APPS"]
            == "${PLATFORM_ENABLED_APPS:-terminal-fun}")


def test_disabling_a_rail_that_is_not_enabled_is_a_no_op(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENABLED_APPS", "terminal-fun")
    monkeypatch.setenv("PLATFORM_DISABLED_APPS", "recipe-book,no-such-rail")
    assert GatewaySettings().enabled_apps == ("terminal-fun",)


def test_a_disabled_app_leaves_every_derived_map(monkeypatch) -> None:
    """Subtracted ONCE, at the field, so every consumer of enabled_apps drops it with no code of
    its own: the proxy registry, the bundle mounts, and the root-origin asset claims."""
    monkeypatch.setenv("PLATFORM_DISABLED_APPS", "openmaic")
    s = GatewaySettings()
    assert "openmaic" not in s.app_backends()
    assert "openmaic" not in s.resolved_app_dists()
    assert "openmaic" not in s.root_asset_routes().values()
    assert s.root_asset_owner("/logos/openai.svg") is None
    # ...and the neighbours are untouched, so the subtraction is not simply emptying the maps.
    assert "terminal-fun" in s.app_backends()


_REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("compose_file", ["deploy/docker-compose.yml",
                                          "deploy/installer/docker-compose.installer.yml"])
def test_the_full_stack_passes_disabled_apps_to_the_gateway_with_a_blank_default(
        compose_file) -> None:
    """The setting is inert unless compose hands it to the container. The full stack gives the
    gateway no env_file, which is exactly how PLATFORM_ENABLED_APPS came to be ignored there
    while a compose comment told operators to set it. The blank default matters as much as the
    line: anything else would disable a rail on every box that sets nothing. Both compose files
    that define a gateway, so a lean box and a full one read the same declaration."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((_REPO / compose_file).read_text(encoding="utf-8"))
    env = compose["services"]["gateway"]["environment"]
    assert env.get("PLATFORM_DISABLED_APPS") == "${PLATFORM_DISABLED_APPS:-}"


def test_both_env_templates_document_it_without_pinning_it() -> None:
    """Documented where an operator looks (deploy/.env.example, and the lean installer's
    template), and COMMENTED in both. The lean one is not cosmetic: a re-run of the installer
    rebuilds deploy/.env from env.lean.example and a key the template DEFINES takes the
    template's value, so a live blank line there would wipe a box's own list on every re-apply."""
    for rel in ("deploy/.env.example", "deploy/installer/env.lean.example"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert re.search(r"^#[ \t]*PLATFORM_DISABLED_APPS=", text, re.M), rel
        assert not re.search(r"^[ \t]*PLATFORM_DISABLED_APPS=", text, re.M), rel


def test_the_full_stack_template_documents_enabled_apps_as_read() -> None:
    """deploy/.env.example used to file PLATFORM_ENABLED_APPS under "Not read from this file",
    which was true while compose ignored it. Now that compose passes it, the template shows the
    line an operator would uncomment, commented so an unset box keeps every rail."""
    text = (_REPO / "deploy/.env.example").read_text(encoding="utf-8")
    assert re.search(r"^#[ \t]*PLATFORM_ENABLED_APPS=", text, re.M)
    assert not re.search(r"^[ \t]*PLATFORM_ENABLED_APPS=", text, re.M)
    assert "this compose file does not pass it" not in text
