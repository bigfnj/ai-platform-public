"""Google Tasks per-user 'Send to Phone' — offline unit tests (httpx monkeypatched, temp DB,
no network, no real Google account). Covers config gating, the OAuth helpers, per-owner token
storage + CSRF state, list-order preservation, and the error surface."""
from __future__ import annotations

import base64
import json

import pytest

from recipe_book import config, db, gtasks


# --- config / OAuth helpers -------------------------------------------------

def test_app_configured_needs_id_secret_and_redirect(monkeypatch):
    monkeypatch.setattr(config, "GTASKS_CLIENT_ID", "")
    monkeypatch.setattr(config, "GTASKS_CLIENT_SECRET", "")
    monkeypatch.setattr(config, "GTASKS_REDIRECT_URI", "")
    assert gtasks.app_configured() is False
    monkeypatch.setattr(config, "GTASKS_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GTASKS_CLIENT_SECRET", "sec")
    assert gtasks.app_configured() is False           # redirect URI still missing
    monkeypatch.setattr(config, "GTASKS_REDIRECT_URI", "https://h/recipe-book/api/gtasks/callback")
    assert gtasks.app_configured() is True


def test_auth_url_carries_client_state_scope_redirect(monkeypatch):
    monkeypatch.setattr(config, "GTASKS_CLIENT_ID", "my-client")
    monkeypatch.setattr(config, "GTASKS_REDIRECT_URI", "https://h/recipe-book/api/gtasks/callback")
    url = gtasks.auth_url("nonce123")
    assert "client_id=my-client" in url
    assert "state=nonce123" in url
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "auth%2Ftasks" in url                       # tasks scope, url-encoded
    assert "redirect_uri=https%3A%2F%2Fh%2Frecipe-book%2Fapi%2Fgtasks%2Fcallback" in url


def test_email_from_id_token():
    payload = base64.urlsafe_b64encode(json.dumps({"email": "chef@gmail.com"}).encode()).rstrip(b"=")
    jwt = "header." + payload.decode() + ".sig"
    assert gtasks.email_from_id_token(jwt) == "chef@gmail.com"
    assert gtasks.email_from_id_token("not-a-jwt") is None
    assert gtasks.email_from_id_token("") is None


# --- per-owner token storage + CSRF state (temp DB) -------------------------

@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    c = db.connect()
    db.init_db(c)
    yield c
    c.close()


def test_token_roundtrip_and_delete(con):
    assert db.gtasks_get(con, 1) is None
    db.gtasks_set(con, 1, "refresh-abc", "chef@gmail.com", "Shopping List")
    row = db.gtasks_get(con, 1)
    assert row["refresh_token"] == "refresh-abc" and row["email"] == "chef@gmail.com"
    db.gtasks_set(con, 1, "refresh-xyz", "chef@gmail.com", "Groceries")  # upsert
    assert db.gtasks_get(con, 1)["refresh_token"] == "refresh-xyz"
    db.gtasks_delete(con, 1)
    assert db.gtasks_get(con, 1) is None


def test_oauth_state_is_single_use(con):
    db.gtasks_state_create(con, "st1", 7)
    assert db.gtasks_state_pop(con, "st1") == 7
    assert db.gtasks_state_pop(con, "st1") is None      # consumed
    assert db.gtasks_state_pop(con, "never") is None


# --- push (httpx monkeypatched) ---------------------------------------------

class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gtasks.httpx.HTTPStatusError("boom", request=None, response=self)


def test_push_preserves_order_and_counts(monkeypatch):
    monkeypatch.setattr(gtasks, "_access_token", lambda rt: "tok")
    posted: list[str] = []
    monkeypatch.setattr(gtasks.httpx, "get",
                        lambda url, **kw: _Resp(200, {"items": [{"id": "L1", "title": "Shopping List"}]}))

    def fake_post(url, **kw):
        if url.endswith("/tasks"):
            posted.append(kw["json"]["title"])
        return _Resp(200, {"id": "x"})

    monkeypatch.setattr(gtasks.httpx, "post", fake_post)
    res = gtasks.push("refresh-abc", ["Eggs", "  ", "Milk", "Flour"], "Shopping List")
    assert res == {"sent": 3, "list_title": "Shopping List"}
    assert posted == ["Flour", "Milk", "Eggs"]          # reversed → first item on top


def test_push_creates_list_when_missing(monkeypatch):
    monkeypatch.setattr(gtasks, "_access_token", lambda rt: "tok")
    monkeypatch.setattr(gtasks.httpx, "get", lambda url, **kw: _Resp(200, {"items": []}))
    created = {}

    def fake_post(url, **kw):
        if url.endswith("/users/@me/lists"):
            created["title"] = kw["json"]["title"]
            return _Resp(200, {"id": "NEW"})
        return _Resp(200, {"id": "t"})

    monkeypatch.setattr(gtasks.httpx, "post", fake_post)
    gtasks.push("refresh-abc", ["Bread"], "Groceries")
    assert created["title"] == "Groceries"


def test_push_empty_is_noop(monkeypatch):
    monkeypatch.setattr(gtasks, "_access_token", lambda rt: pytest.fail("no token needed"))
    monkeypatch.setattr(gtasks.httpx, "get", lambda *a, **k: pytest.fail("should not call Google"))
    assert gtasks.push("rt", ["", "   "], "Shopping List") == {"sent": 0, "list_title": "Shopping List"}
