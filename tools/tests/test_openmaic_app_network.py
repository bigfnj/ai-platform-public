"""openmaic-app runs on a compose network it shares with the openmaic rail and with nothing else.

THE DEFECT. Every rail but terminal-fun and openmaic believes X-Platform-User / X-Platform-Admin
from anything on the compose network that sends them (docs/RAIL_CONTRACT.md, "Identity: fail
closed"). openmaic-app is the upstream OpenMAIC Next.js app (THU-MAIC/OpenMAIC), third-party code
built from an upstream checkout, and it had no `networks:` key in either compose file, so it sat
on `default` beside every rail. An upstream release that turned hostile, or a server-side fetch in
it that can be steered, could name any user, admin included, to every other rail.

THE FIX: openmaic-app joins ONLY `openmaic_app_net`; the openmaic rail joins it as well as
`default`, where the gateway reaches the rail; nothing else joins it. The rail reverse-proxies the
app by the app's service name (OPENMAIC_APP_URL) and the app generates through the rail's shim by
the rail's (OLLAMA_BASE_URL), so each name has to be on the network the two share. The network is
an ordinary bridge: an OPENMAIC_LLM_BASE_URL endpoint (a LAN host, a hosted provider) and anything
else the app is configured to call are outside the stack.

Both compose files, because the lean installer is a separate file a fix can forget, and it is the
stack a podman box runs. A box's own override (PLATFORM_COMPOSE_OVERRIDE) can build openmaic-app
from an upstream checkout by adding a `build:` and nothing else; the last test renders that
combination with the compose CLI wherever one is installed (`config` needs no daemon). The rest of
each file's layout, every other service on `default` alone, is test_terminal_fun_network.py's.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "deploy" / "docker-compose.yml"
LEAN = REPO / "deploy" / "installer" / "docker-compose.installer.yml"
OM_CONFIG = REPO / "rails" / "openmaic" / "backend" / "openmaic_app" / "config.py"

APP_NET = "openmaic_app_net"
APP, RAIL = "openmaic-app", "openmaic"
COMPOSE_FILES = [pytest.param(FULL, id="full"), pytest.param(LEAN, id="lean")]
#: What `compose config` needs to select the two services: the lean file gates them on a profile.
PROFILES = {FULL: [], LEAN: ["--profile", "openmaic"]}


def _cfg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _networks(svc: dict) -> list[str]:
    """The networks a service joins, as compose reads them: list form or mapping form, and the
    implicit `default` when the key is absent."""
    nets = svc.get("networks")
    if nets is None:
        return ["default"]
    return sorted(nets if isinstance(nets, list) else nets.keys())


def _on_app_net(cfg: dict) -> list[str]:
    return sorted(n for n, s in cfg["services"].items() if APP_NET in _networks(s))


def _exposed(svc: dict) -> set[str]:
    return {str(p) for p in svc.get("expose") or []}


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_openmaic_app_is_on_its_own_network_only(path):
    svc = _cfg(path)["services"][APP]
    assert _networks(svc) == [APP_NET], (
        "openmaic-app must join ONLY its own network: on `default` third-party code reaches "
        "every rail, and every rail but two believes an identity header from it")
    assert "network_mode" not in svc, "network_mode replaces the networks list entirely"


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_rail_joins_default_and_the_app_network(path):
    """`default` because the gateway reaches the rail there (PLATFORM_APP_OPENMAIC_URL), the app
    network because nothing else reaches the app now. A service that lists networks gets ONLY
    those: without `default` the gateway loses the rail, without the other the rail loses the
    app."""
    assert _networks(_cfg(path)["services"][RAIL]) == sorted(["default", APP_NET])


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_nothing_but_the_rail_and_the_app_joins_the_app_network(path):
    """A third member is a container the app can open a socket to again: for any rail but
    terminal-fun and openmaic, one that believes an identity header from it."""
    assert _on_app_net(_cfg(path)) == sorted([APP, RAIL])


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_app_network_is_an_ordinary_bridge(path):
    """NOT `internal: true`: that cuts every address outside the stack, and an
    OPENMAIC_LLM_BASE_URL endpoint is one by definition. NOT external: compose creates and owns
    it, so a box needs no out-of-band `docker network create` before the recreate."""
    nets = _cfg(path).get("networks") or {}
    assert APP_NET in nets, f"{APP_NET} is used but not declared"
    spec = nets[APP_NET] or {}
    assert not spec.get("internal"), "internal: true cuts the app off every endpoint outside"
    assert not spec.get("external"), "an external network is one compose does not create"
    assert spec.get("driver", "bridge") == "bridge"


def test_both_compose_files_agree():
    """The same two members with the same networks, and the same network, in both files: the
    lean installer is the one a fix forgets, and a podman box runs it."""
    full, lean = _cfg(FULL), _cfg(LEAN)
    assert _on_app_net(full) == _on_app_net(lean)
    for name in (APP, RAIL):
        assert _networks(full["services"][name]) == _networks(lean["services"][name]), name
    assert (full["networks"].get(APP_NET) or {}) == (lean["networks"].get(APP_NET) or {})


def _rail_default(field: str) -> str:
    tree = ast.parse(OM_CONFIG.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == field):
            return ast.literal_eval(node.value)
    raise AssertionError(f"openmaic_app/config.py no longer declares {field}")


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_rail_reaches_the_app_by_its_service_name(path):
    """The proxy's upstream (compose's OPENMAIC_APP_URL, or the rail's own default if compose
    stops setting it) names openmaic-app at a port it exposes, and the two share a network for
    that name to resolve on: the app network, the only one the app is on."""
    services = _cfg(path)["services"]
    env = services[RAIL].get("environment") or {}
    url = urlsplit(env.get("OPENMAIC_APP_URL") or _rail_default("app_url"))
    assert url.hostname == APP
    assert str(url.port) in _exposed(services[APP])
    assert set(_networks(services[RAIL])) & set(_networks(services[APP])) == {APP_NET}


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_the_app_reaches_the_shim_by_the_rails_service_name(path):
    """With OPENMAIC_LLM_BASE_URL unset the app generates through the default inside
    `${OPENMAIC_LLM_BASE_URL:-...}`: the rail's shim, by the rail's service name, at a port the
    rail exposes. On the app network that name is the one other service that resolves."""
    services = _cfg(path)["services"]
    raw = services[APP]["environment"]["OLLAMA_BASE_URL"]
    m = re.fullmatch(r"\$\{OPENMAIC_LLM_BASE_URL:-([^}]+)\}", raw)
    assert m, f"OLLAMA_BASE_URL is no longer the override with the shim as its default: {raw!r}"
    url = urlsplit(m.group(1))
    assert (url.hostname, url.path) == (RAIL, "/api/llm/v1")
    assert str(url.port) in _exposed(services[RAIL])


def _compose_cli() -> list[str] | None:
    """The standalone `docker-compose`, else the docker CLI's plugin. Never podman's wrapper: it
    may start a machine to answer."""
    exe = shutil.which("docker-compose")
    if exe:
        return [exe]
    exe = shutil.which("docker")
    return [exe, "compose"] if exe else None


def _rendered_networks(files: list[Path], profiles: list[str],
                       env_file: Path) -> tuple[dict, dict]:
    """({service: its networks, sorted}, the rendered services) from `compose config`."""
    cmd = [*_compose_cli(), "--env-file", str(env_file)]
    for f in files:
        cmd += ["-f", str(f)]
    # COMPOSE_FILE / COMPOSE_PROFILES / COMPOSE_PROJECT_NAME in the caller's shell would change
    # what is rendered; nothing else in the environment can change a networks list.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COMPOSE_")}
    p = subprocess.run([*cmd, *profiles, "config", "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60, env=env)
    assert p.returncode == 0, p.stderr
    rendered = json.loads(p.stdout)["services"]
    return {n: sorted(s.get("networks") or {}) for n, s in rendered.items()}, rendered


@pytest.mark.skipif(_compose_cli() is None,
                    reason="no compose CLI on PATH, so the override is not rendered here; the "
                           "YAML-level tests above still pin the layout")
@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_a_box_override_that_only_builds_the_app_keeps_the_layout(path, tmp_path):
    """What `docker compose config` renders, base alone and base plus an override that gives
    openmaic-app a `build:` (as a box building the app from its own checkout does), equals what
    this file reads from the YAML, for every service rendered. The override has to have landed,
    or the comparison proves nothing."""
    override = tmp_path / "override.yml"
    override.write_text("services:\n  openmaic-app:\n    build:\n"
                        f"      context: {json.dumps(tmp_path.as_posix())}\n", encoding="utf-8")
    env_file = tmp_path / "empty.env"     # the box's own deploy/.env must not decide the result
    env_file.write_text("", encoding="utf-8")
    base, _ = _rendered_networks([path], PROFILES[path], env_file)
    merged, services = _rendered_networks([path, override], PROFILES[path], env_file)
    assert "build" in services[APP], "the override did not reach openmaic-app"
    declared = {n: _networks(s) for n, s in _cfg(path)["services"].items() if n in base}
    assert {APP, RAIL} <= set(base)
    assert base == declared
    assert merged == base
