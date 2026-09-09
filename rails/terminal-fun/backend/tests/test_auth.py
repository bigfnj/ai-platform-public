"""Fail-closed identity.

Terminal Fun gated nothing. The caller was read as `Header(default="?")`, so a request that
arrived with no gateway header was not refused — it was handed the literal user "?", which
saves.py folds into a shared "anon" bucket. A sibling container could list and DELETE saves in
that bucket, burn broker time on /api/chat, and open a PTY on the toy container, and every
audit line would name "?" as the culprit. The websocket did the same thing by hand.
"""
import os

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from terminal_fun_app import main
from terminal_fun_app.config import settings
from terminal_fun_app.main import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    """The escape hatch is read at call time, so clearing the env is enough."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Point the save store at tmp: /api/saves reserves a NetHack name on first read, and the
    # default data_dir is a container mount that must not be created on a dev box.
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "nethack_save_dir", str(tmp_path / "nh"))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/api/catalog", "/api/saves"])
def test_no_identity_header_is_401(client, path):
    assert client.get(path).status_code == 401


def test_identity_header_is_allowed(client):
    assert client.get("/api/catalog", headers=HDR).status_code == 200
    assert client.get("/api/saves", headers=HDR).status_code == 200


def test_discard_requires_identity_before_it_looks_anything_up(client):
    """401 must beat the 404 for a non-saveable game, or absence of a save would mask the
    missing auth — and this route deletes another owner's data when it is wrong."""
    assert client.delete("/api/saves/nethack").status_code == 401
    assert client.delete("/api/saves/not-a-game").status_code == 401


@pytest.mark.skipif(main._WEBTOYS_DIR is None, reason="no web-toy assets on this checkout")
def test_static_webtoys_mount_is_gated(client):
    """A mount is not a route, so the app-level dependency does not reach it. Without the
    wrapper this one path kept answering an un-gated caller while everything else 401'd."""
    path = "/api/webtoys/eyeballs/index.html"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=HDR).status_code == 200


def test_openapi_is_disabled(client):
    """FastAPI's doc routes bypass app-level dependencies, so they are turned off — otherwise
    the full route list is readable without any identity at all."""
    assert client.get("/openapi.json", headers=HDR).status_code == 404
    assert client.get("/docs", headers=HDR).status_code == 404


@pytest.mark.parametrize("path", ["/ws/nethack", "/ws/no-such-toy"])
def test_websocket_without_identity_is_refused(client, path):
    """The PTY socket is the real prize, and it used to accept anyone as "?".

    The handshake is refused before accept, so nothing is ever established. An app-level
    dependency covers websocket routes too, so the refusal arrives as a real 401 denial
    response rather than the in-route 4401 close (kept as a backstop in main.py). Both ids are
    asserted because an un-gated caller must not be able to tell a real toy from a made-up one:
    the known and the unknown item must fail identically, with no 4404 leaking the difference.
    """
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive()
    assert getattr(exc.value, "status_code", None) == 401


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway still works, and yields a null owner rather than a
    placeholder username that could be persisted as a real save owner."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/catalog").status_code == 200
    assert client.get("/api/saves").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from terminal_fun_app import identity as ident
    os.environ.pop("PLATFORM_STANDALONE", None)
    assert ident.standalone() is False
