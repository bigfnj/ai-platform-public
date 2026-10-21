"""Only Google saying "invalid_grant" may forget a user's Google Tasks connection.

The audit (2026-10-08) found `_access_token` labelled EVERY non-200 refresh answer
"invalid_grant: ..." and the Send-to-Phone route deleted the stored refresh token whenever that
substring appeared. So a Google 503, a 429, or a bad client secret on the SERVER disconnected
every user who pressed the button, and each had to redo the OAuth consent. Each case below
drives the real route over a temp DB and asserts on the stored row, not only the status code.

The same file pins the lock fix from the 2026-09-11 record: the token refresh held one
process-global lock across a 20 s network call, so one user's stalled refresh blocked every
other user's cached-token hit.

No network: httpx is monkeypatched; no real Google account.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db, gtasks, state
from recipe_book.api.routers import planner

BOB = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}
TOKEN_URL = gtasks._TOKEN_URL


class _Resp:
    def __init__(self, status: int, body=None, text: str | None = None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gtasks.httpx.HTTPStatusError("boom", request=None, response=self)


class _Cat:
    """Just enough catalog for _build_shopping: one line to send."""

    def shopping_list(self, *_a, **_kw):
        return [{"key": "milk", "label": "milk"}]


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    gtasks._cache.clear()
    gtasks._refreshing.clear()
    yield
    gtasks._cache.clear()
    gtasks._refreshing.clear()


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "owner-one")
    monkeypatch.setattr(config, "GTASKS_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GTASKS_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(config, "GTASKS_REDIRECT_URI", "https://example.invalid/cb")
    monkeypatch.setattr(state, "_catalog", _Cat())
    con = db.connect()
    db.init_db(con)
    bob = db.resolve_owner(con, "bob")
    db.gtasks_set(con, bob, "bob-refresh-token", "bob@example.invalid", "Shopping List")
    con.commit()
    con.close()
    app = FastAPI()
    app.include_router(planner.router)
    return TestClient(app, raise_server_exceptions=False), bob


def _still_connected(owner_id: int) -> bool:
    con = db.connect()
    try:
        return db.gtasks_get(con, owner_id) is not None
    finally:
        con.close()


def _refresh_answers(monkeypatch, resp: _Resp) -> list[str]:
    calls: list[str] = []

    def fake_post(url, *a, **kw):
        calls.append(url)
        if url == TOKEN_URL:
            return resp
        raise AssertionError("unexpected POST " + url)

    monkeypatch.setattr(gtasks.httpx, "post", fake_post)
    return calls


@pytest.mark.parametrize("resp", [
    _Resp(503, None, "Service Unavailable (transient)"),
    _Resp(429, {"error": "rate_limit_exceeded"}),
    _Resp(401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}),
    _Resp(500, {"error": "internal_failure"}),
    # The old substring match, defeated: the words appear, but not as Google's error code.
    _Resp(400, None, "<html>proxy says: invalid_grant is not the problem here</html>"),
    _Resp(400, {"error": "invalid_request", "error_description": "invalid_grant? no"}),
], ids=["503", "429", "invalid_client", "500", "text-only", "other-code"])
def test_a_refresh_failure_that_is_not_invalid_grant_keeps_the_connection(app_client, monkeypatch,
                                                                        resp):
    c, bob = app_client
    calls = _refresh_answers(monkeypatch, resp)
    r = c.post("/api/planner/shopping/send", json={"ids": "r1"}, headers=BOB)
    assert calls == [TOKEN_URL]
    assert r.status_code == 502, r.text
    assert _still_connected(bob), "a refresh failure that is not invalid_grant deleted the token"


def test_invalid_grant_forgets_the_connection_and_asks_to_reconnect(app_client, monkeypatch):
    c, bob = app_client
    _refresh_answers(monkeypatch, _Resp(400, {"error": "invalid_grant",
                                              "error_description": "Token has been expired or revoked."}))
    r = c.post("/api/planner/shopping/send", json={"ids": "r1"}, headers=BOB)
    assert r.status_code == 409
    assert "reconnect" in r.json()["detail"]
    assert not _still_connected(bob)


def test_a_tasks_api_error_mentioning_invalid_grant_keeps_the_connection(app_client, monkeypatch):
    """The route matched the text of ANY GTasksError, including the Tasks API's own errors."""
    c, bob = app_client
    monkeypatch.setattr(gtasks, "_access_token", lambda rt: "tok")
    monkeypatch.setattr(gtasks.httpx, "get",
                        lambda url, **kw: _Resp(503, None, "upstream invalid_grant cache miss"))
    r = c.post("/api/planner/shopping/send", json={"ids": "r1"}, headers=BOB)
    assert r.status_code == 502
    assert _still_connected(bob)


def test_only_a_parsed_invalid_grant_is_revoked():
    assert gtasks._oauth_error(_Resp(400, {"error": "invalid_grant"})) == "invalid_grant"
    assert gtasks._oauth_error(_Resp(400, None, "invalid_grant")) == ""
    assert gtasks._oauth_error(_Resp(400, ["invalid_grant"])) == ""
    assert gtasks._oauth_error(_Resp(400, {"error": {"code": "invalid_grant"}})) == ""


# --- the lock --------------------------------------------------------------------------------

def test_one_users_stalled_refresh_does_not_block_another_users_cached_token(monkeypatch):
    stalled = threading.Event()
    release = threading.Event()

    def slow_post(url, *a, **kw):
        stalled.set()
        assert release.wait(10), "the test never released the stalled refresh"
        return _Resp(200, {"access_token": "a-token", "expires_in": 3600})

    monkeypatch.setattr(gtasks.httpx, "post", slow_post)
    gtasks._cache["b-refresh"] = ("b-token", time.time() + 3600)

    a = threading.Thread(target=gtasks._access_token, args=("a-refresh",), daemon=True)
    a.start()
    try:
        assert stalled.wait(5)
        got: list[str] = []
        b = threading.Thread(target=lambda: got.append(gtasks._access_token("b-refresh")),
                             daemon=True)
        t0 = time.monotonic()
        b.start()
        b.join(timeout=2)
        assert got == ["b-token"], "a cached token waited on another user's refresh"
        assert time.monotonic() - t0 < 2
    finally:
        release.set()
        a.join(timeout=5)


def test_concurrent_refreshes_of_one_token_make_one_call(monkeypatch):
    """Four requests for one expired token make one refresh call; the other three wait for it
    and read the token it cached.

    The refresh answers once all four have ARRIVED, each in the refresh call or about to wait on
    a refresh lock, not after a 0.2 s sleep: a thread the host started late found the token
    already cached and never reached the lock, so a waiter that refreshes again could pass."""
    calls: list[str] = []
    gate = threading.Event()
    arrived: set[int] = set()
    all_in = threading.Event()
    ledger = threading.Lock()

    def arrive():
        with ledger:
            arrived.add(threading.get_ident())
            if len(arrived) == 4:
                all_in.set()

    class _ArrivalLock:
        """A refresh lock that marks the thread as arrived before it waits on it."""

        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            arrive()
            self._lock.acquire()
            return self

        def __exit__(self, *exc):
            self._lock.release()

    class _Locks(dict):
        """`_refreshing`, handing out every refresh lock (whatever its key) wrapped."""

        def setdefault(self, key, default=None):
            if key not in self:
                self[key] = _ArrivalLock(default)
            return self[key]

    def post(url, *a, **kw):
        arrive()
        calls.append(url)
        gate.wait(5)
        return _Resp(200, {"access_token": "shared", "expires_in": 3600})

    monkeypatch.setattr(gtasks.httpx, "post", post)
    monkeypatch.setattr(gtasks, "_refreshing", _Locks())
    out: list[str] = []
    threads = [threading.Thread(target=lambda: out.append(gtasks._access_token("rt")), daemon=True)
               for _ in range(4)]
    for t in threads:
        t.start()
    assert all_in.wait(10), f"only {len(arrived)} of 4 requests reached the refresh"
    gate.set()
    for t in threads:
        t.join(timeout=5)
    assert out == ["shared"] * 4
    assert calls == [TOKEN_URL]
