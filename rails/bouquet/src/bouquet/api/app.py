"""Platform API tier: a FastAPI JSON surface over the bouquet core.

The gateway proxies ``/bouquet/api/*`` here; every route lives under ``/api/*``.
Blocking work (sqlite, Pillow, the two broker round-trips) runs in sync path
operations, which FastAPI executes in a threadpool, so the event loop stays free.

The analyze flow is **two steps around a human edit**:

- ``POST /api/identify`` runs the vision model and returns an editable draft
  inventory plus an ``image_token`` (the full-res upload parked under
  ``uploads/pending/``). No row is persisted.
- ``POST /api/generate`` takes the *corrected* inventory + the token, loads the
  writer model, renders the permanent 720px image, deletes the pending original,
  and persists the analysis.

AI is buffered by design (see ``bouquet.broker``): each step runs to completion and
returns a whole result rather than streaming (both the broker and the gateway proxy
buffer). A weekly background sweep mops up abandoned pending uploads.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from bouquet import analyze as analyze_mod
from bouquet import broker, config, db, jobs, kb, maintenance, retrieval

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
# An image token is a uuid4 hex — validate before it ever touches a filesystem path.
_TOKEN_RE = re.compile(r"\A[0-9a-f]{32}\Z")


class FlowerIn(BaseModel):
    name: str
    colors: list[str] = []
    confidence: str | None = None
    notes: str | None = None


class InventoryIn(BaseModel):
    flowers: list[FlowerIn] = []
    greenery: list[str] = []
    palette: str = ""
    arrangement: str = ""
    context: str = ""


class GenerateReq(BaseModel):
    image_token: str
    inventory: InventoryIn
    guidance: str = ""
    mode: str = "florist"          # 'florist' (description) | 'analysis'


# The slow broker work, run off the request thread as a job (see bouquet.jobs). Each
# is pure blocking work — the async endpoints call these via asyncio.to_thread.

def _run_identify(data: bytes, pending: Path) -> dict:
    analyze_mod.save_upload_jpeg(data, pending)
    image_b64 = analyze_mod.prepare_image(data)
    # Retrieval-grounding: a nearest-neighbour shortlist steers the vision model toward
    # profiled flowers. Best-effort — retrieval.shortlist returns [] on any failure.
    shortlist = retrieval.shortlist(image_b64) if config.GROUNDING_ENABLED else []
    inventory = analyze_mod.identify(image_b64, shortlist=shortlist)
    analyze_mod.annotate_inventory(inventory)
    # Stash the raw draft beside the pending upload; generate persists it with the
    # correction as a labeled (draft -> corrected) pair for the vision eval harness.
    try:
        pending.with_suffix(".json").write_text(json.dumps(inventory), encoding="utf-8")
    except OSError:
        pass
    return inventory


def _warm_writer() -> None:
    """Nudge the broker to load the writer model (a 1-token generation) so the swap
    overlaps the human edit pause. Best-effort — generate loads it anyway if needed."""
    try:
        broker.chat(config.DESCRIPTION_MODEL, [{"role": "user", "content": "ok"}],
                    options={"num_predict": 1}, keep_alive="10m")
    except Exception:  # noqa: BLE001
        pass


def _run_generate(inv: dict, guidance: str, mode: str, token: str,
                  source: Path, pending: Path) -> dict:
    result = analyze_mod.generate(inv, guidance=guidance, mode=mode)
    # Recover the vision draft captured at identify (labeled data for the eval harness).
    draft_path = pending.with_suffix(".json")
    vision_draft = None
    if draft_path.is_file():
        try:
            vision_draft = json.loads(draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            vision_draft = None
    # Each analysis owns a unique 720px image; drop the full-res original once derived.
    derivative = f"{token}-{uuid.uuid4().hex[:8]}.jpg"
    analyze_mod.render_derivative(source, config.UPLOADS_DIR / derivative)
    pending.unlink(missing_ok=True)
    analysis_id = db.insert(
        mode=result["mode"], title=result["title"], image_file=derivative,
        model=result["model"], inventory=result["inventory"],
        matched=result["matched_slugs"], unprofiled=result["unprofiled"],
        report_md=result["report_md"], guidance=guidance, vision_draft=vision_draft,
    )
    return {"id": analysis_id,
            "image_url": f"/bouquet/api/analyses/{analysis_id}/image",
            "guidance": guidance, **result}


def _require_admin(
    x_platform_user: str | None = Header(default=None),
    x_platform_admin: str | None = Header(default=None),
) -> None:
    """Gate a route to platform admins. The gateway sets X-Platform-* (stripping any client
    copy), so this trusts them; an un-gated standalone/dev request (no user header) passes."""
    is_admin = (x_platform_admin or "").strip().lower() in ("1", "true", "yes")
    if x_platform_user is not None and not is_admin:
        raise HTTPException(status_code=403, detail="admin only")


def create_api(data_dir: str | Path | None = None) -> FastAPI:
    if data_dir:
        config.DATA_DIR = Path(data_dir)
        config.DB_PATH = str(config.DATA_DIR / "bouquet.db")
        config.UPLOADS_DIR = config.DATA_DIR / "uploads"
    config.ensure_dirs()
    db.init()

    app = FastAPI(title="bouquet", version="0.2.0",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    # -- health / status ----------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "broker": broker.up(), "flowers": len(kb.all_flowers())}

    @app.get("/api/status")
    def status() -> dict:
        try:
            return {"broker_reachable": True, **broker.status()}
        except broker.BrokerError as exc:
            return {"broker_reachable": False, "detail": str(exc)}

    # -- knowledge base: browse the flower library --------------------------

    @app.get("/api/flowers")
    def flowers() -> dict:
        return {"flowers": [f.summary() for f in kb.all_flowers()]}

    @app.get("/api/flowers/{slug}")
    def flower_detail(slug: str) -> dict:
        f = kb.get_flower(slug)
        if f is None:
            raise HTTPException(status_code=404, detail=f"no flower '{slug}'")
        return f.detail()

    @app.get("/api/flowers/{slug}/images/{filename}")
    def flower_image(slug: str, filename: str) -> FileResponse:
        path = kb.image_file(slug, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="no such image")
        return FileResponse(str(path))

    # -- cross-cutting references -------------------------------------------

    @app.get("/api/references")
    def references() -> dict:
        return {"references": kb.list_references()}

    @app.get("/api/references/{slug}")
    def reference_detail(slug: str) -> PlainTextResponse:
        md = kb.get_reference(slug)
        if md is None:
            raise HTTPException(status_code=404, detail=f"no reference '{slug}'")
        return PlainTextResponse(md)

    # -- resolve a (edited/added) flower name to its KB profile -------------

    @app.get("/api/resolve")
    def resolve(name: str = "") -> dict:
        slug = kb.resolve(name)
        flower = kb.get_flower(slug) if slug else None
        return {"slug": slug, "title": flower.title if flower else None,
                "in_library": slug is not None}

    # -- step 1: identify (a polled job — a cold vision load can exceed the ~100s
    #    Cloudflare edge timeout, so the request returns a job id, not the result) --

    @app.post("/api/identify")
    def identify(image: UploadFile = File(...)) -> dict:
        ctype = (image.content_type or "").lower()
        if ctype not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=415,
                                detail="upload a JPEG, PNG, or WebP image")
        data = image.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")

        token = uuid.uuid4().hex
        pending = config.pending_dir() / f"{token}.jpg"
        jid = jobs.create()

        def work() -> None:
            try:
                inventory = _run_identify(data, pending)
                jobs.finish(jid, result={"image_token": token, "inventory": inventory})
                # Pre-load the writer while the florist reviews (see the plan's design).
                if config.WARM_WRITER:
                    threading.Thread(target=_warm_writer, daemon=True).start()
            except broker.BrokerError as exc:
                pending.unlink(missing_ok=True)
                jobs.finish(jid, error=f"broker error: {exc}")
            except Exception as exc:  # noqa: BLE001
                pending.unlink(missing_ok=True)
                jobs.finish(jid, error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": jid}

    # -- step 2: generate from the corrected inventory (also a polled job) ---

    @app.post("/api/generate")
    def generate(req: GenerateReq) -> dict:
        if not _TOKEN_RE.match(req.image_token):
            raise HTTPException(status_code=400, detail="bad image token")

        # Source image for this analysis: the pending full-res on the FIRST generate,
        # else the newest prior 720px derivative for this token (a re-generate —
        # tweak the flowers/guidance and write again without re-uploading).
        pending = config.pending_dir() / f"{req.image_token}.jpg"
        priors = sorted(config.UPLOADS_DIR.glob(f"{req.image_token}-*.jpg"))
        source = pending if pending.is_file() else (priors[-1] if priors else None)
        if source is None:
            raise HTTPException(status_code=404,
                                detail="this upload has expired — please re-upload the photo")

        inv = req.inventory.model_dump()
        # Guard: the writer confabulates a whole bouquet from an empty inventory.
        if not any((f.get("name") or "").strip() for f in inv.get("flowers", [])):
            raise HTTPException(status_code=400, detail="add at least one flower first")

        guidance = (req.guidance or "").strip()
        mode, token = req.mode, req.image_token
        jid = jobs.create()

        def work() -> None:
            try:
                payload = _run_generate(inv, guidance, mode, token, source, pending)
                jobs.finish(jid, result=payload)
            except broker.BrokerError as exc:
                # Keep the source image so the florist can retry without re-uploading.
                jobs.finish(jid, error=f"broker error: {exc}")
            except Exception as exc:  # noqa: BLE001
                jobs.finish(jid, error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": jid}

    # -- poll a job (fast; the browser calls this every couple seconds) -----

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {"status": j["status"], "result": j["result"], "error": j["error"]}

    # -- saved analyses (single-tenant library) -----------------------------

    @app.get("/api/analyses")
    def list_analyses() -> dict:
        return {"analyses": db.list_()}

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: int) -> dict:
        row = db.get(analysis_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such analysis")
        return row

    @app.get("/api/analyses/{analysis_id}/image")
    def analysis_image(analysis_id: int) -> FileResponse:
        name = db.image_name(analysis_id)
        if not name:
            raise HTTPException(status_code=404, detail="no image for this analysis")
        path = config.UPLOADS_DIR / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="image file missing")
        return FileResponse(str(path))

    @app.delete("/api/analyses/{analysis_id}")
    def delete_analysis(analysis_id: int) -> dict:
        name = db.image_name(analysis_id)
        if not db.delete(analysis_id):
            raise HTTPException(status_code=404, detail="no such analysis")
        if name:  # best-effort cleanup of the stored photo
            (config.UPLOADS_DIR / name).unlink(missing_ok=True)
        return {"ok": True}

    # -- maintenance: abandoned-upload sweep (fired by the central scheduler) -

    @app.post("/api/maintenance/sweep")
    def maintenance_sweep(_: None = Depends(_require_admin)) -> dict:
        """Delete abandoned pending uploads + stray orphan files older than the age guard.
        Admin-only; the platform's central scheduler fires this weekly with system-admin
        headers (this replaced the rail's own in-process cleanup loop). Returns the counts."""
        return maintenance.sweep()

    return app
