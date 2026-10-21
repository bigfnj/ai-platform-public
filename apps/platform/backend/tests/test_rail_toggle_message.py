"""Rail Manager: a rail this box cannot run names the setting that is in the way, on its card and
in the refusal to switch it on.

Two settings can keep a rail out of the gateway: it is missing from PLATFORM_ENABLED_APPS, or it
is listed in PLATFORM_DISABLED_APPS (subtracted from the enabled list at startup). The refusal
used to name only the first, so an admin looking at a rail disabled the second way was told to
add it to a list it was already on, did so, restarted, and saw nothing change. The card said the
same until 2026-10-14: the gateway sent no cause, and the shell printed "add to
PLATFORM_ENABLED_APPS and restart" on every such rail. It sends `blocked_by` now (main.py
_rail_blocked_by, the refusal's own rule), and apps/platform/frontend/tests/admin-rails.test.js
holds the card's rendering of it.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from platform_gateway_app import main as gw
from platform_gateway_app.config import GatewaySettings


def _toggle(monkeypatch, session, rid, **settings):
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(**settings), raising=False)
    return gw.admin_rail_toggle(gw.RailToggleBody(rail_id=rid, enabled=True), admin=None,
                                db=session)


def test_a_rail_disabled_on_this_box_names_platform_disabled_apps(monkeypatch, session):
    with pytest.raises(HTTPException) as exc:
        _toggle(monkeypatch, session, "recipe-book",
                enabled_apps=("terminal-fun", "recipe-book"), disabled_apps=("recipe-book",))
    assert exc.value.status_code == 409
    assert "PLATFORM_DISABLED_APPS" in exc.value.detail
    assert "PLATFORM_ENABLED_APPS" not in exc.value.detail, "it is already on that list"


def test_a_rail_never_enabled_still_names_platform_enabled_apps(monkeypatch, session):
    with pytest.raises(HTTPException) as exc:
        _toggle(monkeypatch, session, "recipe-book", enabled_apps=("terminal-fun",))
    assert exc.value.status_code == 409
    assert "Add it to PLATFORM_ENABLED_APPS" in exc.value.detail
    assert "PLATFORM_DISABLED_APPS" not in exc.value.detail


def test_an_installed_rail_still_switches_on(monkeypatch, session):
    out = _toggle(monkeypatch, session, "recipe-book", enabled_apps=("recipe-book",))
    assert out == {"rail_id": "recipe-book", "state": "enabled"}


# --- the card: `blocked_by` (added 2026-10-14) ---------------------------------------------------

def _card(monkeypatch, session, **settings) -> dict[str, dict]:
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(**settings), raising=False)
    monkeypatch.setattr(gw.app.state, "backends", {}, raising=False)
    return {r["id"]: r for r in gw.admin_rail_manager(admin=None, db=session)["rails"]}


BOX = {"enabled_apps": ("terminal-fun", "recipe-book"), "disabled_apps": ("recipe-book",)}


def test_the_card_says_which_setting_keeps_each_rail_off(monkeypatch, session):
    rows = _card(monkeypatch, session, **BOX)
    assert (rows["recipe-book"]["state"], rows["recipe-book"]["blocked_by"]) == (
        "unavailable", "disabled_apps"), "PLATFORM_DISABLED_APPS removed it"
    # A published rail: the public snapshot's catalog holds no withheld one (this asked for one
    # until 2026-10-15, a KeyError there that turned the snapshot's suite red).
    assert (rows["ai-playground"]["state"], rows["ai-playground"]["blocked_by"]) == (
        "unavailable", "not_enabled"), "PLATFORM_ENABLED_APPS never named it"
    assert rows["terminal-fun"]["blocked_by"] is None, "a rail this box runs has no cause"


def test_a_rail_an_admin_switched_off_has_no_cause(monkeypatch, session):
    session.add(gw.RailState(rail_id="terminal-fun", disabled_by="owner-one"))
    session.commit()
    row = _card(monkeypatch, session, **BOX)["terminal-fun"]
    assert (row["state"], row["blocked_by"]) == ("disabled", None), "the Turn on button fixes it"


def test_the_card_and_the_refusal_name_the_same_setting(monkeypatch, session):
    rows = _card(monkeypatch, session, **BOX)
    off = [rid for rid, r in rows.items() if r["state"] == "unavailable"]
    assert {rows[rid]["blocked_by"] for rid in off} == {"disabled_apps", "not_enabled"}
    for rid in off:
        with pytest.raises(HTTPException) as exc:
            _toggle(monkeypatch, session, rid, **BOX)
        named = "PLATFORM_DISABLED_APPS" if rows[rid]["blocked_by"] == "disabled_apps"             else "PLATFORM_ENABLED_APPS"
        assert named in exc.value.detail, (rid, exc.value.detail)
