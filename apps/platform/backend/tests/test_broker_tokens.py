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

import pytest

from platform_core import BrokerError
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
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


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


class TestErrorMapping:
    """A caller's mistake must not read as 'the broker broke' — the same 400-vs-502 distinction
    the broker's own routes make."""

    def test_a_bad_label_is_a_400_not_a_502(self, session, monkeypatch):
        root = mk(session, "admin", superadmin=True)
        monkeypatch.setattr(
            m.app.state, "broker",
            _Broker(fail=BrokerError("broker POST /v1/tokens -> 400: a label is required")),
            raising=False)
        with pytest.raises(Exception) as exc:
            _run(m.admin_create_broker_token(m.BrokerTokenBody(label="x", scope="inference"),
                                             admin=root))
        assert getattr(exc.value, "status_code", None) == 400

    def test_an_unreachable_broker_is_a_502(self, session, monkeypatch):
        root = mk(session, "admin", superadmin=True)
        monkeypatch.setattr(m.app.state, "broker",
                            _Broker(fail=BrokerError("broker GET /v1/tokens unreachable: boom")),
                            raising=False)
        with pytest.raises(Exception) as exc:
            _run(m.admin_list_broker_tokens(admin=root))
        assert getattr(exc.value, "status_code", None) == 502

    def test_revoking_a_missing_token_is_a_404(self, session, monkeypatch):
        root = mk(session, "admin", superadmin=True)
        monkeypatch.setattr(
            m.app.state, "broker",
            _Broker(fail=BrokerError("broker DELETE /v1/tokens/zz -> 404: no such token")),
            raising=False)
        with pytest.raises(Exception) as exc:
            _run(m.admin_revoke_broker_token("zz", admin=root))
        assert getattr(exc.value, "status_code", None) == 404


def test_the_plaintext_is_only_ever_in_the_create_response(session, broker):
    """The list route must never carry a token value. There is no endpoint that can return one
    again -- the broker stores a hash -- so a UI that lost it has to revoke and re-mint."""
    root = mk(session, "admin", superadmin=True)
    listed = _run(m.admin_list_broker_tokens(admin=root))
    assert all("token" not in row and "hash" not in row for row in listed["tokens"])
