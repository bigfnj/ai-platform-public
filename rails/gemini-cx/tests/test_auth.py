"""Fail-closed identity.

gemini-cx was imported from a public sibling repo on 2026-08-20 and arrived with no tests at
all. It is also the one rail that does NOT carry the generated ``identity.py``: its gate is
hand-written inline in ``api.py``, so nothing but this file stands between a drift in that copy
and a sibling container reading the whole GECX corpus and spending answer-model time on
/api/ask. RC021 is the rule; this is the rule with teeth.

Two deliberate departures from the sibling rails are asserted here rather than assumed:

* ``/api/health`` is ungated on purpose. This rail uses the per-route ``Depends(identity)``
  shape, which the contract allows precisely so a liveness probe can reach it.
* The escape hatch is read at IMPORT time into ``config.STANDALONE`` — every other rail reads
  ``os.getenv`` at call time — so it is patched on the module, not in the environment.
"""
import pytest
from fastapi.testclient import TestClient

from gemini_cx import broker, config, store
from gemini_cx.api import create_api

USER = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}

GATED_GET = ["/api/capabilities", "/api/questions", "/api/collections"]
GATED_POST = ["/api/ask", "/api/upload", "/api/speak", "/api/ingest"]

#: A valid upload body whose text sits BELOW the chunker's 40-character floor on purpose:
#: ingest_upload returns 0 before it reaches the embedder, which this suite keeps broken. So an
#: allowed upload here answers 200 about the gate and nothing else — test_api.py owns what a
#: real document does once it is past it.
UPLOAD_BODY = {"name": "notes", "text": "short"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "STANDALONE", False)


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    """Nothing in this suite may reach the broker at :11500. The three reads behind the model
    chips are the ones /api/health and /api/capabilities make without being asked to."""
    def down(*_a, **_kw):
        raise broker.BrokerError("broker is not reachable from the test suite")
    for name in ("roles", "models", "status", "chat", "chat_stream", "embed", "warm",
                 "tts_light"):
        monkeypatch.setattr(broker, name, down)
    monkeypatch.setattr(broker, "media_enabled", lambda: False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Deliberately no default headers — this suite is the one that sends none. Everything the
    # rail writes goes under tmp_path, and the seed tree is an empty stand-in so /api/ingest
    # is a cheap no-op rather than a pass over the real corpus.
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    seed = tmp_path / "seed"
    seed.mkdir()
    monkeypatch.setattr(config, "SEED_KB_DIR", seed)
    app = create_api()
    yield TestClient(app, raise_server_exceptions=False)
    store.reload_matrix()


@pytest.mark.parametrize("path", GATED_GET)
def test_no_identity_header_is_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", GATED_POST)
def test_write_routes_refuse_before_they_read_the_body(client, path):
    """401, not 422: an un-gated caller should not get to learn the payload shape by posting
    rubbish at it, and must never reach the corpus or the answer model."""
    assert client.post(path, json={}).status_code == 401


def test_ask_with_a_valid_body_is_still_401(client):
    """The route that costs GPU time. A well-formed deck click with no identity is refused."""
    assert client.post("/api/ask", json={"question_id": "pricing"}).status_code == 401


@pytest.mark.parametrize("path", GATED_GET)
def test_identity_header_is_allowed(client, path):
    assert client.get(path, headers=USER).status_code == 200


def test_health_is_open_on_purpose(client):
    """Per-route gating leaves the liveness probe reachable — the contract's stated carve-out
    for this shape. It must answer even with the broker down."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_rejects_a_named_non_admin(client):
    """The inverted gate that bit eight rails let a header-less caller through and only
    stopped a named non-admin. Both must be refused here, for different reasons."""
    assert client.post("/api/ingest").status_code == 401
    assert client.post("/api/ingest", headers=USER).status_code == 403


def test_ingest_allows_admin(client):
    assert client.post("/api/ingest", headers=ADMIN).status_code == 200


def test_upload_rejects_a_named_non_admin(client):
    """Upload is destructive by design: ingest_upload -> store.replace_collection DELETEs the
    named collection's chunks before inserting, and rewrites origin from 'seed' to 'upload'. A
    logged-in non-admin posting name="gecx-overview" therefore repoints a curated collection at
    text of their choosing, permanently — every later answer is grounded in it and cites a
    filename they supplied. A plain re-ingest does NOT undo it, because ingest_seed only tracks
    origin == "seed" collections and skips an unchanged fingerprint; only ?force=true does.

    GATED_POST already covers the header-less 401. This is the named non-admin — the half that
    eight rails on this platform had inverted, and the half this route was missing entirely
    until 2026-09-11. smb-partner-enablement gates the byte-identical route the same way.
    """
    assert client.post("/api/upload", json=UPLOAD_BODY).status_code == 401
    assert client.post("/api/upload", json=UPLOAD_BODY, headers=USER).status_code == 403


def test_upload_allows_admin(client):
    r = client.post("/api/upload", json=UPLOAD_BODY, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["chunks"] == 0  # see UPLOAD_BODY: the gate is what is under test here


def test_admin_flag_must_come_from_the_trusted_header(client):
    """X-Platform-Admin is set by the gateway from a verified session. Only the literal '1'
    grants admin, so a client-supplied 'true' cannot self-assert its way past require_admin."""
    hdr = {"X-Platform-User": "admin", "X-Platform-Admin": "true"}
    assert client.post("/api/ingest", headers=hdr).status_code == 403


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev and the native harness run this rail with no gateway in front."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert client.get("/api/questions").status_code == 200
    assert client.get("/api/collections").status_code == 200


def test_standalone_is_not_the_default(client):
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    assert config.STANDALONE is False
    assert client.get("/api/collections").status_code == 401


def test_websocket_without_identity_is_refused(client):
    # Regression: /ws/ask used to call ws.accept() with no identity check at all, and a
    # sibling container connecting straight to :8880/ws/ask got the corpus and the answer
    # model. The gateway does authenticate the WS handshake (main.py ws_proxy closes 4401) --
    # that is exactly the reasoning RC021 rejects, because the rail's own port is reachable
    # on the compose network and a guard living only in the gateway disappears with it.
    # Fixed 2026-09-10 by ws_user(); smb-partner-enablement had the identical hole.
    """A websocket cannot return 401, so the refusal has to arrive as a close instead of an
    accept — before any frame is read, or the close code becomes a probe."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/ask"):
            pass


# --- a PRESENT but blank header -----------------------------------------------------------
#
# Found 2026-09-11, and this rail's hand-written copy of the gate had it too. ``identity``
# tested ``x_platform_user is None``, and Starlette hands `X-Platform-User:` through as ""
# rather than None — so a caller asserting an empty identity was not refused, it was resolved
# to ``{"user": "", "is_admin": False}`` and reached every per-route ``Depends(identity)``: the
# whole GECX corpus, /api/upload into it, and answer-model time on /api/ask.
#
# Asserted over HTTP rather than by calling ``identity()`` directly, because the empty string
# only exists once Starlette has parsed the request: a unit call passing user="" proves what
# the function does with an argument, not what the header does to the gate. The detail string
# is asserted once, because "blank platform identity" and "no platform identity" are separate
# branches and only the wording says which one answered.


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_blank_identity_header_is_401(client, blank):
    r = client.get("/api/collections", headers={"X-Platform-User": blank})
    assert r.status_code == 401
    assert r.json()["detail"] == "blank platform identity"


@pytest.mark.parametrize("path", GATED_POST)
def test_write_routes_refuse_a_blank_identity_too(client, path):
    """The same reasoning as the header-less case: refused before the body is read, so an
    un-gated caller never reaches the corpus or the answer model by sending an empty name."""
    assert client.post(path, json={}, headers={"X-Platform-User": ""}).status_code == 401


def test_blank_identity_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. Omitting an identity is a
    topology; asserting an empty one is a claim with nothing in it to honour, so the blank guard
    sits AHEAD of the STANDALONE check. Both halves in one test, because the contrast is the
    point — and the flag is patched on the module, per this file's docstring."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert client.get("/api/questions").status_code == 200
    assert client.get("/api/questions", headers={"X-Platform-User": ""}).status_code == 401
