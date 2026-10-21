"""Fail-closed identity.

gemini-cx was imported from a public sibling repo on 2026-08-20 and arrived with no tests at
all, and with a hand-written identity gate that had drifted from the contract. It carries the
GENERATED ``identity.py`` now (``tools/rail_template.py check`` holds it byte for byte), and
this file holds the rail to it: a sibling container must never read the GECX corpus or spend
answer-model time on /api/ask. RC021 is the rule; this is the rule with teeth.

What changed with the adoption, each asserted below rather than assumed:

* ``/api/health`` is gated like every other route. It was left open for a container probe
  that does not exist, and it was the rail's most expensive read.
* ``PLATFORM_STANDALONE`` is read at CALL time, as on every other rail, so the tests set the
  environment; it used to be snapshotted into ``config.STANDALONE`` at import.
* Standalone resolves to ``user=None``, never the literal ``"standalone"``.
* ``X-Platform-Admin`` reads ``1``/``true``/``yes`` like the canonical helper.
"""
import pytest
from fastapi.testclient import TestClient

from gemini_cx import broker, config, store
from gemini_cx.api import create_api

USER = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}

GATED_GET = ["/api/health", "/api/capabilities", "/api/questions", "/api/collections"]
GATED_POST = ["/api/ask", "/api/upload", "/api/speak", "/api/ingest"]

#: A valid upload body whose text sits BELOW the chunker's 40-character floor on purpose:
#: ingest_upload returns 0 before it reaches the embedder, which this suite keeps broken. So an
#: allowed upload here answers 200 about the gate and nothing else — test_api.py owns what a
#: real document does once it is past it.
UPLOAD_BODY = {"name": "notes", "text": "short"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


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


def test_health_answers_an_identified_caller_with_the_broker_down(client):
    """Gated, and still a health route: it must answer even when the broker is down."""
    r = client.get("/api/health", headers=USER)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_never_reaches_the_broker_for_an_un_gated_caller(client, monkeypatch):
    """The open route cost three broker round-trips per call, one spawning nvidia-smi: an
    un-credentialed amplifier. Refused before any of it runs."""
    calls: list[str] = []
    for name in ("roles", "models", "status"):
        monkeypatch.setattr(broker, name, lambda *a, _n=name, **kw: calls.append(_n) or [])
    assert client.get("/api/health").status_code == 401
    assert calls == []


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


@pytest.mark.parametrize("flag, admin", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True),
    ("0", False), ("no", False), ("", False), ("2", False), ("admin", False)])
def test_the_admin_flag_reads_like_every_other_rail(client, flag, admin):
    """The canonical helper accepts 1/true/yes. This copy accepted only the exact "1", so a
    gateway sending `true` would have dropped admin here alone. Stricter was never safer:
    the gateway strips any client copy of X-Platform-*, so the header is the gateway's word
    or a sibling container's, and the sibling can send "1" as easily as "true"."""
    hdr = {"X-Platform-User": "owner-one", "X-Platform-Admin": flag}
    assert client.post("/api/ingest", headers=hdr).status_code == (200 if admin else 403)


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev and the native harness run this rail with no gateway in front."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/questions").status_code == 200
    assert client.get("/api/collections").status_code == 200


def test_standalone_is_not_the_default(client):
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    assert client.get("/api/collections").status_code == 401


@pytest.mark.parametrize("raw, open_", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True),
    ("0", False), ("false", False), ("no", False), ("", False), ("maybe", False)])
def test_the_standalone_hatch_opens_only_for_explicit_truth(client, monkeypatch, raw, open_):
    monkeypatch.setenv("PLATFORM_STANDALONE", raw)
    assert client.get("/api/questions").status_code == (200 if open_ else 401)


def test_the_standalone_flag_is_read_when_the_request_arrives(client, monkeypatch):
    """It was snapshotted into config at import, so a process (or a test) that changed the
    variable afterwards was not heard. The app here was built with it unset."""
    assert client.get("/api/questions").status_code == 401
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/questions").status_code == 200
    monkeypatch.delenv("PLATFORM_STANDALONE")
    assert client.get("/api/questions").status_code == 401


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
    assert r.json()["detail"] == "unauthenticated (blank platform identity)"


@pytest.mark.parametrize("path", GATED_POST)
def test_write_routes_refuse_a_blank_identity_too(client, path):
    """The same reasoning as the header-less case: refused before the body is read, so an
    un-gated caller never reaches the corpus or the answer model by sending an empty name."""
    assert client.post(path, json={}, headers={"X-Platform-User": ""}).status_code == 401


def test_blank_identity_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. Omitting an identity is a
    topology; asserting an empty one is a claim with nothing in it to honour, so the blank guard
    sits AHEAD of the STANDALONE check. Both halves in one test, because the contrast is the
    point."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/questions").status_code == 200
    assert client.get("/api/questions", headers={"X-Platform-User": ""}).status_code == 401
