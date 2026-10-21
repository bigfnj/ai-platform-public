"""Fail-closed identity. This rail opens a shell on the host, so an un-gated call is refused.

Before this, workstation read X-Platform-User only to NAME its audit line, defaulting it to "?"
when absent — so a sibling container could list presets, download an .rdp launcher, and open a
PTY-over-SSH session as the host Admin account, and the audit trail would record "?" as the
user. The only thing standing in the way was that rails are expose:-only in compose.
"""
import os

import pytest
from fastapi.testclient import TestClient

from workstation_app.main import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    """The escape hatch is read at call time, so clearing the env is enough."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)



@pytest.fixture
def client():
    # As a context manager, so the lifespan runs and app.state.settings exists — without it
    # every route 500s and the 401 assertions below would pass for the wrong reason.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("path", ["/api/presets", "/api/remoteapp"])
def test_no_identity_header_is_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/api/presets", "/api/remoteapp"])
def test_identity_header_is_allowed(client, path):
    assert client.get(path, headers=HDR).status_code == 200


def test_healthz_requires_identity_too(client):
    """It used to stay open "for the container's own healthcheck", and there is no such
    healthcheck: no HEALTHCHECK in the image, no healthcheck: in compose, and rail_smoke (its
    only automated reader) sends identity. An un-gated read told anyone on the compose network
    whether this host-shell rail runs with host-key checking off."""
    assert client.get("/api/healthz").status_code == 401
    r = client.get("/api/healthz", headers=HDR)
    assert r.status_code == 200 and r.json()["insecure_host_key"] is False


def test_rdp_launcher_requires_identity(client):
    """The launcher opens a session on the host; 401 must come before the 404 for an
    unconfigured RemoteApp, or absence of config would mask the missing auth."""
    assert client.get("/api/remoteapp/anything.rdp").status_code == 401


def test_websocket_without_identity_is_refused(client):
    """The PTY socket is the real prize here. A handshake with no gateway identity must be
    refused, never accepted. The app-wide identity dependency covers websocket routes, so the
    refusal arrives as a real 401 denial response; ws_terminal's own 4401 close stays as the
    backstop, pinned in test_hardening.py."""
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/local") as ws:
            ws.receive()
    assert getattr(exc.value, "status_code", None) == 401


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway still works, and yields a null owner rather than a
    placeholder username that could be persisted as if it were real."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/presets").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from workstation_app import identity as ident
    os.environ.pop("PLATFORM_STANDALONE", None)
    assert ident.standalone() is False


# --- a PRESENT but blank header -----------------------------------------------------------
#
# Found 2026-09-11, after the gate above closed the header-less case. identity() tested
# `x_platform_user is None`, and Starlette hands `X-Platform-User:` through as "" rather than
# None — so a blank header was accepted as a named caller, which on this rail means a shell on
# the host: presets, an .rdp launcher, and a PTY-over-SSH session as the Admin account. It also
# reinstated the old audit defect in a worse form, since the line would name "" as the user.
#
# Asserted over HTTP rather than by calling identity() directly, because the empty string only
# exists once Starlette has parsed the request: a unit call passing user="" proves what the
# function does with an argument, not what the header does to the gate.


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_blank_identity_header_is_401(client, blank):
    assert client.get("/api/presets", headers={"X-Platform-User": blank}).status_code == 401


def test_rdp_launcher_refuses_a_blank_identity_too(client):
    """401 still has to beat the 404 for an unconfigured RemoteApp, for the same reason as the
    header-less case: absence of config must not mask the missing auth."""
    assert client.get("/api/remoteapp/anything.rdp",
                      headers={"X-Platform-User": ""}).status_code == 401


def test_blank_identity_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. Omitting an identity is a
    topology; asserting an empty one is a claim with nothing in it to honour, so the blank guard
    sits AHEAD of the hatch. Both halves in one test, because the contrast is the point."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/presets").status_code == 200
    assert client.get("/api/presets", headers={"X-Platform-User": ""}).status_code == 401
