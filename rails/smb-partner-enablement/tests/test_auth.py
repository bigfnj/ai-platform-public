"""Fail-closed identity — RC021.

The gateway authenticates every request and sets ``X-Platform-User``, stripping any client
copy first. A request that arrives without it did not come through the gateway; on this
platform that means a sibling container talking to the rail directly. The only safe answer is
401 — not "anonymous", and certainly not admin.

This rail was imported from a public sibling repo and had no suite at all, so nothing was
standing on the gate. What it protects is not theoretical: ``/api/ask`` and
``/api/scenario/generate`` each spend a resident 3B model, ``/api/upload`` writes into the
shared retrieval index that every other caller's answers are then grounded in, and
``/api/ingest`` re-embeds the whole corpus.

Two gaps found while writing this suite -- an unguarded WebSocket handshake and an open
/openapi.json -- were recorded here as strict xfails rather than left silent, and both
were fixed on 2026-09-10. The assertions stayed; only the markers went.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from smb_partner import config
from smb_partner.api import create_api

from conftest import ADMIN, USER

#: Long enough to survive the chunker's 40-character floor, so an allowed upload returns 200
#: for the right reason rather than because it indexed nothing.
UPLOAD_TEXT = "# Notes\n\nMicrosoft 365 Business Premium is the SMB security plan we lead with."

#: Every gated route, with a body where the route needs one. ``/api/health`` is deliberately
#: absent — see ``test_health_is_open_as_a_liveness_probe``.
GATED = [
    ("GET", "/api/capabilities", None),
    ("GET", "/api/collections", None),
    ("GET", "/api/scenarios", None),
    ("POST", "/api/ask", {"question": "what is the seat cap?"}),
    ("POST", "/api/ingest", None),
    ("POST", "/api/upload", {"name": "notes", "text": UPLOAD_TEXT}),
    ("POST", "/api/speak", {"text": "hello"}),
    ("POST", "/api/transcribe", {"audio_b64": "QUJD"}),
    ("POST", "/api/scenario/generate", {"scenario_id": "retail-chain", "answers": {}}),
]

ADMIN_ONLY = [
    ("/api/ingest", None),
    ("/api/upload", {"name": "notes", "text": UPLOAD_TEXT}),
]


@pytest.fixture()
def client(fake_broker):
    """The app is built inside the test so ``store.init()`` lands on the tmp DB the autouse
    fixture configured. TestClient is not entered as a context manager on purpose: that would
    fire the startup task, which ingests the corpus (and, with WARM_ON_BOOT, warms two
    models)."""
    return TestClient(create_api(), raise_server_exceptions=False)


@pytest.mark.parametrize("method,path,body", GATED, ids=[f"{m} {p}" for m, p, _ in GATED])
def test_no_identity_header_is_401(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("method,path,body", GATED, ids=[f"{m} {p}" for m, p, _ in GATED])
def test_identity_header_is_accepted(client, method, path, body):
    """The same requests with a gateway identity must not be refused. Admin-only routes still
    answer 403 here, which is the point: the caller is known, they are just not an admin."""
    assert client.request(method, path, json=body, headers=USER).status_code != 401


def test_identity_401_beats_body_validation(client):
    """A malformed body must not be answered before the caller is known. If 422 arrived first,
    an un-gated caller could map the request schema of every route by fuzzing it."""
    assert client.post("/api/ask", json={}).status_code == 401
    assert client.post("/api/ask", json={}, headers=USER).status_code == 422


@pytest.mark.parametrize("path,body", ADMIN_ONLY)
def test_admin_routes_refuse_a_named_non_admin(client, path, body):
    """403 for a real user without the flag, 401 for no identity at all. Three rails on this
    platform had these inverted — rejecting the named non-admin and waving the header-less
    caller straight through."""
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers=USER).status_code == 403


@pytest.mark.parametrize("path,body", ADMIN_ONLY)
def test_admin_routes_allow_an_admin(client, path, body):
    assert client.post(path, json=body, headers=ADMIN).status_code == 200


def test_admin_flag_is_read_off_the_resolved_identity(client):
    """``require_admin`` must consult the identity dependency, not a bare header — otherwise
    the header-less case is decided by ``X-Platform-Admin`` alone."""
    assert client.post("/api/ingest", headers={"X-Platform-Admin": "1"}).status_code == 401


def test_health_is_open_as_a_liveness_probe(client):
    """Deliberate carve-out: this rail gates per-route rather than app-wide, which leaves
    ``/api/health`` reachable for the container probe. It is only allowed to be open because it
    says nothing about the caller or the corpus contents — assert that it stays that way.

    ``ingest`` is the one addition since: a state literal plus, on failure, the exception text
    from the boot ingest. Neither names the caller nor quotes a chunk, and the alternative was
    a literal ``ok`` that could not report a half-built corpus at all."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert set(resp.json()) == {"ok", "ingest", "app", "chunks", "dims", "collections"}


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway in front still works."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert client.get("/api/collections").status_code == 200
    assert client.get("/api/capabilities").status_code == 200


def test_standalone_is_off_unless_set(client):
    """The autouse fixture leaves the flag off, which is the default a container inherits. If a
    missing value ever read as enabled the whole gate would evaporate silently."""
    assert config.STANDALONE is False
    assert client.get("/api/collections").status_code == 401


# Regression: docs_url/openapi_url were left enabled, so /openapi.json and /docs served the
# full route list and request schemas to a caller with no identity, on a port every sibling
# container can reach. Closed 2026-09-10; gemini-cx had the same gap.
def test_openapi_is_not_readable_without_identity(client):
    assert client.get("/api/openapi.json").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


# Regression: /ws/ask and /ws/scenario used to accept the handshake unconditionally, reading
# no identity at all, so a sibling container could stream answers and generate whole packages
# on the GPU. Fixed 2026-09-10 by ws_user(), matching terminal-fun and workstation, which
# refuse with a close code because a websocket cannot carry a 401 body. gemini-cx had the
# identical hole and was fixed in the same pass.
@pytest.mark.parametrize("path", ["/ws/ask", "/ws/scenario"])
def test_websocket_without_identity_is_refused(client, path):
    """A websocket cannot carry a 401 body, so the refusal is a close code — and it has to come
    before any other validation, or the close codes themselves become a probe."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path):
            pass


# Regression, found 2026-09-11: ``identity`` gated on ``x_platform_user is None``, and Starlette
# hands `X-Platform-User:` through as "" rather than None. So a header-less caller was refused
# while a caller asserting an EMPTY identity was resolved to ``{"user": "", "is_admin": False}``
# and allowed through every gated route in GATED above — the resident 3B model on /api/ask and
# /api/scenario/generate, and the shared retrieval index behind the admin routes. Fixed by
# refusing blank ahead of the STANDALONE hatch, and the accepted value is now stripped.
#
# Asserted over HTTP rather than by calling ``identity()`` directly, because the empty string
# only exists once Starlette has parsed the request: a unit call passing user="" proves what
# the function does with an argument, not what the header does to the gate.


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_blank_identity_header_is_401(client, blank):
    r = client.get("/api/collections", headers={"X-Platform-User": blank})
    assert r.status_code == 401
    # "blank platform identity" vs "no platform identity": separate branches, and the wording is
    # the only thing that says which one answered.
    assert r.json()["detail"] == "blank platform identity"


@pytest.mark.parametrize("method,path,body", GATED, ids=[f"{m} {p}" for m, p, _ in GATED])
def test_blank_identity_is_refused_on_every_gated_route(client, method, path, body):
    assert client.request(method, path, json=body,
                          headers={"X-Platform-User": ""}).status_code == 401


def test_blank_identity_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. Omitting an identity is a
    topology; asserting an empty one is a claim with nothing in it to honour, so the guard sits
    AHEAD of the STANDALONE check. Both halves in one test, because the contrast is the point."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert client.get("/api/collections").status_code == 200
    assert client.get("/api/collections", headers={"X-Platform-User": ""}).status_code == 401


def test_a_padded_username_is_trimmed_not_taken_literally(client):
    """The flip side of refusing blank: the accepted value is stripped, so a header the gateway
    padded identifies the same user rather than a second one whose name has spaces in it.
    ``/api/capabilities`` echoes the resolved identity, which is what the UI renders from."""
    padded = {"X-Platform-User": "  admin  ", "X-Platform-Admin": "0"}
    assert client.get("/api/capabilities", headers=padded).json()["user"] == "admin"
    assert client.get("/api/capabilities", headers=USER).json()["user"] == "admin"
