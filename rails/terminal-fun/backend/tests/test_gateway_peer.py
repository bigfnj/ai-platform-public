"""Platform identity headers count only when they come from the gateway's address.

THE DEFECT. Every rail trusts X-Platform-User / X-Platform-Admin when present:
the gateway strips client copies and sets its own, so the header is believed. This rail hands
players a real bash on purpose (NetHack's `!`, tmux/byobu panes), and from that shell, as
gameuser, `curl http://recipe-book:8830/api/capabilities` answered 401 while the same request with
`-H "X-Platform-User: admin"` answered 200. Any player could be any user, admin included, to
every rail, and to THIS backend over loopback: another player's saves, the assistant as them.

The compose files now put this container on a network it shares only with the gateway
(tools/tests/test_terminal_fun_network.py). That cannot cover loopback, so main.py refuses any
x-platform-* header from a peer that is not one of the gateway's addresses. These tests drive the
real app through Starlette's TestClient with the peer address set, and a stubbed lookup of the
gateway's name (conftest.py `gateway`), which is the whole of what the check reads.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from terminal_fun_app import main, saves
from terminal_fun_app.config import settings

GATEWAY = "10.89.0.2"           # what conftest's `gateway` fixture resolves the name to
USER ={"X-Platform-User": "admin", "X-Platform-Admin": "1"}
DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "Dockerfile"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "nethack_save_dir", str(tmp_path / "nh"))
    monkeypatch.setattr(main.broker, "up", lambda: True)      # healthz must not dial a broker


def client_from(peer: str, app=None) -> TestClient:
    return TestClient(app or main.app, raise_server_exceptions=False, client=(peer, 50000))


# --- a forged identity from anywhere but the gateway ----------------------------------------

# The peers a player's shell can actually be: loopback both ways, this container's own address,
# and (before the network fix) a sibling's. None of them is the gateway.
STRANGERS = ["127.0.0.1", "::1", "10.89.0.3", "172.18.0.9"]


@pytest.mark.parametrize("peer", STRANGERS)
def test_a_forged_identity_from_a_non_gateway_peer_is_refused(gateway, peer):
    resp = client_from(peer).get("/api/saves", headers=USER)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform identity is accepted from the gateway only"


@pytest.mark.parametrize("header", ["X-Platform-User", "X-Platform-Admin", "X-Platform-Peers",
                                    "X-Platform-Rooms", "X-Platform-Anything"])
def test_any_platform_header_counts_not_just_the_user(gateway, header):
    """The gateway strips the whole x-platform-* family from clients, so any member arriving from
    a stranger is a forgery, including ones no route reads today."""
    assert client_from("127.0.0.1").get("/api/catalog", headers={header: "1"}).status_code == 403


def test_the_refusal_comes_before_the_route_runs(gateway, tmp_path):
    """The route that deletes. A forged DELETE must not reach saves.discard() at all."""
    home = tmp_path / "home"
    (home / ".crawl" / "saves").mkdir(parents=True)
    (home / ".crawl" / "saves" / "game.cs").write_text("SAVEDATA")
    saves.capture("admin", "crawl", str(home))

    assert client_from("127.0.0.1").delete("/api/saves/crawl", headers=USER).status_code == 403
    assert client_from(GATEWAY).get("/api/saves", headers=USER).json()["saves"] == ["crawl"]


def _handshake(peer: str, path: str, headers: dict) -> int:
    """The close code a handshake is refused with, asserting it was refused BEFORE accept: the
    session's __enter__ raises only when the first thing the app sends is a close. An accept
    followed by a close returns a session instead, and that fails here."""
    session = client_from(peer).websocket_connect(path, headers=headers)
    try:
        session.__enter__()
    except WebSocketDisconnect as exc:
        return exc.code
    with contextlib.suppress(Exception):      # release the session's thread before failing
        session.__exit__(None, None, None)
    pytest.fail(f"the handshake on {path} from {peer} was ACCEPTED")


@pytest.mark.parametrize("peer", STRANGERS)
def test_a_forged_identity_on_the_terminal_socket_is_closed_before_accept(gateway, peer):
    """The PTY socket is the prize: it spawns a process and owns the player's save. 4403 is the
    middleware's own code, so the handler (and its 4401/4404/4400 codes) is never reached."""
    assert _handshake(peer, "/ws/nethack", USER) == 4403


def test_a_peer_that_is_not_an_address_is_refused(gateway):
    """Starlette's TestClient default peer is the NAME "testclient". A TCP peer is always an
    address, so a name could only have come from a header, and even a lookup that answered with
    that name must not make it trusted."""
    gateway.addrs = ["testclient", GATEWAY]
    assert client_from("testclient").get("/api/catalog", headers=USER).status_code == 403
    assert main.gateway_peers.allows(None) is False
    assert main.gateway_peers.allows("") is False


# --- the gateway itself ---------------------------------------------------------------------

def test_the_gateway_peer_is_accepted(gateway):
    c = client_from(GATEWAY)
    assert c.get("/api/catalog", headers=USER).status_code == 200
    assert c.get("/api/saves", headers=USER).status_code == 200
    assert gateway.calls == [settings.gateway_host], "one lookup, of the configured name"


def test_the_gateway_reaches_the_terminal_socket(gateway):
    """Past the middleware and into the handler: the web toy's 4400 is the handler's own answer
    (web toys have no PTY), which only a handshake the middleware let through can get."""
    assert _handshake(GATEWAY, "/ws/eyeballs", USER) == 4400


def test_the_configured_gateway_host_is_the_name_looked_up(gateway, monkeypatch):
    monkeypatch.setattr(settings, "gateway_host", "my-gateway")
    client_from(GATEWAY).get("/api/catalog", headers=USER)
    assert gateway.calls == ["my-gateway"]


def test_healthz_reports_the_check_as_on(gateway):
    body = client_from(GATEWAY).get("/api/healthz", headers=USER).json()
    assert body["peer_check"] == "on" and body["items"] == len(main.ITEMS)


# --- a request with no platform header ------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/healthz", "/api/catalog", "/api/saves"])
def test_a_request_with_no_platform_header_is_untouched(gateway, path):
    """Not refused by the peer check (403) but by the identity gate it always met (401), and no
    lookup is spent on it: a header-less probe costs this check nothing."""
    assert client_from("127.0.0.1").get(path).status_code == 401
    assert gateway.calls == []


def test_a_headerless_health_probe_still_answers_in_standalone(gateway, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client_from("127.0.0.1").get("/api/healthz").status_code == 200


# --- standalone ------------------------------------------------------------------------------

def test_standalone_keeps_todays_behaviour(gateway, monkeypatch):
    """No gateway in front, so nothing to hold the header to: a header from loopback is honoured
    as it always was, and the name is never looked up."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    c = client_from("127.0.0.1")
    assert c.get("/api/saves", headers=USER).status_code == 200
    assert _handshake("127.0.0.1", "/ws/eyeballs", USER) == 4400
    assert gateway.calls == []
    assert c.get("/api/healthz").json()["peer_check"] == "off (PLATFORM_STANDALONE)"


# --- the lookup: cached, re-resolved on a miss, rate-limited ----------------------------------

NEW_GATEWAY = "10.89.0.7"


def test_a_recreated_gateway_is_recognised_after_its_ip_changes(gateway):
    """A redeploy recreates the gateway with a new address. The new one is a miss, and a miss
    re-resolves (here: long after the last lookup), so it is accepted on its first request; the
    old address, gone from the new answer, stops counting."""
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    gateway.addrs = [NEW_GATEWAY]
    gateway.now += 60
    assert client_from(NEW_GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    gateway.now += 60
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 403
    assert len(gateway.calls) == 3


def test_a_miss_re_resolves_at_most_once_per_interval(gateway):
    """A loop of forged requests is a loop of misses. One lookup per interval, however many."""
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    stranger = client_from("127.0.0.1")
    gateway.now += settings.gateway_resolve_interval   # the interval has passed: one lookup
    for _ in range(25):
        assert stranger.get("/api/catalog", headers=USER).status_code == 403
        gateway.now += 0.1
    assert len(gateway.calls) == 2


def test_inside_the_interval_a_new_gateway_waits_for_the_next_lookup(gateway):
    """The other side of the rate limit, pinned so it cannot quietly grow: a gateway that changed
    address moments after a lookup is refused until the interval has run, then accepted."""
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    gateway.addrs = [NEW_GATEWAY]
    gateway.now += 1
    assert client_from(NEW_GATEWAY).get("/api/catalog", headers=USER).status_code == 403
    gateway.now += settings.gateway_resolve_interval
    assert client_from(NEW_GATEWAY).get("/api/catalog", headers=USER).status_code == 200


def test_a_failed_lookup_keeps_the_last_answer(gateway, caplog):
    caplog.set_level(logging.WARNING, logger="terminal-fun")
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    gateway.addrs = socket.gaierror(-2, "Name or service not known")
    gateway.now += 60
    assert client_from("127.0.0.1").get("/api/catalog", headers=USER).status_code == 403
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 200
    assert "cannot resolve the gateway host 'gateway'" in caplog.text


def test_a_gateway_that_never_resolves_admits_nobody(gateway):
    gateway.addrs = socket.gaierror(-2, "Name or service not known")
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 403


# --- addresses: IPv6, IPv4-mapped, loopback, this container's own ----------------------------

@pytest.mark.parametrize("resolved, peer", [
    ([GATEWAY], "::ffff:" + GATEWAY),          # a dual-stack listener reports IPv4 peers mapped
    (["::ffff:" + GATEWAY], GATEWAY),          # and a lookup can answer mapped
    (["fd00:89::2"], "fd00:89:0:0:0:0:0:2"),   # IPv6, spelled two ways
    (["fe80::2%eth0"], "fe80::2"),             # a scope id is not part of the address
    ([GATEWAY, "fd00:89::2"], "fd00:89::2"),   # a dual-stack gateway, either family
])
def test_the_gateway_is_matched_whichever_way_the_address_is_spelled(gateway, resolved, peer):
    gateway.addrs = resolved
    assert client_from(peer).get("/api/catalog", headers=USER).status_code == 200


@pytest.mark.parametrize("resolved, peer", [
    ([GATEWAY], "::ffff:10.89.0.3"),           # mapped, but a different IPv4 address
    (["fd00:89::2"], "fd00:89::3"),
    (["fd00:89::2"], "::ffff:" + GATEWAY),     # an IPv4 peer is not an IPv6 gateway
    ([GATEWAY], "::1"),
])
def test_a_near_miss_is_still_a_stranger(gateway, resolved, peer):
    gateway.addrs = resolved
    assert client_from(peer).get("/api/catalog", headers=USER).status_code == 403


def test_loopback_is_trusted_only_when_the_gateway_resolves_to_it(gateway, caplog):
    """A dev box with the gateway on localhost. It works, and it says it is degraded, because then
    any process here, a player's shell included, can assert an identity."""
    caplog.set_level(logging.WARNING, logger="terminal-fun")
    assert client_from("127.0.0.1").get("/api/catalog", headers=USER).status_code == 403
    gateway.addrs = ["127.0.0.1", "::1"]
    gateway.now += 60
    c = client_from("127.0.0.1")
    assert c.get("/api/catalog", headers=USER).status_code == 200
    assert c.get("/api/healthz", headers=USER).json()["peer_check"] == "degraded"
    assert "DEGRADED identity check" in caplog.text


def test_the_mode_looks_the_gateway_up_before_it_reports(gateway):
    """mode() read "on" while nothing had been looked up, so a dev box whose gateway resolves to
    localhost reported "on" until the first lookup. /api/healthz itself needs an identity (a
    header-less GET is a 401), so every HTTP reader triggers a lookup first; mode() no longer
    relies on that."""
    gateway.addrs = ["127.0.0.1", "::1"]
    assert main.gateway_peers.mode() == "degraded"
    assert gateway.calls, "mode() did not look the gateway up"


def test_a_gateway_name_that_never_resolved_reports_unresolved(gateway):
    gateway.addrs = socket.gaierror("no such host")
    assert main.gateway_peers.mode() == "unresolved"
    assert client_from(GATEWAY).get("/api/catalog", headers=USER).status_code == 403


def test_the_containers_own_address_is_degraded_too(gateway, monkeypatch):
    monkeypatch.setattr(main, "_own_addresses", lambda: frozenset({"10.89.0.3"}))
    gateway.addrs = ["10.89.0.3"]
    c = client_from("10.89.0.3")
    assert c.get("/api/catalog", headers=USER).status_code == 200
    assert main.gateway_peers.mode() == "degraded"


def test_a_real_gateway_address_is_not_degraded(gateway, monkeypatch):
    monkeypatch.setattr(main, "_own_addresses", lambda: frozenset({"10.89.0.3"}))
    client_from(GATEWAY).get("/api/catalog", headers=USER)
    assert main.gateway_peers.mode() == "on"


# --- X-Forwarded-For cannot turn loopback into the gateway ------------------------------------

@pytest.fixture()
def uvicorn_default():
    """The app as uvicorn serves it with its DEFAULT proxy handling: X-Forwarded-For rewrites the
    client whenever the socket's peer is 127.0.0.1. The image turns that off (the Dockerfile test
    below); this proves the guard holds even where it is on."""
    return ProxyHeadersMiddleware(main.app, trusted_hosts="127.0.0.1")


@pytest.mark.parametrize("xff", [GATEWAY, f"203.0.113.9, {GATEWAY}", f"{GATEWAY}:4242",
                                 f"[::ffff:{GATEWAY}]:4242"])
def test_x_forwarded_for_from_loopback_cannot_claim_the_gateway(gateway, uvicorn_default, xff):
    resp = client_from("127.0.0.1", uvicorn_default).get(
        "/api/saves", headers={**USER, "X-Forwarded-For": xff})
    assert resp.status_code == 403


def test_the_gateways_own_forwarded_for_is_not_a_false_positive(gateway, uvicorn_default):
    """The gateway passes on the X-Forwarded-For caddy wrote, which names the browser's side. That
    must not trip the check (the gateway is not a trusted proxy, so nothing is rewritten)."""
    resp = client_from(GATEWAY, uvicorn_default).get(
        "/api/saves", headers={**USER, "X-Forwarded-For": "172.18.0.1"})
    assert resp.status_code == 200


def test_the_image_runs_uvicorn_without_proxy_headers():
    """The fix for the rewrite itself. uvicorn's --proxy-headers is ON by default and trusts
    127.0.0.1, which is precisely where a player's shell connects from."""
    cmd = re.search(r"^CMD\s+(\[.*\])\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert cmd, "no exec-form CMD in the Dockerfile"
    argv = json.loads(cmd.group(1))
    assert argv[:2] == ["uvicorn", "terminal_fun_app.main:app"], argv
    assert "--no-proxy-headers" in argv and "--proxy-headers" not in argv, argv
    assert not any(a.startswith("--forwarded-allow-ips") for a in argv), argv


# --- the refusal log ---------------------------------------------------------------------------

def test_refusals_are_logged_but_at_most_once_a_second(gateway, caplog):
    caplog.set_level(logging.INFO, logger="terminal-fun")
    stranger = client_from("127.0.0.1")
    for _ in range(10):
        stranger.get("/api/catalog", headers=USER)
    gateway.now += 1.0
    stranger.get("/api/catalog", headers=USER)
    lines = [json.loads(r.getMessage()) for r in caplog.records
             if '"identity_refused"' in r.getMessage()]
    assert [ln["suppressed"] for ln in lines] == [0, 9]
    assert lines[0]["peer"] == "127.0.0.1" and lines[0]["path"] == "/api/catalog"
