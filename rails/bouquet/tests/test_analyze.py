"""Pipeline + API — offline, with the broker faked (no GPU, no network).

Covers the two-step flow: identify -> (human edit) -> generate, the resolve
endpoint, guidance reaching the writer, the 720px derivative + pending cleanup,
delete, and the weekly sweep.
"""
from __future__ import annotations

import base64
import io
import os
import time

import pytest
from fastapi.testclient import TestClient

from bouquet import analyze as analyze_mod
from bouquet import broker, config, db, maintenance
from bouquet.api import create_api

# A representative vision inventory: two profiled flowers + one unprofiled.
_FAKE_INVENTORY = {
    "flowers": [
        {"name": "coral garden rose", "colors": ["coral"], "confidence": "high"},
        {"name": "ranunculus", "colors": ["blush"], "confidence": "high"},
        {"name": "plastic flamingo", "colors": ["pink"], "confidence": "low"},
    ],
    "greenery": ["eucalyptus"],
    "palette": "coral and blush",
    "arrangement": "loose hand-tied posy",
    "context": "held by a bride",
}


def _jpeg(w: int = 64, h: int = 48) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 90)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def fake_broker(monkeypatch):
    calls = {"json": 0, "chat": 0, "last_images": None,
             "last_system": "", "last_user": "", "last_model": ""}

    def fake_json(model, messages, **kw):
        calls["json"] += 1
        calls["last_images"] = messages[-1].get("images")
        return dict(_FAKE_INVENTORY)

    def fake_chat(model, messages, **kw):
        calls["chat"] += 1
        calls["last_model"] = model
        calls["last_system"] = messages[0]["content"]
        calls["last_user"] = messages[-1]["content"]
        return "# At a glance\nA coral-blush wedding posy."

    monkeypatch.setattr(broker, "chat_json", fake_json)
    monkeypatch.setattr(broker, "chat", fake_chat)
    monkeypatch.setattr(broker, "up", lambda: True)
    monkeypatch.setattr(config, "WARM_WRITER", False)         # no background warm during tests
    monkeypatch.setattr(config, "GROUNDING_ENABLED", False)   # grounding covered in test_retrieval
    return calls


def _run_job(client, resp):
    """Start-call + poll the job to completion; return its result (or raise its error).
    Sync validation errors (non-200 start) are surfaced by the caller instead."""
    assert resp.status_code == 200, resp.text
    jid = resp.json()["job_id"]
    for _ in range(400):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] == "done":
            return j["result"]
        if j["status"] == "error":
            raise AssertionError(f"job failed: {j['error']}")
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


# --- pure pipeline units ----------------------------------------------------

def test_identify_normalizes_and_annotates(fake_broker):
    inv = analyze_mod.identify(analyze_mod.prepare_image(_jpeg()))
    analyze_mod.annotate_inventory(inv)
    names = {f["name"]: f for f in inv["flowers"]}
    assert names["coral garden rose"]["slug"] == "rose"
    assert names["coral garden rose"]["in_library"] is True
    assert names["plastic flamingo"]["slug"] is None
    assert names["plastic flamingo"]["in_library"] is False
    assert fake_broker["last_images"] and isinstance(fake_broker["last_images"][0], str)


def test_generate_matches_and_reports(fake_broker):
    result = analyze_mod.generate(_FAKE_INVENTORY, mode="analysis")
    assert result["mode"] == "analysis"
    assert result["matched_slugs"] == ["rose", "ranunculus"]      # resolved + de-duped
    assert result["unprofiled"] == ["plastic flamingo"]           # no profile -> flagged
    assert result["report_md"].startswith("# At a glance")
    assert result["title"]
    assert fake_broker["last_model"] == config.ANALYSIS_MODEL


def test_generate_uses_edited_inventory(fake_broker):
    # The florist corrects "plastic flamingo" -> "tulip" and drops ranunculus.
    edited = {
        "flowers": [
            {"name": "coral garden rose", "colors": ["coral"]},
            {"name": "tulip", "colors": ["yellow"]},
        ],
        "palette": "coral and gold", "arrangement": "hand-tied",
    }
    result = analyze_mod.generate(edited, mode="florist")
    assert result["matched_slugs"] == ["rose", "tulip"]
    assert result["unprofiled"] == []
    assert "tulip" in fake_broker["last_user"].lower()
    assert fake_broker["last_model"] == config.DESCRIPTION_MODEL


def test_florist_mode_uses_frenchies_persona(fake_broker):
    analyze_mod.generate(_FAKE_INVENTORY, mode="florist")
    assert "Frenchies Flowers" in fake_broker["last_system"]


def test_guidance_reaches_the_writer(fake_broker):
    analyze_mod.generate(_FAKE_INVENTORY, mode="florist",
                         guidance="For a sympathy arrangement, keep it gentle.")
    ctx = fake_broker["last_user"]
    assert "Florist's direction" in ctx
    assert "sympathy arrangement" in ctx


def test_parse_json_loose_tolerates_fences():
    assert broker._parse_json_loose('{"a": 1}') == {"a": 1}
    assert broker._parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert broker._parse_json_loose('here you go: {"a": 1} cheers') == {"a": 1}
    assert broker._parse_json_loose("not json") == {}


def test_strip_md_fence():
    assert analyze_mod._strip_md_fence("```markdown\n# Hi\n\ntext\n```") == "# Hi\n\ntext"
    assert analyze_mod._strip_md_fence("```\n# Hi\n```") == "# Hi"
    assert analyze_mod._strip_md_fence("# Hi\n\ntext") == "# Hi\n\ntext"


def test_prepare_image_downscales():
    assert base64.b64decode(analyze_mod.prepare_image(_jpeg()))  # valid base64


def test_render_derivative_downscales(tmp_path):
    from PIL import Image
    src = tmp_path / "src.jpg"
    Image.new("RGB", (1000, 800), (1, 2, 3)).save(src, format="JPEG")
    dst = tmp_path / "dst.jpg"
    analyze_mod.render_derivative(src, dst)
    assert max(Image.open(dst).size) == config.DERIVATIVE_EDGE   # 720


# --- API via TestClient -----------------------------------------------------

@pytest.fixture()
def client(tmp_path, fake_broker):
    app = create_api(data_dir=tmp_path)
    return TestClient(app)


def test_health_and_library(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["flowers"] == 50
    assert len(client.get("/api/flowers").json()["flowers"]) == 50
    assert client.get("/api/flowers/rose").json()["title"] == "Rose"
    img = client.get("/api/flowers/rose/images/rose-01.jpg")
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    assert client.get("/api/flowers/nope").status_code == 404


def test_references_endpoint(client):
    r = client.get("/api/references")
    assert any(x["slug"] == "color-symbolism" for x in r.json()["references"])
    assert client.get("/api/references/color-symbolism").status_code == 200


def test_resolve_endpoint(client):
    r = client.get("/api/resolve", params={"name": "peruvian lily"}).json()
    assert r["slug"] == "alstroemeria" and r["in_library"] is True and r["title"]
    r = client.get("/api/resolve", params={"name": "plastic flamingo"}).json()
    assert r["slug"] is None and r["in_library"] is False


def _identify(client) -> str:
    body = _run_job(client, client.post(
        "/api/identify", files={"image": ("b.jpg", _jpeg(), "image/jpeg")}))
    token = body["image_token"]
    assert len(token) == 32
    # the full-res upload is parked pending, with the raw draft stashed beside it
    assert (config.pending_dir() / f"{token}.jpg").is_file()
    assert (config.pending_dir() / f"{token}.json").is_file()
    # inventory came back annotated for the editor
    assert any(f["slug"] == "rose" for f in body["inventory"]["flowers"])
    return token


def test_identify_then_generate_persists(client):
    token = _identify(client)
    edited = {
        "flowers": [{"name": "coral garden rose", "colors": ["coral"]},
                    {"name": "tulip", "colors": ["yellow"]}],
        "greenery": ["eucalyptus"], "palette": "coral and gold",
        "arrangement": "hand-tied", "context": "",
    }
    body = _run_job(client, client.post("/api/generate", json={
        "image_token": token, "inventory": edited,
        "guidance": "Short and cheerful, for a birthday.", "mode": "florist"}))
    aid = body["id"]
    assert body["mode"] == "florist"
    assert body["matched_slugs"] == ["rose", "tulip"]
    assert body["guidance"].startswith("Short and cheerful")

    # a single 720px derivative exists (owned by this analysis); pending is gone
    derivs = list(config.UPLOADS_DIR.glob(f"{token}-*.jpg"))
    assert len(derivs) == 1
    assert not (config.pending_dir() / f"{token}.jpg").exists()

    # persisted + retrievable, with the edited inventory + guidance stored
    detail = client.get(f"/api/analyses/{aid}").json()
    assert detail["guidance"].startswith("Short and cheerful")
    assert {f["name"] for f in detail["inventory"]["flowers"]} == {"coral garden rose", "tulip"}
    # the raw vision draft was captured alongside the correction (labeled eval data)
    draft_names = {f["name"] for f in detail["vision_draft"]["flowers"]}
    assert "plastic flamingo" in draft_names and "tulip" not in draft_names
    assert client.get(f"/api/analyses/{aid}/image").status_code == 200

    # delete removes the row and the image file
    assert client.delete(f"/api/analyses/{aid}").status_code == 200
    assert client.get(f"/api/analyses/{aid}").status_code == 404
    assert not derivs[0].exists()


def test_regenerate_without_reupload(client):
    # First generate consumes the pending original; a second generate for the same
    # token reuses the prior derivative (tweak + rewrite, no re-upload), and each
    # analysis owns a distinct image file.
    token = _identify(client)
    inv = {"flowers": [{"name": "rose", "colors": ["red"]}]}
    b1 = _run_job(client, client.post("/api/generate", json={
        "image_token": token, "inventory": inv, "mode": "florist"}))
    assert not (config.pending_dir() / f"{token}.jpg").exists()   # original dropped

    b2 = _run_job(client, client.post("/api/generate", json={
        "image_token": token, "inventory": inv, "guidance": "now the analysis",
        "mode": "analysis"}))
    assert b1["id"] != b2["id"]
    assert len(list(config.UPLOADS_DIR.glob(f"{token}-*.jpg"))) == 2  # one per analysis


def test_generate_guards_empty_inventory(client):
    token = _identify(client)
    r = client.post("/api/generate", json={
        "image_token": token, "inventory": {"flowers": []}, "mode": "florist"})
    assert r.status_code == 400
    # the pending upload is left in place for a retry
    assert (config.pending_dir() / f"{token}.jpg").is_file()


def test_generate_rejects_expired_token(client):
    r = client.post("/api/generate", json={
        "image_token": "0" * 32,
        "inventory": {"flowers": [{"name": "rose"}]}, "mode": "florist"})
    assert r.status_code == 404


def test_identify_rejects_non_image(client):
    r = client.post("/api/identify", files={"image": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 415


def test_job_status_unknown_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_cleanup_sweep(client):
    old = time.time() - 72 * 3600  # older than the 48h orphan window

    # 1. a stale pending upload -> swept
    stale_pending = config.pending_dir() / "stalepending.jpg"
    stale_pending.write_bytes(b"x")
    os.utime(stale_pending, (old, old))

    # 2. a fresh pending upload -> kept
    fresh_pending = config.pending_dir() / "freshpending.jpg"
    fresh_pending.write_bytes(b"x")

    # 3. a stray unreferenced upload -> swept
    orphan = config.UPLOADS_DIR / "orphan.jpg"
    orphan.write_bytes(b"x")
    os.utime(orphan, (old, old))

    # 4. a real analysis image, referenced by the DB and old -> kept
    token = _identify(client)
    _run_job(client, client.post("/api/generate", json={
        "image_token": token, "inventory": {"flowers": [{"name": "rose"}]},
        "mode": "florist"}))
    live = next(config.UPLOADS_DIR.glob(f"{token}-*.jpg"))
    os.utime(live, (old, old))

    counts = maintenance.sweep()
    assert counts == {"pending_removed": 1, "orphan_removed": 1}
    assert not stale_pending.exists()
    assert fresh_pending.exists()
    assert not orphan.exists()
    assert live.is_file()   # referenced -> survives even though it is old


def test_seconds_until_next_run_is_sunday_three_am():
    from datetime import datetime, timedelta
    # A Wednesday afternoon -> next Sunday (dow 6) 03:00 local.
    wed = datetime(2026, 8, 5, 14, 0, 0)          # 2026-08-05 is a Wednesday
    secs = maintenance.seconds_until_next_run(wed)
    nxt = wed + timedelta(seconds=secs)
    assert nxt.weekday() == config.CLEANUP_DOW and nxt.hour == config.CLEANUP_HOUR
    assert secs > 0
