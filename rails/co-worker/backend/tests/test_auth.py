"""Fail-closed identity.

Co-Worker gated nothing, and it looked like it did: eight routes declared
`x_platform_user: str = Header(default="?")` and not one of them ever read the value. A
sibling container that sent no header was not refused — it was quietly accepted as "?" and
handed a real person's harvested email, calendar and Teams inbox, could read any markdown
under that directory via /api/doc, rewrite triage state, and spend a model run per request on
/api/brief/refresh. This suite exists because the rail previously had no tests at all.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from co_worker_app.config import settings
from co_worker_app.main import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    """The escape hatch is read at call time, so clearing the env is enough."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # A real inbox with one item, so an allowed request returns 200 for the right reason
    # rather than because the default /data/inbox mount is missing on a dev box.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "item-1.json").write_text(
        json.dumps({"title": "a thing", "source": "email", "period": "2026W34"}),
        encoding="utf-8")
    (inbox / "notes").mkdir()
    (inbox / "notes" / "week.md").write_text("# week", encoding="utf-8")
    monkeypatch.setattr(settings, "inbox_dir", str(inbox))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/api/healthz", "/api/inbox", "/api/brief", "/api/archive"])
def test_no_identity_header_is_401(client, path):
    assert client.get(path).status_code == 401


def test_identity_header_is_allowed(client):
    r = client.get("/api/inbox", headers=HDR)
    assert r.status_code == 200
    assert [i["_id"] for i in r.json()["items"]] == ["item-1"]


def test_writes_require_identity(client):
    """Triage state and a synthesis run are both side effects, and neither was gated."""
    assert client.patch("/api/inbox/item-1", json={"status": "done"}).status_code == 401
    assert client.post("/api/brief/refresh").status_code == 401


def test_doc_read_requires_identity_before_path_validation(client):
    """401 must beat the 400/404 the path checks would return, or those replies become a probe
    an un-gated caller can use to map what is on disk."""
    assert client.get("/api/doc/notes/week.md").status_code == 401
    assert client.get("/api/doc/notes/absent.md").status_code == 401   # would be 404
    assert client.get("/api/doc/notes/secret.txt").status_code == 401  # would be 400
    assert client.get("/api/doc/notes/week.md", headers=HDR).status_code == 200


def test_openapi_is_disabled(client):
    """FastAPI's doc routes bypass app-level dependencies, so they are turned off — otherwise
    the full route list is readable without any identity at all."""
    assert client.get("/openapi.json", headers=HDR).status_code == 404
    assert client.get("/docs", headers=HDR).status_code == 404


def test_standalone_allows_headerless_access(client, monkeypatch):
    """Local dev without a gateway still works. Nothing in this rail persists the caller, so
    the null owner identity() returns has nowhere to leak to."""
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.get("/api/inbox").status_code == 200
    assert client.get("/api/healthz").status_code == 200


def test_standalone_is_off_by_default():
    """A missing env var must not read as enabled — the whole gate hinges on this."""
    from co_worker_app import identity as ident
    os.environ.pop("PLATFORM_STANDALONE", None)
    assert ident.standalone() is False
