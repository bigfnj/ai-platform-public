"""Fail-closed identity.

ai-playground gated its DATA routes (corpora, runs, the bench registry) and stopped there, so
everything that reads only shared state answered a caller with no ``X-Platform-User`` at all.
Probed from a sibling container on the compose network, ``/api/capabilities`` returned 200 —
it reports which models this deployment is running and whether they are resident. ``/api/demos``
and ``/api/nim/probe`` were open beside it, and nim/probe spends the deployment's NVIDIA
credentials against a hosted endpoint on request and reports whether they worked.

Both websockets were the same shape one layer down: they accepted the handshake, read the
header purely to label the run, and carried on with ``user=None``. That is not the null OWNER
of a shared corpus — it is a null CALLER driving the shared GPU through a streamed generation
or a full benchmark sweep.

``tests/test_security.py`` next door covers require_admin and the bench path-traversal guards;
this file is the route-level 401 the contract asks every rail for (RAIL_CONTRACT.md, RC021).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_playground import broker, config, db, modelstate, nim
from ai_playground.api import app as appmod

USER = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    """STANDALONE is read off the config MODULE (not the env) at call time, so this is what
    turns the escape hatch off for a test."""
    monkeypatch.setattr(config, "STANDALONE", False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The real application object, with the broker and the seed ingest stubbed.

    ``create_api()`` kicks off a background seed thread that embeds every seed corpus through
    the broker; the whole point of these tests is that no request gets far enough to need one.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ap.db"))
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(appmod, "_seed_in_background", lambda: None)
    monkeypatch.setattr(modelstate, "resolve", lambda specs: {"broker": "ok", "models": []})
    monkeypatch.setattr(broker, "up", lambda: False)
    monkeypatch.setattr(broker, "resolved_model", lambda ref: "stub-model")
    monkeypatch.setattr(broker, "status", lambda: {"gpu": {"gpu_name": "stub"}})
    monkeypatch.setattr(nim, "available", lambda: False)
    con = db.connect()
    db.init_db(con)
    con.close()
    return TestClient(appmod.create_api(), raise_server_exceptions=False)


def test_status_route_without_identity_is_401(client):
    """The rail's declared status_route (rail.json). This is the one a live probe caught
    serving 200, and the one RC021 now proves from the AST rather than from the presence of
    the characters "401" somewhere in the rail."""
    assert client.get("/api/capabilities").status_code == 401


def test_status_route_with_identity_is_200(client):
    assert client.get("/api/capabilities", headers=USER).status_code == 200


@pytest.mark.parametrize("path", ["/api/demos", "/api/rag/corpora", "/api/bench/models"])
def test_read_routes_without_identity_are_401(client, path):
    assert client.get(path).status_code == 401


def test_nim_probe_without_identity_is_401(client):
    """This one spends the deployment's NVIDIA credentials on demand and reports back whether
    they are valid — a credential oracle, not a read."""
    assert client.post("/api/nim/probe").status_code == 401


def test_health_stays_open_as_a_liveness_probe(client):
    """The ONE deliberate exception. The contract allows a per-route rail to leave /api/health
    open; pinning it means closing it stays a decision rather than an accident."""
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_without_identity_is_refused(client, path):
    """Refused BEFORE accept(), so nothing is ever established and an un-gated caller cannot
    learn anything from what comes back. These used to accept() first."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive()
    assert exc.value.code == 4401


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_with_identity_is_accepted(client, path, monkeypatch):
    """The gate must not cost a real caller its socket: with the header the handshake completes
    and the rail answers on the frame's own terms (an empty request is an `error` frame, not a
    closed connection)."""
    with client.websocket_connect(path, headers=USER) as ws:
        ws.send_json({"question": "", "corpus": 0, "configs": []})
        assert ws.receive_json()["type"] == "error"


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway still works."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert client.get("/api/capabilities").status_code == 200


def test_standalone_is_off_by_default(monkeypatch):
    """A missing env var must not read as enabled — the whole gate hinges on this. Asserted
    against the expression config.py evaluates, since STANDALONE is bound at import time."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    import importlib

    assert importlib.reload(config).STANDALONE is False
