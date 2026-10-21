"""One load of the Rails tab costs each remote box ONE probe, counted over real HTTP.

Before this, a load asked the broker three things in turn: the roles (whose sweep dialled each
delegated remote's /healthz, /v1/models and /v1/status), the upstream list (each /healthz
AGAIN), and each box's models (each /v1/models AGAIN). Harmless while every remote answers
quickly, and twice the wait the moment one is slow, on a tab whose whole job is to be looked at
when something is wrong.

Nothing is stubbed between the gateway and the remote here: the gateway's real `_rails_payload`
calls a real `BrokerClient`, which reaches the broker's real FastAPI app over an ASGI transport,
whose real `UpstreamClient` reaches the remote boxes over HTTP. Only the far ends are fakes:
the remotes (an httpx.MockTransport counting every request by box and path) and the broker's
local Ollama. So the count is what a remote would see in its own access log.

Two remotes, because they take different paths: `offsite` runs a delegated role (so its
residency is read too), `lab` is registered but runs nothing (health and inventory only, for
the picker).
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

# The broker is imported from THIS tree. A gateway test run on its own does not put
# services/broker on sys.path, and the venv's editable install of the broker points at whichever
# checkout installed it, which in a worktree is the wrong one.
_BROKER_DIR = Path(__file__).resolve().parents[4] / "services" / "broker"
if str(_BROKER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROKER_DIR))

import app as broker_pkg  # noqa: E402
from app import main as broker_main  # noqa: E402
from app import upstream as up_mod  # noqa: E402
from app.broker import Broker  # noqa: E402
from app.config import BrokerSettings  # noqa: E402
from platform_core import BrokerClient  # noqa: E402
from platform_gateway_app import main as gw  # noqa: E402
from platform_gateway_app.config import GatewaySettings  # noqa: E402

OFFSITE = "offsite.test"
LAB = "lab.test"
LOCAL_TAGS = [{"name": "gemma4:12b", "size": 1, "digest": "d1",
               "details": {"family": "gemma", "parameter_size": "12B"}}]


def test_the_broker_under_test_is_this_trees():
    assert Path(broker_pkg.__file__).resolve().is_relative_to(_BROKER_DIR), broker_pkg.__file__


def _remote_handler(hits: Counter):
    remotes = {
        OFFSITE: [{"name": "mistral-small3.2:24b", "class": "heavy", "parameter_size": "24B"}],
        LAB: [{"name": "qwen3:8b", "class": "heavy", "parameter_size": "8B"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        hits[(host, path)] += 1
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/models":
            return httpx.Response(200, json={"models": remotes[host]})
        if path == "/v1/status":
            return httpx.Response(200, json={"loaded": []})
        if path == "/v1/roles":
            return httpx.Response(200, json={"roles": []})
        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


def _ollama_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/tags":
        return httpx.Response(200, json={"models": LOCAL_TAGS})
    if path == "/api/show":
        return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
    if path == "/api/ps":
        return httpx.Response(200, json={"models": []})
    if path == "/api/version":
        return httpx.Response(200, json={"version": "test"})
    return httpx.Response(404)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Gateway -> BrokerClient -> broker app (ASGI) -> UpstreamClient -> counted remotes."""
    for key in ("BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE", "PLATFORM_ENABLED_APPS"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "roles.json").write_text(json.dumps({
        "recipe": "offsite::mistral-small3*:24b",
        "recipe-vision": "gemma4*:12b",
    }), encoding="utf-8")
    (tmp_path / "upstreams.json").write_text(json.dumps({
        "offsite": {"url": f"http://{OFFSITE}:11500", "token": "t"},
        "lab": {"url": f"http://{LAB}:11500"},
    }), encoding="utf-8")
    settings = BrokerSettings(
        roles_file=str(tmp_path / "roles.json"),
        upstreams_file=str(tmp_path / "upstreams.json"),
        disabled_file=str(tmp_path / "disabled.json"),
        tokens_file=str(tmp_path / "tokens.json"), auth_token="")
    broker = Broker(settings)
    broker.ollama._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(_ollama_handler))
    monkeypatch.setattr(broker_main.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(broker_main.app.state, "broker", broker, raising=False)
    monkeypatch.setattr(broker_main.app.state, "token_seen", {}, raising=False)

    hits: Counter = Counter()
    real = httpx.AsyncClient
    # Patched for upstream.py ONLY, so nothing else in the chain is pointed at the fake remotes.
    # Every name upstream.py reads from httpx: without `Limits` (its pool's keep-alive expiry,
    # 2026-10-14) building a client raised AttributeError, which healthy() reads as a dark box.
    monkeypatch.setattr(up_mod, "httpx", SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=httpx.MockTransport(_remote_handler(hits)),
                                      **kw),
        Limits=httpx.Limits, Response=httpx.Response,
        HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError))

    broker_hits: Counter = Counter()

    async def _count(request: httpx.Request) -> None:
        broker_hits[(request.method, request.url.path, request.url.query.decode())] += 1

    client = BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.ASGITransport(app=broker_main.app),
        event_hooks={"request": [_count]}))
    monkeypatch.setattr(gw.app.state, "broker", client, raising=False)
    monkeypatch.setattr(gw.app.state, "settings",
                        GatewaySettings(enabled_apps=("recipe-book",)), raising=False)
    return SimpleNamespace(hits=hits, broker_hits=broker_hits)


def _load_tab():
    return asyncio.run(gw._rails_payload(set()))


def test_a_rails_tab_load_probes_each_remote_once(wired):
    payload = _load_tab()
    hits = wired.hits
    # The delegated box: health, inventory, residency, each exactly once.
    assert hits[(OFFSITE, "/healthz")] == 1
    assert hits[(OFFSITE, "/v1/models")] == 1
    assert hits[(OFFSITE, "/v1/status")] == 1
    assert hits[(OFFSITE, "/v1/roles")] == 0          # no remote ROLE is delegated to
    # The registered-but-idle box: health and inventory for the picker, nothing else.
    assert hits[(LAB, "/healthz")] == 1
    assert hits[(LAB, "/v1/models")] == 1
    assert hits[(LAB, "/v1/status")] == 0
    # And the gateway asked its broker exactly once for the whole tab.
    assert sum(wired.broker_hits.values()) == 1, dict(wired.broker_hits)
    assert ("GET", "/v1/roles", "inventory=true") in wired.broker_hits

    # The one probe still produced everything the tab shows.
    assert [u["name"] for u in payload["upstreams"]] == ["local", "lab", "offsite"]
    assert all(u["healthy"] for u in payload["upstreams"])
    assert [m["name"] for m in payload["models"]["offsite"]] == ["mistral-small3.2:24b"]
    assert [m["name"] for m in payload["models"]["lab"]] == ["qwen3:8b"]
    assert [m["name"] for m in payload["models"]["local"]] == ["gemma4:12b"]
    recipe = next(s for r in payload["rails"] for s in r["slots"] if s["role"] == "recipe")
    assert recipe["upstream"] == "offsite" and recipe["installed"] is True
    assert recipe["pattern"] == "mistral-small3*:24b"


def test_a_dark_remote_costs_one_health_probe_and_nothing_else(wired, monkeypatch):
    """A box that is down is asked /healthz once and never dialled for a read."""
    real_handler = _remote_handler(wired.hits)

    def handler(request):
        if request.url.host == LAB:
            wired.hits[(LAB, request.url.path)] += 1
            return httpx.Response(503, json={"status": "degraded"})
        return real_handler(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(up_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    payload = _load_tab()
    assert wired.hits[(LAB, "/healthz")] == 1
    assert wired.hits[(LAB, "/v1/models")] == 0
    assert payload["models"]["lab"] == []
    assert {u["name"]: u["healthy"] for u in payload["upstreams"]}["lab"] is False
    assert wired.hits[(OFFSITE, "/healthz")] == 1     # the other box is unaffected


# --- the Models tab: asks about THIS card only, so it dials no remote at all -----------------
#
# Both routes feed _roles_using, which skips delegated rows (their names are the remote's
# models). Fetching the roles with the remote probe cost every Models-tab load, and every
# Delete, a /healthz + /v1/models + /v1/status per delegated box, up to the view bound each.

@pytest.fixture
def no_gpu_probe(monkeypatch):
    from app import gpu
    async def _none():
        return None
    monkeypatch.setattr(gpu, "vram", _none)


def test_the_models_tab_dials_no_remote_box(wired, session, no_gpu_probe):
    out = asyncio.run(gw.admin_models(admin=None, db=session))
    assert sum(wired.hits.values()) == 0, dict(wired.hits)
    assert ("GET", "/v1/roles", "local_only=true") in wired.broker_hits
    gemma = next(m for m in out["models"] if m["name"] == "gemma4:12b")
    assert gemma["in_use"] is True and "recipe-vision" in gemma["roles"]


def test_the_models_tab_reads_residency_from_ps_not_status(wired, session):
    """Only the resident names are used. /v1/status also spawns nvidia-smi and reads the
    queue, so asking it cost a process spawn per tab load for nothing the tab shows."""
    asyncio.run(gw.admin_models(admin=None, db=session))
    paths = [hit[1] for hit in wired.broker_hits]
    assert "/v1/status" not in paths, wired.broker_hits
    assert "/v1/ps" in paths


def test_a_model_delete_check_dials_no_remote_box(wired):
    """The in-use refusal is reached without touching any remote (and before any delete)."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gw.admin_model_delete(gw.ModelBody(model="gemma4:12b"), admin=None))
    assert exc.value.status_code == 409 and "recipe-vision" in exc.value.detail
    assert sum(wired.hits.values()) == 0, dict(wired.hits)


def test_local_only_and_inventory_together_are_refused(wired):
    from platform_core import BrokerError
    with pytest.raises(BrokerError) as exc:
        asyncio.run(gw.app.state.broker.roles(inventory=True, local_only=True))
    assert "-> 400" in str(exc.value)
