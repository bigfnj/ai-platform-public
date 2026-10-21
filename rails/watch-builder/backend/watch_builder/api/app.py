"""Watch Builder HTTP surface.

Identity is enforced APP-WIDE via `dependencies=[Depends(identity)]`, with docs_url and
openapi_url set to None. App-level dependencies are the shape that cannot be forgotten when
a route is added later, and FastAPI's own doc routes bypass them, which is why they are
switched off rather than left open.

There are no static mounts here. `app.mount(...)` is not a route and is NOT covered by an
app-level dependency; terminal-fun shipped an un-gated 200 that way.
"""
from __future__ import annotations

import base64
import functools
import json
import shutil
import tempfile
from pathlib import Path

import anyio
from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from watch_builder import ai, codegen, images, jobs, presets, sdk, store, sunlight
from watch_builder.api.body_limit import BodyLimit
from watch_builder.api.identity import Identity, identity
from watch_builder.config import settings
from watch_builder.design import (
    ALL_PLATFORMS,
    Design,
    PLATFORMS,
    check_semantics,
    clock_elements,
)
from watch_builder.layout import audit, resolved_boxes, summarise
from watch_builder.modelstate import resolve

# The header chips this rail opts into the Claude CLI: the slots whose calls pass the person
# asking as `requester`. One module-level literal, which /api/capabilities passes as it is and
# the gateway's test_rails_cli_switch.py reads, so RAIL_MODEL_SLOTS' "cli" flag cannot drift
# from it (docs/RAIL_CONTRACT.md).
CLI_SLOTS = ("designer",)

app = FastAPI(
    title="Watch Builder",
    docs_url=None,
    openapi_url=None,
    dependencies=[Depends(identity)],
)


# --------------------------------------------------------------------------------------
# Health and capabilities
# --------------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "app": settings.app_name}


@app.get("/api/capabilities")
def capabilities(ident: Identity = Depends(identity)) -> dict:
    """Model chips plus what this rail can actually do in this environment.

    `build_available` is a first-class answer, not a degraded one. The Pebble SDK has no
    Windows build, so on the host it is genuinely false and the frontend has to hide the
    build button rather than offer one that always fails.

    The designer is opted into the Claude CLI (CLI_SLOTS): both AI routes create a job whose
    every call carries the viewer as `requester`, the same `ident.user` judged here, and waits
    the full broker.CHAT_TIMEOUT (jobs.py), so the chip describes every call a job makes.
    """
    state = resolve([("designer", "Designer", settings.designer_model)],
                    requester=ident.user, cli_slots=CLI_SLOTS)
    return {
        **state,
        "build_available": sdk.available(),
        "sdk_version": sdk.sdk_version(),
        "platforms": [
            {
                "name": p.name, "label": p.label, "width": p.width, "height": p.height,
                "round": p.round, "colors": p.colors, "quick_view": p.quick_view,
                "has_leco60": p.has_leco60,
            }
            for p in PLATFORMS.values()
        ],
        "ai": ai.describe_capabilities(),
    }


# --------------------------------------------------------------------------------------
# Validation and audit: the part that works with no GPU and no SDK
# --------------------------------------------------------------------------------------


def _parse(doc: dict) -> Design:
    try:
        return Design.model_validate(doc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "error": "invalid design",
            "problems": [
                {"at": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg")}
                for e in exc.errors()[:20]
            ],
        }) from exc


@app.post("/api/validate")
def validate(doc: dict = Body(...)) -> dict:
    """Schema, semantics and a per-platform layout audit. No GPU, no compiler."""
    design = _parse(doc)
    report = summarise(audit(design))
    report["semantics"] = check_semantics(design)
    report["clock_elements"] = clock_elements(design)
    if report["clock_elements"] == 0:
        report["semantics"].append(
            "Nothing on this face shows the time. That is allowed, and is almost never what "
            "was meant."
        )
    colors = [design.background] + [el.color for el in design.elements]
    report["colour_shift"] = sunlight.worst_shifts(colors)
    # The canvas draws from these. One resolver of record: see layout.resolved_boxes.
    report["boxes"] = resolved_boxes(design)
    report["screens"] = {p.name: {"w": p.width, "h": p.height, "round": p.round,
                                  "colors": p.colors, "quick_view": p.quick_view}
                         for p in (PLATFORMS[n] for n in design.platforms)}
    return report


@app.post("/api/source")
def source(doc: dict = Body(...)) -> dict:
    """The generated project, as files. Useful without an SDK, and the honest export path."""
    design = _parse(doc)
    return {"files": codegen.generate_project(design)}


# --------------------------------------------------------------------------------------
# Build and render
# --------------------------------------------------------------------------------------


class BuildRequest(BaseModel):
    design: dict
    platforms: list[str] | None = None
    render: bool = True


@app.post("/api/build")
def build(req: BuildRequest, ident: Identity = Depends(identity)) -> dict:
    """Compile, and optionally screenshot every requested platform.

    A refused build returns 503 rather than 500: the SDK being absent is a property of the
    environment, not an error in the request, and a caller distinguishing the two can offer
    the source-export path instead of a retry.
    """
    if not sdk.available():
        raise HTTPException(status_code=503, detail={
            "error": "sdk-unavailable",
            "message": ("No Pebble SDK in this environment. The SDK has no Windows build; "
                        "use /api/source to export a project, or run the rail's container."),
        })

    design = _parse(req.design)
    if req.platforms:
        unknown = [p for p in req.platforms if p not in ALL_PLATFORMS]
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"unknown platform(s): {', '.join(unknown)}")
        design = design.model_copy(update={"platforms": req.platforms})

    # A build that will produce a cropped or overlapping face is still built, but the errors
    # travel with the result. Refusing would be worse: a designer iterating deliberately
    # passes through broken intermediate states, and a tool that will not render them is a
    # tool you stop using.
    layout_report = summarise(audit(design))

    root = Path(tempfile.mkdtemp(prefix="wb-", dir=_work_dir()))
    try:
        codegen_files = codegen.generate_project(design)
        sdk.write_project(codegen_files, root)

        # Images are resampled per platform to the exact box they will be drawn into,
        # because graphics_draw_bitmap_in_rect clips a smaller rect and tiles a larger one.
        try:
            image_files, image_report = images.render_resources(design, ident.user)
        except images.ImageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for rel, data in image_files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        result = sdk.build(root)
        out: dict = {
            "ok": result.ok,
            "platforms": result.platforms,
            "duration_s": round(result.duration_s, 2),
            "layout": layout_report,
            "images": image_report,
            "image_warnings": images.budget_warnings(image_report),
        }
        if not result.ok:
            out["error"] = result.error
            out["log_tail"] = result.log[-4000:]
            return JSONResponse(status_code=422, content=out)

        if result.pbw is not None:
            out["pbw_bytes"] = result.pbw.stat().st_size
            out["pbw_base64"] = base64.b64encode(result.pbw.read_bytes()).decode("ascii")
            # Named from the DESIGN, not from the build directory. waf names the bundle after
            # the project dir, which here is a mkdtemp path, so the honest-looking
            # `result.pbw.name` is something like "wb-ma06nyic.pbw" and lands in the user's
            # downloads meaning nothing.
            out["pbw_name"] = f"{codegen.slug(design.name)}.pbw"

        if req.render:
            shots = sdk.render(root, design.platforms)
            # `on_watch` is the SDK's DEFAULT screenshot, which already has the sunlight
            # correction applied: a GColorRed fill screenshots as #E35462, measured. So it
            # is the on-wrist appearance as it comes, and running a correction table over it
            # would apply the correction twice.
            #
            # `raw` is `pebble screenshot --no-correction`: the literal palette colours the
            # design specified. Kept because it answers a different question, not because it
            # is more truthful.
            out["render"] = {
                "duration_s": round(shots.duration_s, 2),
                "errors": shots.errors,
                "shots": {
                    plat: {
                        "on_watch": base64.b64encode(png).decode("ascii"),
                        **({"raw": base64.b64encode(shots.raw_shots[plat]).decode("ascii")}
                           if plat in shots.raw_shots else {}),
                    }
                    for plat, png in shots.shots.items()
                },
            }
        return out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _work_dir() -> str:
    path = Path(settings.work_dir)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# --------------------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------------------


@app.get("/api/ai/estimate")
def ai_estimate(kind: str = Query("design"), count: int = Query(1, ge=1, le=64),
                ident: Identity = Depends(identity)) -> dict:
    """What an AI action will cost, so the UI can ask before spending it: the GPU, or, for a
    viewer the broker answers from Claude, no GPU unless a call falls back. Judged for the same
    `ident.user` the job's calls would carry."""
    return ai.estimate(kind, count, requester=ident.user)


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    platforms: list[str] | None = None


def _submit(ident: Identity, kind: str, total: int, unit: str, work: jobs.Work) -> dict:
    """Start a job owned by the viewer; its calls carry `ident.user` and nothing else
    (jobs.py, "Whose name a job's calls carry")."""
    try:
        return jobs.submit(ident.user, kind, total, unit, work)
    except jobs.Busy as exc:
        if exc.finishing:
            # Nothing of theirs is running, so the page has nothing to attach to: `finishing`
            # tells it to show the message alone (module.tsx runPending).
            raise HTTPException(status_code=409, detail={
                "error": "finishing", "job_id": exc.job_id,
                "message": "Your cancelled AI job is still waiting on a model call it sent "
                           "before the cancel; that call finishes and its answer is dropped. "
                           "You can start another once it has returned.",
            }) from exc
        raise HTTPException(status_code=409, detail={
            "error": "busy", "job_id": exc.job_id,
            "message": "You already have an AI job running. It is shown here; cancel it to "
                       "start another.",
        }) from exc
    except jobs.Full as exc:
        raise HTTPException(status_code=503, detail=(
            f"{jobs.MAX_JOBS} AI jobs are already queued, running or finishing a call here; "
            "try again when one has finished")) from exc


@app.post("/api/ai/design", status_code=202)
def ai_design(req: PromptRequest, ident: Identity = Depends(identity)) -> dict:
    """Describe a face, get a validated design document, as a job: 202 with the job's id at
    once, and GET /api/ai/jobs/{job_id} for its progress and, when done, its result.

    The model never sees a compiler. It emits a document, which is validated and layout-
    audited here; a rejected document is repaired once and then given up on, with the
    problems in the failed job's error so the person can finish it in the editor.
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="say what you want the face to look like")

    def work(run: jobs.Run) -> dict:
        # The viewer's own description, sent by them in this request: theirs to send, under
        # the job's own name (run.requester is the job's owner, ident.user).
        design = ai.design_from_prompt(req.prompt, req.platforms, requester=run.requester,
                                       gate=run.gate)
        doc = json.loads(design.model_dump_json(by_alias=True, exclude_none=True))
        return {"design": doc, "layout": summarise(audit(design))}

    return _submit(ident, "design", 1, "design", work)


class VariationRequest(BaseModel):
    design: dict
    count: int | None = Field(default=None, ge=1, le=64)


@app.post("/api/ai/variations", status_code=202)
def ai_variations(req: VariationRequest, ident: Identity = Depends(identity)) -> dict:
    """N variants along named axes, as a job. Each is a separate broker call, and each variant
    joins the job's `partial` list the moment it is finished.

    Partial success is a success. A pass that produced seven usable variants and two failures
    is a done job whose result holds all nine entries with `ok` on each, because a grid with
    seven cells is useful and an exception is not.
    """
    base = _parse(req.design)
    try:
        n = ai.variation_count(req.count)
    except ai.AIError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def work(run: jobs.Run) -> dict:
        # The design the viewer is editing, sent by them in this request, under the job's own
        # name (run.requester is the job's owner, ident.user).
        variants = ai.variations(base, n, requester=run.requester, gate=run.gate,
                                 on_variant=run.variant_done)
        return {"variants": variants, "requested": n}

    return _submit(ident, "variations", n, "variant", work)


_LOST = (f"no such job. A finished job is kept for {jobs.KEEP_FINISHED_S / 60:g} minutes, and a "
         "restart of this rail loses every job in flight; nothing is running for this id")


@app.get("/api/ai/jobs")
def ai_jobs(ident: Identity = Depends(identity)) -> dict:
    """The viewer's own jobs, newest first, so a reloaded page can attach to one. Renews the
    lease of each (jobs.LEASE_S): the viewer is looking."""
    return {"jobs": jobs.listing(ident.user)}


@app.get("/api/ai/jobs/{job_id}")
def ai_job(job_id: str, keepalive: bool = False, ident: Identity = Depends(identity)) -> dict:
    """One of the viewer's jobs: state, progress, partial variants, and the result or error.
    Someone else's job is a 404, the same answer as an id nobody has. The owner's read renews
    the job's lease (jobs.LEASE_S): this is the page's poll, and its keep-alive, and
    a job nobody reads for that long starts no new call. The keep-alive sends
    ``?keepalive=1`` when no page is mounted to show its answer, which renews the lease
    without marking a finished job delivered (jobs.get), so the listing still offers it to
    the next page; every read a page shows is plain and delivers. A plain bool default, not
    Query(), so tools/rail_smoke.py's probe, which calls this function directly, reads
    as the page does."""
    view = jobs.get(job_id, ident.user, deliver=not keepalive)
    if view is None:
        raise HTTPException(status_code=404, detail=_LOST)
    return view


@app.delete("/api/ai/jobs/{job_id}")
def ai_job_cancel(job_id: str, ident: Identity = Depends(identity)) -> dict:
    """Cancel one of the viewer's jobs: no new call starts after this; a call already sent
    finishes and its answer is dropped (jobs.cancel). Answers the job's view."""
    view = jobs.cancel(job_id, ident.user)
    if view is None:
        raise HTTPException(status_code=404, detail=_LOST)
    return view


# --------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------

# An upload is bounded twice before Pillow sees it. FastAPI parses (and Starlette spools) the
# whole multipart body before the handler or any dependency runs, so the first bound is an
# ASGI ceiling in front of the app; the second is the route's own stepped read. The gateway
# relays with no body limit of its own, so these are the rail's.
MULTIPART_SLACK = 64 * 1024   # boundaries and part headers around the one file part
READ_STEP = 64 * 1024


def upload_body_limit() -> int:
    """The most bytes POST /api/images may carry: the image cap and its multipart framing.
    Called per request, so it follows images.MAX_UPLOAD_BYTES."""
    return images.MAX_UPLOAD_BYTES + MULTIPART_SLACK


app.add_middleware(BodyLimit, method="POST", path="/api/images",
                   limit=upload_body_limit, detail=images.too_large)


# How many uploads are decoded at once. A store decodes the whole image before it thumbnails it,
# ~320 MB for one at MAX_IMAGE_PIXELS, and the threadpool alone would run as many as its 40
# tokens allow, where the event loop had run one (the delta audit, 2026-10-15). A third upload
# waits here for a slot, holding no thread, so polls and the other routes still get one.
_STORE_LIMITER = anyio.CapacityLimiter(2)


async def _read_capped(file: UploadFile, cap: int) -> bytes:
    """The upload's bytes, or a 413 the moment they pass `cap`. Read in steps, so an upload
    over the cap is refused one step past it rather than copied into memory whole first.
    ai-playground's `_read_capped` has the same shape; a rail does not import another's code."""
    parts: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(READ_STEP)
        if not piece:
            return b"".join(parts)
        total += len(piece)
        if total > cap:
            raise HTTPException(status_code=413, detail=images.too_large())
        parts.append(piece)


@app.get("/api/images")
def list_images(ident: Identity = Depends(identity)) -> dict:
    return {"images": images.listing(ident.user), "max_stored_px": images.MAX_STORED,
            "max_upload_bytes": images.MAX_UPLOAD_BYTES}


@app.post("/api/images")
async def upload_image(file: UploadFile = File(...),
                       ident: Identity = Depends(identity)) -> dict:
    """Accept an upload, downscale it immediately, store it as PNG.

    Downscaled on the way IN rather than at build time. A phone photo is several megabytes
    against a 256 KB published resource budget, and finding that out at build time produces a
    waf error about pack size that never mentions the photo.
    """
    raw = await _read_capped(file, images.MAX_UPLOAD_BYTES)
    try:
        # In a thread: the decode, transpose, thumbnail and optimised PNG encode take ~0.7 s for
        # a 40 MP JPEG, and on the event loop that stalled every other request, job polls
        # included, for as long (until 2026-10-14). Two at a time (_STORE_LIMITER). The capped
        # read above stays async.
        rec = await anyio.to_thread.run_sync(
            functools.partial(images.store, ident.user, raw, name=file.filename or "image"),
            limiter=_STORE_LIMITER)
    except images.ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rec.__dict__


@app.get("/api/images/{image_id}/raw")
def image_raw(image_id: str, ident: Identity = Depends(identity)) -> Response:
    """The stored source, for the editor's live canvas.

    Cached hard: the id is the content hash, so the bytes behind a given id can never change
    and a revalidation request would always be answered with the same body.
    """
    data = images.raw_png(ident.user, image_id)
    if data is None:
        raise HTTPException(status_code=404, detail="no such image")
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.delete("/api/images/{image_id}")
def delete_image(image_id: str, ident: Identity = Depends(identity)) -> Response:
    if not images.delete(ident.user, image_id):
        raise HTTPException(status_code=404, detail="no such image")
    return Response(status_code=204)


# --------------------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------------------


@app.get("/api/presets")
def preset_list() -> dict:
    """Starting points, each a complete design document plus what it is for.

    No identity dependency and no per-user state: these are the same eight documents for
    everybody, and they are the one thing in this rail worth reading before you have decided
    what you are building.

    The `live` and `limits` fields ship with the document rather than living in the docs. A
    preset derived from a real face carries elements that LOOK like gauges, and the honest
    answer to "does this ring show my battery" belongs next to the ring, not in a README the
    designer has not opened.
    """
    return {"presets": presets.listing()}


@app.get("/api/presets/{preset_id}")
def preset_get(preset_id: str) -> dict:
    found = presets.get(preset_id)
    if found is None:
        raise HTTPException(status_code=404, detail={
            "error": "no such preset", "known": presets.ids(),
        })
    return found


# --------------------------------------------------------------------------------------
# Saved designs
# --------------------------------------------------------------------------------------


@app.get("/api/designs")
def designs(ident: Identity = Depends(identity)) -> dict:
    return {"designs": store.listing(ident.user)}


class SaveRequest(BaseModel):
    id: str | None = None
    design: dict


@app.post("/api/designs")
def save_design(req: SaveRequest, ident: Identity = Depends(identity)) -> dict:
    design = _parse(req.design)
    design_id = req.id or store.new_id()
    doc = json.loads(design.model_dump_json(by_alias=True, exclude_none=True))
    record = store.save(ident.user, design_id, doc, name=design.name)
    return {"id": record["id"], "updated": record["updated"]}


@app.get("/api/designs/{design_id}")
def get_design(design_id: str, ident: Identity = Depends(identity)) -> dict:
    record = store.load(ident.user, design_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such design")
    return record


@app.delete("/api/designs/{design_id}")
def delete_design(design_id: str, ident: Identity = Depends(identity)) -> Response:
    if not store.delete(ident.user, design_id):
        raise HTTPException(status_code=404, detail="no such design")
    return Response(status_code=204)
