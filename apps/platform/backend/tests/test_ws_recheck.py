"""A live WebSocket is re-checked, not trusted for its whole life on the strength of its handshake.

The WS proxy authenticated the session and the entitlement once, at the handshake. A revoked
entitlement, a deleted account or an expired session then kept an open socket (workstation's
SSH terminal is one) for as long as the browser held it. _pump_ws now re-asks on a timer and
ends both sides with 4403 when the answer turns to no.
"""
from __future__ import annotations

import asyncio

from platform_gateway_app import main as m


class _Client:
    """A browser side that never sends anything and never disconnects on its own."""

    def __init__(self):
        self.closed_with: list = []

    async def receive(self):
        await asyncio.Event().wait()

    async def send_bytes(self, _b):
        pass

    async def send_text(self, _t):
        pass

    async def close(self, code: int = 1000):
        self.closed_with.append(code)


class _Upstream:
    """A rail side that never sends a frame and never ends on its own."""

    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()

    async def send(self, _m):
        pass

    async def close(self):
        self.closed = True


def _pump(still_allowed, every=0.01, budget=2.0):
    client, upstream = _Client(), _Upstream()

    async def go():
        await asyncio.wait_for(m._pump_ws(client, upstream, still_allowed, every=every), budget)

    asyncio.run(go())
    return client, upstream


def test_a_revoked_session_ends_the_socket_with_4403():
    answers = iter([True, True, False])
    client, upstream = _pump(lambda: next(answers))
    assert client.closed_with == [m._WS_REVOKED]
    assert upstream.closed is True


def test_a_check_that_raises_is_not_a_yes():
    def broken():
        raise RuntimeError("database is locked")
    client, _ = _pump(broken)
    assert client.closed_with == [m._WS_REVOKED]


def test_an_allowed_socket_stays_open():
    """The other half: a check that keeps saying yes must not end anything.

    Counted in re-checks, not slept: the socket must still be open once the check has said yes
    20 times and been asked again. A 0.2 s sleep meant about 20 re-checks on an idle host and
    perhaps one on a loaded one, so a socket that a later yes ended could pass."""
    yeses = 20

    async def go():
        client, upstream = _Client(), _Upstream()
        asked = 0
        asked_again = asyncio.Event()

        def still_allowed():
            nonlocal asked
            asked += 1
            if asked > yeses:
                asked_again.set()
            return True

        task = asyncio.create_task(m._pump_ws(client, upstream, still_allowed, every=0.01))
        waiter = asyncio.ensure_future(asked_again.wait())
        await asyncio.wait({task, waiter}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        assert not task.done(), f"an allowed socket was closed after {asked} yes answers"
        assert asked > yeses, f"the check was asked {asked} times in 30 s"
        assert client.closed_with == [] and upstream.closed is False
        for t in (task, waiter):
            t.cancel()

    asyncio.run(go())


# --- the re-check itself, against a real database --------------------------------------

def _session_for(db, monkeypatch, *, superadmin=False, apps=()):
    from platform_gateway_app.auth import create_session, hash_password
    from platform_gateway_app.models import Entitlement, User
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    with db.session_ctx() as s:
        u = User(username="bob", password_hash=hash_password("x"), is_superadmin=superadmin,
                 is_admin=superadmin)
        u.entitlements = [Entitlement(app_id=a) for a in apps]
        s.add(u)
        s.commit()
        return create_session(s, u, ttl_hours=1).token, u.id


def test_the_recheck_says_yes_while_nothing_changed(db, monkeypatch):
    token, uid = _session_for(db, monkeypatch, apps=("workstation",))
    assert m._ws_still_allowed(token, uid, "workstation") is True


def test_the_recheck_says_no_once_the_entitlement_is_revoked(db, monkeypatch):
    from platform_gateway_app.models import Entitlement
    token, uid = _session_for(db, monkeypatch, apps=("workstation",))
    with db.session_ctx() as s:
        s.query(Entitlement).filter_by(user_id=uid).delete()
        s.commit()
    assert m._ws_still_allowed(token, uid, "workstation") is False


def test_the_recheck_says_no_once_the_session_is_gone(db, monkeypatch):
    from platform_gateway_app.models import SessionRow
    token, uid = _session_for(db, monkeypatch, apps=("workstation",))
    with db.session_ctx() as s:
        s.query(SessionRow).delete()
        s.commit()
    assert m._ws_still_allowed(token, uid, "workstation") is False


def test_the_recheck_says_no_once_the_account_is_deleted(db, monkeypatch):
    from platform_gateway_app.models import User
    token, uid = _session_for(db, monkeypatch, apps=("workstation",))
    with db.session_ctx() as s:
        s.delete(s.get(User, uid))
        s.commit()
    assert m._ws_still_allowed(token, uid, "workstation") is False


def test_a_super_admin_needs_no_row(db, monkeypatch):
    token, uid = _session_for(db, monkeypatch, superadmin=True)
    assert m._ws_still_allowed(token, uid, "workstation") is True
