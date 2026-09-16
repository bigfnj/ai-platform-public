"""Fail-closed identity.

The gateway strips any client-supplied X-Platform-User and sets its own, so a request arriving
here without one did not come through the gateway — on this platform that means a sibling
container. This rail reverse-proxies a whole application and fronts the GPU broker, so an
un-gated caller would get both: somebody else's classroom, and free use of the card.

The /api/llm shim is the interesting case and has its own tests below. It is a MOUNT, and a
mount is not a route — the app-level dependency never runs for it. That is not an oversight
being papered over: its caller is the openmaic-app container, which has no platform identity to
present, so it carries a service-token check of its own. The tests here are what keep "has its
own gate" from quietly becoming "has no gate".
"""
import os

import pytest
from fastapi.testclient import TestClient

from openmaic_app.api import llm
from openmaic_app.api.app import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}

GATED = ["/api/healthz", "/api/capabilities", "/api/app/", "/api/app/some/deep/path"]


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    """The escape hatch is read at call time, so clearing the env is enough."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Keep the suite off the broker and the app container.

    Without this these tests pass or fail depending on whether a broker happens to be running
    on the developer's box, which is the kind of test that gets deleted rather than fixed.
    """
    from openmaic_app import broker

    monkeypatch.setattr(broker, "roles", lambda: [])
    monkeypatch.setattr(broker, "models", lambda: [])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": []})
    monkeypatch.setattr(broker, "up", lambda: False)

    async def _unreachable():
        return False

    monkeypatch.setattr("openmaic_app.api.app.app_reachable", _unreachable)


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", GATED)
def test_no_identity_header_is_401(client, path):
    assert client.get(path).status_code == 401


def test_identity_header_is_allowed(client):
    assert client.get("/api/healthz", headers=HDR).status_code == 200
    assert client.get("/api/capabilities", headers=HDR).status_code == 200


def test_proxy_requires_identity_before_it_dials_the_app_container(client):
    """401 must beat the 502. If the app container is down, an un-gated caller must still be
    told 'unauthenticated' — a 502 would confirm the route exists and that something is behind
    it, and it means the gate ran after the outbound connection rather than before."""
    assert client.get("/api/app/anything").status_code == 401


def test_openapi_is_disabled(client):
    """FastAPI's doc routes bypass app-level dependencies, so they are turned off — otherwise
    the full route list is readable without any identity at all."""
    assert client.get("/openapi.json", headers=HDR).status_code == 404
    assert client.get("/docs", headers=HDR).status_code == 404


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway still works."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/capabilities").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from openmaic_app.api import identity as ident

    os.environ.pop("PLATFORM_STANDALONE", None)
    assert ident.standalone() is False


# --- the shim mount ---------------------------------------------------------------------------

SHIM = ["/api/llm/v1/models"]


@pytest.mark.parametrize("path", SHIM)
def test_shim_without_any_credential_is_401(client, path, monkeypatch):
    """The mount does not inherit the app-level gate, so this is the test that proves it has
    one of its own."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "s3cret")
    assert client.get(path).status_code == 401


def test_shim_accepts_the_service_token(client, monkeypatch):
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "s3cret")
    r = client.get("/api/llm/v1/models", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert r.json()["object"] == "list"


def test_shim_accepts_a_platform_identity(client, monkeypatch):
    """A human poking at the shim through the gateway is a legitimate caller too."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "s3cret")
    assert client.get("/api/llm/v1/models", headers=HDR).status_code == 200


def test_shim_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "s3cret")
    r = client.get("/api/llm/v1/models", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_unset_shim_token_does_not_mean_open(client, monkeypatch):
    """An unconfigured token must fail closed. The tempting `if not token: return` shape — which
    the broker itself uses deliberately — would here turn a missing compose variable into an
    open proxy onto the GPU."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "")
    assert client.get("/api/llm/v1/models").status_code == 401
    assert client.get("/api/llm/v1/models",
                      headers={"Authorization": "Bearer "}).status_code == 401


def test_shim_error_uses_the_openai_envelope(client, monkeypatch):
    """An OpenAI client parses {"error": {...}} and shows the reason; a bare FastAPI
    {"detail": ...} surfaces to the user as an unexplained failure."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", "s3cret")
    body = client.get("/api/llm/v1/models").json()
    assert "error" in body and "message" in body["error"]
