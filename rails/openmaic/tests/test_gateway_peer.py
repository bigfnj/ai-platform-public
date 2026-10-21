"""Platform identity headers count only when they come from the gateway's address.

THE DEFECT. Every rail believes X-Platform-User / X-Platform-Admin when present, because the
gateway strips client copies and sets its own. This rail shares the compose network with
openmaic-app (third-party code, THU-MAIC/OpenMAIC) and every other container there, and any of
them could name anybody: to the reverse proxy, which feeds the shim's sole-user ledger
(api/proxy.py `note_user`) and so decides whether the shim names OPENMAIC_CLI_REQUESTER; and to
the shim, whose credential check took any X-Platform-User as a platform identity, so a peer never
needed the service token at all.

THE FIX (api/gateway_only.py): every x-platform-* header from a peer that is not one of the
gateway's addresses is dropped before any route, the shim mount or the root-asset catch-all sees
it, and the request is judged as one that carried no identity. Dropped rather than refused with
terminal-fun's 403, because openmaic-app is a legitimate non-gateway caller of the shim, whose
token check must keep deciding. These tests drive the real app through Starlette's TestClient with
the peer address set, and a stubbed lookup of the gateway's name (conftest.py `gateway`), which is
the whole of what the check reads.
"""
from __future__ import annotations

import json
import logging
import re
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from openmaic_app import broker
from openmaic_app.api import app as app_module
from openmaic_app.api import gateway_only, llm, proxy
from openmaic_app.config import Settings, settings

OWNER = "owner-one"
GATEWAY = "10.89.0.2"            # what conftest's `gateway` fixture resolves the name to
APP = "10.89.0.5"                # openmaic-app on the compose network: the shim's caller
SIBLING = "10.89.0.9"            # any other container on that network
# The peers that are not the gateway: the app container, a sibling, and loopback both ways.
STRANGERS = [APP, SIBLING, "127.0.0.1", "::1"]
USER = {"X-Platform-User": OWNER, "X-Platform-Admin": "1"}
TOKEN = "a-synthetic-service-token"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"model": "@openmaic", "messages": [{"role": "user", "content": "a synthetic lesson"}]}
# Every kind of gated thing this rail serves: its own status routes, the proxy, a root asset.
GATED = ["/api/capabilities", "/api/healthz", "/api/app/some/page", "/logos/mark.png"]
DOCKERFILE = Path(__file__).resolve().parents[1] / "deploy" / "Dockerfile"
REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (REPO / "deploy" / "docker-compose.yml",
                 REPO / "deploy" / "installer" / "docker-compose.installer.yml")


class _Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """A box with OPENMAIC_CLI_REQUESTER=owner-one and a token of its own, an empty ledger, a
    recording openmaic-app behind the proxy and a recording broker behind the shim."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(settings, "cli_requester", OWNER)
    monkeypatch.setattr(settings, "cli_lease_s", 1800.0)
    monkeypatch.setattr(settings, "llm_model", "@openmaic")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(llm, "_SHIM_TOKEN", TOKEN)
    monkeypatch.setattr(proxy, "_seen", {})
    monkeypatch.setattr(proxy, "_clock", lambda: 5000.0)
    monkeypatch.setattr(broker, "roles", lambda: [])
    monkeypatch.setattr(broker, "models", lambda: [])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": []})
    monkeypatch.setattr(broker, "up", lambda: True)

    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(app_module, "app_reachable", unreachable)

    rec = {"upstream": [], "broker": []}

    def handler(request: httpx.Request) -> httpx.Response:
        rec["upstream"].append(request.headers)
        return httpx.Response(200, headers=[("content-type", "text/plain")], stream=_Body())

    monkeypatch.setattr(proxy, "_client", httpx.AsyncClient(
        base_url="http://openmaic-app:3000", transport=httpx.MockTransport(handler)))

    async def fake_apost(path, payload, timeout=None):
        rec["broker"].append(payload)
        return {"model": "m:1", "message": {"role": "assistant", "content": "ok"},
                "done": True, "done_reason": "stop"}

    monkeypatch.setattr(broker, "_apost", fake_apost)
    return rec


def client_from(peer: str, app=None) -> TestClient:
    return TestClient(app or app_module.app, raise_server_exceptions=False, client=(peer, 50000))


# --- an identity from anywhere but the gateway ------------------------------------------------

@pytest.mark.parametrize("path", GATED)
@pytest.mark.parametrize("peer", STRANGERS)
def test_a_non_gateway_peers_identity_is_not_believed(peer, path):
    """401 is the identity gate's own answer to a request with no identity: the header was
    dropped, so the gate never saw one."""
    assert client_from(peer).get(path, headers=USER).status_code == 401


def test_a_non_gateway_peer_never_reaches_the_ledger(env):
    """The ledger decides whether the shim names the owner. From a peer it must not move: no
    visit recorded, nothing relayed to the app, and the shim still names nobody."""
    for peer in STRANGERS:
        for path in ("/api/app/some/page", "/logos/mark.png"):
            assert client_from(peer).get(path, headers=USER).status_code == 401
    assert proxy._seen == {}
    assert env["upstream"] == []
    assert llm._shim_requester() is None
    # The same visit from the gateway is what lets the name on.
    assert client_from(GATEWAY).get("/api/app/some/page", headers=USER).status_code == 200
    assert set(proxy._seen) == {OWNER}
    assert llm._shim_requester() == OWNER


def test_the_gateways_requests_still_identify(env):
    c = client_from(GATEWAY)
    assert c.get("/api/capabilities", headers=USER).status_code == 200
    assert c.get("/api/healthz", headers=USER).status_code == 200
    assert c.get("/api/app/some/page", headers=USER).status_code == 200
    (sent,) = env["upstream"]
    assert sent.get("x-platform-user") == OWNER


# --- the shim: openmaic-app is a peer, and keeps working --------------------------------------

def test_the_shim_still_works_from_the_app_container(env):
    """The app calls with the service token and no identity, from its own address. With the
    owner alone in the app (a visit through the gateway), its generation carries the name."""
    assert client_from(GATEWAY).get("/api/app/some/page", headers=USER).status_code == 200
    app = client_from(APP)
    assert app.get("/api/llm/v1/models", headers=BEARER).status_code == 200
    r = app.post("/api/llm/v1/chat/completions", headers=BEARER, json=BODY)
    assert r.status_code == 200, r.text
    assert [p.get("requester") for p in env["broker"]] == [OWNER]


def test_another_user_calling_the_shim_through_the_gateway_names_nobody(env):
    """The gateway relays /openmaic/api/llm/..., so any entitled user can call the shim. With the
    owner alone in the app, the ledger would let the name on, and a second user's direct call
    was answered under the owner's name. It names nobody now; the app's next call still does."""
    gateway = client_from(GATEWAY)
    assert gateway.get("/api/app/some/page", headers=USER).status_code == 200
    other = {"X-Platform-User": "other-two", "X-Platform-Admin": "0"}
    r = gateway.post("/api/llm/v1/chat/completions", headers=other, json=BODY)
    assert r.status_code == 200, r.text
    assert set(proxy._seen) == {OWNER}
    r = client_from(APP).post("/api/llm/v1/chat/completions", headers=BEARER, json=BODY)
    assert r.status_code == 200, r.text
    assert [p.get("requester") for p in env["broker"]] == [None, OWNER]


@pytest.mark.parametrize("peer", STRANGERS)
def test_a_peer_cannot_use_a_platform_identity_as_the_shims_credential(env, peer):
    """`llm_caller` takes a platform identity as a credential, for a human poking the shim
    through the gateway. From a peer, without the token, that was a way in for anyone."""
    c = client_from(peer)
    assert c.get("/api/llm/v1/models", headers=USER).status_code == 401
    assert c.post("/api/llm/v1/chat/completions", headers=USER, json=BODY).status_code == 401
    assert env["broker"] == []


def test_a_human_through_the_gateway_still_reaches_the_shim():
    assert client_from(GATEWAY).get("/api/llm/v1/models", headers=USER).status_code == 200


# --- what the middleware hands on --------------------------------------------------------------

def _seen_by_the_app(peer: str, headers: dict) -> dict[str, str]:
    """The headers an app behind GatewayOnlyIdentity is handed, from `peer`."""
    seen: dict[str, str] = {}

    async def inner(scope, receive, send):
        seen.update({k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]})
        await PlainTextResponse("ok")(scope, receive, send)

    mw = gateway_only.GatewayOnlyIdentity(inner, gateway_only.gateway_peers)
    assert client_from(peer, mw).get("/x", headers=headers).status_code == 200
    return seen


@pytest.mark.parametrize("header", ["X-Platform-User", "X-Platform-Admin", "X-Platform-Peers",
                                    "X-Platform-Rooms", "X-Platform-Anything"])
def test_every_platform_header_from_a_stranger_is_dropped_and_nothing_else(header):
    """The gateway strips the whole x-platform-* family from clients, so any member arriving from
    a stranger is a forgery, including ones no route reads today. The rest of the request, the
    shim's Authorization included, goes on untouched."""
    sent = {header: "1", "Authorization": f"Bearer {TOKEN}", "Accept": "text/plain"}
    seen = _seen_by_the_app(SIBLING, sent)
    assert not [k for k in seen if k.startswith("x-platform-")], seen
    assert seen["authorization"] == f"Bearer {TOKEN}" and seen["accept"] == "text/plain"
    assert _seen_by_the_app(GATEWAY, sent)[header.lower()] == "1"


def test_a_request_with_no_platform_header_costs_no_lookup(gateway):
    for path in GATED:
        assert client_from("127.0.0.1").get(path).status_code == 401
    assert client_from(APP).get("/api/llm/v1/models", headers=BEARER).status_code == 200
    assert gateway.calls == []


def test_a_peer_that_is_not_an_address_is_never_trusted(gateway):
    """Starlette's TestClient default peer is the NAME "testclient". A TCP peer is always an
    address, so a name could only have come from a header, and even a lookup that answered with
    that name must not make it trusted."""
    gateway.addrs = ["testclient", GATEWAY]
    c = TestClient(app_module.app, raise_server_exceptions=False)
    assert c.get("/api/capabilities", headers=USER).status_code == 401
    assert gateway_only.gateway_peers.allows(None) is False
    assert gateway_only.gateway_peers.allows("") is False


def test_the_configured_gateway_host_is_the_name_looked_up(gateway, monkeypatch):
    monkeypatch.setattr(settings, "gateway_host", "my-gateway")
    client_from(GATEWAY).get("/api/capabilities", headers=USER)
    assert gateway.calls == ["my-gateway"]


# --- standalone --------------------------------------------------------------------------------

def test_standalone_keeps_todays_behaviour(gateway, monkeypatch):
    """No gateway in front, so nothing to hold the header to: a header from loopback is honoured
    as it always was, and the name is never looked up."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    c = client_from("127.0.0.1")
    assert c.get("/api/app/some/page", headers=USER).status_code == 200
    assert set(proxy._seen) == {OWNER}
    assert gateway.calls == []
    assert c.get("/api/healthz").json()["peer_check"] == "off (PLATFORM_STANDALONE)"


# --- what /api/healthz says ------------------------------------------------------------------

def test_healthz_reports_the_check_as_on():
    body = client_from(GATEWAY).get("/api/healthz", headers=USER).json()
    assert body["peer_check"] == "on"


def test_loopback_is_believed_only_when_the_gateway_resolves_to_it(gateway, caplog):
    """A dev box with the gateway on localhost. It works, and it says it is degraded."""
    caplog.set_level(logging.WARNING, logger="openmaic.identity")
    assert client_from("127.0.0.1").get("/api/capabilities", headers=USER).status_code == 401
    gateway.addrs = ["127.0.0.1", "::1"]
    gateway.now += 60
    c = client_from("127.0.0.1")
    assert c.get("/api/capabilities", headers=USER).status_code == 200
    assert c.get("/api/healthz", headers=USER).json()["peer_check"] == "degraded"
    assert "DEGRADED identity check" in caplog.text


def test_the_mode_looks_the_gateway_up_before_it_reports(gateway):
    gateway.addrs = ["127.0.0.1", "::1"]
    assert gateway_only.gateway_peers.mode() == "degraded"
    assert gateway.calls, "mode() did not look the gateway up"


def test_a_gateway_name_that_never_resolved_reports_unresolved(gateway):
    gateway.addrs = socket.gaierror("no such host")
    assert gateway_only.gateway_peers.mode() == "unresolved"
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 401


def test_the_containers_own_address_is_degraded_too(gateway, monkeypatch):
    monkeypatch.setattr(gateway_only, "_own_addresses", lambda: frozenset({"10.89.0.3"}))
    gateway.addrs = ["10.89.0.3"]
    assert client_from("10.89.0.3").get("/api/capabilities", headers=USER).status_code == 200
    assert gateway_only.gateway_peers.mode() == "degraded"


def test_a_real_gateway_address_is_not_degraded(gateway, monkeypatch):
    monkeypatch.setattr(gateway_only, "_own_addresses", lambda: frozenset({"10.89.0.3"}))
    client_from(GATEWAY).get("/api/capabilities", headers=USER)
    assert gateway_only.gateway_peers.mode() == "on"


# --- the lookup: cached, re-resolved on a miss, rate-limited ------------------------------------

NEW_GATEWAY = "10.89.0.7"


def test_a_recreated_gateway_is_recognised_after_its_ip_changes(gateway):
    """A redeploy recreates the gateway with a new address. The new one is a miss, and a miss
    re-resolves, so it is believed on its first request; the old address stops counting."""
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    gateway.addrs = [NEW_GATEWAY]
    gateway.now += 60
    assert client_from(NEW_GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    gateway.now += 60
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 401
    assert len(gateway.calls) == 3


def test_a_miss_re_resolves_at_most_once_per_interval(gateway):
    """A loop of forged requests is a loop of misses. One lookup per interval, however many."""
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    stranger = client_from(SIBLING)
    gateway.now += settings.gateway_resolve_interval
    for _ in range(25):
        assert stranger.get("/api/capabilities", headers=USER).status_code == 401
        gateway.now += 0.1
    assert len(gateway.calls) == 2


def test_inside_the_interval_a_new_gateway_waits_for_the_next_lookup(gateway):
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    gateway.addrs = [NEW_GATEWAY]
    gateway.now += 1
    assert client_from(NEW_GATEWAY).get("/api/capabilities", headers=USER).status_code == 401
    gateway.now += settings.gateway_resolve_interval
    assert client_from(NEW_GATEWAY).get("/api/capabilities", headers=USER).status_code == 200


def test_a_failed_lookup_keeps_the_last_answer(gateway, caplog):
    caplog.set_level(logging.WARNING, logger="openmaic.identity")
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    gateway.addrs = socket.gaierror(-2, "Name or service not known")
    gateway.now += 60
    assert client_from(SIBLING).get("/api/capabilities", headers=USER).status_code == 401
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 200
    assert "cannot resolve the gateway host 'gateway'" in caplog.text


def test_a_gateway_that_never_resolves_believes_nobody(gateway):
    gateway.addrs = socket.gaierror(-2, "Name or service not known")
    assert client_from(GATEWAY).get("/api/capabilities", headers=USER).status_code == 401


# --- addresses: IPv6, IPv4-mapped ------------------------------------------------------------

@pytest.mark.parametrize("resolved, peer", [
    ([GATEWAY], "::ffff:" + GATEWAY),          # a dual-stack listener reports IPv4 peers mapped
    (["::ffff:" + GATEWAY], GATEWAY),          # and a lookup can answer mapped
    (["fd00:89::2"], "fd00:89:0:0:0:0:0:2"),   # IPv6, spelled two ways
    (["fe80::2%eth0"], "fe80::2"),             # a scope id is not part of the address
])
def test_the_gateway_is_matched_whichever_way_the_address_is_spelled(gateway, resolved, peer):
    gateway.addrs = resolved
    assert client_from(peer).get("/api/capabilities", headers=USER).status_code == 200


@pytest.mark.parametrize("resolved, peer", [
    ([GATEWAY], "::ffff:10.89.0.3"),           # mapped, but a different IPv4 address
    (["fd00:89::2"], "fd00:89::3"),
    ([GATEWAY], "::1"),
])
def test_a_near_miss_is_still_a_stranger(gateway, resolved, peer):
    gateway.addrs = resolved
    assert client_from(peer).get("/api/capabilities", headers=USER).status_code == 401


# --- X-Forwarded-For cannot turn loopback into the gateway ------------------------------------

@pytest.fixture()
def uvicorn_default():
    """The app as uvicorn serves it with its DEFAULT proxy handling: X-Forwarded-For rewrites the
    client whenever the socket's peer is 127.0.0.1. The image turns that off (the Dockerfile test
    below); this proves the check holds even where it is on."""
    return ProxyHeadersMiddleware(app_module.app, trusted_hosts="127.0.0.1")


@pytest.mark.parametrize("xff", [GATEWAY, f"203.0.113.9, {GATEWAY}", f"{GATEWAY}:4242",
                                 f"[::ffff:{GATEWAY}]:4242"])
def test_x_forwarded_for_from_loopback_cannot_claim_the_gateway(uvicorn_default, xff):
    resp = client_from("127.0.0.1", uvicorn_default).get(
        "/api/capabilities", headers={**USER, "X-Forwarded-For": xff})
    assert resp.status_code == 401


def test_the_gateways_own_forwarded_for_is_not_a_false_positive(uvicorn_default):
    """The gateway passes on the X-Forwarded-For caddy wrote, which names the browser's side."""
    resp = client_from(GATEWAY, uvicorn_default).get(
        "/api/capabilities", headers={**USER, "X-Forwarded-For": "172.18.0.1"})
    assert resp.status_code == 200


def test_the_image_runs_uvicorn_without_proxy_headers():
    """uvicorn's --proxy-headers is ON by default and trusts 127.0.0.1."""
    cmd = re.search(r"^CMD\s+(\[.*\])\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert cmd, "no exec-form CMD in the Dockerfile"
    argv = json.loads(cmd.group(1))
    assert argv[:2] == ["uvicorn", "openmaic_app.api.app:app"], argv
    assert "--no-proxy-headers" in argv and "--proxy-headers" not in argv, argv
    assert not any(a.startswith("--forwarded-allow-ips") for a in argv), argv


# --- the drop log --------------------------------------------------------------------------------

def test_drops_are_logged_but_at_most_once_a_second(gateway, caplog):
    caplog.set_level(logging.WARNING, logger="openmaic.identity")
    stranger = client_from(SIBLING)
    for _ in range(10):
        stranger.get("/api/capabilities", headers=USER)
    gateway.now += 1.0
    stranger.get("/api/capabilities", headers=USER)
    lines = [json.loads(r.getMessage()) for r in caplog.records
             if '"identity_dropped"' in r.getMessage()]
    assert [ln["suppressed"] for ln in lines] == [0, 9]
    assert lines[0]["peer"] == SIBLING and lines[0]["path"] == "/api/capabilities"
    assert lines[0]["reason"] == "not the gateway"


# --- compose: the name resolves to the gateway, on a network both share ------------------------

def _networks(svc: dict) -> set[str]:
    nets = svc.get("networks")
    if nets is None:
        return {"default"}
    return set(nets if isinstance(nets, list) else nets.keys())


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=["full", "lean"])
def test_the_rail_looks_up_the_gateway_by_its_service_name_on_a_shared_network(compose,
                                                                            monkeypatch):
    """The rail believes identity headers only from what `gateway_host` resolves to. That name
    must be the gateway's compose service, on a network both containers join, or the lookup
    answers nothing (no identity is ever believed) or, worse, someone else. Nothing in compose
    sets it: the default, "gateway", is right on both stacks."""
    yaml = pytest.importorskip("yaml")
    if not compose.is_file():
        pytest.skip(f"no {compose.name} beside this rail (an extracted rail)")
    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    env = services["openmaic"].get("environment") or {}
    monkeypatch.delenv("OPENMAIC_GATEWAY_HOST", raising=False)
    host = env.get("OPENMAIC_GATEWAY_HOST") or Settings(_env_file=None).gateway_host
    assert host == "gateway" and host in services
    assert _networks(services["openmaic"]) & _networks(services["gateway"])
    assert "network_mode" not in services["openmaic"]
