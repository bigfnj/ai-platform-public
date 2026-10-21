"""The Rails tab's Claude switch: PUT /api/platform/admin/rails/{role}/cli and what the tab reads.

Which ROLES may send an owner's own call to the Claude CLI used to be one broker env var read at
startup. The switch moves that one decision into the admin pane, and only that one: who may use
the CLI (BROKER_CLI_OWNERS) and whether it runs at all (BROKER_CLI_ENABLED) stay broker env
settings that no route reaches. The tests pin the edges that would fail quietly:

  * a PLAIN admin flips nothing (super-admin only, like the broker tokens), and the broker is not
    even asked;
  * a vision role has a switch and a model choice like a text role's, under the same rules,
    and an image role has neither (it renders pictures, which no Claude model stands in for);
  * a vision role is switched ON only on a broker that says it sends images to the CLI, and
    the tab marks its slots with the route's reason before the click;
  * a streaming role (a slot's `stream` flag) is switched ON only on a broker that says it
    streams to the CLI, under the vision rule's shape, and the flag is held equal to each
    rail's own streamed calls, read from its source;
  * an unwired role cannot be switched ON: today it would change nothing, and the day its rail
    is wired it would start sending data off the box with nobody deciding it then;
  * the broker's refusals reach the admin in its own words, and a broker failure is a 502;
  * the static `cli` flag in RAIL_MODEL_SLOTS is held equal to each rail's own opt-in, its
    module-level CLI_SLOTS literal read from the rail's source, so the tab cannot call a
    slot wired that is not.

No live broker: the client is faked, or is the real BrokerClient over an httpx MockTransport.
Neutral account names only. The vision rules also run on a synthetic rail (PHOTO_LAB, added for
one test by conftest's add_rail), and the unwired text rule runs on another (NOTE_LAB), because
which real slots are wired moves with the rails.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from platform_core import BrokerClient, BrokerError
from platform_gateway_app import main as gw
from platform_gateway_app.auth import hash_password
from platform_gateway_app.config import GatewaySettings
from platform_gateway_app.models import User
from platform_gateway_app.rails_models import (
    CLI_SLOT_KINDS,
    CLI_WIRED_ROLES,
    IMAGE_SLOT_ROLES,
    RAIL_MODEL_SLOTS,
    STREAM_SLOT_ROLES,
    VISION_SLOT_ROLES,
    role_sets,
)

REPO = Path(__file__).resolve().parents[4]


def _text_slot_roles() -> set[str]:
    """Roles with at least one TEXT slot, read off the catalog itself when called, so a synthetic
    rail add_rail put there counts too. The gateway keeps no such set (the switch goes by
    CLI_SLOT_ROLES, text or vision); rails_models.CHAT_SLOT_ROLES was this, read only by tests."""
    return {s["role"] for slots in RAIL_MODEL_SLOTS.values() for s in slots if s["kind"] == "chat"}

HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
READY = {"ready": True, "blockers": [], "enabled_roles": [], "roles_source": "file",
         "roles_file_set": True, "roles_problem": "", "models": [HAIKU, SONNET, OPUS],
         "default_model": "", "role_models": {}}
#: The fake broker's `model` when a call leaves it as it is, like BrokerClient's.
_KEEP = object()


def mk(session, name: str, admin: bool = False, superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("x"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    session.add(u)
    session.commit()
    return u


class _Broker:
    """Stands in for BrokerClient: answers the Rails tab's one read and records every switch."""

    def __init__(self, cli=None, *, legacy=False):
        self.cli = dict(READY, **(cli or {}))
        self.legacy = legacy
        self.switched: list[tuple[str, bool]] = []

    async def roles(self, inventory=False):
        rows = [{"role": s["role"], "pattern": s["default"], "resolved": "m", "installed": True,
                 "upstream": "local"} for slots in RAIL_MODEL_SLOTS.values() for s in slots]
        out = {"roles": rows, "upstreams": [{"name": "local", "url": "", "healthy": True}],
               "models": {"local": []}}
        if not self.legacy:
            out["cli"] = dict(self.cli)
        return out

    async def disabled(self):
        return []

    async def set_cli_role(self, role, enabled=None, model=_KEEP):
        """Records (role, enabled) for a switch alone and (role, enabled, model) once a model is
        sent, and refuses a model off its allowlist the way the broker does (a 400)."""
        if model is _KEEP:
            self.switched.append((role, enabled))
        else:
            self.switched.append((role, enabled, model))
            if model is not None and model not in self.cli.get("models", []):
                raise BrokerError("broker 400", status=400, detail=(
                    f"{model!r} is not a Claude model this broker allows (BROKER_CLI_MODELS: "
                    f"{', '.join(self.cli.get('models', []))})"))
        if enabled is not None:
            on = set(self.cli["enabled_roles"])
            (on.add if enabled else on.discard)(role)
            self.cli["enabled_roles"] = sorted(on)
        if model is not _KEEP:
            chosen = dict(self.cli.get("role_models") or {})
            if model is None:
                chosen.pop(role, None)
            else:
                chosen[role] = model
            self.cli["role_models"] = chosen
        return {"cli": dict(self.cli)}


@pytest.fixture()
def broker(monkeypatch):
    b = _Broker()
    _install(monkeypatch, b)
    return b


# A synthetic rail: one WIRED vision slot, one unwired vision slot and an image slot, each on a
# role of its own. Neutral names that no real rail or role uses.
PHOTO_LAB = "syn-photo-lab"
PHOTO_WIRED, PHOTO_UNWIRED, PHOTO_ICON = "syn-photo-reader", "syn-photo-scanner", "syn-photo-icon"


def _photo_slot(slot: str, role: str, kind: str, **extra) -> dict:
    return {"slot": slot, "label": slot.title(), "role": role, "kind": kind, "env": "X",
            "default": "m", "description": "", **extra}


PHOTO_SLOTS = [_photo_slot("reader", PHOTO_WIRED, "vision", cli=True),
               _photo_slot("scanner", PHOTO_UNWIRED, "vision"),
               _photo_slot("icon", PHOTO_ICON, "image")]


@pytest.fixture()
def photo_lab(monkeypatch, add_rail):
    """The broker fake, installed with PHOTO_LAB in the catalog and among the enabled rails.
    Its cli block says it sends images to the CLI (`vision`), so a vision role here is held to
    the text rules alone; the tests that take `vision` away are the image check's own."""
    add_rail(PHOTO_LAB, PHOTO_SLOTS)
    return _install(monkeypatch, _Broker({"vision": True}))


# A synthetic rail with one UNWIRED text slot, for the wired rule on a text role. The real
# catalog's chat slots get wired one by one; a test that picked an unwired one from it would
# skip, green and unseen, the day the last one is wired.
NOTE_LAB = "syn-note-lab"
NOTE_UNWIRED = "syn-note-writer"
NOTE_SLOTS = [_photo_slot("writer", NOTE_UNWIRED, "chat")]


@pytest.fixture()
def note_lab(monkeypatch, add_rail):
    """The broker fake, installed with NOTE_LAB in the catalog and among the enabled rails."""
    add_rail(NOTE_LAB, NOTE_SLOTS)
    assert NOTE_UNWIRED in _text_slot_roles() and NOTE_UNWIRED not in CLI_WIRED_ROLES
    return _install(monkeypatch, _Broker())


def _install(monkeypatch, b):
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(gw.app.state, "broker", b, raising=False)
    monkeypatch.setattr(gw.app.state, "settings",
                        GatewaySettings(enabled_apps=tuple(RAIL_MODEL_SLOTS)), raising=False)
    return b


def _run(coro):
    return asyncio.run(coro)


def _put(role, enabled, admin):
    return _run(gw.admin_set_rail_cli(role, gw.RailCliBody(enabled=enabled), admin=admin))


def _put_model(role, model, admin, **also):
    """A PUT whose body SENDS `model` (None included), parsed as the route parses it."""
    body = gw.RailCliBody.model_validate({"model": model, **also})
    return _run(gw.admin_set_rail_cli(role, body, admin=admin))


def _slots(payload):
    return [s for r in payload["rails"] for s in r["slots"]]


def _wired_role() -> str:
    """A wired TEXT role that does not stream. A vision role's ON is also held to the broker's
    image path, and a streaming role's to its stream path, which the tests that take this role
    are not about (and a wired vision or streaming role could sort first)."""
    roles = sorted((CLI_WIRED_ROLES & _text_slot_roles()) - VISION_SLOT_ROLES - STREAM_SLOT_ROLES)
    if not roles:
        pytest.skip("no text slot in this tree's catalog is wired for the CLI")
    return roles[0]


# --- who may switch ---------------------------------------------------------------------------

def test_a_plain_admin_cannot_switch_and_the_broker_is_not_asked(session, broker):
    admin = mk(session, "admin-a", admin=True)
    with pytest.raises(HTTPException) as exc:
        _put(_wired_role(), True, admin)
    assert exc.value.status_code == 403 and "super-admin" in exc.value.detail
    assert broker.switched == [], "the broker was called before the guard ran"


def test_a_superadmin_switches_one_role_and_gets_the_fresh_tab(session, broker):
    root = mk(session, "owner-one", superadmin=True)
    role = _wired_role()
    out = _put(role, True, root)
    assert broker.switched == [(role, True)]
    assert all(s["cli_on"] is (s["role"] == role) for s in _slots(out))
    assert out["cli_editable"] is True


# --- which roles have a switch ----------------------------------------------------------------

def test_a_role_no_rail_slot_uses_is_a_404(session, broker):
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put("chat", True, root)          # a generic class role, not a rail slot's
    assert exc.value.status_code == 404
    assert broker.switched == []


def _refused_as_an_image_role(call) -> None:
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 400, exc.value.detail
    assert "no text or vision slot" in exc.value.detail and "image slot" in exc.value.detail


def test_an_image_role_has_no_switch(session, broker):
    """An image role renders pictures through the media worker: no Claude model stands in for
    it, so it has no switch, ON or OFF, and the broker is not asked."""
    if not IMAGE_SLOT_ROLES:
        pytest.skip("no image slot in this tree's catalog")
    root = mk(session, "owner-one", superadmin=True)
    role = sorted(IMAGE_SLOT_ROLES)[0]
    _refused_as_an_image_role(lambda: _put(role, True, root))
    _refused_as_an_image_role(lambda: _put(role, False, root))
    assert broker.switched == []


def test_each_vision_role_in_the_catalog_has_a_switch(session, broker):
    """The real catalog's vision roles, whatever is wired today: each one takes a switch OFF
    (always allowed), a wired one takes it ON and an unwired one is the 409 an unwired text role
    gets. None is the image role's 400. The text-only rule this replaced answered 400 to all.
    A wired one's ON also needs a broker that says it sends images to the CLI."""
    if not VISION_SLOT_ROLES:
        pytest.skip("no vision slot in this tree's catalog")
    root = mk(session, "owner-one", superadmin=True)
    for role in sorted(VISION_SLOT_ROLES):
        _put(role, False, root)
        assert broker.switched[-1] == (role, False)
        if role in CLI_WIRED_ROLES:
            broker.cli["vision"] = True
            _put(role, True, root)
            assert broker.switched[-1] == (role, True)
            broker.cli.pop("vision")
            with pytest.raises(HTTPException) as exc:
                _put(role, True, root)
            assert exc.value.status_code == 409 and gw.CLI_NO_VISION in exc.value.detail
        else:
            with pytest.raises(HTTPException) as exc:
                _put(role, True, root)
            assert exc.value.status_code == 409, (role, exc.value.detail)


def test_a_wired_vision_role_switches_on_like_a_text_role(session, photo_lab):
    root = mk(session, "owner-one", superadmin=True)
    out = _put(PHOTO_WIRED, True, root)
    assert photo_lab.switched == [(PHOTO_WIRED, True)]
    slot = next(s for s in _slots(out) if s["role"] == PHOTO_WIRED)
    assert (slot["kind"], slot["cli_on"], slot["cli_wired"]) == ("vision", True, True)
    # And off again, like any role.
    out = _put(PHOTO_WIRED, False, root)
    assert photo_lab.switched[-1] == (PHOTO_WIRED, False)
    assert next(s for s in _slots(out) if s["role"] == PHOTO_WIRED)["cli_on"] is False


def test_a_plain_admin_cannot_switch_a_vision_role(session, photo_lab):
    admin = mk(session, "admin-a", admin=True)
    for call in (lambda: _put(PHOTO_WIRED, True, admin),
                 lambda: _put_model(PHOTO_WIRED, HAIKU, admin)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403 and "super-admin" in exc.value.detail
    assert photo_lab.switched == []


def test_an_unwired_vision_role_cannot_be_switched_on_but_can_be_switched_off(session, photo_lab):
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(PHOTO_UNWIRED, True, root)
    assert exc.value.status_code == 409 and "no rail passes who is asking" in exc.value.detail
    assert photo_lab.switched == []
    _put(PHOTO_UNWIRED, False, root)
    assert photo_lab.switched == [(PHOTO_UNWIRED, False)]


def test_a_vision_role_takes_a_claude_model_under_the_same_rules(session, photo_lab):
    """Wired: a model is chosen and every slot on the role carries it. Unwired: a model is the
    409, and clearing one is allowed."""
    root = mk(session, "owner-one", superadmin=True)
    out = _put_model(PHOTO_WIRED, HAIKU, root)
    assert photo_lab.switched == [(PHOTO_WIRED, None, HAIKU)]
    assert next(s for s in _slots(out) if s["role"] == PHOTO_WIRED)["cli_model"] == HAIKU
    with pytest.raises(HTTPException) as exc:
        _put_model(PHOTO_UNWIRED, HAIKU, root)
    assert exc.value.status_code == 409 and "no rail passes who is asking" in exc.value.detail
    _put_model(PHOTO_UNWIRED, None, root)
    assert photo_lab.switched[-1] == (PHOTO_UNWIRED, None, None)


def test_an_image_role_beside_a_vision_one_has_no_switch_nor_model(session, photo_lab):
    root = mk(session, "owner-one", superadmin=True)
    _refused_as_an_image_role(lambda: _put(PHOTO_ICON, True, root))
    _refused_as_an_image_role(lambda: _put(PHOTO_ICON, False, root))
    _refused_as_an_image_role(lambda: _put_model(PHOTO_ICON, HAIKU, root))
    _refused_as_an_image_role(lambda: _put_model(PHOTO_ICON, None, root))
    assert photo_lab.switched == []


def test_a_vision_slot_carries_the_switch_fields_like_a_text_slot(session, photo_lab):
    """The Rails payload: a vision slot's cli_on, cli_wired, cli_ready and cli_model are worked
    out exactly as a text slot's are, from the broker's block and the catalog's flag."""
    photo_lab.cli.update(enabled_roles=[PHOTO_WIRED], role_models={PHOTO_WIRED: OPUS})
    out = _run(gw._rails_payload(set()))
    got = {s["role"]: (s["cli_on"], s["cli_wired"], s["cli_ready"], s["cli_model"])
           for s in _slots(out) if s["role"] in (PHOTO_WIRED, PHOTO_UNWIRED)}
    assert got == {PHOTO_WIRED: (True, True, True, OPUS),
                   PHOTO_UNWIRED: (False, False, True, None)}
    photo_lab.cli.update(blockers=["disabled"], ready=False)
    out = _run(gw._rails_payload(set()))
    assert {s["cli_ready"] for s in _slots(out) if s["kind"] == "vision"} == {False}


# --- a vision role's ON needs a broker that sends images to the CLI --------------------------
#
# The gateway cannot see the broker's code, but the broker says it in its cli block (`vision`,
# with the stream-json image path). Before that broker runs, every call with an image falls
# back to the local model (vision_unsupported) while the rail's chip reads Claude for the
# owner. So a vision role cannot be switched ON there, with the reason; switching it OFF is
# always allowed, and a text role is not held to it.

def test_a_vision_role_cannot_be_switched_on_on_a_broker_that_cannot_send_images(session,
                                                                                 photo_lab):
    """No `vision` key (every broker before the image path), false, and values that are only
    truthy: each is the 409 with the reason, and the broker is not asked to switch."""
    root = mk(session, "owner-one", superadmin=True)
    for said in ({}, {"vision": False}, {"vision": "true"}, {"vision": 1}):
        photo_lab.cli.pop("vision", None)
        photo_lab.cli.update(said)
        for call in (lambda: _put(PHOTO_WIRED, True, root),
                     lambda: _put_model(PHOTO_WIRED, HAIKU, root, enabled=True)):
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 409, (said, exc.value.detail)
            assert gw.CLI_NO_VISION in exc.value.detail, exc.value.detail
            assert f"@{PHOTO_WIRED}" in exc.value.detail
    assert photo_lab.switched == []


def test_switching_a_vision_role_off_and_choosing_its_model_need_no_images(session, photo_lab):
    """On a broker that does not send images, a vision role that is on (BROKER_CLI_ROLES, say)
    can still be switched OFF, and its model chosen or cleared: neither sends anything off the
    box that was not going already."""
    photo_lab.cli.update(vision=False, enabled_roles=[PHOTO_WIRED])
    root = mk(session, "owner-one", superadmin=True)
    _put_model(PHOTO_WIRED, HAIKU, root)
    _put_model(PHOTO_WIRED, None, root)
    out = _put(PHOTO_WIRED, False, root)
    assert photo_lab.switched == [(PHOTO_WIRED, None, HAIKU), (PHOTO_WIRED, None, None),
                                  (PHOTO_WIRED, False)]
    assert next(s for s in _slots(out) if s["role"] == PHOTO_WIRED)["cli_on"] is False


def test_a_text_role_is_not_held_to_the_image_check(session, broker):
    role = _wired_role()
    root = mk(session, "owner-one", superadmin=True)
    for said in ({}, {"vision": False}):
        broker.cli.pop("vision", None)
        broker.cli.update(said)
        _put(role, True, root)
        assert broker.switched[-1] == (role, True), said


def test_a_capability_read_that_fails_refuses_and_switches_nothing(session, monkeypatch,
                                                                   add_rail):
    """The gateway asks before the switch. When it cannot tell, the answer is no: a guess
    that lets the switch through is the one that would mislead."""
    add_rail(PHOTO_LAB, PHOTO_SLOTS)
    b = _install(monkeypatch, _ReadFailsAfterSwitch({"vision": True}))
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(PHOTO_WIRED, True, root)
    assert exc.value.status_code == 502, exc.value.detail
    assert "could not read whether the broker can send images" in exc.value.detail
    assert b.switched == []


def test_the_route_reads_the_image_path_from_the_brokers_inventory_read(session, monkeypatch,
                                                                       add_rail):
    """The real BrokerClient over a mock transport. The broker sends its cli block, and so
    `vision`, only on GET /v1/roles?inventory=true: a lighter read would never see it, and every
    vision ON would be refused. The read comes before the switch."""
    add_rail(PHOTO_LAB, PHOTO_SLOTS)
    seen: list[httpx.Request] = []
    on = {**READY, "vision": True, "enabled_roles": [PHOTO_WIRED]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"cli": on})
        if request.url.path == "/v1/disabled":
            return httpx.Response(200, json={"disabled": []})
        if request.url.params.get("inventory") == "true":
            return httpx.Response(200, json={"roles": [], "upstreams": [], "models": {},
                                             "cli": {**READY, "vision": True}})
        return httpx.Response(200, json={"roles": []})

    _install(monkeypatch, BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler))))
    root = mk(session, "owner-one", superadmin=True)
    _put(PHOTO_WIRED, True, root)
    calls = [(r.method, r.url.path, r.url.params.get("inventory")) for r in seen]
    assert calls[:2] == [("GET", "/v1/roles", "true"),
                         ("PUT", f"/v1/cli/roles/{PHOTO_WIRED}", None)], calls


def test_the_tab_marks_each_slot_on_a_vision_role_with_the_routes_reason(session, monkeypatch,
                                                                       add_rail):
    """`cli_unavailable` on every slot: the route's own sentence on each slot whose ROLE backs
    a vision slot (the switch is per role, so a text slot on such a role is marked too) while
    the broker does not say it sends images, else None."""
    add_rail(PHOTO_LAB, PHOTO_SLOTS)
    add_rail("syn-photo-notes", [_photo_slot("notes", PHOTO_WIRED, "chat", cli=True)])
    # A broker that streams to the CLI, so the streaming rule marks nothing here.
    b = _install(monkeypatch, _Broker({"streams": True}))
    out = _run(gw._rails_payload(set()))
    assert out["cli"]["vision"] is False
    for s in _slots(out):
        want = gw.CLI_NO_VISION if s["role"] in VISION_SLOT_ROLES else None
        assert s["cli_unavailable"] == want, s
    by = {(s["role"], s["kind"]): s["cli_unavailable"] for s in _slots(out)}
    assert by[(PHOTO_WIRED, "vision")] == by[(PHOTO_UNWIRED, "vision")] == gw.CLI_NO_VISION
    assert by[(PHOTO_WIRED, "chat")] == gw.CLI_NO_VISION, "a text slot on the vision role"
    assert by[(PHOTO_ICON, "image")] is None
    assert by[(_wired_role(), "chat")] is None
    # The same words the route refuses with.
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(PHOTO_WIRED, True, root)
    assert exc.value.status_code == 409 and by[(PHOTO_WIRED, "vision")] in exc.value.detail
    # On a broker that says it sends images, nothing is marked.
    b.cli["vision"] = True
    out = _run(gw._rails_payload(set()))
    assert out["cli"]["vision"] is True
    assert {s["cli_unavailable"] for s in _slots(out)} == {None}


# --- a streaming role's ON needs a broker that streams to the CLI -------------------------------
#
# The image rule's shape, for streamed answers. A broker from before streamed CLI calls answers
# every /v1/chat/stream locally, named or not, while the rail's chip reads Claude for the owner;
# the broker says it can in its cli block (`streams`). Which roles stream is the catalog's
# `stream` flag, held to each rail's source below. A synthetic rail again (STREAM_LAB), since
# the real streaming slots move with the rails; a second synthetic rail shares its role without
# the flag, the way a rail can borrow another's role, and is held to the check all the same.

STREAM_LAB = "syn-stream-lab"
STREAM_WIRED, STREAM_PHOTO = "syn-stream-writer", "syn-stream-reader"
STREAM_SLOTS = [_photo_slot("writer", STREAM_WIRED, "chat", cli=True, stream=True),
                _photo_slot("reader", STREAM_PHOTO, "vision", cli=True, stream=True)]
STREAM_BORROWER = [_photo_slot("borrower", STREAM_WIRED, "chat", cli=True)]


@pytest.fixture()
def stream_lab(monkeypatch, add_rail):
    """The broker fake, installed with STREAM_LAB (and the rail that borrows its role) in the
    catalog. Its cli block says it streams to the CLI and sends it images, so a role here is held
    to the other rules alone; the tests that take `streams` away are the stream check's own."""
    add_rail(STREAM_LAB, STREAM_SLOTS)
    add_rail("syn-stream-borrower", STREAM_BORROWER)
    return _install(monkeypatch, _Broker({"streams": True, "vision": True}))


def test_a_streaming_role_cannot_be_switched_on_on_a_broker_that_does_not_stream(session,
                                                                                  stream_lab):
    """No `streams` key (every broker before streamed CLI calls), false, and values that are only
    truthy: each is the 409 with the reason, and the broker is not asked to switch."""
    root = mk(session, "owner-one", superadmin=True)
    for said in ({}, {"streams": False}, {"streams": "true"}, {"streams": 1}):
        stream_lab.cli.pop("streams", None)
        stream_lab.cli.update(said)
        for call in (lambda: _put(STREAM_WIRED, True, root),
                     lambda: _put_model(STREAM_WIRED, HAIKU, root, enabled=True)):
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 409, (said, exc.value.detail)
            assert gw.CLI_NO_STREAM in exc.value.detail, exc.value.detail
            assert f"@{STREAM_WIRED}" in exc.value.detail
    assert stream_lab.switched == []


def test_a_streaming_role_switches_on_on_a_broker_that_streams(session, stream_lab):
    root = mk(session, "owner-one", superadmin=True)
    out = _put(STREAM_WIRED, True, root)
    assert stream_lab.switched == [(STREAM_WIRED, True)]
    on = {s["slot"] for s in _slots(out) if s["cli_on"]}
    assert on == {"writer", "borrower"}, "the switch is the role's, the borrower's slot too"


def test_switching_a_streaming_role_off_and_choosing_its_model_need_no_stream(session,
                                                                            stream_lab):
    """On a broker that does not stream, a streaming role that is on (BROKER_CLI_ROLES, say) can
    still be switched OFF, and its model chosen or cleared."""
    stream_lab.cli.pop("streams")
    stream_lab.cli.update(enabled_roles=[STREAM_WIRED])
    root = mk(session, "owner-one", superadmin=True)
    _put_model(STREAM_WIRED, HAIKU, root)
    _put_model(STREAM_WIRED, None, root)
    out = _put(STREAM_WIRED, False, root)
    assert stream_lab.switched == [(STREAM_WIRED, None, HAIKU), (STREAM_WIRED, None, None),
                                   (STREAM_WIRED, False)]
    assert not any(s["cli_on"] for s in _slots(out) if s["role"] == STREAM_WIRED)


def test_a_role_that_does_not_stream_is_not_held_to_the_stream_check(session, broker):
    role = _wired_role()
    root = mk(session, "owner-one", superadmin=True)
    for said in ({}, {"streams": False}):
        broker.cli.pop("streams", None)
        broker.cli.update(said)
        _put(role, True, root)
        assert broker.switched[-1] == (role, True), said


def test_a_stream_read_that_fails_refuses_and_switches_nothing(session, monkeypatch, add_rail):
    add_rail(STREAM_LAB, STREAM_SLOTS)
    b = _install(monkeypatch, _ReadFailsAfterSwitch({"streams": True}))
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(STREAM_WIRED, True, root)
    assert exc.value.status_code == 502, exc.value.detail
    assert "could not read whether the broker streams to Claude" in exc.value.detail
    assert b.switched == []


def test_the_route_reads_the_stream_path_from_the_brokers_inventory_read(session, monkeypatch,
                                                                         add_rail):
    """The real BrokerClient over a mock transport, as for the image check: the broker sends
    `streams` only on GET /v1/roles?inventory=true, and the read comes before the switch."""
    add_rail(STREAM_LAB, STREAM_SLOTS[:1])
    seen: list[httpx.Request] = []
    on = {**READY, "streams": True, "enabled_roles": [STREAM_WIRED]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"cli": on})
        if request.url.path == "/v1/disabled":
            return httpx.Response(200, json={"disabled": []})
        if request.url.params.get("inventory") == "true":
            return httpx.Response(200, json={"roles": [], "upstreams": [], "models": {},
                                             "cli": {**READY, "streams": True}})
        return httpx.Response(200, json={"roles": []})

    _install(monkeypatch, BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler))))
    root = mk(session, "owner-one", superadmin=True)
    _put(STREAM_WIRED, True, root)
    calls = [(r.method, r.url.path, r.url.params.get("inventory")) for r in seen]
    assert calls[:2] == [("GET", "/v1/roles", "true"),
                         ("PUT", f"/v1/cli/roles/{STREAM_WIRED}", None)], calls


def test_a_role_that_reads_images_and_streams_is_held_to_both(session, stream_lab):
    """Each check in its own words: the image one first, then the stream one."""
    root = mk(session, "owner-one", superadmin=True)
    for said, words in (({"vision": True}, gw.CLI_NO_STREAM),
                        ({"streams": True}, gw.CLI_NO_VISION),
                        ({}, gw.CLI_NO_VISION)):
        stream_lab.cli.pop("vision")
        stream_lab.cli.pop("streams")
        stream_lab.cli.update(said)
        with pytest.raises(HTTPException) as exc:
            _put(STREAM_PHOTO, True, root)
        assert exc.value.status_code == 409 and words in exc.value.detail, (said, exc.value.detail)
        stream_lab.cli.update(vision=True, streams=True)
    _put(STREAM_PHOTO, True, root)
    assert stream_lab.switched == [(STREAM_PHOTO, True)]


def test_the_tab_marks_each_slot_on_a_streaming_role_with_the_routes_reason(session,
                                                                          stream_lab):
    """`cli_unavailable` on every slot whose ROLE some slot marks as streamed, the borrower's
    unflagged slot included, while the broker does not say it streams; the image reason first on
    a role that is both; nothing on a broker that does both."""
    stream_lab.cli.pop("streams")
    out = _run(gw._rails_payload(set()))
    assert out["cli"]["streams"] is False
    by = {s["slot"]: s["cli_unavailable"] for s in _slots(out)
          if s["role"] in (STREAM_WIRED, STREAM_PHOTO)}
    assert by == {"writer": gw.CLI_NO_STREAM, "borrower": gw.CLI_NO_STREAM,
                  "reader": gw.CLI_NO_STREAM}, by
    for s in _slots(out):
        if s["role"] not in STREAM_SLOT_ROLES:
            assert s["cli_unavailable"] != gw.CLI_NO_STREAM, s
    # The same words the route refuses with.
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(STREAM_WIRED, True, root)
    assert exc.value.status_code == 409 and by["writer"] in exc.value.detail
    # A role that is both: the image reason while the broker sends no images.
    stream_lab.cli.pop("vision")
    out = _run(gw._rails_payload(set()))
    reader = next(s for s in _slots(out) if s["role"] == STREAM_PHOTO)
    assert reader["cli_unavailable"] == gw.CLI_NO_VISION
    # On a broker that streams and sends images, nothing is marked.
    stream_lab.cli.update(streams=True, vision=True)
    out = _run(gw._rails_payload(set()))
    assert out["cli"]["streams"] is True
    assert {s["cli_unavailable"] for s in _slots(out)} == {None}


def test_each_streaming_role_in_the_catalog_is_held_to_the_stream_check(session, broker):
    """The real catalog's streaming roles, whatever streams today: OFF is always taken; ON is
    the 409 on a broker that does not say it streams, and is taken on one that does."""
    if not STREAM_SLOT_ROLES:
        pytest.skip("no slot in this tree's catalog streams")
    root = mk(session, "owner-one", superadmin=True)
    broker.cli["vision"] = True
    for role in sorted(STREAM_SLOT_ROLES):
        _put(role, False, root)
        assert broker.switched[-1] == (role, False)
        broker.cli.pop("streams", None)
        with pytest.raises(HTTPException) as exc:
            _put(role, True, root)
        assert exc.value.status_code == 409 and gw.CLI_NO_STREAM in exc.value.detail, role
        broker.cli["streams"] = True
        _put(role, True, root)
        assert broker.switched[-1] == (role, True)


def test_a_slot_has_a_switch_on_the_tab_exactly_where_the_route_takes_one(session, photo_lab):
    """`cli_switchable` is the shell's one source for drawing the switch row (rails_view.ts
    hasCliSwitch). It is held to the ROUTE, not to a copy of its kinds: a slot says it has a
    switch exactly when the route takes its role's OFF, and has none exactly when the route
    answers that role with the image slot's 400."""
    root = mk(session, "owner-one", superadmin=True)
    out = _run(gw._rails_payload(set()))
    for s in _slots(out):
        try:
            _put(s["role"], False, root)
            taken = True
        except HTTPException as refused:
            assert refused.status_code == 400, refused.detail
            taken = False
        assert s["cli_switchable"] is taken, s
    kinds: dict[str, set] = {}
    for s in _slots(out):
        kinds.setdefault(s["kind"], set()).add(s["cli_switchable"])
    assert kinds == {"chat": {True}, "vision": {True}, "image": {False}}


def test_an_unwired_role_cannot_be_switched_on_but_can_be_switched_off(session, note_lab):
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(NOTE_UNWIRED, True, root)
    assert exc.value.status_code == 409 and "no rail passes who is asking" in exc.value.detail
    assert note_lab.switched == []
    _put(NOTE_UNWIRED, False, root)
    assert note_lab.switched == [(NOTE_UNWIRED, False)]


def test_the_body_is_a_json_boolean_only():
    """As at the broker: a switch that parses "false" as a value is one somebody mistypes."""
    with pytest.raises(Exception):
        gw.RailCliBody(enabled="false")
    with pytest.raises(Exception):
        gw.RailCliBody(enabled=0)


# --- the broker's answers ---------------------------------------------------------------------

def _real_client(status: int, body) -> BrokerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("status", [400, 409])
def test_a_broker_refusal_reaches_the_admin_in_its_own_words(session, monkeypatch, status):
    said = ("BROKER_CLI_ROLES_FILE is not set, so the eligible roles come from "
            "BROKER_CLI_ROLES and cannot be switched here: set it on the broker")
    _install(monkeypatch, _real_client(status, {"detail": said}))
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(_wired_role(), True, root)
    assert (exc.value.status_code, exc.value.detail) == (status, said)


@pytest.mark.parametrize("status,words", [(500, "broker unreachable"), (404, "older than")])
def test_a_broker_failure_is_a_502(session, monkeypatch, status, words):
    _install(monkeypatch, _real_client(status, {"detail": "boom"}))
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put(_wired_role(), False, root)
    assert exc.value.status_code == 502 and words in exc.value.detail


class _ReadFailsAfterSwitch(_Broker):
    """The broker takes the switch, then the tab's re-read fails (a transient broker error)."""

    async def roles(self, inventory=False):
        raise BrokerError("connection reset", status=None)


def test_a_switch_that_landed_is_reported_even_when_the_tab_cannot_be_reread(session,
                                                                            monkeypatch):
    """The switch is LIVE once the broker wrote it. Answering 502 because the follow-up read
    failed told the admin it had failed, the shell kept the old checkbox, and owner calls on
    the role went to Claude meanwhile. The reply is now the switch's own: the broker's cli
    block, normalised like the tab's, and a sentence saying the tab could not be re-read."""
    b = _install(monkeypatch, _ReadFailsAfterSwitch())
    root = mk(session, "owner-one", superadmin=True)
    role = _wired_role()
    out = _put(role, True, root)
    assert b.switched == [(role, True)]
    assert "rails" not in out
    assert out["cli"]["enabled_roles"] == [role]
    assert out["cli"]["switch_blockers"] == [] and out["cli"]["roles_source"] == "file"
    assert out["cli_editable"] is True
    assert "could not be re-read" in out["reload_error"]
    assert "broker unreachable" in out["reload_error"]


def test_the_client_puts_one_role_with_a_boolean(session, monkeypatch):
    """The real BrokerClient end to end: per role, never the whole set."""
    seen: list[httpx.Request] = []
    role = _wired_role()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"cli": READY})
        if request.url.path == "/v1/disabled":
            return httpx.Response(200, json={"disabled": []})
        return httpx.Response(200, json={"roles": [], "upstreams": [], "models": {},
                                         "cli": {**READY, "enabled_roles": [role]}})

    _install(monkeypatch, BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler))))
    root = mk(session, "owner-one", superadmin=True)
    out = _put(role, True, root)
    put = next(r for r in seen if r.method == "PUT")
    assert put.url.path == f"/v1/cli/roles/{role}"
    assert json.loads(put.read()) == {"enabled": True}
    assert out["cli"]["enabled_roles"] == [role]


# --- what the tab reads -----------------------------------------------------------------------

def test_every_slot_carries_the_switch_fields(session, monkeypatch):
    role = _wired_role()
    _install(monkeypatch, _Broker({"enabled_roles": [role]}))
    root = mk(session, "owner-one", superadmin=True)
    out = _run(gw.admin_rails(admin=root))
    slots = _slots(out)
    assert slots
    for s in slots:
        assert s["cli_on"] is (s["role"] == role), s
        assert s["cli_wired"] is _catalog_flag(s), s
        assert s["cli_ready"] is True, s
        assert "cli_model" in s, s
    assert out["cli"]["roles_source"] == "file" and out["cli"]["roles_file_set"] is True


def _catalog_flag(slot) -> bool:
    return any(c["slot"] == slot["slot"] and c["role"] == slot["role"] and c.get("cli") is True
               for slots in RAIL_MODEL_SLOTS.values() for c in slots)


def test_cli_editable_is_true_for_a_superadmin_only(session, broker):
    root = mk(session, "owner-one", superadmin=True)
    plain = mk(session, "admin-a", admin=True)
    assert _run(gw.admin_rails(admin=root))["cli_editable"] is True
    assert _run(gw.admin_rails(admin=plain))["cli_editable"] is False
    assert _run(gw._rails_payload(set()))["cli_editable"] is False


def test_a_model_apply_answers_with_the_same_cli_editable(session, broker, monkeypatch):
    """The model PUT answers with the whole tab too. Built without the admin, it would come
    back cli_editable false and grey out every switch for the super-admin who just clicked
    Apply."""
    async def set_role(role, model):
        return {"role": role, "model": model}

    monkeypatch.setattr(broker, "set_role", set_role, raising=False)
    root = mk(session, "owner-one", superadmin=True)
    role = sorted(_text_slot_roles())[0]
    out = _run(gw.admin_set_rail_model(role, gw.RailModelBody(model="m"), admin=root))
    assert out["cli_editable"] is True


@pytest.mark.parametrize("blockers,ready", [
    ([], True),
    (["cli_all_roles_off"], True),         # what the switch itself fixes
    (["no_roles_configured"], True),
    (["disabled"], False),                 # BROKER_CLI_ENABLED is env-only: not the tab's to fix
    (["cli_roles_file_invalid"], False),
    (["cli_all_roles_off", "token_unreadable"], False),
])
def test_a_switch_is_ready_apart_from_which_roles_are_on(session, monkeypatch, blockers, ready):
    """Switching the last role off makes the broker not ready (cli_all_roles_off). If that
    disabled the switches, nothing on the tab could switch a role back on."""
    _install(monkeypatch, _Broker({"blockers": blockers, "ready": not blockers}))
    out = _run(gw._rails_payload(set()))
    assert {s["cli_ready"] for s in _slots(out)} == {ready}
    assert out["cli"]["blockers"] == blockers


def test_a_broker_older_than_the_switch_shows_every_switch_unavailable(session, monkeypatch):
    _install(monkeypatch, _Broker(legacy=True))
    out = _run(gw._rails_payload(set()))
    assert out["cli"]["switch_blockers"] == ["broker_has_no_cli_switch"]
    assert {s["cli_ready"] for s in _slots(out)} == {False}
    assert {s["cli_on"] for s in _slots(out)} == {False}
    # Nor any model to choose.
    assert out["cli_models"] == [] and {s["cli_model"] for s in _slots(out)} == {None}


# --- the Claude model per role ----------------------------------------------------------------
#
# The same route chooses which Claude model a role's CLI calls use. The allowlist is the
# broker's (BROKER_CLI_MODELS), so the broker judges the id and its 400 is relayed; the gateway
# keeps the super-admin rule, the text-slot rule and the wired rule, as for switching ON.

def test_a_plain_admin_cannot_choose_a_model_and_the_broker_is_not_asked(session, broker):
    admin = mk(session, "admin-a", admin=True)
    with pytest.raises(HTTPException) as exc:
        _put_model(_wired_role(), HAIKU, admin)
    assert exc.value.status_code == 403 and "super-admin" in exc.value.detail
    assert broker.switched == []


def test_a_superadmin_chooses_a_model_and_every_slot_on_the_role_carries_it(session, broker):
    root = mk(session, "owner-one", superadmin=True)
    role = _wired_role()
    out = _put_model(role, HAIKU, root)
    assert broker.switched == [(role, None, HAIKU)]
    assert all(s["cli_model"] == (HAIKU if s["role"] == role else None) for s in _slots(out))
    assert out["cli_models"] == [HAIKU, SONNET, OPUS]
    assert out["cli"]["role_models"] == {role: HAIKU} and out["cli"]["default_model"] == ""
    # The switch is untouched by a model choice, and null clears the choice.
    assert all(s["cli_on"] is False for s in _slots(out))
    out = _put_model(role, None, root)
    assert broker.switched[-1] == (role, None, None)
    assert {s["cli_model"] for s in _slots(out)} == {None}


def test_a_switch_and_a_model_can_go_in_one_body(session, broker):
    root = mk(session, "owner-one", superadmin=True)
    role = _wired_role()
    out = _put_model(role, OPUS, root, enabled=True)
    assert broker.switched == [(role, True, OPUS)]
    slot = next(s for s in _slots(out) if s["role"] == role)
    assert (slot["cli_on"], slot["cli_model"]) == (True, OPUS)


def test_a_model_off_the_allowlist_is_the_brokers_400_in_its_own_words(session, broker,
                                                                       monkeypatch):
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put_model(_wired_role(), "claude-x-9", root)
    assert exc.value.status_code == 400 and "BROKER_CLI_MODELS" in exc.value.detail
    said = "'claude-x-9' is not a Claude model this broker allows (BROKER_CLI_MODELS: none)"
    _install(monkeypatch, _real_client(400, {"detail": said}))
    with pytest.raises(HTTPException) as exc:
        _put_model(_wired_role(), "claude-x-9", root)
    assert (exc.value.status_code, exc.value.detail) == (400, said)


def test_an_unwired_role_cannot_be_given_a_model_but_can_have_it_cleared(session, note_lab):
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put_model(NOTE_UNWIRED, HAIKU, root)
    assert exc.value.status_code == 409 and "no rail passes who is asking" in exc.value.detail
    assert note_lab.switched == []
    _put_model(NOTE_UNWIRED, None, root)
    assert note_lab.switched == [(NOTE_UNWIRED, None, None)]


def test_an_image_role_has_no_model_choice(session, broker):
    if not IMAGE_SLOT_ROLES:
        pytest.skip("no image slot in this tree's catalog")
    root = mk(session, "owner-one", superadmin=True)
    _refused_as_an_image_role(lambda: _put_model(sorted(IMAGE_SLOT_ROLES)[0], HAIKU, root))
    assert broker.switched == []


def test_the_body_takes_a_model_string_or_null_and_something_to_change():
    assert gw.RailCliBody.model_validate({"model": None}).model_fields_set == {"model"}
    for bad in ({"model": 5}, {"model": [HAIKU]}, {"model": "x" * 129}, {}, {"enabled": None}):
        with pytest.raises(Exception):
            gw.RailCliBody.model_validate(bad)


def test_a_broker_that_requires_enabled_on_every_call_is_called_older(session, monkeypatch):
    _install(monkeypatch, _real_client(422, {"detail": [{"msg": "Field required"}]}))
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put_model(_wired_role(), HAIKU, root)
    assert exc.value.status_code == 502 and "older than this gateway" in exc.value.detail


class _DropsTheModel(_Broker):
    """A broker from before the model choice: it applies the switch and ignores `model`, and its
    cli block has no `models`."""

    async def set_cli_role(self, role, enabled=None, model=_KEEP):
        self.switched.append((role, enabled, model))
        return {"cli": {k: v for k, v in self.cli.items()
                        if k not in ("models", "role_models", "default_model")}}


def test_a_broker_that_drops_the_model_is_not_reported_as_having_taken_it(session,
                                                                          monkeypatch):
    b = _install(monkeypatch, _DropsTheModel())
    root = mk(session, "owner-one", superadmin=True)
    with pytest.raises(HTTPException) as exc:
        _put_model(_wired_role(), HAIKU, root, enabled=True)
    assert exc.value.status_code == 502 and "not the model" in exc.value.detail
    assert len(b.switched) == 1


def test_the_slots_carry_only_well_formed_models_from_the_broker(session, monkeypatch):
    role = _wired_role()
    other = next(r for r in sorted(_text_slot_roles()) if r != role)
    _install(monkeypatch, _Broker({"role_models": {role: HAIKU, other: 5}}))
    out = _run(gw._rails_payload(set()))
    for s in _slots(out):
        assert s["cli_model"] == (HAIKU if s["role"] == role else None), s
    assert out["cli"]["role_models"] == {role: HAIKU}


def test_a_model_that_landed_is_reported_when_the_tab_cannot_be_reread(session, monkeypatch):
    b = _install(monkeypatch, _ReadFailsAfterSwitch())
    root = mk(session, "owner-one", superadmin=True)
    role = _wired_role()
    out = _put_model(role, SONNET, root)
    assert b.switched == [(role, None, SONNET)] and "rails" not in out
    assert out["cli"]["role_models"] == {role: SONNET}
    assert out["cli_models"] == [HAIKU, SONNET, OPUS]
    assert "could not be re-read" in out["reload_error"]


def test_the_client_puts_the_model_as_given(session, monkeypatch):
    seen: list[httpx.Request] = []
    role = _wired_role()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"cli": READY})
        if request.url.path == "/v1/disabled":
            return httpx.Response(200, json={"disabled": []})
        return httpx.Response(200, json={"roles": [], "upstreams": [], "models": {},
                                         "cli": READY})

    _install(monkeypatch, BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler))))
    root = mk(session, "owner-one", superadmin=True)
    _put_model(role, HAIKU, root)
    _put_model(role, None, root)
    puts = [json.loads(r.read()) for r in seen if r.method == "PUT"]
    assert puts == [{"model": HAIKU}, {"model": None}]


# --- the static flag is the rails' own opt-in --------------------------------------------------
#
# RAIL_MODEL_SLOTS' `cli` says "this rail passes who is asking for this slot". The gateway cannot
# see a rail's call sites, but a rail that passes requester also opts the slot's chip in, and it
# does that in ONE readable form (docs/RAIL_CONTRACT.md): a module-level tuple literal
#
#     CLI_SLOTS = ("designer",)
#
# which its chip's modelstate resolve passes as `cli_slots=`, either as it is or through a filter
# over it (`tuple(s for s in CLI_SLOTS if <runtime rule>)`). A filter can narrow the literal and
# never add to it, so the literal is the most the chip can ever claim, and that is what the flag
# must equal, slot for slot.
#
# What the scan reads is the rail's SOURCE (every .py outside tests/ and the other skipped
# directories), never a running module, and it fails the forms listed here: a way of passing
# cli_slots other than the two above (the ones _scan_file names: inline or computed, by position,
# through * or **), a second binding of CLI_SLOTS (an assignment, a parameter, an import, an
# attribute write such as `app.CLI_SLOTS = ...`), the name spelled as a string, which is how
# `globals()["CLI_SLOTS"]`, `setattr(module, "CLI_SLOTS", ...)` and `vars()` reach it, and
# resolve reached through getattr; test_the_scan_refuses_what_it_cannot_read pins each case,
# a few more besides (a star import, resolve kept in a variable, a declaration no chip passes).
# It does not catch everything: forms it misses include a `match` pattern that rebinds
# CLI_SLOTS and resolve reached through `__dict__`, and a binding the source does not show at
# all (a name computed at runtime, exec, a module outside the rail). Each rail's own chip test
# (its test_cli_requester.py) is the backstop for those: a `match` plant that rebinds finance's
# CLI_SLOTS to ("chat",) passes this scan and fails finance's test_the_owner_sees_fraud_on_claude.

_SKIP_DIRS = {"tests", "node_modules", ".venv", "venv", "native", "frontend", "dist",
              "__pycache__", "seed", "data"}
_SLOTS_NAME = "CLI_SLOTS"
_FILTER_WRAPPERS = {"tuple", "list", "set", "frozenset"}


def _rail_sources(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py") and name != "modelstate.py":
                yield Path(dirpath) / name


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.as_posix()


def _str_tuple(node: ast.AST) -> tuple[str, ...] | None:
    """A tuple display of string constants, as its values; anything else is None. A list is
    refused too: `CLI_SLOTS.append(...)` would add to it at runtime, past this scan."""
    if isinstance(node, ast.Tuple) and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                                           for e in node.elts):
        return tuple(e.value for e in node.elts)
    return None


def _slots_form(node: ast.AST) -> str | None:
    """'literal' for `CLI_SLOTS`, 'filter' for a one-loop comprehension over it that yields its
    own loop variable (optionally inside tuple()/list()/set()/frozenset()), else None."""
    if isinstance(node, ast.Name) and node.id == _SLOTS_NAME:
        return "literal"
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FILTER_WRAPPERS and len(node.args) == 1 and not node.keywords):
        node = node.args[0]
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)) and len(node.generators) == 1:
        gen = node.generators[0]
        if (isinstance(gen.iter, ast.Name) and gen.iter.id == _SLOTS_NAME and not gen.is_async
                and isinstance(gen.target, ast.Name) and isinstance(node.elt, ast.Name)
                and node.elt.id == gen.target.id):
            return "filter"
    return None


def _resolve_names(tree: ast.AST) -> tuple[set[str], set[str], list[int]]:
    """The names bound to the modelstate module, the names bound to its resolve(), and the lines
    of any star import from it (which would bind resolve where this scan cannot see it)."""
    modules: set[str] = set()
    funcs: set[str] = set()
    stars: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "modelstate" or a.name.endswith(".modelstate"):
                    # `import pkg.modelstate` binds `pkg`; that spelling is read through the
                    # attribute chain in _is_resolve_ref.
                    if a.asname or a.name == "modelstate":
                        modules.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom):
            src = node.module or ""
            if src == "modelstate" or src.endswith(".modelstate"):
                for a in node.names:
                    if a.name == "*":
                        stars.append(node.lineno)
                    elif a.name == "resolve":
                        funcs.add(a.asname or a.name)
            else:
                modules |= {a.asname or a.name for a in node.names if a.name == "modelstate"}
    return modules, funcs, stars


def _is_resolve_ref(node: ast.AST, modules: set[str], funcs: set[str]) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr == "resolve":
        v = node.value
        if (isinstance(v, ast.Name) and v.id in modules) or (
                isinstance(v, ast.Attribute) and v.attr == "modelstate"):
            return "module-attr"
    if isinstance(node, ast.Name) and node.id in funcs and isinstance(node.ctx, ast.Load):
        return "bare-import"
    return None


def _other_binding(node: ast.AST) -> bool:
    """Does `node` bind CLI_SLOTS (any binding but the module-level literal, checked by the
    caller)? A second binding would let the chip read something the scan never saw."""
    if isinstance(node, ast.Name):
        return node.id == _SLOTS_NAME and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.Attribute):
        # `app.CLI_SLOTS = ...` from another module, or `del app.CLI_SLOTS`.
        return node.attr == _SLOTS_NAME and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.arg):
        return node.arg == _SLOTS_NAME
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == _SLOTS_NAME
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return _SLOTS_NAME in node.names
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)):
        return node.name == _SLOTS_NAME
    return False


def _named_as_text(node: ast.AST) -> bool:
    """Is CLI_SLOTS reached by its name as DATA: the string "CLI_SLOTS" (`globals()[...]`,
    `setattr(module, ...)`, `vars()[...]`, a dict key handed to `globals().update`), or a keyword
    argument of that name (`globals().update(CLI_SLOTS=...)`)? Any of those can rebind it at
    runtime where no assignment to the name appears. No rail needs to spell the name as text."""
    if isinstance(node, ast.Constant):
        return node.value == _SLOTS_NAME
    if isinstance(node, ast.keyword):
        return node.arg == _SLOTS_NAME
    return False


def _resolve_by_getattr(node: ast.AST, modules: set[str]) -> bool:
    """`getattr(modelstate, "resolve")`: resolve reached by name, which _is_resolve_ref cannot
    see, so its cli_slots would never be read."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2):
        return False
    owner, name = node.args[0], node.args[1]
    is_modelstate = (isinstance(owner, ast.Name) and owner.id in modules) or (
        isinstance(owner, ast.Attribute) and owner.attr == "modelstate")
    return is_modelstate and isinstance(name, ast.Constant) and name.value == "resolve"


def _scan_file(path: Path, tree: ast.AST) -> tuple[set[str], list[str], set[str], bool]:
    """(slot names opted in, problems, reading paths used, declares CLI_SLOTS) for one module."""
    rel = _rel(path)
    names: set[str] = set()
    problems: list[str] = []
    forms: set[str] = set()
    modules, funcs, stars = _resolve_names(tree)
    problems += [f"{rel}:{ln}: a star import from modelstate hides resolve from this scan"
                 for ln in stars]

    decl_target: ast.AST | None = None
    decl_value: tuple[str, ...] | None = None
    decl_line = 0
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == _SLOTS_NAME):
            target, value = stmt.targets[0], stmt.value
        elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
              and stmt.target.id == _SLOTS_NAME and stmt.value is not None):
            target, value = stmt.target, stmt.value
        else:
            continue
        if decl_target is None:
            decl_target, decl_value, decl_line = target, _str_tuple(value), stmt.lineno
            if decl_value is None:
                problems.append(f"{rel}:{stmt.lineno}: {_SLOTS_NAME} is not a tuple of string "
                                f"literals")
    for node in ast.walk(tree):
        if node is not decl_target and _other_binding(node):
            problems.append(f"{rel}:{getattr(node, 'lineno', 0)}: {_SLOTS_NAME} is bound again "
                            f"here; a chip must read the one module-level literal")
        elif _named_as_text(node):
            problems.append(f"{rel}:{getattr(node, 'lineno', 0)}: {_SLOTS_NAME} is reached by "
                            f"name as text here, which can rebind it unseen; a chip must read "
                            f"the one module-level literal")
        elif _resolve_by_getattr(node, modules):
            problems.append(f"{rel}:{node.lineno}: modelstate's resolve is reached through "
                            f"getattr, which this scan cannot follow; call it, or hand it to a "
                            f"helper")

    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    uses = 0
    for node in ast.walk(tree):
        how = _is_resolve_ref(node, modules, funcs)
        if how is None:
            continue
        call = parents.get(id(node))
        if isinstance(call, ast.Call) and call.func is node:
            args = call.args
        elif isinstance(call, ast.Call) and any(a is node for a in call.args):
            # Handed to a helper (run_in_threadpool, an executor): what follows it is resolve's.
            args = call.args[[i for i, a in enumerate(call.args) if a is node][0] + 1:]
        else:
            problems.append(f"{rel}:{node.lineno}: modelstate's resolve is referenced in a way "
                            f"this scan cannot follow; call it, or hand it to a helper")
            continue
        where = f"{rel}:{call.lineno}"
        if any(isinstance(a, ast.Starred) for a in args) or any(k.arg is None
                                                                for k in call.keywords):
            problems.append(f"{where}: resolve gets * or ** arguments, so cli_slots is unreadable")
            continue
        if len(args) > 2:
            problems.append(f"{where}: cli_slots is passed by position; pass "
                            f"cli_slots={_SLOTS_NAME} by keyword")
            continue
        forms.add(how)
        for kw in call.keywords:
            if kw.arg != "cli_slots":
                continue
            form = _slots_form(kw.value)
            if form is not None:
                forms.add(form)
                uses += 1
                continue
            problems.append(f"{where}: cli_slots is not {_SLOTS_NAME} or a filter over it")

    if uses and decl_target is None:
        problems.append(f"{rel}: a chip passes {_SLOTS_NAME}, and the module declares no "
                        f"module-level {_SLOTS_NAME} literal")
    if decl_target is not None and not uses:
        problems.append(f"{rel}:{decl_line}: {_SLOTS_NAME} is declared, and no chip's resolve "
                        f"passes it")
    if uses and decl_value:
        names |= set(decl_value)
    return names, problems, forms, decl_target is not None


def _scan_rail(root: Path) -> tuple[set[str], list[str], set[str]]:
    """(slot names the rail's chips can opt in, problems, reading paths used) under `root`."""
    names: set[str] = set()
    problems: list[str] = []
    forms: set[str] = set()
    declaring: list[str] = []
    for path in _rail_sources(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{_rel(path)}: unparseable, so its opt-ins are unreadable ({exc})")
            continue
        got, bad, used, declares = _scan_file(path, tree)
        names |= got
        problems += bad
        forms |= used
        if declares:
            declaring.append(_rel(path))
    if len(declaring) > 1:
        problems.append(f"more than one module under {root.name} declares {_SLOTS_NAME}: "
                        f"{declaring}; a rail has one")
    return names, problems, forms


def _manifests(repo: Path = REPO) -> list[tuple[Path, list[str], set[str]]]:
    out = []
    for manifest in sorted((repo / "rails").glob("*/rail.json")):
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        chip_slots = {s["slot"] for s in data.get("model_slots", []) if s.get("slot")}
        out.append((manifest.parent, [data["id"], *data.get("also_serves", [])], chip_slots))
    return out


def _panel_slots(root: Path) -> set[str]:
    """The model_slots a rail's rail.json puts on the Rails tab (`admin_panel`)."""
    path = root / "rail.json"
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {s["slot"] for s in data.get("model_slots", []) if s.get("slot") and s.get("admin_panel")}


def _opt_in_problems(manifests, catalog: dict[str, list[dict]]) -> list[str]:
    """Where `catalog`'s `cli` flags and the rails' own CLI_SLOTS disagree, slot for slot and
    whatever the slot's kind: a vision slot is held exactly as a text slot is."""
    served: dict[str, Path] = {}
    problems: list[str] = []
    for root, ids, chip_slots in manifests:
        opted, unreadable, _forms = _scan_rail(root)
        problems += unreadable
        mine = [i for i in ids if i in catalog]
        listed = {s["slot"] for i in mine for s in catalog[i]}
        # A chip slot the gateway does not list (course-builder's `local` tier) has no flag to
        # hold, but it must be one of the rail's own manifest slots: anything else is a typo.
        for name in sorted(opted - listed - chip_slots):
            problems.append(f"{root.name} opts in chip slot {name!r}, which no RAIL_MODEL_SLOTS "
                            f"slot of {mine} and no model_slots entry of its rail.json is called")
        # One its rail.json puts on the Rails tab must be listed: its calls name the owner, so
        # its role's Claude switch moves them, and with no row the tab names no slot of this
        # rail beside that switch. The flags below cannot see it, as there is no row to compare.
        for name in sorted((opted & _panel_slots(root)) - listed):
            problems.append(f"{root.name} opts in chip slot {name!r}, which its rail.json puts on "
                            f"the Rails tab and no RAIL_MODEL_SLOTS slot of {mine} is called, so "
                            f"the tab shows no slot of this rail beside its role's Claude switch")
        for rid in mine:
            served[rid] = root
            flagged = {s["slot"] for s in catalog[rid] if s.get("cli") is True}
            wired = {s["slot"] for s in catalog[rid] if s["slot"] in opted}
            if flagged != wired:
                problems.append(f"{rid}: RAIL_MODEL_SLOTS flags {sorted(flagged)} as wired, its "
                                f"source opts in {sorted(wired)} ({_SLOTS_NAME} under {root.name})")
    for rid, slots in catalog.items():
        if rid not in served and any(s.get("cli") is True for s in slots):
            problems.append(f"{rid} is flagged wired but no rail in this tree serves it")
    return problems


def test_the_cli_flag_is_each_rails_own_cli_slots_opt_in():
    problems = _opt_in_problems(_manifests(), RAIL_MODEL_SLOTS)
    assert not problems, "\n".join(problems)


# The equality above, on a synthetic rail whose chip opts its VISION slot in or not, against a
# catalog that flags it or not. The real catalog's vision slots are wired by the rails in their
# own change, so this pins the rule for them before (and whatever) they do.
_PHOTO_CHIP = ("def caps(u):\n"
               "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n")


def _photo_rail(tmp_path: Path, cli_slots: tuple[str, ...], panel: bool = False) -> list:
    root = tmp_path / "rails" / "syn-photos"
    pkg = root / "backend" / "syn_photos"
    pkg.mkdir(parents=True)
    slots = [{"slot": "reader"}, {"slot": "writer"}]
    for s in slots if panel else ():
        s["admin_panel"] = True
    (root / "rail.json").write_text(json.dumps({"id": "syn-photos", "model_slots": slots}),
                                    encoding="utf-8")
    (pkg / "chips.py").write_text(f"{_HEAD}CLI_SLOTS = {cli_slots!r}\n{_PHOTO_CHIP}",
                                  encoding="utf-8")
    return _manifests(tmp_path)


def _photo_catalog(reader_flagged: bool) -> dict[str, list[dict]]:
    reader = {"slot": "reader", "role": "syn-photo-vision", "kind": "vision"}
    if reader_flagged:
        reader["cli"] = True
    return {"syn-photos": [reader, {"slot": "writer", "role": "syn-photo-writer", "kind": "chat"}]}


@pytest.mark.parametrize("opted,flagged,agree", [
    (("reader",), True, True),
    ((), True, False),
    (("reader",), False, False),
    (("reader", "writer"), True, False),
], ids=["opted-and-flagged", "flagged-not-opted", "opted-not-flagged", "the-text-slot-too"])
def test_the_flag_equality_holds_for_a_vision_slot(tmp_path, opted, flagged, agree):
    problems = _opt_in_problems(_photo_rail(tmp_path, opted), _photo_catalog(flagged))
    if agree:
        assert problems == []
    else:
        assert any(p.startswith("syn-photos: RAIL_MODEL_SLOTS flags") for p in problems), problems


@pytest.mark.parametrize("panel,listed,agree", [
    (True, True, True),
    (True, False, False),
    (False, False, True),
], ids=["on-the-tab-and-listed", "on-the-tab-not-listed", "off-the-tab-not-listed"])
def test_an_opted_in_slot_on_the_rails_tab_is_a_catalog_slot(tmp_path, panel, listed, agree):
    """A slot the rail opts in and its rail.json puts on the Rails tab, with no catalog row: its
    calls name the owner and follow its role's switch, and the tab shows no slot of this rail
    beside that switch. A slot off the tab (course-builder's `local` tier) needs no row."""
    catalog = _photo_catalog(True)
    if listed:
        catalog["syn-photos"][1]["cli"] = True
    else:
        catalog["syn-photos"] = catalog["syn-photos"][:1]
    problems = _opt_in_problems(_photo_rail(tmp_path, ("reader", "writer"), panel=panel), catalog)
    if agree:
        assert problems == []
    else:
        assert problems == [
            "syn-photos opts in chip slot 'writer', which its rail.json puts on the Rails tab and "
            "no RAIL_MODEL_SLOTS slot of ['syn-photos'] is called, so the tab shows no slot of "
            "this rail beside its role's Claude switch"], problems


def test_the_opt_in_scan_is_not_degenerate():
    """The scan must find the opt-ins that exist, or the equality above holds over nothing."""
    scans = {root.name: _scan_rail(root) for root, _ids, _slots in _manifests()}
    assert any(opted for opted, _p, _f in scans.values()), "the scan reads nothing"
    flagged = {(rid, s["slot"]) for rid, slots in RAIL_MODEL_SLOTS.items() for s in slots
               if s.get("cli") is True}
    if any(rid == "iep-goals" for rid, _slot in flagged):
        assert scans["iep"][0] == {"draft", "translate"}


# Each way the scan reads an opt-in, and the rails in this tree that use it. A regression in one
# way of reading would otherwise pass over the rails that use it in silence. The public snapshot
# withholds every rail that filters, so there that case SKIPS and says why; the synthetic cases
# below still drive every path in every tree.
_FORM_WITNESSES = {"module-attr": ("recipe-book", "co-worker"), "literal": ("recipe-book",),
                   "bare-import": ("watch-builder",),
                   "filter": ("course-builder", "finance", "job-aid")}


@pytest.mark.parametrize("form", sorted(_FORM_WITNESSES))
def test_each_way_of_reading_is_used_by_a_real_rail(form):
    roots = {root.name: root for root, _ids, _slots in _manifests()}
    here = [r for r in _FORM_WITNESSES[form] if r in roots]
    if not here:
        pytest.skip(f"no rail in this tree reads its opt-in as {form!r} "
                    f"({', '.join(_FORM_WITNESSES[form])} are absent)")
    assert any(form in _scan_rail(roots[r])[2] for r in here), (form, here)


# The scan, against sources written for it: what it must read, and what it must refuse.
_HEAD = "from . import modelstate\n"
_DECL = 'CLI_SLOTS = ("writer", "draft")\n'
_SCAN_READS = {
    "the literal as it is": (
        _HEAD + _DECL + "def caps(u):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n",
        {"writer", "draft"}, {"module-attr", "literal"}),
    "a filter over it": (
        _HEAD + _DECL + "def caps(u, ok):\n"
        "    return modelstate.resolve([], requester=u,\n"
        "                              cli_slots=tuple(s for s in CLI_SLOTS if ok(s)))\n",
        {"writer", "draft"}, {"module-attr", "filter"}),
    "a bare-imported resolve": (
        "from .modelstate import resolve as res\n" + _DECL + "def caps(u):\n"
        "    return res([], requester=u, cli_slots=[s for s in CLI_SLOTS if s])\n",
        {"writer", "draft"}, {"bare-import", "filter"}),
    "resolve handed to a helper with its specs only": (
        _HEAD + "async def caps(pool, specs):\n"
        "    return await pool(modelstate.resolve, specs)\n",
        set(), {"module-attr"}),
}
_SCAN_REFUSES = {
    "an inline literal": (
        "def caps(u):\n    return modelstate.resolve([], requester=u, cli_slots=('writer',))\n",
        "is not CLI_SLOTS"),
    "a computed value": (
        "def caps(u):\n    return modelstate.resolve([], requester=u, cli_slots=_slots())\n",
        "is not CLI_SLOTS"),
    "a filter that adds": (
        _DECL + "def caps(u):\n    return modelstate.resolve([], requester=u,\n"
        "        cli_slots=tuple(s for s in CLI_SLOTS) + ('vision',))\n", "is not CLI_SLOTS"),
    "a comprehension that maps": (
        _DECL + "def caps(u):\n    return modelstate.resolve([], requester=u,\n"
        "        cli_slots=tuple(s + 'x' for s in CLI_SLOTS))\n", "is not CLI_SLOTS"),
    "a comprehension that yields another name": (
        _DECL + "EXTRA = 'vision'\ndef caps(u):\n    return modelstate.resolve([], requester=u,\n"
        "        cli_slots=tuple(EXTRA for s in CLI_SLOTS))\n", "is not CLI_SLOTS"),
    "a comprehension with two loops": (
        _DECL + "def caps(u):\n    return modelstate.resolve([], requester=u,\n"
        "        cli_slots=[s for t in 'ab' for s in CLI_SLOTS])\n", "is not CLI_SLOTS"),
    "cli_slots by position": (
        _DECL + "def caps(u):\n    return modelstate.resolve([], u, CLI_SLOTS)\n", "by position"),
    "cli_slots by position through a helper": (
        _DECL + "async def caps(pool, u):\n"
        "    return await pool(modelstate.resolve, [], u, CLI_SLOTS)\n", "by position"),
    "** arguments": (
        _DECL + "def caps(kw):\n    return modelstate.resolve([], **kw)\n", "unreadable"),
    "resolve kept in a variable": (
        _DECL + "def caps(u):\n    r = modelstate.resolve\n"
        "    return r([], requester=u, cli_slots=CLI_SLOTS)\n", "cannot follow"),
    "a list literal": (
        'CLI_SLOTS = ["writer"]\ndef caps(u):\n'
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n",
        "not a tuple of string literals"),
    "a computed declaration": (
        'CLI_SLOTS = tuple(["writer"])\ndef caps(u):\n'
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n",
        "not a tuple of string literals"),
    "a second binding in a function": (
        _DECL + "def caps(u):\n    CLI_SLOTS = ('vision',)\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "bound again"),
    "an augmented assignment": (
        _DECL + "CLI_SLOTS += ('vision',)\ndef caps(u):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "bound again"),
    "a parameter of the same name": (
        _DECL + "def caps(u, CLI_SLOTS=('vision',)):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "bound again"),
    "an imported CLI_SLOTS": (
        "from .other import CLI_SLOTS\ndef caps(u):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "declares no"),
    # The review's plant (2026-10-09): finance's literal rebound through globals() passed this
    # scan, 53 passed, while the chip opted the Fraud slot in at runtime.
    "a rebinding through globals()": (
        _DECL + 'globals()["CLI_SLOTS"] = ("writer", "vision")\ndef caps(u):\n'
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "as text"),
    "a rebinding through setattr": (
        _DECL + "import sys\nsetattr(sys.modules[__name__], 'CLI_SLOTS', ('vision',))\n"
        "def caps(u):\n    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n",
        "as text"),
    "a rebinding through globals().update by keyword": (
        _DECL + "globals().update(CLI_SLOTS=('vision',))\ndef caps(u):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "as text"),
    "an attribute write from another module": (
        _DECL + "from . import m0 as me\nme.CLI_SLOTS = ('vision',)\ndef caps(u):\n"
        "    return modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "bound again"),
    "resolve reached through getattr": (
        _DECL + "def caps(u):\n    modelstate.resolve([], requester=u, cli_slots=CLI_SLOTS)\n"
        "    return getattr(modelstate, 'resolve')([], requester=u, cli_slots=('vision',))\n",
        "through getattr"),
    "a declaration no chip passes": (
        _DECL + "def caps(u):\n    return modelstate.resolve([], requester=u)\n",
        "no chip's resolve passes it"),
    "a star import": (
        "from .modelstate import *\n" + _DECL + "def caps(u):\n"
        "    return resolve([], requester=u, cli_slots=CLI_SLOTS)\n", "star import"),
}


def _scan_source(tmp_path: Path, source: str, files: int = 1):
    pkg = tmp_path / "rail" / "pkg"
    pkg.mkdir(parents=True)
    for n in range(files):
        (pkg / f"m{n}.py").write_text(source, encoding="utf-8")
    return _scan_rail(tmp_path / "rail")


@pytest.mark.parametrize("case", sorted(_SCAN_READS))
def test_the_scan_reads_each_form_a_rail_may_use(tmp_path, case):
    source, names, forms = _SCAN_READS[case]
    got, problems, used = _scan_source(tmp_path, source)
    assert (got, problems, used) == (names, [], forms)


@pytest.mark.parametrize("case", sorted(_SCAN_REFUSES))
def test_the_scan_refuses_what_it_cannot_read(tmp_path, case):
    source, why = _SCAN_REFUSES[case]
    _names, problems, _forms = _scan_source(tmp_path, _HEAD + source)
    assert any(why in p for p in problems), problems


def test_the_scan_refuses_two_declaring_modules(tmp_path):
    source, _names, _forms = _SCAN_READS["the literal as it is"]
    _got, problems, _used = _scan_source(tmp_path, source, files=2)
    assert any("more than one module" in p for p in problems), problems


# --- the static `stream` flag is the rails' own streamed calls ---------------------------------
#
# RAIL_MODEL_SLOTS' `stream` says "this rail sends this slot's role on a /v1/chat/stream call that
# names a requester". Every rail that streams to the broker does it in its broker facade's stream
# function (the one that POSTs to /v1/chat/stream), and keeps the requester to its own answer
# role through a gate of its own:
#
#     who = _cli_requester(model, requester)          # in the stream function
#     if who:
#         payload["requester"] = who
#
#     def _cli_requester(model, requester):            # module level, the same module
#         return requester if requester and model == config.RAG_MODEL else None
#
# The scan reads that chain from the rail's SOURCE (the files the CLI_SLOTS scan reads): a
# function holding the literal path, the value it puts under "requester" (a
# `payload["requester"] = ...` store or a `{"requester": ...}` display), the module-level gate
# that value comes from, the name the gate compares the model with (`model == X`), and the
# "@role" default that name is given anywhere in the rail (`X = os.environ.get("ENV", "@role")`,
# `X = "@role"`, or a settings field `x: str = "@role"`). Those are the rail's streamed roles,
# and the flag must mark exactly its slots on them. Per role, as the flag is: a role is streamed
# when ANY of the rail's calls on it go through the stream function, even if others are buffered.
#
# A stream function that names a requester any other way is a problem, never a silent "does not
# stream": a requester passed straight through, a gate the module does not define, a compared
# name with no "@role" default. A stream that names nobody streams no role here: the broker sends
# no call without a requester to the CLI, whatever the switch says. Forms it does not follow (a
# requester added through `.update()`, a role computed at runtime) go past it; each rail's own
# test_cli_requester.py pins which calls carry a name.

_STREAM_PATH = re.compile(r"\S*/v1/chat/stream")


def _posts_a_stream(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
               and _STREAM_PATH.fullmatch(n.value) for n in ast.walk(fn))


def _requester_values(fn: ast.AST) -> list[ast.AST]:
    """What `fn` puts in a payload under "requester": a subscript store or a dict display."""
    out: list[ast.AST] = []
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            for tgt in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if (isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "requester"):
                    out.append(n.value)
        elif isinstance(n, ast.Dict):
            out += [v for k, v in zip(n.keys, n.values)
                    if isinstance(k, ast.Constant) and k.value == "requester"]
    return out


def _terminal(node: ast.AST) -> str | None:
    """`X` for a name and `x` for `a.b.x`, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _gate_of(fn: ast.AST, value: ast.AST, gates: dict[str, ast.AST]) -> ast.AST | None:
    """The module-level function whose result `value` is, directly or through the one name in
    `fn` it is bound to; None when it is anything else."""
    if isinstance(value, ast.Name):
        bound = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == value.id for t in n.targets)]
        bound += [n.value for n in ast.walk(fn)
                  if isinstance(n, ast.NamedExpr) and n.target.id == value.id]
        if len(bound) != 1:
            return None
        value = bound[0]
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return gates.get(value.func.id)
    return None


def _gate_names(gate: ast.AST) -> set[str]:
    """The names a gate compares one of its parameters with (`model == X`, either way round)."""
    a = gate.args
    params = {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
    out: set[str] = set()
    for n in ast.walk(gate):
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Eq):
            for mine, other in ((n.left, n.comparators[0]), (n.comparators[0], n.left)):
                name = _terminal(other)
                if (isinstance(mine, ast.Name) and mine.id in params and name
                        and not (isinstance(other, ast.Name) and other.id in params)):
                    out.add(name)
    return out


def _role_literal(node: ast.AST) -> str | None:
    """"@role" as a role: the constant itself, or the default of `os.environ.get(...)`,
    `os.getenv(...)` or any other `.get(key, default)`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value[:1] == "@":
        return node.value[1:]
    if (isinstance(node, ast.Call) and len(node.args) >= 2
            and _terminal(node.func) in ("get", "getenv")):
        return _role_literal(node.args[1])
    return None


def _role_defaults(trees) -> dict[str, set[str]]:
    """name -> the "@role" defaults it is given at module level or as a class field."""
    out: dict[str, set[str]] = {}
    for tree in trees:
        for body in [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for stmt in body:
                if isinstance(stmt, ast.Assign):
                    targets, value = stmt.targets, stmt.value
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                    targets, value = [stmt.target], stmt.value
                else:
                    continue
                role = _role_literal(value)
                for tgt in targets:
                    if role is not None and isinstance(tgt, ast.Name):
                        out.setdefault(tgt.id, set()).add(role)
    return out


def _scan_streams(root: Path) -> tuple[set[str], list[str]]:
    """(roles the rail under `root` sends on a /v1/chat/stream call that names a requester,
    problems)."""
    parsed: list[tuple[Path, ast.AST]] = []
    problems: list[str] = []
    for path in _rail_sources(root):
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8-sig"))))
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{_rel(path)}: unparseable, so its streams are unreadable ({exc})")
    defaults = _role_defaults(tree for _path, tree in parsed)
    roles: set[str] = set()
    for path, tree in parsed:
        gates = {s.name: s for s in tree.body
                 if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _posts_a_stream(fn):
                continue
            for value in _requester_values(fn):
                gate = _gate_of(fn, value, gates)
                names = _gate_names(gate) if gate is not None else set()
                found = {r for name in names for r in defaults.get(name, set())}
                if not found:
                    problems.append(
                        f"{_rel(path)}:{fn.lineno}: {fn.name} names a requester on "
                        f"/v1/chat/stream, and the scan cannot tell for which role; keep it to "
                        f"one role through a module-level gate that compares the model with a "
                        f"name given an \"@role\" default")
                roles |= found
    return roles, problems


def _stream_problems(manifests, catalog: dict[str, list[dict]]) -> list[str]:
    """Where `catalog`'s `stream` flags and the rails' own streamed calls disagree, slot for
    slot: a slot is streamed exactly when its role is one its rail's stream function names."""
    served: set[str] = set()
    problems: list[str] = []
    for root, ids, _chip_slots in manifests:
        roles, unreadable = _scan_streams(root)
        problems += unreadable
        for rid in (i for i in ids if i in catalog):
            served.add(rid)
            flagged = {s["slot"] for s in catalog[rid] if s.get("stream") is True}
            streamed = {s["slot"] for s in catalog[rid] if s["role"] in roles}
            if flagged != streamed:
                problems.append(f"{rid}: RAIL_MODEL_SLOTS marks {sorted(flagged)} as streamed, "
                                f"its source streams {sorted(streamed)} with a requester "
                                f"(roles {sorted(roles)} under {root.name})")
    for rid, slots in catalog.items():
        if rid not in served and any(s.get("stream") is True for s in slots):
            problems.append(f"{rid} is marked streamed but no rail in this tree serves it")
    return problems


def test_the_stream_flag_is_each_rails_own_streamed_calls():
    problems = _stream_problems(_manifests(), RAIL_MODEL_SLOTS)
    assert not problems, "\n".join(problems)


# Published rails that stream their answer role, so the scan is seen reading a real facade in
# every tree, the public snapshot included.
_STREAM_WITNESSES = {"ai-playground": "ai-playground", "gemini-cx": "gemini-cx-rag",
                     "smb-partner-enablement": "smb-partner-rag", "openmaic": "openmaic"}


def test_the_stream_scan_is_not_degenerate():
    """It finds the streams that exist, or the equality above holds over nothing."""
    scans = {root.name: _scan_streams(root)[0] for root, _ids, _slots in _manifests()}
    assert any(scans.values()), "the scan reads no stream in this tree"
    present = {name: role for name, role in _STREAM_WITNESSES.items() if name in scans}
    assert present, "none of the witness rails is in this tree"
    for name, role in present.items():
        assert role in scans[name], (name, scans[name])
    if "recipe-book" in scans:
        assert scans["recipe-book"] == set(), "a rail that only buffers streams nothing"


# The scan, against facades written for it.
_SYN_CONFIG = 'import os\nRAG_MODEL = os.environ.get("SYN_RAG_MODEL", "@syn-rag")\n'
_SYN_GATE = ("from . import config\n"
             "def _cli_requester(model, requester):\n"
             "    return requester if requester and model == config.RAG_MODEL else None\n")
_SYN_POST = ("    async with client.stream('POST', BROKER_URL + '/v1/chat/stream',\n"
             "                             json=payload) as resp:\n"
             "        yield resp\n")
_SYN_STREAM = (_SYN_GATE + "async def chat_stream(model, messages, requester=None):\n"
               "    payload = {'model': model, 'messages': messages}\n"
               "    who = _cli_requester(model, requester)\n"
               "    if who:\n"
               "        payload['requester'] = who\n" + _SYN_POST)
_STREAM_READS = {
    "a gate on a config default": ({"config.py": _SYN_CONFIG, "broker.py": _SYN_STREAM},
                                   {"syn-rag"}),
    "a settings field, and a dict display in an f-string path": (
        {"config.py": 'class Settings:\n    llm_model: str = "@syn-course"\nsettings = Settings()\n',
         "broker.py": "from .config import settings\n"
                      "def _gate(model, requester):\n"
                      "    return requester if settings.llm_model == model else None\n"
                      "async def chat_stream(model, requester=None):\n"
                      "    async with client.stream('POST', f'{BROKER_URL}/v1/chat/stream',\n"
                      "            json={'model': model, 'requester': _gate(model, requester)}) as r:\n"
                      "        yield r\n"},
        {"syn-course"}),
    "a stream that names nobody": (
        {"config.py": _SYN_CONFIG,
         "broker.py": _SYN_GATE + "async def chat_stream(model, messages):\n"
                      "    payload = {'model': model, 'messages': messages}\n" + _SYN_POST},
        set()),
    "a buffered call that names a requester": (
        {"config.py": _SYN_CONFIG,
         "broker.py": _SYN_GATE + "def chat(model, requester=None):\n"
                      "    payload = {'model': model}\n"
                      "    payload['requester'] = _cli_requester(model, requester)\n"
                      "    return post(BROKER_URL + '/v1/chat', payload)\n"},
        set()),
}
_STREAM_REFUSES = {
    "a requester passed straight through": (
        "async def chat_stream(model, requester=None):\n"
        "    payload = {'model': model}\n"
        "    payload['requester'] = requester\n" + _SYN_POST),
    "a gate the module does not define": (
        "from .other import gate\n"
        "async def chat_stream(model, requester=None):\n"
        "    payload = {'model': model}\n"
        "    payload['requester'] = gate(model, requester)\n" + _SYN_POST),
    "a compared name with no role default": (
        "WHO = os.environ.get('SYN_WHO')\n"
        "def _cli_requester(model, requester):\n"
        "    return requester if model == WHO else None\n"
        "async def chat_stream(model, requester=None):\n"
        "    payload = {'model': model}\n"
        "    who = _cli_requester(model, requester)\n"
        "    payload['requester'] = who\n" + _SYN_POST),
}


def _stream_rail(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "rails" / "syn-streams"
    pkg = root / "backend" / "syn_streams"
    pkg.mkdir(parents=True)
    (root / "rail.json").write_text(json.dumps(
        {"id": "syn-streams", "model_slots": [{"slot": "answer"}, {"slot": "draft"}]}),
        encoding="utf-8")
    for name, source in files.items():
        (pkg / name).write_text(source, encoding="utf-8")
    return root


@pytest.mark.parametrize("case", sorted(_STREAM_READS))
def test_the_stream_scan_reads_each_facade_form(tmp_path, case):
    files, roles = _STREAM_READS[case]
    assert _scan_streams(_stream_rail(tmp_path, files)) == (roles, [])


@pytest.mark.parametrize("case", sorted(_STREAM_REFUSES))
def test_the_stream_scan_refuses_what_it_cannot_read(tmp_path, case):
    got, problems = _scan_streams(_stream_rail(tmp_path, {"broker.py": _STREAM_REFUSES[case]}))
    assert got == set() and any("cannot tell for which role" in p for p in problems), problems


@pytest.mark.parametrize("streams,flagged,agree", [
    (True, ("answer",), True),
    (False, (), True),
    (True, (), False),
    (False, ("answer",), False),
    (True, ("answer", "draft"), False),
], ids=["streams-and-marked", "neither", "streams-not-marked", "marked-not-streaming",
        "the-other-role-too"])
def test_the_stream_flag_equality_holds_on_a_synthetic_rail(tmp_path, streams, flagged, agree):
    quiet = _STREAM_READS["a stream that names nobody"][0]["broker.py"]
    files = {"config.py": _SYN_CONFIG, "broker.py": _SYN_STREAM if streams else quiet}
    _stream_rail(tmp_path, files)
    catalog = {"syn-streams": [
        {"slot": "answer", "role": "syn-rag", "kind": "chat", "cli": True},
        {"slot": "draft", "role": "syn-draft", "kind": "chat", "cli": True}]}
    for s in catalog["syn-streams"]:
        if s["slot"] in flagged:
            s["stream"] = True
    problems = _stream_problems(_manifests(tmp_path), catalog)
    if agree:
        assert problems == []
    else:
        assert any(p.startswith("syn-streams: RAIL_MODEL_SLOTS marks") for p in problems), problems


def _flag_shape_problems(catalog: dict[str, list[dict]]) -> list[str]:
    out: list[str] = []
    for rid, slots in catalog.items():
        for s in slots:
            if not isinstance(s.get("cli", False), bool):
                out.append(f"{rid}/{s['slot']}: cli is {s['cli']!r}, not a bool")
            elif s.get("cli") is True and s["kind"] not in ("chat", "vision"):
                out.append(f"{rid}/{s['slot']}: an {s['kind']} slot is flagged wired")
    return out


def test_no_role_backs_an_image_slot_and_a_text_or_vision_slot():
    """The switch route refuses an image slot by its ROLE (`role not in CLI_SLOT_ROLES`, the
    400). A role that an image slot shared with a text or vision slot would be in
    CLI_SLOT_ROLES, so the route would take a switch for the image slot's role and the tab
    would draw that slot ON. The catalog keeps the two apart, role by role."""
    kinds: dict[str, set[str]] = {}
    for rid, slots in RAIL_MODEL_SLOTS.items():
        for s in slots:
            kinds.setdefault(s["role"], set()).add(s["kind"])
    assert IMAGE_SLOT_ROLES, "no image slot in this tree's catalog: the check reads nothing"
    mixed = {role: sorted(k) for role, k in kinds.items() if "image" in k and k & CLI_SLOT_KINDS}
    assert mixed == {}, f"roles that back an image slot and a text or vision slot: {mixed}"


def test_every_role_set_the_catalog_rules_compute_is_one_the_gateway_reads():
    """rails_models.role_sets works out each role set the routes guard with, and add_rail
    recomputes every one of them for a synthetic rail. A set the gateway never reads is a second
    rule nothing enforces, and it reads as one: CHAT_SLOT_ROLES (the roles with a text slot)
    outlived the switch moving to CLI_SLOT_ROLES, used only by tests to pick a text role, until
    2026-10-14. Each name must be imported by main.py and be a module attribute."""
    import platform_gateway_app.rails_models as rm
    src = (REPO / "apps" / "platform" / "backend" / "platform_gateway_app" / "main.py").read_text(
        encoding="utf-8")
    block = re.search(r"^from platform_gateway_app\.rails_models import \(([^)]*)\)", src, re.M)
    assert block, "main.py no longer imports from rails_models in one block"
    imported = {n.strip() for n in block.group(1).split(",") if n.strip()}
    names = set(role_sets(RAIL_MODEL_SLOTS))
    assert names and "CLI_SLOT_ROLES" in names, names
    assert sorted(names - imported) == [], "role sets the gateway never reads"
    assert all(getattr(rm, n, None) == role_sets(RAIL_MODEL_SLOTS)[n] for n in names)


def test_the_flag_is_a_boolean_and_only_a_text_or_vision_slot_may_set_it():
    """Absent means not wired. Present, it is a real bool: `"cli": "yes"` would be truthy to a
    careless reader and False to `is True`, two answers from one entry. A text or a vision slot
    may carry it; an image slot renders pictures, which no Claude model stands in for."""
    assert _flag_shape_problems(RAIL_MODEL_SLOTS) == []
    # The rule's own cases, whatever the real catalog flags today.
    assert _flag_shape_problems({"r": [{"slot": "a", "kind": "chat", "cli": True},
                                       {"slot": "b", "kind": "vision", "cli": True},
                                       {"slot": "c", "kind": "image", "cli": False}]}) == []
    assert _flag_shape_problems({"r": [{"slot": "c", "kind": "image", "cli": True}]}) == [
        "r/c: an image slot is flagged wired"]
    assert _flag_shape_problems({"r": [{"slot": "b", "kind": "vision", "cli": "yes"}]}) == [
        "r/b: cli is 'yes', not a bool"]


def _stream_shape_problems(catalog: dict[str, list[dict]]) -> list[str]:
    out: list[str] = []
    for rid, slots in catalog.items():
        for s in slots:
            if not isinstance(s.get("stream", False), bool):
                out.append(f"{rid}/{s['slot']}: stream is {s['stream']!r}, not a bool")
            elif s.get("stream") is True and s.get("cli") is not True:
                out.append(f"{rid}/{s['slot']}: marked streamed but not wired")
    return out


def test_the_stream_flag_is_a_boolean_and_only_a_wired_slot_may_set_it():
    """Absent means no streamed call names a requester on the role. Present, a real bool, for
    the reason `cli` is one; and only on a wired slot, because a stream that names a requester
    is a wired call (and the wired check refuses an unwired ON before the stream check runs)."""
    assert _stream_shape_problems(RAIL_MODEL_SLOTS) == []
    assert _stream_shape_problems({"r": [{"slot": "a", "kind": "chat", "cli": True, "stream": True},
                                         {"slot": "b", "kind": "vision", "cli": True,
                                          "stream": False}]}) == []
    assert _stream_shape_problems({"r": [{"slot": "c", "kind": "chat", "stream": True}]}) == [
        "r/c: marked streamed but not wired"]
    assert _stream_shape_problems({"r": [{"slot": "d", "kind": "chat", "cli": True,
                                          "stream": "yes"}]}) == ["r/d: stream is 'yes', not a bool"]
