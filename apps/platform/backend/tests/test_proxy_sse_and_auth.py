"""Three gateway behaviours that had no test at all, and each cost something real.

1. SSE must pass through the proxy unbuffered. `http.request()` reads the whole upstream
   body first, so an EventSource saw nothing until the generator finished and then got every
   event at once -- iep-goals' live job log rendered empty for the entire run.
2. Changing a password must revoke that user's sessions. An admin resets a password exactly
   when they believe the account is compromised; without this the stolen cookie stayed valid
   for the rest of its 7-day TTL, so the reset looked like containment and was not.
3. The failed-login map must not grow without bound. Its only cleanup ran for the address
   currently logging in, so a bucket from an address that never returned lived forever.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from platform_gateway_app import main as m
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.models import SessionRow, User


# --- 1. SSE streams -------------------------------------------------------

class _FakeUpstream:
    """An httpx streaming response that yields events one at a time."""

    def __init__(self, chunks: list[bytes], ctype: str = "text/event-stream"):
        self._chunks = chunks
        self.status_code = 200
        self.headers = {"content-type": ctype}
        self.closed = False

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self) -> None:
        self.closed = True


class _FakeRequest:
    def __init__(self, accept: str):
        self.method = "GET"
        self.headers = {"accept": accept}
        self.query_params = {}
        self.state = SimpleNamespace(platform_user="admin", platform_is_admin=True)

    async def body(self) -> bytes:
        return b""


@pytest.fixture()
def wired(monkeypatch):
    """Point the proxy at a fake backend and a fake httpx client."""
    sent: dict[str, object] = {}
    upstream = _FakeUpstream([b"data: one\n\n", b"data: two\n\n"])

    class _Http:
        def build_request(self, *a, **kw):
            sent["timeout"] = kw.get("timeout")
            sent["streamed"] = True
            return SimpleNamespace()

        async def send(self, _req, stream=False):
            sent["stream_flag"] = stream
            return upstream

        async def request(self, *a, **kw):
            sent["buffered"] = True
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                                   content=b'{"ok":true}')

    monkeypatch.setattr(m.app.state, "backends", {"iep-goals": "http://x:8800"}, raising=False)
    monkeypatch.setattr(m.app.state, "http", _Http(), raising=False)
    return sent, upstream


async def test_event_stream_request_is_not_buffered(wired):
    sent, upstream = wired
    resp = await m.proxy("iep-goals", "jobs/1/events", _FakeRequest("text/event-stream"))

    assert isinstance(resp, StreamingResponse), "SSE was buffered instead of streamed"
    assert sent.get("stream_flag") is True
    assert "buffered" not in sent, "took the buffering path for an SSE request"

    body = b"".join([c async for c in resp.body_iterator])
    assert body == b"data: one\n\ndata: two\n\n"


async def test_event_stream_drops_the_read_timeout(wired):
    """A stage that goes quiet longer than the read timeout used to kill the log."""
    sent, _ = wired
    await m.proxy("iep-goals", "jobs/1/events", _FakeRequest("text/event-stream"))
    assert sent["timeout"].read is None, "a quiet SSE stream will still trip the read timeout"


async def test_ordinary_request_still_takes_the_buffered_path(wired):
    """The streaming branch must not change anything that did not ask for SSE."""
    sent, _ = wired
    await m.proxy("iep-goals", "students", _FakeRequest("application/json"))
    assert sent.get("buffered") is True
    assert "stream_flag" not in sent


# --- 2. a password change revokes sessions --------------------------------

def test_password_change_revokes_sessions(session):
    u = User(username="victim", password_hash=hash_password("old"), is_admin=False)
    session.add(u)
    session.commit()
    create_session(session, u, 168)
    create_session(session, u, 168)
    assert session.query(SessionRow).filter_by(user_id=u.id).count() == 2

    admin = User(username="root", password_hash=hash_password("x"),
                 is_admin=True, is_superadmin=True)
    session.add(admin)
    session.commit()

    m.admin_update_user(u.id, m.UserUpdate(password="new"), admin=admin, db=session)

    assert session.query(SessionRow).filter_by(user_id=u.id).count() == 0, \
        "the stolen cookie survived the password reset"


def test_unrelated_update_leaves_sessions_alone(session):
    """Only a password change revokes -- renaming or re-granting must not log the user out."""
    u = User(username="keeper", password_hash=hash_password("pw"), is_admin=False)
    session.add(u)
    session.commit()
    create_session(session, u, 168)

    admin = User(username="root2", password_hash=hash_password("x"),
                 is_admin=True, is_superadmin=True)
    session.add(admin)
    session.commit()

    m.admin_update_user(u.id, m.UserUpdate(is_admin=True), admin=admin, db=session)

    assert session.query(SessionRow).filter_by(user_id=u.id).count() == 1


# --- 3. the throttle map is bounded ---------------------------------------

class _FakeLoginRequest:
    """Just enough Request for login(): the client address its throttle keys on. The 401 path
    never reaches _set_session_cookie, so nothing else is needed."""

    def __init__(self, ip: str):
        self.client = SimpleNamespace(host=ip)


@pytest.fixture()
def throttled(monkeypatch):
    """A fresh throttle map and a settings stub, both restored after the test."""
    monkeypatch.setattr(m, "_login_fails", {})
    monkeypatch.setattr(
        m.app.state, "settings",
        SimpleNamespace(login_window_seconds=300, login_max_fails=8, session_ttl_hours=168,
                        cookie_secure=False, session_cookie="platform_session",
                        cookie_samesite="lax"),
        raising=False)
    return m._login_fails


def test_lapsed_login_buckets_are_swept(session, throttled):
    """Drive the sweep through the real login(), not a copy of it.

    This test used to inline login()'s body and assert its own copy worked, so deleting the
    production sweep left it green -- the guard it existed to protect was untested. Calling
    login() means the only way this passes is if the shipped sweep runs.
    """
    stale = time.time() - 10_000
    for i in range(m._LOGIN_FAILS_SWEEP_AT + 50):
        throttled[f"10.0.{i // 256}.{i % 256}"] = [stale]
    before = len(throttled)
    assert before > m._LOGIN_FAILS_SWEEP_AT, "the map never got big enough to trip the sweep"

    ip = "203.0.113.9"
    with pytest.raises(HTTPException) as err:
        m.login(m.LoginBody(username="nobody", password="wrong"),
                _FakeLoginRequest(ip), Response(), db=session)
    assert err.value.status_code == 401

    # Everyone else's lapsed bucket is gone; the caller's own failure is recorded, which is the
    # whole point -- an assertion of len()==0 would pass against a sweep that also dropped it.
    assert set(throttled) == {ip}, "lapsed buckets were never reclaimed"
    assert len(throttled[ip]) == 1, "the failed attempt was not recorded"


def test_a_live_bucket_survives_the_sweep(session, throttled):
    """The sweep is age-based, so a recent failure from an unrelated address must be kept --
    otherwise a busy map would reset every throttle it passes over."""
    for i in range(m._LOGIN_FAILS_SWEEP_AT + 50):
        throttled[f"10.0.{i // 256}.{i % 256}"] = [time.time() - 10_000]
    live = "198.51.100.7"
    throttled[live] = [time.time()]

    with pytest.raises(HTTPException):
        m.login(m.LoginBody(username="nobody", password="wrong"),
                _FakeLoginRequest("203.0.113.9"), Response(), db=session)

    assert live in throttled, "the sweep reclaimed a bucket that was still inside the window"
