"""terminal-fun runs on a compose network it shares with the gateway and with nothing else.

THE DEFECT. Every rail trusts X-Platform-User / X-Platform-Admin when present,
because the gateway strips client copies and sets its own. terminal-fun hands its players a real
bash on purpose (NetHack's `!`, tmux/byobu panes), and its container sat on the compose default
network with every rail. Measured from inside it as gameuser: curl, python3 and bash present,
every rail resolving by name, and `GET http://recipe-book:8830/api/capabilities` answering 401
with no header and 200 with `X-Platform-User: admin`. Any player could be any user, admin
included, to every rail.

THE FIX, network half (the backend half is rails/terminal-fun/backend/tests/test_gateway_peer.py):
terminal-fun is attached ONLY to `terminal_fun_net`; the gateway joins it as well as `default`;
nothing else joins it. Checked in BOTH compose files that run terminal-fun, because the lean
installer is a separate file a fix can forget. The one other departure from `default` alone is
openmaic-app's network, shared with the openmaic rail only (2026-10-14, the same defect from
third-party code); tools/tests/test_openmaic_app_network.py pins it, and LAYOUT below lists it so
this file still names every service that is not on `default` alone.

What this reads is the YAML the compose CLI reads, so each assertion is a fact about what
`docker compose up` builds, not about comments. The same layout was confirmed rendered by
`docker compose config` when this was written. The smoke tool's side (rail_smoke.py probes
every rail off `default` from the gateway's container) is in tools/tests/test_rail_smoke.py,
beside the tool, because the public snapshot withholds both.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "deploy" / "docker-compose.yml"
LEAN = REPO / "deploy" / "installer" / "docker-compose.installer.yml"
BUNDLED = REPO / "deploy" / "Dockerfile.gateway.bundled"
TF_CONFIG = REPO / "rails" / "terminal-fun" / "backend" / "terminal_fun_app" / "config.py"

ISOLATED = "terminal_fun_net"
COMPOSE_FILES = [pytest.param(FULL, id="full"), pytest.param(LEAN, id="lean")]

#: Every service that is not on `default` alone, and exactly what it joins, in both files.
LAYOUT = {
    "gateway": sorted(["default", ISOLATED]),
    "terminal-fun": [ISOLATED],
    "openmaic": sorted(["default", "openmaic_app_net"]),
    "openmaic-app": ["openmaic_app_net"],
}


def _cfg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _networks(svc: dict) -> list[str]:
    """The networks a service joins, as compose reads them: list form or mapping form, and the
    implicit `default` when the key is absent."""
    nets = svc.get("networks")
    if nets is None:
        return ["default"]
    return sorted(nets if isinstance(nets, list) else nets.keys())


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_terminal_fun_is_on_its_own_network_only(path):
    svc = _cfg(path)["services"]["terminal-fun"]
    assert _networks(svc) == [ISOLATED], (
        "terminal-fun must join ONLY its own network: on `default` a player's shell reaches "
        "every rail and can assert any identity to it")
    assert "network_mode" not in svc, "network_mode replaces the networks list entirely"


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_gateway_joins_both_networks(path):
    """`default` for caddy and every other rail, the isolated one for terminal-fun. A service that
    lists networks gets ONLY those, so dropping `default` here would cut the gateway off caddy."""
    assert _networks(_cfg(path)["services"]["gateway"]) == sorted(["default", ISOLATED])


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_nothing_else_joins_the_isolated_network_or_changes_networks(path):
    """The whole file's layout: every service in LAYOUT joins exactly what it lists there, and
    every other one is on `default` alone. So nothing else joins terminal_fun_net either."""
    services = _cfg(path)["services"]
    others = [n for n in services if n not in LAYOUT]
    assert len(others) >= 3, "almost nothing to compare, so this proves nothing"
    assert set(LAYOUT) <= set(services), "a pinned service is missing from this file"
    assert {n: _networks(s) for n, s in services.items() if _networks(s) != ["default"]} == LAYOUT


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_no_service_borrows_another_ones_network_namespace(path):
    """`network_mode: service:gateway` gives a sidecar the gateway's own source address on
    terminal_fun_net, which terminal-fun's peer check trusts; `service:terminal-fun` puts its
    ports on the player's loopback. Either has no `networks` key, so _networks() reads it as
    `default` and the test above passes it. No service sets network_mode today."""
    services = _cfg(path)["services"]
    assert {n: s["network_mode"] for n, s in services.items() if "network_mode" in s} == {}


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_isolated_network_is_an_ordinary_bridge(path):
    """NOT `internal: true`: that also cuts host.docker.internal, which is the broker the
    assistant calls, and plain internet egress. NOT external: compose creates and owns it, so a
    fresh install needs no out-of-band `docker network create`."""
    nets = _cfg(path).get("networks") or {}
    assert ISOLATED in nets, f"{ISOLATED} is used but not declared"
    spec = nets[ISOLATED] or {}
    assert not spec.get("internal"), "internal: true cuts the broker (host.docker.internal)"
    assert not spec.get("external"), "an external network is one compose does not create"
    assert spec.get("driver", "bridge") == "bridge"


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_terminal_fun_keeps_the_broker_and_publishes_nothing(path):
    svc = _cfg(path)["services"]["terminal-fun"]
    assert any(h.startswith("host.docker.internal:") for h in svc.get("extra_hosts") or [])
    assert "ports" not in svc, "a published port is a way in that skips the gateway entirely"


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_no_network_is_named_after_a_service(path):
    """RC033 (tools/rail_conformance.py) reads a `- terminal-fun` list line in the gateway's block
    as its depends_on entry. A network keyed `terminal-fun` would put exactly that line in the
    gateway's `networks:` list and satisfy the rule with the dependency deleted."""
    cfg = _cfg(path)
    joined = {n for s in cfg["services"].values() for n in _networks(s)}
    declared = set(cfg.get("networks") or {}) | joined
    assert declared & set(cfg["services"]) == set()


def test_the_gateway_reaches_terminal_fun_by_its_service_name():
    """Over the shared network, by the name Docker's DNS answers there. Full stack: the gateway's
    own environment. Lean: the bundled image's ENV, which the installer compose does not override."""
    full = _cfg(FULL)["services"]["gateway"]["environment"]["PLATFORM_APP_TERMINAL_FUN_URL"]
    assert full == "http://terminal-fun:8730"
    lean = _cfg(LEAN)["services"]["gateway"].get("environment") or {}
    assert "PLATFORM_APP_TERMINAL_FUN_URL" not in lean
    assert re.search(r"PLATFORM_APP_TERMINAL_FUN_URL=http://terminal-fun:8730\b",
                     BUNDLED.read_text(encoding="utf-8"))


def _default_gateway_host() -> str:
    tree = ast.parse(TF_CONFIG.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "gateway_host"):
            return ast.literal_eval(node.value)
    raise AssertionError("terminal_fun_app/config.py no longer declares gateway_host")


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_terminal_fun_looks_up_the_gateway_by_the_gateways_service_name(path):
    """The backend accepts identity headers only from the addresses its `gateway_host` resolves
    to. That name must be the gateway's compose service, or the lookup answers nothing (every
    request 403s) or, worse, someone else."""
    cfg = _cfg(path)
    env = cfg["services"]["terminal-fun"].get("environment") or {}
    host = env.get("TERMINAL_FUN_GATEWAY_HOST") or _default_gateway_host()
    assert host == "gateway" and host in cfg["services"]

