"""The Rails tab's Claude switch through the REAL routing: path, method, Depends and body parsing.

test_rails_cli_switch.py calls `admin_set_rail_cli` as a function, which pins what the handler
decides but not how a request reaches it. A planted rename of the route path survived every
gateway test (the shell's PUT would then miss the route in the live app), and the railcheck fake
gateway matches its own copy of the path. These tests go through FastAPI's TestClient with a real
session cookie, so the path, the `Depends(require_admin)` wiring and the StrictBool body are the
production ones. The broker is a recording fake: no network. A vision slot's switch, its
refusal on a broker that does not send images, and an image slot's refusal, go through the
same routing on a synthetic rail (photo_http); a streaming slot's refusal on a broker that does
not stream to Claude on another (stream_http).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from platform_gateway_app import main as gw
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.config import GatewaySettings
from platform_gateway_app.models import User
from platform_gateway_app.rails_models import (
    CLI_WIRED_ROLES,
    RAIL_MODEL_SLOTS,
    STREAM_SLOT_ROLES,
    VISION_SLOT_ROLES,
)

HAIKU = "claude-haiku-5-5"


class _Broker:
    """Answers the Rails tab's one read and records every switch the route passes on."""

    def __init__(self) -> None:
        self.cli = {"ready": True, "blockers": [], "enabled_roles": [], "roles_source": "file",
                    "roles_file_set": True, "roles_problem": "", "models": [HAIKU],
                    "default_model": "", "role_models": {}}
        self.switched: list[tuple] = []

    async def roles(self, inventory=False):
        rows = [{"role": s["role"], "pattern": s["default"], "resolved": "m", "installed": True,
                 "upstream": "local"} for slots in RAIL_MODEL_SLOTS.values() for s in slots]
        return {"roles": rows, "upstreams": [{"name": "local", "url": "", "healthy": True}],
                "models": {"local": []}, "cli": dict(self.cli)}

    async def disabled(self):
        return []

    async def set_cli_role(self, role, enabled=None, **model):
        self.switched.append((role, enabled, *model.values()))
        if enabled is not None:
            on = set(self.cli["enabled_roles"])
            (on.add if enabled else on.discard)(role)
            self.cli["enabled_roles"] = sorted(on)
        if model:
            chosen = model["model"]
            self.cli["role_models"] = {} if chosen is None else {role: chosen}
        return {"cli": dict(self.cli)}


@pytest.fixture()
def gw_http(db, monkeypatch):
    """(broker fake, login) where login(name, admin=, superadmin=) -> a TestClient carrying that
    user's session cookie, and login(None) -> an anonymous one."""
    if not _text_roles():
        pytest.skip("no text slot in this tree's catalog is wired for the CLI")
    return _serve(db, monkeypatch)


def _text_roles() -> list[str]:
    """Wired TEXT roles that do not stream: a vision role's ON is also held to the broker's
    image path, and a streaming role's to its stream path. Text roles are read off the catalog
    (RAIL_MODEL_SLOTS); the gateway keeps no set of them."""
    text = {s["role"] for slots in RAIL_MODEL_SLOTS.values() for s in slots if s["kind"] == "chat"}
    return sorted((CLI_WIRED_ROLES & text) - VISION_SLOT_ROLES - STREAM_SLOT_ROLES)


# A synthetic rail with a WIRED vision slot and an image slot (conftest's add_rail), so the
# vision rule goes through the real routing whatever the real catalog wires today.
PHOTO_WIRED, PHOTO_ICON = "syn-photo-reader", "syn-photo-icon"


@pytest.fixture()
def photo_http(db, monkeypatch, add_rail):
    add_rail("syn-photo-lab", [
        {"slot": "reader", "label": "Reader", "role": PHOTO_WIRED, "kind": "vision", "cli": True,
         "env": "X", "default": "m", "description": ""},
        {"slot": "icon", "label": "Icon", "role": PHOTO_ICON, "kind": "image",
         "env": "X", "default": "m", "description": ""},
    ])
    b, login = _serve(db, monkeypatch)
    b.cli["vision"] = True          # a broker that sends images to the CLI
    return b, login


# A synthetic rail with a WIRED streaming slot, so the stream rule goes through the real routing
# whatever the real catalog streams today.
STREAM_WIRED = "syn-stream-writer"


@pytest.fixture()
def stream_http(db, monkeypatch, add_rail):
    add_rail("syn-stream-lab", [
        {"slot": "writer", "label": "Writer", "role": STREAM_WIRED, "kind": "chat", "cli": True,
         "stream": True, "env": "X", "default": "m", "description": ""},
    ])
    b, login = _serve(db, monkeypatch)
    b.cli["streams"] = True         # a broker that streams to the CLI
    return b, login


def _serve(db, monkeypatch):
    b = _Broker()
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(gw.app.state, "broker", b, raising=False)
    monkeypatch.setattr(gw.app.state, "settings",
                        GatewaySettings(enabled_apps=tuple(RAIL_MODEL_SLOTS)), raising=False)
    monkeypatch.setattr(gw.app.state, "db", db, raising=False)

    def login(name, admin=False, superadmin=False):
        client = TestClient(gw.app)
        if name is None:
            return client
        with db.session_ctx() as s:
            u = User(username=name, password_hash=hash_password("x"),
                     is_admin=admin or superadmin, is_superadmin=superadmin)
            s.add(u)
            s.commit()
            token = create_session(s, u, ttl_hours=1).token
        client.cookies.set(gw.app.state.settings.session_cookie, token)
        return client

    return b, login


def _url() -> str:
    return f"/api/platform/admin/rails/{_text_roles()[0]}/cli"


def test_an_anonymous_switch_is_a_401_and_reaches_no_broker(gw_http):
    b, login = gw_http
    r = login(None).put(_url(), json={"enabled": True})
    assert r.status_code == 401, r.text
    assert b.switched == []


def test_a_signed_in_user_who_is_not_an_admin_is_a_403(gw_http):
    b, login = gw_http
    r = login("user-a").put(_url(), json={"enabled": True})
    assert r.status_code == 403, r.text
    assert b.switched == []


def test_a_plain_admin_is_a_403_and_the_broker_is_never_called(gw_http):
    b, login = gw_http
    r = login("admin-a", admin=True).put(_url(), json={"enabled": True})
    assert r.status_code == 403, r.text
    assert "super-admin" in r.json()["detail"]
    assert b.switched == [], "the broker was called before the super-admin guard ran"


@pytest.mark.parametrize("enabled", ["true", 1, None], ids=["string", "number", "null"])
def test_a_body_that_is_not_a_json_boolean_is_a_422(gw_http, enabled):
    b, login = gw_http
    r = login("root-a", superadmin=True).put(_url(), json={"enabled": enabled})
    assert r.status_code == 422, r.text
    assert b.switched == []


def test_a_superadmin_switch_reaches_the_broker_and_answers_with_the_tab(gw_http):
    b, login = gw_http
    role = _text_roles()[0]
    r = login("root-b", superadmin=True).put(_url(), json={"enabled": True})
    assert r.status_code == 200, r.text
    assert b.switched == [(role, True)]
    slots = [s for rail in r.json()["rails"] for s in rail["slots"] if s["role"] == role]
    assert slots and all(s["cli_on"] is True for s in slots)


def test_a_superadmin_model_choice_reaches_the_broker_and_the_tab_carries_it(gw_http):
    """The model through the real routing: a body with only `model` is parsed, relayed as a
    model (not as a switch), and the tab answers with it on every slot of the role."""
    b, login = gw_http
    role = _text_roles()[0]
    root = login("root-c", superadmin=True)
    r = root.put(_url(), json={"model": HAIKU})
    assert r.status_code == 200, r.text
    assert b.switched == [(role, None, HAIKU)]
    body = r.json()
    slots = [s for rail in body["rails"] for s in rail["slots"] if s["role"] == role]
    assert slots and all(s["cli_model"] == HAIKU for s in slots)
    assert body["cli_models"] == [HAIKU]
    r = root.put(_url(), json={"model": None})
    assert r.status_code == 200 and b.switched[-1] == (role, None, None), r.text


def test_a_superadmin_switches_a_wired_vision_role_through_the_route(photo_http):
    """A vision slot's switch and model through the real routing, like a text slot's."""
    b, login = photo_http
    root = login("root-e", superadmin=True)
    url = f"/api/platform/admin/rails/{PHOTO_WIRED}/cli"
    r = root.put(url, json={"enabled": True})
    assert r.status_code == 200, r.text
    slot = next(s for rail in r.json()["rails"] for s in rail["slots"] if s["role"] == PHOTO_WIRED)
    assert (slot["kind"], slot["cli_on"], slot["cli_wired"]) == ("vision", True, True)
    r = root.put(url, json={"model": HAIKU})
    assert r.status_code == 200, r.text
    assert b.switched == [(PHOTO_WIRED, True), (PHOTO_WIRED, None, HAIKU)]
    # A plain admin is still refused before the broker is asked.
    r = login("admin-e", admin=True).put(url, json={"enabled": False})
    assert r.status_code == 403 and len(b.switched) == 2, r.text


def test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route(photo_http):
    b, login = photo_http
    b.cli.pop("vision")              # every broker before the stream-json image path
    root = login("root-g", superadmin=True)
    url = f"/api/platform/admin/rails/{PHOTO_WIRED}/cli"
    r = root.put(url, json={"enabled": True})
    assert r.status_code == 409, r.text
    assert gw.CLI_NO_VISION in r.json()["detail"]
    assert b.switched == []
    slot = next(s for rail in root.get("/api/platform/admin/rails").json()["rails"]
                for s in rail["slots"] if s["role"] == PHOTO_WIRED)
    assert slot["cli_unavailable"] == gw.CLI_NO_VISION and slot["cli_switchable"] is True
    r = root.put(url, json={"enabled": False})
    assert r.status_code == 200 and b.switched == [(PHOTO_WIRED, False)], r.text


def test_a_streaming_role_on_a_broker_that_does_not_stream_is_a_409_through_the_route(stream_http):
    b, login = stream_http
    b.cli.pop("streams")             # every broker before streamed CLI calls
    root = login("root-h", superadmin=True)
    url = f"/api/platform/admin/rails/{STREAM_WIRED}/cli"
    r = root.put(url, json={"enabled": True})
    assert r.status_code == 409, r.text
    assert gw.CLI_NO_STREAM in r.json()["detail"]
    assert b.switched == []
    slot = next(s for rail in root.get("/api/platform/admin/rails").json()["rails"]
                for s in rail["slots"] if s["role"] == STREAM_WIRED)
    assert slot["cli_unavailable"] == gw.CLI_NO_STREAM and slot["cli_switchable"] is True
    r = root.put(url, json={"enabled": False})
    assert r.status_code == 200 and b.switched == [(STREAM_WIRED, False)], r.text
    b.cli["streams"] = True
    r = root.put(url, json={"enabled": True})
    assert r.status_code == 200 and b.switched[-1] == (STREAM_WIRED, True), r.text


def test_an_image_role_is_a_400_through_the_route(photo_http):
    b, login = photo_http
    root = login("root-f", superadmin=True)
    for body in ({"enabled": True}, {"enabled": False}, {"model": HAIKU}):
        r = root.put(f"/api/platform/admin/rails/{PHOTO_ICON}/cli", json=body)
        assert r.status_code == 400 and "image slot" in r.json()["detail"], r.text
    assert b.switched == []


@pytest.mark.parametrize("body", [{"model": 5}, {}, {"model": ["x"]}],
                         ids=["number", "empty", "list"])
def test_a_model_body_that_is_not_a_string_or_null_is_a_422(gw_http, body):
    b, login = gw_http
    r = login("root-d", superadmin=True).put(_url(), json=body)
    assert r.status_code == 422, r.text
    assert b.switched == []
