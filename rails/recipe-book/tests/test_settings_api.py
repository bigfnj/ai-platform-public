"""GET /api/settings — the route the admin Settings panel loads from.

It raised ``NameError`` on every single call: the refactor that introduced ``require_admin``
rewrote the PUT and left the GET evaluating ``_is_admin(x_platform_admin)``, where neither the
function nor the header parameter existed any more (the route's only parameter was ``_``).
Nothing caught it because the PUT beside it returned 200 and no test read the GET.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db, settings as settings_mod
from recipe_book.api.routers import settings as settings_router

ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}
ALICE = {"X-Platform-User": "alice", "X-Platform-Admin": "0"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.close()
    app = FastAPI()
    app.include_router(settings_router.router)
    # raise_server_exceptions=False so the NameError surfaces as the 500 a browser sees,
    # rather than exploding out of the test client with a different traceback.
    return TestClient(app, raise_server_exceptions=False)


def test_get_settings_returns_the_panel_payload(client):
    """The regression: this returned 500 (NameError), so the panel could never load."""
    r = client.get("/api/settings", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_retention_days"] == config.PLAN_RETENTION_DAYS
    assert body["plan_recency_days"] == config.PLAN_RECENCY_DAYS
    assert body["ranges"]["plan_retention_days"] == list(settings_mod.RETENTION_RANGE)
    assert body["defaults"]["plan_recency_days"] == config.PLAN_RECENCY_DAYS


def test_is_admin_comes_from_the_resolved_identity(client):
    """The flag drives whether the UI shows the gear. It must track the trusted header, and
    the fix must not hardcode it — `is_admin: True` would also make the test above pass."""
    assert client.get("/api/settings", headers=ADMIN).json()["is_admin"] is True
    assert client.get("/api/settings", headers=ALICE).json()["is_admin"] is False


def test_get_settings_requires_identity(client):
    """Readable by any signed-in user (the UI decides what to show), but not by a
    header-less sibling container."""
    assert client.get("/api/settings").status_code == 401


def test_get_reflects_a_put(client):
    """End to end through the pair, which is what would have caught the split refactor:
    the PUT was updated, the GET was not, and nothing read them together."""
    assert client.put("/api/settings", json={"plan_retention_days": 90},
                      headers=ADMIN).status_code == 200
    assert client.get("/api/settings", headers=ADMIN).json()["plan_retention_days"] == 90
