"""FastAPI dashboard: upload documents, pick a workflow, watch live staged
status, download a self-contained zip, browse the library."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from edu_media_core import broker_media, present_levels

from . import library, workflows
from .queue import JobQueue
from .store import Store

store = Store(library.db_path())
queue = JobQueue(store)

# IEP Present Levels is deployed as its OWN platform app: a second instance of this
# image with IEP_ONLY=1 serves only that workflow (own container, library, entitlement —
# isolating student PII). The default (edu-suite) instance HIDES it, so the IEP tab never
# appears among the content workflows. The two instances are mutually exclusive by design.
IEP_ONLY = os.getenv("IEP_ONLY", "").strip().lower() in ("1", "true", "yes")
_ONLY_KEY = "iep_present_levels"

# Retention: on the IEP app, held generations carry student PII, so completed/failed jobs
# auto-expire after this many days (files on the isolated volume + the DB row). 0 = keep
# forever. Env-overridable (IEP_RETENTION_DAYS). Only the IEP instance sweeps — the
# edu-suite instance never deletes anything automatically.
try:
    IEP_RETENTION_DAYS = int(os.getenv("IEP_RETENTION_DAYS", "30"))
except ValueError:
    IEP_RETENTION_DAYS = 30
_retention_stop = threading.Event()

# Default (edu-suite) instance retention. Content-team work, so a long default (1 year). Unlike
# the IEP instance's in-process sweep, this one is driven by the central scheduler (it fires
# POST /api/maintenance/expire daily). 0 = keep forever. Env-overridable.
try:
    EDU_SUITE_RETENTION_DAYS = int(os.getenv("EDU_SUITE_RETENTION_DAYS", "365"))
except ValueError:
    EDU_SUITE_RETENTION_DAYS = 365


def _expire_old_iep_jobs() -> int:
    """Delete IEP generations older than IEP_RETENTION_DAYS (done/failed only). Returns
    the count removed. No-op unless this is the IEP instance with retention enabled."""
    if not IEP_ONLY or IEP_RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - IEP_RETENTION_DAYS * 86400
    removed = 0
    for job in store.list_jobs(workflow=_ONLY_KEY, limit=10000):
        if job.get("status") in ("done", "failed") and (job.get("created_at") or 0) < cutoff:
            shutil.rmtree(job["dir"], ignore_errors=True)
            store.delete_job(job["id"])
            removed += 1
    return removed


def _expire_jobs(days: int, workflow: str | None) -> int:
    """Delete this instance's done/failed jobs (their files + DB row) older than ``days``. Returns
    the count removed; ``days <= 0`` is a no-op. Each instance has its own isolated store, so this
    only ever touches the calling instance's jobs."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for job in store.list_jobs(workflow=workflow, limit=10000):
        if job.get("status") in ("done", "failed") and (job.get("created_at") or 0) < cutoff:
            shutil.rmtree(job["dir"], ignore_errors=True)
            store.delete_job(job["id"])
            removed += 1
    return removed


def _retention_loop() -> None:
    """Sweep at startup, then once a day, until shutdown."""
    while not _retention_stop.is_set():
        try:
            _expire_old_iep_jobs()
        except Exception:  # noqa: BLE001 — a sweep hiccup must never take the app down
            pass
        _retention_stop.wait(24 * 3600)


def _visible_workflows():
    wfs = workflows.all_workflows()
    if IEP_ONLY:
        return [w for w in wfs if w.key == _ONLY_KEY]
    return [w for w in wfs if w.key != _ONLY_KEY]  # edu-suite instance hides the IEP app

_DRIVE = re.compile(r"^[A-Za-z]:$")
_JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db"}


def safe_relpath(name: str) -> Path | None:
    """Turn an uploaded file's (possibly folder-relative) name into a safe path
    under input/. Folder uploads send ``webkitRelativePath`` like
    ``Great Expectations/Week 1/reading.pdf``; a plain multi-file upload sends
    just the basename. Rejects path traversal, absolute paths, and drive letters,
    and drops OS junk, so a crafted folder can never write outside input/."""
    raw = (name or "").replace("\\", "/")
    parts = [p for p in raw.split("/")
             if p not in ("", ".", "..") and not _DRIVE.match(p) and p not in _JUNK]
    parts = [p for p in parts if not p.startswith(".")]  # dotfiles/dot-dirs
    return Path(*parts) if parts else None


def _default_job_name(files: list[UploadFile]) -> str:
    """When the teacher leaves the name blank, name the job after what they uploaded:
    the master folder for a folder upload, else the first document's name (no extension)."""
    for uf in files:
        raw = (uf.filename or "").replace("\\", "/")
        parts = [p for p in raw.split("/") if p]
        if not parts:
            continue
        return parts[0] if len(parts) > 1 else Path(parts[-1]).stem
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    queue.start()
    if IEP_ONLY and IEP_RETENTION_DAYS > 0:
        threading.Thread(target=_retention_loop, name="iep-retention", daemon=True).start()
    yield
    _retention_stop.set()
    queue.stop()


app = FastAPI(title="edu-suite dashboard", lifespan=lifespan)

# Per-user ownership. The gateway authenticates each request and sets the trusted X-Platform-*
# headers; jobs are owned by their creator, and a user only sees/touches their own (admins see all,
# and legacy NULL-owner jobs are admin-only). Fail closed when the identity header is absent (a
# direct-to-rail call) unless EDU_STANDALONE is set for local dev.
EDU_STANDALONE = os.getenv("EDU_STANDALONE", "").strip().lower() in ("1", "true", "yes")


class Identity:
    __slots__ = ("user", "is_admin")

    def __init__(self, user: str | None, is_admin: bool) -> None:
        self.user = user
        self.is_admin = is_admin


def identity(
    x_platform_user: str | None = Header(default=None),
    x_platform_admin: str | None = Header(default=None),
) -> Identity:
    # A present header (from the gateway) is always honored. Absent means a direct-to-rail call:
    # reject it in the deployed topology, but in standalone dev/tests grant a null dev-admin so the
    # rail is usable without a gateway.
    if x_platform_user is None:
        if EDU_STANDALONE:
            return Identity(None, True)
        raise HTTPException(status_code=401, detail="unauthenticated (no platform identity)")
    return Identity(x_platform_user,
                    (x_platform_admin or "").strip().lower() in ("1", "true", "yes"))


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route. The scheduler fires with X-Platform-Admin, so it passes."""
    if not ident.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return ident


@app.post("/api/maintenance/expire")
def maintenance_expire(_admin: Identity = Depends(require_admin)) -> dict:
    """Delete this instance's done/failed jobs past its retention window (files + DB rows).
    Admin-only; the central scheduler fires this daily for the edu-suite instance (365-day
    default). The IEP instance keeps its own 30-day in-process sweep (unchanged)."""
    if IEP_ONLY:
        return {"removed": _expire_jobs(IEP_RETENTION_DAYS, _ONLY_KEY)}
    return {"removed": _expire_jobs(EDU_SUITE_RETENTION_DAYS, None)}


def _restrict(ident: Identity) -> str | None:
    """The owner filter for list queries: None for admins (see all), the username otherwise."""
    return None if ident.is_admin else ident.user


def _authorize_job(job: dict | None, ident: Identity) -> dict:
    """Return the job if the caller may access it, else 404 (a 404 rather than 403 avoids
    disclosing that a job id exists). Admins see all; a user sees only jobs they own; legacy
    NULL-owner jobs are admin-only."""
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if ident.is_admin or (job.get("owner") is not None and job.get("owner") == ident.user):
        return job
    raise HTTPException(status_code=404, detail="job not found")


@app.get("/api/workflows")
def api_workflows():
    return [{"key": w.key, "label": w.label, "description": w.description}
            for w in _visible_workflows()]


# The AI reads a teacher's free-text "additional instructions" for a workflow, decides
# what is in scope for THAT workflow, and confirms it back BEFORE the job runs — mapping
# only onto real levers and pushing back, honestly, on the rest.
_INTERPRET_TAIL = """

A teacher may add free-text instructions to tweak a job. Decide what is in scope and confirm \
it back. Be concrete and HONEST — never promise anything outside the allowed levers.

Return ONLY JSON:
{
  "understanding": "<1-2 plain sentences stating exactly what you WILL do, only in terms of the allowed levers>",
  "applies": ["<short phrase per in-scope adjustment>"],
  "ignored": [{"text": "<the unsupported/out-of-scope part>", "reason": "<brief, kind reason>"}],
  "needs_clarification": <true if the instruction is ambiguous>,
  "question": "<short friendly confirmation; if nothing is in scope, ask them to revise>",
  "guidance": "<concise direct guidance for the AI containing ONLY the in-scope adjustments; empty string if nothing is in scope>"
}"""

_INTERPRET_BY_WF = {
    "teachtown_builder": """You are the assistant for "TeachTown Builder", which turns uploaded \
worksheet PDFs into a drafted interactive lesson: for each worksheet it drafts a few vocabulary \
words and ONE short activity (multiple-choice, typing, or sorting), grouped by the Week folders \
(top-level files form an "Overview" section); optionally translated to Mexican Spanish with audio.

YOU CAN adjust (the only real levers): the STYLE and CONTENT of the drafted vocabulary and \
activities — reading level / simplicity, tone, how many vocabulary words, which activity type to \
prefer (multiple choice / typing / sorting), and which subjects or topics to emphasize or avoid.

YOU CANNOT (say so plainly): limit or target translation/audio to specific weeks (translation is \
whole-unit or off, a separate checkbox — "only translate Week 1" is not supported); change \
visuals, colors, images, or layout; do anything unrelated to drafting a lesson from the \
worksheets (e.g. "make the sky purple", "dinosaurs in hats"); or invent facts.""" + _INTERPRET_TAIL,

    "just_translate": """You are the assistant for "Just Translate", which translates uploaded \
documents into Mexican Spanish (es_MX) and produces a side-by-side English/Spanish page with \
English + Spanish audio for every passage.

YOU CAN adjust (the only real levers): the STYLE of the translation — formality/register \
(informal tú vs. formal usted), reading level / simplicity, tone, regional Mexican word choices, \
and how names or specialized terms are handled.

YOU CANNOT (say so plainly): change which documents are translated or add/remove content; turn \
off the audio (it is always included); translate to any language other than Mexican Spanish; \
change the page layout or visuals; or anything unrelated to translating the uploaded text.""" + _INTERPRET_TAIL,

    "cvc": """You are the assistant for "CVC Words", which builds a bilingual phonics worksheet \
from a list of CVC words: each word gets a Mexican-Spanish translation, a simple children's \
cartoon picture, and English + Spanish audio.

YOU CAN adjust (the only real levers): the Spanish word choices (e.g. prefer the most common / \
simpler / regional Mexican word) and the SUBJECT or emphasis of each word's picture.

YOU CANNOT (say so plainly): change the word list itself (it comes from the upload); change the \
worksheet layout or format; turn off the audio; change the picture STYLE away from a simple \
children's cartoon (photos/realistic images aren't available); or anything unrelated to the \
worksheet.""" + _INTERPRET_TAIL,
}


# Deterministic backstop: intents the pipeline genuinely CANNOT honor, per workflow.
# The LLM guard usually catches these, but it's non-deterministic — so we also force any
# match into "ignored", strip it from the guidance/applies, and correct the understanding,
# so the confirmation can never promise something we won't actually do.
_UNSUPPORTED = {
    "_all": [
        (re.compile(r"\b(font|colou?rs?|layout|spacing|margins?|background)\b", re.I),
         "I can't change fonts, colors, or page layout."),
        (re.compile(r"\b(photos?|photograph\w*|photo-?realistic|realistic)\b", re.I),
         "This tool doesn't produce photos or realistic images."),
        (re.compile(r"(no|without|remove|turn off|disable|mute)\s+(the\s+)?(audio|sound|voice)", re.I),
         "Audio isn't turned on or off by instructions."),
    ],
    "teachtown_builder": [
        (re.compile(r"(only|just)\s+translat|translat\w*[\s\S]{0,25}week|week[\s\S]{0,25}translat", re.I),
         "Translation is whole-unit or off (a checkbox) — it can't be limited to specific weeks."),
    ],
}


def _enforce_scope(workflow: str, instructions: str, r: dict) -> dict:
    """Force known-unsupported asks out of the 'will do' side, wherever they appear."""
    rules = _UNSUPPORTED.get("_all", []) + _UNSUPPORTED.get(workflow, [])

    def bad(text: str) -> bool:
        return any(rx.search(text or "") for rx, _ in rules)

    # A rule fires if the teacher asked for it OR the model claimed it in its output.
    claimed = [r.get("understanding", ""), r.get("guidance", ""), *r.get("applies", [])]
    triggered: dict[str, str] = {}
    for rx, reason in rules:
        m = rx.search(instructions)
        if m:
            triggered[reason] = m.group(0)  # prefer the teacher's own wording
        elif any(rx.search(c or "") for c in claimed):
            triggered.setdefault(reason, "(unsupported)")
    if not triggered:
        return r

    have = {i["reason"] for i in r["ignored"]}
    for reason, text in triggered.items():
        if reason not in have:
            r["ignored"].append({"text": text, "reason": reason})
            have.add(reason)

    r["applies"] = [a for a in r["applies"] if not bad(a)]
    r["guidance"] = "; ".join(c.strip() for c in re.split(r"[;,\n]", r["guidance"])
                              if c.strip() and not bad(c))
    if bad(r["understanding"]):
        r["understanding"] = ("I'll " + "; ".join(r["applies"]) + ".") if r["applies"] else ""
    if not r["applies"] and not r["guidance"].strip():
        r["needs_clarification"] = True
        if not r["question"] or bad(r["question"]):
            r["question"] = ("I can only adjust things this tool actually supports — "
                             "please revise your instructions.")
    return r


@app.post("/api/interpret")
def api_interpret(payload: dict = Body(...)):
    """Interpret a teacher's additional instructions for a workflow and confirm scope
    before running. Sync def so the blocking broker call runs in a threadpool."""
    empty = {"understanding": "", "applies": [], "ignored": [],
             "needs_clarification": False, "question": "", "guidance": ""}
    instructions = (payload.get("instructions") or "").strip()[:2000]
    workflow = payload.get("workflow") or ""
    system = _INTERPRET_BY_WF.get(workflow)
    if not instructions or system is None:
        return empty
    n = payload.get("worksheets")
    if workflow == "teachtown_builder":
        weeks = payload.get("weeks") or []
        ctx = f"Week folders detected: {weeks if weeks else 'none (Overview only)'}."
        if n:
            ctx += f" Worksheets selected: {n}."
    elif workflow == "just_translate":
        ctx = f"Documents uploaded: {n}." if n else "One or more documents uploaded."
    elif workflow == "cvc":
        ctx = (f"Word-list file(s) uploaded: {n} (individual CVC words are parsed from them)."
               if n else "A CVC word list will be provided (or the built-in sample set).")
    else:
        ctx = ""
    try:
        r = broker_media.chat_json(system, f"{ctx}\n\nTeacher's instructions:\n{instructions}",
                                   options={"temperature": 0.2, "num_ctx": 4096})
    except broker_media.BrokerUnavailable as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Could not interpret the instructions: {e}"}, status_code=502)
    ignored = []
    for i in (r.get("ignored") or []):
        if isinstance(i, dict):
            ignored.append({"text": str(i.get("text", "")), "reason": str(i.get("reason", ""))})
        elif i:
            ignored.append({"text": str(i), "reason": ""})
    result = {
        "understanding": str(r.get("understanding") or ""),
        "applies": [str(x) for x in (r.get("applies") or [])],
        "ignored": ignored,
        "needs_clarification": bool(r.get("needs_clarification")),
        "question": str(r.get("question") or ""),
        "guidance": str(r.get("guidance") or ""),
    }
    # Deterministic backstop over the model's (non-deterministic) output.
    return _enforce_scope(workflow, instructions, result)


@app.post("/api/jobs")
async def create_job(workflow: str = Form(...), name: str = Form(""),
                     params: str = Form("{}"),
                     files: list[UploadFile] = File(default=[]),
                     ident: Identity = Depends(identity)):
    try:
        workflows.get(workflow)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if IEP_ONLY and workflow != _ONLY_KEY:
        return JSONResponse({"error": "this instance only serves the IEP Present Levels workflow"},
                            status_code=403)
    if not IEP_ONLY and workflow == _ONLY_KEY:
        return JSONResponse({"error": "IEP Present Levels runs as its own app, not here"},
                            status_code=403)
    try:
        params_dict = json.loads(params) if params else {}
        if not isinstance(params_dict, dict):
            params_dict = {}
    except Exception:
        params_dict = {}
    valid = [(uf, rel) for uf in files if (rel := safe_relpath(uf.filename or "")) is not None]

    # Just Translate: each uploaded file is its own book, so make ONE job per file (named after
    # the file) instead of a single multi-book bundle. The serialized queue then starts the first
    # and queues the rest, so the first book is viewable while the others process. TeachTown
    # (folder = one unit) and CVC (a word list) keep all files in a single job.
    if workflow == "just_translate" and len(valid) > 1:
        created = []
        for uf, rel in valid:
            data = await uf.read()
            jid = library.new_job_id()
            jname = Path(rel.name).stem or f"{workflow}-{jid}"
            jdir = library.make_job_dir(workflow, jname, jid)
            dest = jdir / "input" / rel.name  # one file -> its own book, flattened to the basename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            store.create_job(jid, jname, workflow, str(jdir), params=params_dict, owner=ident.user)
            created.append({"id": jid, "name": jname})
        queue.notify()
        first = created[0]
        return JSONResponse({"id": first["id"], "name": first["name"], "workflow": workflow,
                             "files": 1, "queued": len(created), "jobs": created})

    job_id = library.new_job_id()
    name = (name or "").strip() or _default_job_name(files) or f"{workflow}-{job_id}"
    job_dir = library.make_job_dir(workflow, name, job_id)
    saved = 0
    for uf, rel in valid:
        dest = job_dir / "input" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await uf.read())
        saved += 1
    store.create_job(job_id, name, workflow, str(job_dir), params=params_dict, owner=ident.user)
    queue.notify()
    return JSONResponse({"id": job_id, "name": name, "workflow": workflow, "files": saved})


@app.get("/api/jobs")
def api_jobs(workflow: str | None = None, status: str | None = None, q: str | None = None,
             ident: Identity = Depends(identity)):
    return store.list_jobs(workflow=workflow, status=status, query=q, restrict_owner=_restrict(ident))


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, ident: Identity = Depends(identity)):
    job = _authorize_job(store.get_job(job_id), ident)
    job["events"] = store.get_events(job_id)
    return job


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, ident: Identity = Depends(identity)):
    _authorize_job(store.get_job(job_id), ident)

    def gen():
        last = 0
        while True:
            evs = store.get_events(job_id, after_id=last)
            for e in evs:
                last = e["id"]
                yield f"data: {json.dumps(e)}\n\n"
            job = store.get_job(job_id)
            if not job:
                break
            if job["status"] in ("done", "failed") and not evs:
                yield f"data: {json.dumps({'kind': '_end', 'status': job['status']})}\n\n"
                break
            time.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")


def _download_name(job: dict) -> str:
    """The download filename: the shared bundle base name + '.zip' (so the zip and
    the bundle's root HTML always match). See library.bundle_basename."""
    return library.bundle_basename(job.get("name") or "", job.get("created_at")) + ".zip"


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, ident: Identity = Depends(identity)):
    job = _authorize_job(store.get_job(job_id), ident)
    zip_path = Path(job["dir"]) / "output.zip"
    if not zip_path.exists():
        return JSONResponse({"error": "bundle not ready"}, status_code=409)
    return FileResponse(zip_path, filename=_download_name(job),
                        media_type="application/zip")


@app.get("/api/jobs/{job_id}/site")
@app.get("/api/jobs/{job_id}/site/{path:path}")
def job_site(job_id: str, path: str = "", ident: Identity = Depends(identity)):
    """Serve a finished job's built site over HTTP so it can be LAUNCHED live off the
    platform (no local Node server needed for the launch path). Gated upstream by the
    gateway's login + entitlement. `…/site/` serves the entry document (index.html, else
    the single root .html); `…/site/<rel>` serves that asset. Path-traversal-guarded to
    the job's output/ dir. The zip download still ships the offline server bits."""
    job = _authorize_job(store.get_job(job_id), ident)
    root = (Path(job["dir"]) / "output").resolve()
    rel = (path or "").strip("/")
    if not rel:  # entry document
        target = root / "index.html"
        if not target.exists():
            htmls = sorted(root.glob("*.html"))
            if not htmls:
                return JSONResponse({"error": "no launchable site for this job"}, status_code=404)
            target = htmls[0]
    else:
        target = (root / rel).resolve()
        try:
            target.relative_to(root)  # reject path traversal
        except ValueError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(target))


@app.patch("/api/jobs/{job_id}")
def api_rename(job_id: str, name: str = Body(..., embed=True),
               ident: Identity = Depends(identity)):
    _authorize_job(store.get_job(job_id), ident)
    store.rename_job(job_id, (name or "").strip() or job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def api_delete(job_id: str, ident: Identity = Depends(identity)):
    job = _authorize_job(store.get_job(job_id), ident)
    shutil.rmtree(job["dir"], ignore_errors=True)
    store.delete_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/prune")
def api_prune(job_id: str, ident: Identity = Depends(identity)):
    """Delete a job's work/ intermediates, keeping input/output/zip."""
    job = _authorize_job(store.get_job(job_id), ident)
    shutil.rmtree(Path(job["dir"]) / "work", ignore_errors=True)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/unit")
def api_unit(job_id: str, ident: Identity = Depends(identity)):
    job = _authorize_job(store.get_job(job_id), ident)
    p = Path(job["dir"]) / "output" / "unit.json"
    if not p.exists():
        return JSONResponse({"error": "no draft unit for this job"}, status_code=404)
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.post("/api/jobs/{job_id}/finalize")
def api_finalize(job_id: str, payload: dict = Body(...), ident: Identity = Depends(identity)):
    """Build (or rebuild) a TeachTown unit from an edited draft, reusing the
    source job's worksheets."""
    src = _authorize_job(store.get_job(job_id), ident)
    unit = payload.get("unit")
    if not isinstance(unit, dict):
        return JSONResponse({"error": "edited unit required"}, status_code=400)
    name = (payload.get("name") or src["name"] or "Unit").strip()
    new_id = library.new_job_id()
    jd = library.make_job_dir("teachtown_builder", name, new_id)
    in_root = Path(src["dir"]) / "input"
    for f in in_root.rglob("*"):  # preserve the uploaded folder structure so
        if f.is_file() and f.suffix.lower() == ".pdf":  # worksheet keys stay stable
            dest = jd / "input" / f.relative_to(in_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, dest)
    (jd / "input" / "unit.json").write_text(json.dumps(unit, ensure_ascii=False, indent=2), encoding="utf-8")
    params = {"name": name, "review": False,
              "enrich": bool(payload.get("enrich")), "audio": bool(payload.get("audio"))}
    store.create_job(new_id, name, "teachtown_builder", str(jd), params=params, owner=ident.user)
    queue.notify()
    return {"id": new_id}


# --- IEP Present Levels -----------------------------------------------------

@app.post("/api/iep/parse")
async def api_iep_parse(file: UploadFile = File(...), ident: Identity = Depends(identity)):
    """Quick, JOBLESS parse: OCR-extract the 8 present-levels sections from an
    uploaded SEIS PDF and return them for the inline review form. The PDF is written
    to a temp file, extracted, and deleted immediately — nothing is persisted to the
    library, so the raw student PDF never lands on disk (minimal PII at rest). This
    replaces the old "extract job" step for the single-page IEP flow. IEP-only."""
    if not IEP_ONLY:
        return JSONResponse({"error": "parse is only available on the IEP Present Levels app"},
                            status_code=403)
    data = await file.read()
    if not data:
        return JSONResponse({"error": "empty upload"}, status_code=400)
    if not (file.filename or "").lower().endswith(".pdf"):
        return JSONResponse({"error": "Upload a SEIS Present-Levels PDF."}, status_code=400)
    tmp = tempfile.NamedTemporaryFile(prefix="iep-", suffix=".pdf", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        result = await run_in_threadpool(present_levels.extract, tmp.name)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Could not parse the PDF: {e}"}, status_code=502)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    result["source"] = file.filename or "present-levels.pdf"  # not the temp name
    return JSONResponse(result)


@app.post("/api/iep/generate")
def api_iep_generate(payload: dict = Body(...), ident: Identity = Depends(identity)):
    """Generate the elaborated present-levels narrative directly from the teacher-filled
    form — no prior extract job needed (the single-page flow parses joblessly, then
    submits here). Creates an iep_present_levels job whose input holds filled.json; the
    queue runs the generate step and bundles output.zip. Returns the new job id. IEP-only."""
    if not IEP_ONLY:
        return JSONResponse({"error": "generate is only available on the IEP Present Levels app"},
                            status_code=403)
    filled = payload.get("filled")
    if not isinstance(filled, dict) or not isinstance(filled.get("sections"), dict):
        return JSONResponse({"error": "filled sections required"}, status_code=400)
    name = (payload.get("name") or filled.get("name") or "Present Levels").strip()
    new_id = library.new_job_id()
    jd = library.make_job_dir("iep_present_levels", name, new_id)
    (jd / "input" / "filled.json").write_text(
        json.dumps(filled, ensure_ascii=False, indent=2), encoding="utf-8")
    store.create_job(new_id, name, "iep_present_levels", str(jd),
                     params={"name": name, "kind": "generate"}, owner=ident.user)
    queue.notify()
    return {"id": new_id}


@app.get("/api/jobs/{job_id}/present-levels-final")
def api_present_levels_final(job_id: str, ident: Identity = Depends(identity)):
    """The GENERATED (elaborated) 8 sections + Areas of Need for a finished generate
    job — what the results screen renders for the on-screen preview and per-section copy."""
    job = _authorize_job(store.get_job(job_id), ident)
    p = Path(job["dir"]) / "output" / "present_levels_final.json"
    if not p.exists():
        return JSONResponse({"error": "not generated yet"}, status_code=404)
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.get("/api/jobs/{job_id}/present-levels")
def api_present_levels(job_id: str, ident: Identity = Depends(identity)):
    """The OCR-extracted 8 present-levels sections for a finished extract job —
    what the two-column review form loads."""
    job = _authorize_job(store.get_job(job_id), ident)
    p = Path(job["dir"]) / "output" / "present_levels.json"
    if not p.exists():
        return JSONResponse({"error": "no extracted present levels for this job"}, status_code=404)
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.post("/api/jobs/{job_id}/generate-iep")
def api_generate_iep(job_id: str, payload: dict = Body(...), ident: Identity = Depends(identity)):
    """Generate the elaborated present-levels narrative from the teacher-filled
    form: create a new iep_present_levels job whose input holds filled.json (the
    extracted 'current' + the teacher's new input per section)."""
    src = _authorize_job(store.get_job(job_id), ident)
    filled = payload.get("filled")
    if not isinstance(filled, dict) or not isinstance(filled.get("sections"), dict):
        return JSONResponse({"error": "filled sections required"}, status_code=400)
    name = (payload.get("name") or src["name"] or "Present Levels").strip()
    new_id = library.new_job_id()
    jd = library.make_job_dir("iep_present_levels", name, new_id)
    (jd / "input").mkdir(parents=True, exist_ok=True)
    (jd / "input" / "filled.json").write_text(
        json.dumps(filled, ensure_ascii=False, indent=2), encoding="utf-8")
    store.create_job(new_id, name, "iep_present_levels", str(jd), params={"name": name},
                     owner=ident.user)
    queue.notify()
    return {"id": new_id}
