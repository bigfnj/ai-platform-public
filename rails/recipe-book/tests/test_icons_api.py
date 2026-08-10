"""Admin icon (re)generation endpoints: admin-gated, single-flight, background.

Offline — the broker render + LLM subject authoring are mocked, so nothing touches the GPU;
we assert the gating, that repass authors subjects *then* renders, and the single-flight guard.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import config, db, icon_prompts, icons
from recipe_book.api.routers import icons as icon_routes

ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}
ALICE = {"X-Platform-User": "alice"}


def _reset_run() -> None:
    with icons._run_lock:
        icons._run.update(running=False, phase=None, started_at=None, finished_at=None, last=None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    con = db.connect()
    db.init_db(con)
    con.execute("INSERT INTO recipes (id, title, category, kind) VALUES (?,?,?,?)",
                ("aaa", "Test Dish", "Entrees", "meal"))
    con.commit()
    con.close()
    _reset_run()

    calls = {"build": 0, "generate": 0, "build_force": None, "gen_force": None}

    def fake_build(con, **kw):
        calls["build"] += 1
        calls["build_force"] = kw.get("force")
        return {"made": 1, "failed": 0, "targets": 1}

    def fake_generate(con, **kw):
        calls["generate"] += 1
        calls["gen_force"] = kw.get("force")
        return {"made": 1, "failed": 0, "targets": 1}

    monkeypatch.setattr(icon_prompts, "build", fake_build)
    monkeypatch.setattr(icons, "generate", fake_generate)

    app = FastAPI()
    app.include_router(icon_routes.router)
    c = TestClient(app)
    c.calls = calls  # type: ignore[attr-defined]
    yield c
    _reset_run()


def test_status_is_open_and_reports_counts(client):
    j = client.get("/api/icons/status").json()
    assert j["total"] == 1 and j["ready"] == 0 and j["pending"] == 1
    assert j["running"] is False


@pytest.mark.parametrize("path", ["/api/icons/generate", "/api/icons/repass"])
def test_nonadmin_cannot_trigger(client, path):
    assert client.post(path, headers=ALICE).status_code == 403


def test_admin_repass_authors_subjects_then_renders(client):
    r = client.post("/api/icons/repass?force=true", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["queued"] is True
    # TestClient runs the background task before the request returns.
    assert client.calls["build"] == 1          # subjects authored
    assert client.calls["generate"] == 1       # then rendered
    assert client.calls["build_force"] is True and client.calls["gen_force"] is True
    assert icons.run_state()["running"] is False   # flag released on finish


def test_admin_generate_is_render_only(client):
    r = client.post("/api/icons/generate", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.calls["generate"] == 1
    assert client.calls["build"] == 0          # generate never authors subjects


def test_ungated_dev_may_trigger(client):
    # No identity header (standalone/dev) passes require_admin, matching the rest of the app.
    assert client.post("/api/icons/generate").status_code == 200


def test_single_flight_guard():
    _reset_run()
    assert icons.try_begin("render") is True
    assert icons.try_begin("render") is False      # already in flight -> refused
    icons.set_phase("render")
    assert icons.run_state()["phase"] == "render"
    icons.finish({"made": 5})
    assert icons.run_state()["running"] is False
    assert icons.try_begin("subjects") is True     # freed after finish
    _reset_run()


def test_repass_refused_while_running(client):
    """A repass triggered while a run is already in flight is refused, not queued."""
    assert icons.try_begin("render") is True       # simulate an in-flight run
    try:
        j = client.post("/api/icons/repass", headers=ADMIN).json()
        assert j["queued"] is False
        assert client.calls["build"] == 0          # nothing kicked off
    finally:
        _reset_run()
