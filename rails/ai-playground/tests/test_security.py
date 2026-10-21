"""Regression tests for the bench path-traversal + identity fail-closed hardening (audit fixes)."""
import asyncio

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from ai_playground import config, corpora, db
from ai_playground.api import app as appmod
from ai_playground.bench import assets, querysets
from conftest import StubWS, make_corpus, make_queryset, ws_endpoint


def test_model_dir_rejects_parent_traversal():
    with pytest.raises(ValueError):
        assets.model_dir("..")


def test_model_dir_rejects_nested_traversal():
    with pytest.raises(ValueError):
        assets.model_dir("../../etc")


def test_model_dir_rejects_dot():
    with pytest.raises(ValueError):
        assets.model_dir(".")


def test_model_dir_allows_plain_id():
    p = assets.model_dir("bge-small-en-v1.5")
    assert p.name == "bge-small-en-v1.5"
    assert p.resolve().parent == config.MODELS_DIR.resolve()


def test_valid_model_id():
    assert appmod._valid_model_id("bge-small-en-v1.5")
    assert appmod._valid_model_id("ms-marco-minilm-l6")
    assert not appmod._valid_model_id("..")
    assert not appmod._valid_model_id("../x")
    assert not appmod._valid_model_id("a/b")
    assert not appmod._valid_model_id("a\\b")
    assert not appmod._valid_model_id("")


def test_safe_rel_path():
    assert appmod._safe_rel_path("onnx/model_quantized.onnx")
    assert appmod._safe_rel_path("tokenizer.json")
    assert not appmod._safe_rel_path("../secret")
    assert not appmod._safe_rel_path("/etc/passwd")
    assert not appmod._safe_rel_path("C:\\x")
    assert not appmod._safe_rel_path("a/../../b")
    assert not appmod._safe_rel_path("")
    assert not appmod._safe_rel_path(None)


def test_require_admin_fails_closed_without_header(monkeypatch):
    # Deployed topology (STANDALONE off): a header-less call must be rejected, not treated as admin.
    monkeypatch.setattr(config, "STANDALONE", False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        appmod.deps.require_admin(appmod.deps.Identity(None, False))
    assert e.value.status_code == 403


def test_require_admin_allows_admin_identity(monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    ident = appmod.deps.Identity("system", True)
    assert appmod.deps.require_admin(ident) is ident


# --- FIX 1: an EMPTY X-Platform-User is not an identity ------------------------------------
#
# identity() gated on `x_platform_user is None` and then normalised with `x_platform_user or
# None`. Starlette hands a present-but-empty header through as "", not None, so `X-Platform-User:`
# sailed past the 401 and arrived at every route as the NULL OWNER — which three ownership checks
# then read as "nobody to compare against" and skipped. No header was a 401; a blank one was a 200
# with the run of the place.


def test_identity_rejects_a_blank_platform_user(monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    with pytest.raises(HTTPException) as e:
        appmod.deps.identity(x_platform_user="", x_platform_admin=None)
    assert e.value.status_code == 401


def test_identity_rejects_a_whitespace_platform_user(monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    with pytest.raises(HTTPException) as e:
        appmod.deps.identity(x_platform_user="   ", x_platform_admin=None)
    assert e.value.status_code == 401


def test_identity_rejects_a_blank_platform_user_even_standalone(monkeypatch):
    """Standalone dev is allowed the header-LESS path, not a blank one: omitting an identity is
    a topology, asserting an empty one is a claim, and there is nothing in it to honour."""
    monkeypatch.setattr(config, "STANDALONE", True)
    with pytest.raises(HTTPException) as e:
        appmod.deps.identity(x_platform_user="", x_platform_admin=None)
    assert e.value.status_code == 401


def test_identity_accepts_a_real_user(monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    ident = appmod.deps.identity(x_platform_user="alice", x_platform_admin="1")
    assert ident.user == "alice" and ident.is_admin is True


def test_blank_identity_header_is_refused_over_http(api):
    """End to end, because the empty string only appears once Starlette has parsed the request."""
    client = TestClient(api)
    assert client.get("/api/whoami", headers={"X-Platform-User": ""}).status_code == 401
    ok = client.get("/api/whoami", headers={"X-Platform-User": "alice"})
    assert ok.status_code == 200 and ok.json()["user"] == "alice"


def test_a_null_owner_cannot_read_another_users_corpus(con):
    """`owner is not None and ...` made the check opt-in for anyone the rail could not name."""
    cid = make_corpus(con, "bob", name="Bobs Docs")
    with pytest.raises(PermissionError):
        corpora.retrieve(cid, "anything", 4, None)


def test_a_null_owner_still_reads_a_shared_corpus(con):
    """The null owner is a scope, so it must keep the scope it is entitled to."""
    cid = db.add_corpus(con, slug="shared", name="Shared", kind="seed", owner=None,
                        embed_model="test")
    assert corpora.retrieve(cid, "anything", 4, None) == []


def test_a_null_owner_cannot_delete_another_users_corpus(con):
    cid = make_corpus(con, "bob", name="Bobs Docs")
    with pytest.raises(PermissionError):
        corpora.delete(cid, None, False)
    assert db.get_corpus(con, cid) is not None


def test_an_owner_still_deletes_their_own_corpus(con):
    cid = make_corpus(con, "bob", name="Bobs Docs")
    corpora.delete(cid, "bob", False)
    assert db.get_corpus(con, cid) is None


def test_a_null_owner_cannot_delete_another_users_queryset(con):
    qsid = make_queryset(con, "bob", name="Bobs Set")
    with pytest.raises(PermissionError):
        querysets.delete(con, qsid, None, False)
    assert db.get_queryset(con, qsid, "bob") is not None


def test_an_owner_still_deletes_their_own_queryset(con):
    qsid = make_queryset(con, "bob", name="Bobs Set")
    querysets.delete(con, qsid, "bob", False)
    assert db.get_queryset(con, qsid, "bob") is None


def test_an_admin_still_deletes_any_queryset(con):
    """The admin override is the reason querysets.delete reads unscoped; keep it working."""
    qsid = make_queryset(con, "bob", name="Bobs Set")
    querysets.delete(con, qsid, "alice", True)
    assert db.get_queryset(con, qsid, "bob") is None


# --- FIX 3: no route, and no socket, without an identity -----------------------------------


@pytest.mark.parametrize("method,path", [
    ("get", "/api/health"),
    ("get", "/api/capabilities"),
    ("get", "/api/demos"),
    ("post", "/api/nim/probe"),
    ("get", "/api/whoami"),
])
def test_every_route_requires_a_platform_identity(api, method, path):
    """These four had no identity dependency at all, on a port every sibling container can
    reach — /api/nim/probe spending the platform's NVIDIA key among them. The `no_outbound`
    fixture behind `api` is half the assertion: a route that answers here also calls out."""
    resp = getattr(TestClient(api), method)(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc", "/api/openapi.json",
                                  "/docs", "/redoc", "/openapi.json"])
def test_docs_and_schema_are_not_served(api, path):
    """FastAPI serves these through plain Starlette routes that bypass app-level dependencies,
    so 'gate them' is not on the menu — they have to be off. Other rails already close them."""
    assert TestClient(api).get(path, headers={"X-Platform-User": "alice"}).status_code == 404


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_refuses_an_unidentified_handshake(api, path):
    """Refused BEFORE accept(), with a close code — a socket cannot be handed a 401 body.
    ws_bench used to build Identity(user, False) from the raw header with no rejection at all."""
    ws = StubWS()
    asyncio.run(ws_endpoint(api, path)(ws))
    assert ws.accepted is False
    assert ws.close_code == 4401
    assert ws.sent == []


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_accepts_an_identified_handshake(api, path):
    """The guard must refuse the un-gated caller, not the gateway. An empty request body makes
    each handler answer with its own validation error, which is as far as this needs to go."""
    ws = StubWS(headers={"x-platform-user": "alice"}, inbox=[{}])
    asyncio.run(ws_endpoint(api, path)(ws))
    assert ws.accepted is True
    assert [f["type"] for f in ws.sent] == ["error"]


def test_ws_user_refuses_a_blank_header(api):
    ws = StubWS(headers={"x-platform-user": "   "})
    assert appmod.deps.ws_user(ws) is None


def test_ws_user_standalone_is_a_null_owner_not_a_placeholder(monkeypatch):
    """Standalone yields "" — falsy, so it resolves to the shared scope — never a stand-in
    username, which this rail would persist into an `owner` column as real-looking data."""
    monkeypatch.setattr(config, "STANDALONE", True)
    assert appmod.deps.ws_user(StubWS()) == ""


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_handshake_is_refused_end_to_end(api, path):
    """Both guards, through a real handshake. The app-level dependency denies the upgrade
    outright; if it ever stops reaching websocket scope, the in-handler ws_user() check still
    closes with 4401. Either way nothing is established and no frame is ever answered."""
    with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)) as excinfo:
        with TestClient(api).websocket_connect(path):
            pass
    if isinstance(excinfo.value, WebSocketDenialResponse):
        assert excinfo.value.status_code == 401
    else:
        assert excinfo.value.code == 4401


@pytest.mark.parametrize("path", ["/ws/rag", "/ws/bench"])
def test_websocket_still_serves_an_identified_client_end_to_end(api, path):
    with TestClient(api).websocket_connect(
            path, headers={"X-Platform-User": "alice"}) as ws:
        ws.send_json({})
        assert ws.receive_json()["type"] == "error"


def test_no_outbound_covers_every_way_out_of_the_process(no_outbound):
    """The fixture is half the assertion in the tests above, so it needs one of its own.

    Found by the 2026-09-15 audit: `no_outbound` refused `broker._post` and `broker._get`
    but NOT `broker.chat_stream`, which builds its own `httpx.AsyncClient` rather than going
    through `_post`. Latent at the time, because the /ws/rag security tests stop at
    validation, but a test that called it would have dialled 127.0.0.1:11500 for real with a
    600s timeout while sitting behind a fixture named `no_outbound`.

    Enumerated from the module rather than hardcoded, so a NEW way out of the process fails
    here instead of quietly escaping. That is the property worth pinning: the list of exits
    grows, and a fixture that lists them by hand rots the moment one is added.
    """
    import ast
    import inspect
    from pathlib import Path

    from ai_playground import broker

    # Read the SOURCE, not the live module. Two traps, both hit while writing this:
    #
    #  1. matching "httpx" anywhere flagged _stream_status_detail, which only TAKES an
    #     httpx.HTTPStatusError and reads its status code. Match the call shapes instead.
    #  2. inspecting vars(broker) finds nothing, because the fixture under test has already
    #     replaced every exit with `refuse`, whose __module__ is this conftest. A detector
    #     that runs after the patch can only ever report an empty list, which would make
    #     this test pass vacuously for exactly the rails it is supposed to protect.
    DIALS = ("httpx.AsyncClient(", "httpx.Client(", "httpx.post(", "httpx.get(",
             "httpx.stream(", "httpx.request(")

    src = Path(inspect.getsourcefile(broker)).read_text(encoding="utf-8")
    exits = sorted(
        node.name
        for node in ast.parse(src).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(d in (ast.get_source_segment(src, node) or "") for d in DIALS)
    )
    assert exits, "found no outbound callables in broker, so this test proves nothing"

    unguarded = [n for n in exits if getattr(broker, n) is not no_outbound]
    assert not unguarded, (
        f"these broker functions reach the network and are NOT refused by the no_outbound "
        f"fixture: {unguarded}. Add them to conftest.no_outbound.")
