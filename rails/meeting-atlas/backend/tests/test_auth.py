"""Fail-closed identity.

meeting-atlas had no identity code at all. The module docstring asserted that "the gateway
sits in front and authenticates every request, so this backend is never directly reachable by
a browser" — true of a browser, and the entire defence. Any sibling container on the pod
network could pull a meeting's full transcript from /api/meetings/{id}, stream the recording
from its audio route, and trigger an unauthenticated full-index rebuild with POST /api/reindex.

The app object is a module-level singleton and builds its index during startup, so the client
fixture enters TestClient as a context manager to let the lifespan run, pointed at an empty
tmp dir rather than the real read-only mount.
"""
import pytest
from fastapi.testclient import TestClient

from meeting_atlas_app import main

USER = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # An empty recordings root: the index build is real, it just finds nothing. Deliberately no
    # default headers on the client — this suite is the one that sends none.
    monkeypatch.setattr(main.settings, "recordings_dir", str(tmp_path))
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("path", ["/api/healthz", "/api/meetings",
                                  "/api/meetings/anything",
                                  "/api/meetings/anything/audio"])
def test_no_identity_header_is_401(client, path):
    """401 rather than 404: an un-gated caller should not get to enumerate meeting ids by
    reading which ones come back 'no such meeting'."""
    assert client.get(path).status_code == 401


def test_identity_header_is_allowed(client):
    assert client.get("/api/healthz", headers=USER).status_code == 200
    assert client.get("/api/meetings", headers=USER).status_code == 200


def test_reindex_rejects_a_header_less_caller(client):
    assert client.post("/api/reindex").status_code == 401


def test_reindex_rejects_a_non_admin(client):
    """This was an open trigger for a full rebuild of the whole mount. Being a named user is
    not enough — the index is swapped out from under every reader on each call."""
    assert client.post("/api/reindex", headers=USER).status_code == 403


def test_reindex_allows_admin(client):
    """The ingest task that writes the sidecars calls this, and it fires with admin headers."""
    r = client.post("/api/reindex", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_openapi_is_disabled(client):
    """This rail was on FastAPI's defaults, so /docs, /redoc and /openapi.json were all live.
    Doc routes bypass app-level dependencies, so they are off rather than gated — otherwise
    the route list stays readable by the caller the app just refused."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path, headers=ADMIN).status_code == 404, path


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Running the backend on its own with uvicorn, per the docstring's dev command, has no
    gateway to set the header."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/meetings").status_code == 200


# --- a PRESENT but blank header -----------------------------------------------------------
#
# Found 2026-09-11, after the gate above closed the header-less case. identity() tested
# `x_platform_user is None`, and Starlette hands `X-Platform-User:` through as "" rather than
# None — so one empty header put a caller back exactly where this rail started: transcripts out
# of /api/meetings/{id}, the recording out of its audio route, as a named platform user.
#
# Asserted over HTTP rather than by calling identity() directly, because the empty string only
# exists once Starlette has parsed the request: a unit call passing user="" proves what the
# function does with an argument, not what the header does to the gate.


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_blank_identity_header_is_401(client, blank):
    assert client.get("/api/meetings", headers={"X-Platform-User": blank}).status_code == 401


def test_blank_identity_cannot_reach_a_transcript_or_the_audio(client):
    """The two routes the docstring names, kept separate from the parametrized case above so a
    later refactor cannot quietly narrow the guard to the list route."""
    for path in ("/api/meetings/anything", "/api/meetings/anything/audio"):
        assert client.get(path, headers={"X-Platform-User": ""}).status_code == 401, path


def test_blank_identity_is_refused_even_in_standalone(client, monkeypatch):
    """Unlike an ABSENT header, which standalone deliberately allows. Omitting an identity is a
    topology; asserting an empty one is a claim with nothing in it to honour, so the blank guard
    sits AHEAD of the hatch. Both halves in one test, because the contrast is the point."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/meetings").status_code == 200
    assert client.get("/api/meetings", headers={"X-Platform-User": ""}).status_code == 401
