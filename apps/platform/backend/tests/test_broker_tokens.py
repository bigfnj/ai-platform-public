"""Admin routes for named broker tokens.

These are SUPER-ADMIN only, deliberately stricter than the rest of the admin pane, and that is
the property worth testing: a broker token is not scoped to a rail or a user, it is direct
access to the GPU and — at `full` scope — the ability to repoint every rail's model. A plain
admin manages users and rooms; handing out machine credentials for the whole platform is a
different kind of authority.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from platform_core import BrokerClient, BrokerError
from platform_gateway_app import main as m
from platform_gateway_app.auth import hash_password
from platform_gateway_app.models import User


def mk(session, name: str, admin: bool = False, superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("x"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    session.add(u)
    session.commit()
    return u


class _Broker:
    """Stands in for the real BrokerClient. Records what it was asked."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.seen: list[tuple] = []

    async def tokens(self):
        if self.fail:
            raise self.fail
        self.seen.append(("list",))
        return {"tokens": [{"id": "a1", "label": "studio", "scope": "inference",
                            "prefix": "bt_abc1234", "created": "2026-09-17", "last_seen": None}],
                "scopes": ["inference", "full"], "shared_token_in_use": True}

    async def create_token(self, label, scope):
        if self.fail:
            raise self.fail
        self.seen.append(("create", label, scope))
        return {"token": "bt_PLAINTEXT", "id": "b2", "label": label, "scope": scope,
                "prefix": "bt_PLAINTEX", "created": "2026-09-17"}

    async def revoke_token(self, token_id):
        if self.fail:
            raise self.fail
        self.seen.append(("revoke", token_id))
        return {"revoked": token_id}


@pytest.fixture()
def broker(monkeypatch):
    b = _Broker()
    monkeypatch.setattr(m.app.state, "broker", b, raising=False)
    return b


def _run(coro):
    # asyncio.run, not get_event_loop_policy().new_event_loop(): the policy API is deprecated (a
    # DeprecationWarning per call on 3.14, removal slated for 3.16), and that loop was never closed.
    return asyncio.run(coro)


class TestSuperAdminOnly:
    """A plain admin may run the platform; only a super-admin may hand out credentials to it."""

    def test_a_plain_admin_cannot_list(self, session, broker):
        admin = mk(session, "alice", admin=True)
        with pytest.raises(Exception) as exc:
            _run(m.admin_list_broker_tokens(admin=admin))
        assert "super-admin" in str(exc.value)

    def test_a_plain_admin_cannot_create(self, session, broker):
        admin = mk(session, "alice", admin=True)
        with pytest.raises(Exception) as exc:
            _run(m.admin_create_broker_token(m.BrokerTokenBody(label="x", scope="inference"),
                                             admin=admin))
        assert "super-admin" in str(exc.value)
        assert broker.seen == [], "the broker was called before the guard ran"

    def test_a_plain_admin_cannot_revoke(self, session, broker):
        admin = mk(session, "alice", admin=True)
        with pytest.raises(Exception) as exc:
            _run(m.admin_revoke_broker_token("a1", admin=admin))
        assert "super-admin" in str(exc.value)
        assert broker.seen == []

    def test_a_superadmin_may_do_all_three(self, session, broker):
        root = mk(session, "admin", superadmin=True)
        assert _run(m.admin_list_broker_tokens(admin=root))["tokens"][0]["label"] == "studio"
        out = _run(m.admin_create_broker_token(
            m.BrokerTokenBody(label="laptop-lan", scope="inference"), admin=root))
        assert out["token"] == "bt_PLAINTEXT"
        assert _run(m.admin_revoke_broker_token("a1", admin=root)) == {"revoked": "a1"}
        assert broker.seen == [("list",), ("create", "laptop-lan", "inference"),
                               ("revoke", "a1")]


def _real_client(status: int, body) -> BrokerClient:
    """The REAL BrokerClient over a MockTransport answering in FastAPI's own error shape.

    These tests used to hand the route a BrokerError whose STRING they wrote themselves, so
    they agreed with the route's parser rather than with the broker: a refused label holding
    ": " came back cut in half and nothing here could notice."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler)))


class TestErrorMapping:
    """A caller's mistake must not read as 'the broker broke' — the same 400-vs-502 distinction
    the broker's own routes make."""

    def test_a_bad_label_is_a_400_carrying_the_brokers_whole_sentence(self, session,
                                                                     monkeypatch):
        root = mk(session, "owner-one", superadmin=True)
        said = "a token labelled 'studio: east' already exists"
        monkeypatch.setattr(m.app.state, "broker", _real_client(400, {"detail": said}),
                            raising=False)
        with pytest.raises(HTTPException) as exc:
            _run(m.admin_create_broker_token(
                m.BrokerTokenBody(label="studio: east", scope="inference"), admin=root))
        assert exc.value.status_code == 400
        assert exc.value.detail == said

    def test_the_first_mint_refusal_reaches_the_admin_as_a_409(self, session, monkeypatch):
        """The broker refuses the mint that would lock every client out. Relayed as a 502
        'broker unreachable', that reads as an outage instead of the instruction it is."""
        root = mk(session, "owner-one", superadmin=True)
        said = "refused: this broker has no BROKER_AUTH_TOKEN, and creating its first ..."
        monkeypatch.setattr(m.app.state, "broker", _real_client(409, {"detail": said}),
                            raising=False)
        with pytest.raises(HTTPException) as exc:
            _run(m.admin_create_broker_token(
                m.BrokerTokenBody(label="studio", scope="full"), admin=root))
        assert exc.value.status_code == 409 and exc.value.detail == said

    def test_an_unreachable_broker_is_a_502(self, session, monkeypatch):
        root = mk(session, "owner-one", superadmin=True)
        monkeypatch.setattr(m.app.state, "broker",
                            _Broker(fail=BrokerError("broker GET /v1/tokens unreachable: boom")),
                            raising=False)
        with pytest.raises(Exception) as exc:
            _run(m.admin_list_broker_tokens(admin=root))
        assert getattr(exc.value, "status_code", None) == 502

    def test_a_broker_500_on_create_is_still_a_502(self, session, monkeypatch):
        root = mk(session, "owner-one", superadmin=True)
        monkeypatch.setattr(m.app.state, "broker", _real_client(500, {"detail": "boom"}),
                            raising=False)
        with pytest.raises(HTTPException) as exc:
            _run(m.admin_create_broker_token(
                m.BrokerTokenBody(label="studio", scope="full"), admin=root))
        assert exc.value.status_code == 502

    def test_revoking_a_missing_token_is_a_404(self, session, monkeypatch):
        root = mk(session, "owner-one", superadmin=True)
        monkeypatch.setattr(m.app.state, "broker",
                            _real_client(404, {"detail": "no such token 'zz'"}), raising=False)
        with pytest.raises(HTTPException) as exc:
            _run(m.admin_revoke_broker_token("zz", admin=root))
        assert exc.value.status_code == 404

    def test_a_revoke_the_broker_cannot_write_is_a_409_in_its_words(self, session, monkeypatch):
        """The broker answers a tokens.json it cannot land a write in with a 409 naming the file.
        Relayed as a 502 'broker unreachable', the admin went looking for an outage."""
        root = mk(session, "owner-one", superadmin=True)
        said = "refused: tokens.json cannot be read, so revoking would rewrite it from nothing"
        monkeypatch.setattr(m.app.state, "broker", _real_client(409, {"detail": said}),
                            raising=False)
        with pytest.raises(HTTPException) as exc:
            _run(m.admin_revoke_broker_token("a1", admin=root))
        assert exc.value.status_code == 409 and exc.value.detail == said


def test_the_plaintext_is_only_ever_in_the_create_response(session, broker):
    """The list route must never carry a token value. There is no endpoint that can return one
    again -- the broker stores a hash -- so a UI that lost it has to revoke and re-mint."""
    root = mk(session, "admin", superadmin=True)
    listed = _run(m.admin_list_broker_tokens(admin=root))
    assert all("token" not in row and "hash" not in row for row in listed["tokens"])
