"""The access gate works out peers and rooms only for a request it forwards to a rail that reads
them.

Every request under /<rail>/ passes the gate, including each file of the rail's static bundle,
and each one ran peer_usernames() and room_ids() as well as the session and entitlement
checks: 4-5 queries per asset where 2 decide the answer. Peers and rooms only travel on a
request the gateway relays (/<rail>/api/..., a rail's root-origin assets), so only those
pay for them now. The session and entitlement checks still run on every request.

And since 2026-10-15 only the tile whose rail reads a list is sent it: peers to the tiles in
config.PEER_TILES (edu-suite's content instance), rooms to config.ROOM_TILES (iep-goals). The
gate had a denylist (PEERLESS_TILES, the iep tile), so every other rail was sent both headers
and cost two queries per forwarded request, and the next tile whose records are a child's would
have been sent peers by default.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.websockets import WebSocketDisconnect

from platform_gateway_app import main as m
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.models import User, Workspace, WorkspaceMember

RAIL = "recipe-book"


@pytest.fixture()
def client(db, monkeypatch):
    calls = {"peers": 0, "rooms": 0}
    real_peers, real_rooms = m.peer_usernames, m.room_ids

    def peers(*a, **k):
        calls["peers"] += 1
        return real_peers(*a, **k)

    def rooms(*a, **k):
        calls["rooms"] += 1
        return real_rooms(*a, **k)

    monkeypatch.setattr(m, "peer_usernames", peers)
    monkeypatch.setattr(m, "room_ids", rooms)
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(
        session_cookie="platform_session", root_asset_owner=lambda _p: None), raising=False)
    monkeypatch.setattr(m.app.state, "backends", {}, raising=False)
    with db.session_ctx() as s:
        u = User(username="owner-one", password_hash=hash_password("x"), is_admin=True,
                 is_superadmin=True)
        s.add(u)
        s.commit()
        token = create_session(s, u, ttl_hours=1).token
    c = TestClient(m.app)
    c.cookies.set("platform_session", token)
    return c, calls


@pytest.fixture()
def sql(db):
    """Every SQL statement the gateway's engine runs from here on, in order: the query count
    itself, not the helpers' call count, so a peer or room query made any other way shows."""
    seen: list[str] = []
    event.listen(db.engine, "before_cursor_execute",
                 lambda _c, _cur, statement, *_a: seen.append(statement))
    return seen


def _workspace_queries(statements: list[str]) -> list[str]:
    return [s for s in statements if "workspace_members" in s.lower()]


def test_a_static_asset_request_skips_peers_and_rooms(client):
    """For the tiles that read a list too (since 2026-10-15 recipe-book is sent neither on any
    request): a bundle file is served from here, not forwarded."""
    c, calls = client
    for tile in (RAIL, *(t for t in ("edu-suite", "iep-goals") if t in m.APP_IDS)):
        c.get(f"/{tile}/assets/index-abc123.js")
    assert calls == {"peers": 0, "rooms": 0}


def test_an_api_request_to_a_rail_that_reads_neither_works_out_neither(client, sql):
    c, calls = client
    c.get(f"/{RAIL}/api/recipes")          # 404 (no backend wired); the gate already ran
    assert calls == {"peers": 0, "rooms": 0}
    assert sql, "the statement counter saw nothing, so it proves nothing"
    assert _workspace_queries(sql) == []


def test_a_static_asset_still_needs_a_session(client):
    c, _calls = client
    c.cookies.clear()
    assert c.get(f"/{RAIL}/assets/index-abc123.js").status_code == 401


# --- each list goes only to the tile that reads it (2026-10-15) --------------------------------
# The caller is in a room with a peer throughout, so a missing header is the rule and not an
# empty workspace; the tile that reads each list is the witness.

needs_readers = pytest.mark.skipif(not {"edu-suite", "iep-goals"} <= set(m.APP_IDS),
                                   reason="this snapshot carries neither tile that reads a list")
needs_iep = pytest.mark.skipif("iep" not in m.APP_IDS, reason="this snapshot carries no iep tile")

# What each tile is sent: (peers, rooms). The iep tile (IEP Present Levels) is owner-only.
EXPECTED = {"recipe-book": (False, False), "edu-suite": (True, False),
            "iep-goals": (False, True), "iep": (False, False)}
TILES = [t for t in EXPECTED if t in m.APP_IDS]


class _Upstream:
    status_code = 200
    headers = httpx.Headers([("content-type", "application/json"), ("content-length", "2")])

    async def aiter_raw(self):
        yield b"{}"

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def relay(client, db, monkeypatch):
    """The client above, a workspace that makes `aide-one` its user's peer, and every tile in
    EXPECTED wired to a fake rail client that keeps the headers each request went out with."""
    with db.session_ctx() as s:
        owner = s.query(User).filter_by(username="owner-one").one()
        aide = User(username="aide-one", password_hash=hash_password("x"))
        room = Workspace(name="Room 12")
        s.add_all([aide, room])
        s.flush()
        s.add_all([WorkspaceMember(workspace_id=room.id, user_id=owner.id),
                   WorkspaceMember(workspace_id=room.id, user_id=aide.id)])
        s.commit()
        room_id = room.id
    sent: dict[str, dict] = {}

    class _Http:
        def build_request(self, method, url, **kw):
            sent[url.split("//", 1)[1].split(".rail", 1)[0]] = dict(kw.get("headers") or {})
            return SimpleNamespace()

        async def send(self, req, stream=False):
            return _Upstream()

    monkeypatch.setattr(m.app.state, "backends",
                        {t: f"http://{t}.rail" for t in EXPECTED}, raising=False)
    monkeypatch.setattr(m.app.state, "http", _Http(), raising=False)
    c, calls = client
    return SimpleNamespace(c=c, calls=calls, sent=sent, room=room_id)


def _check(headers: dict, tile: str, room: int) -> None:
    want_peers, want_rooms = EXPECTED[tile]
    assert headers["x-platform-user"] == "owner-one", headers
    if want_peers:
        assert json.loads(headers["x-platform-peers"]) == ["aide-one"]
    else:
        assert "x-platform-peers" not in headers, (tile, headers)
    if want_rooms:
        assert json.loads(headers["x-platform-rooms"]) == [room]
    else:
        assert "x-platform-rooms" not in headers, (tile, headers)


def test_recipe_book_is_sent_neither_list_and_costs_no_workspace_query(relay, sql):
    assert relay.c.get(f"/{RAIL}/api/recipes").status_code == 200
    _check(relay.sent[RAIL], RAIL, relay.room)
    assert relay.calls == {"peers": 0, "rooms": 0}
    assert sql and _workspace_queries(sql) == []


@needs_readers
def test_edu_suite_still_gets_peers_and_iep_goals_still_gets_rooms(relay, sql):
    """Each reader gets its own list and works out only that one."""
    assert relay.c.get("/edu-suite/api/jobs").status_code == 200
    assert relay.calls == {"peers": 1, "rooms": 0}
    _check(relay.sent["edu-suite"], "edu-suite", relay.room)
    relay.calls.update(peers=0, rooms=0)
    assert relay.c.get("/iep-goals/api/students").status_code == 200
    assert relay.calls == {"peers": 0, "rooms": 1}
    _check(relay.sent["iep-goals"], "iep-goals", relay.room)
    assert len(_workspace_queries(sql)) == 2, "one query for each list, and no other"


@needs_iep
def test_the_iep_tile_is_sent_neither_list(relay):
    """IEP Present Levels is owner-only: each of its jobs IS a child's present levels."""
    assert relay.c.get("/iep/api/jobs").status_code == 200
    assert relay.calls == {"peers": 0, "rooms": 0}
    _check(relay.sent["iep"], "iep", relay.room)


def test_each_websocket_handshake_carries_only_the_list_its_tile_reads(relay, monkeypatch):
    seen: dict[str, dict] = {}

    def connect(url, *, additional_headers, **_kw):
        seen[url.split("//", 1)[1].split(".rail", 1)[0]] = dict(additional_headers)
        raise OSError("no upstream in this test")      # the proxy closes 1011 on it

    monkeypatch.setattr(m, "ws_connect", connect)
    for tile in TILES:
        relay.calls.update(peers=0, rooms=0)
        with relay.c.websocket_connect(f"/{tile}/ws/live") as ws, \
                pytest.raises(WebSocketDisconnect):
            ws.receive_text()
        _check(seen[tile], tile, relay.room)
        want_peers, want_rooms = EXPECTED[tile]
        assert relay.calls == {"peers": int(want_peers), "rooms": int(want_rooms)}, tile
    assert RAIL in seen


@pytest.mark.parametrize("owner", TILES)
def test_a_root_asset_works_out_only_its_owners_list(client, monkeypatch, owner):
    """A rail's root-origin assets are forwarded too (config.ROOT_ASSETS), under the owner's id."""
    c, calls = client
    monkeypatch.setattr(m.app.state.settings, "root_asset_owner",
                        lambda p: owner if p.startswith("/logos/") else None)
    c.get("/logos/mark.png")
    want_peers, want_rooms = EXPECTED[owner]
    assert calls == {"peers": int(want_peers), "rooms": int(want_rooms)}
