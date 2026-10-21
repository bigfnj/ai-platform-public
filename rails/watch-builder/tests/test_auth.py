"""Identity, fail-closed. RC021's contract, asserted rather than assumed.

The gateway authenticates every request and sets X-Platform-User, stripping any client copy
first. A request arriving WITHOUT that header did not come through the gateway, which in this
topology means a sibling container on the compose network. 401 is the only safe answer.

Most of the rails got this wrong at once when the contract was first written, three of
them with an INVERTED gate that rejected a named non-admin and waved through a caller with no
header at all. So this file tests the negative case first and by name.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Import with the hatch explicitly OFF, so a stray PLATFORM_STANDALONE in the developer's
# environment cannot turn the 401 assertions green. identity() reads the flag at CALL time
# precisely so a test can control it.
os.environ.pop("PLATFORM_STANDALONE", None)

from watch_builder.api.app import app  # noqa: E402

client = TestClient(app)

GATED = [
    ("get", "/api/capabilities"),
    ("post", "/api/validate"),
    ("post", "/api/source"),
    ("post", "/api/build"),
    ("get", "/api/ai/estimate"),
    ("post", "/api/ai/design"),
    ("post", "/api/ai/variations"),
    ("get", "/api/ai/jobs"),
    ("get", "/api/ai/jobs/abc"),
    ("delete", "/api/ai/jobs/abc"),
    ("get", "/api/designs"),
    ("post", "/api/designs"),
    ("get", "/api/designs/abc"),
    ("delete", "/api/designs/abc"),
    # /api/health is gated too: identity is an APP-LEVEL dependency here, which is the shape
    # that cannot be forgotten when a route is added. The cost is no un-authenticated
    # liveness probe, which the gateway does not need.
    ("get", "/api/health"),
]


def _call(method: str, path: str, **kw):
    """httpx's get/delete take no json body, so the payload is only sent where it is legal.

    Passing json= to client.get() raises a TypeError, which pytest reports as a failure and
    reads exactly like a route answering the wrong status. Worth the helper.
    """
    if method in ("get", "delete"):
        return getattr(client, method)(path, **kw)
    return getattr(client, method)(path, json={}, **kw)


@pytest.mark.parametrize("method,path", GATED)
def test_no_identity_header_is_401(method, path):
    resp = _call(method, path)
    assert resp.status_code == 401, f"{method.upper()} {path} answered {resp.status_code}"


@pytest.mark.parametrize("method,path", GATED)
def test_blank_identity_header_is_401(method, path):
    """A header that is present but blank is a claim, not an omission.

    Starlette hands `X-Platform-User:` through as "" rather than None. Gating on `is None`
    alone let a blank header past and produced a real identity of "", which on a rail that
    persists the caller into an owner column becomes real-looking data.
    """
    resp = _call(method, path, headers={"X-Platform-User": "  "})
    assert resp.status_code == 401


def test_identity_header_is_accepted():
    resp = client.get("/api/health", headers={"X-Platform-User": "someone"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_admin_header_alone_does_not_authenticate():
    """X-Platform-Admin without a user is still unauthenticated.

    The inverted gate that shipped on three rails accepted exactly this shape. Worth its own
    test because it reads as privileged and is in fact anonymous.
    """
    resp = client.get("/api/designs", headers={"X-Platform-Admin": "1"})
    assert resp.status_code == 401


def test_docs_routes_are_off():
    """FastAPI's doc routes bypass app-level dependencies, so they are disabled.

    Without this the openapi schema is readable by any sibling container, which is a map of
    every route and payload shape on the rail.
    """
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404
