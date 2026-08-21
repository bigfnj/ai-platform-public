"""Meeting Atlas backend — FastAPI.

Run (dev): uvicorn meeting_atlas_app.main:app --app-dir rails/meeting-atlas/backend --port 8740

Routes:
  GET  /api/healthz              liveness + whether the recordings mount is visible
  GET  /api/meetings             the index (every meeting, light records + roll-up data)
  GET  /api/meetings/{id}        one meeting's transcript and parsed summary
  GET  /api/meetings/{id}/audio  the recording's audio, range-capable
  POST /api/reindex              rebuild from disk — the hook an ingest task calls

The gateway sits in front and authenticates every request, so this backend is never
directly reachable by a browser.

WHY THE INDEX IS IN MEMORY
--------------------------
The recordings directory is a Windows path bind-mounted through Podman/Hyper-V, which
means 9p, and 9p rejects rename-over-an-existing-file — the trap that silently broke
every co-worker triage write once its state file existed. Rather than get that right,
this backend never writes to the mount at all. Indexing ~250 segments per meeting is
milliseconds; there is nothing here worth persisting.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from meeting_atlas_app.config import MeetingAtlasSettings
from meeting_atlas_app.indexer import build_index

_log = logging.getLogger("meeting-atlas")

settings = MeetingAtlasSettings()
app = FastAPI(title="Meeting Atlas", version="0.1.0")

# --- the index ---------------------------------------------------------------
# One writer at a time; readers get whatever snapshot is current. The payload is
# replaced wholesale rather than mutated, so a reader mid-request keeps a coherent
# view without holding the lock.
_lock = threading.Lock()
_index: dict = {"corpus": {"available": False, "n_meetings": 0}, "meetings": [],
                "details": {}}
_built_at = 0.0
_build_secs = 0.0
_fingerprint: tuple | None = None


def _mount_fingerprint() -> tuple:
    """Cheap change detector: (count, newest mtime) over the top-level folders.

    Deliberately shallow. A sidecar written into an existing meeting folder bumps
    that folder's mtime, so this catches ingestion without walking every file.
    """
    root = settings.recordings_dir
    try:
        entries = [os.path.join(root, n) for n in os.listdir(root)]
    except OSError:
        return (0, 0.0)
    dirs = [p for p in entries if os.path.isdir(p)]
    newest = 0.0
    for d in dirs:
        try:
            newest = max(newest, os.path.getmtime(d))
        except OSError:
            continue
    return (len(dirs), round(newest, 3))


def reindex(reason: str = "manual") -> dict:
    global _index, _built_at, _build_secs, _fingerprint
    t0 = time.time()
    payload = build_index(settings.recordings_dir, settings.meetily_db or None,
                          settings.tz(), _log)
    with _lock:
        _index = payload
        _built_at = time.time()
        _build_secs = round(_built_at - t0, 3)
        _fingerprint = _mount_fingerprint()
    c = payload["corpus"]
    _log.info("indexed %d meeting(s) in %.3fs (%s)", c.get("n_meetings", 0),
              _build_secs, reason)
    return payload


def _maybe_refresh() -> None:
    """Re-index on read when the mount looks changed and the interval has elapsed."""
    if settings.autoreindex_seconds <= 0:
        return
    if time.time() - _built_at < settings.autoreindex_seconds:
        return
    if _mount_fingerprint() == _fingerprint:
        return
    reindex("mount changed")


@app.on_event("startup")
def _startup() -> None:
    if not os.path.isdir(settings.recordings_dir):
        _log.warning("recordings dir %s is not visible — the rail will render an "
                     "empty state until it is mounted", settings.recordings_dir)
    if settings.meetily_db and not os.path.isfile(settings.meetily_db):
        _log.warning("meetily db %s is not visible — meeting titles will fall back "
                     "to the folder's auto-generated name", settings.meetily_db)
    reindex("startup")


# --- routes ------------------------------------------------------------------

@app.get("/api/healthz")
def healthz() -> dict:
    c = _index["corpus"]
    return {
        "ok": True,
        "app": settings.app_name,
        "recordings_dir": settings.recordings_dir,
        "recordings_mounted": os.path.isdir(settings.recordings_dir),
        "meetily_db": settings.meetily_db or None,
        "meetily_db_mounted": bool(settings.meetily_db)
                              and os.path.isfile(settings.meetily_db),
        "display_tz": settings.display_tz,
        "n_meetings": c.get("n_meetings", 0),
        "indexed_at": c.get("generated_at"),
        "index_seconds": _build_secs,
    }


@app.get("/api/meetings")
def meetings() -> dict:
    _maybe_refresh()
    snap = _index
    return {"corpus": snap["corpus"], "meetings": snap["meetings"]}


@app.get("/api/meetings/{meeting_id}")
def meeting(meeting_id: str) -> dict:
    _maybe_refresh()
    snap = _index
    det = snap["details"].get(meeting_id)
    if det is None:
        raise HTTPException(status_code=404, detail="no such meeting")
    row = next((m for m in snap["meetings"] if m["id"] == meeting_id), None)
    return {"meeting": row, "detail": det}


@app.get("/api/meetings/{meeting_id}/audio")
def audio(meeting_id: str):
    if not settings.serve_audio:
        raise HTTPException(status_code=404, detail="audio serving is disabled")
    det = _index["details"].get(meeting_id)
    if det is None or not det.get("audio"):
        raise HTTPException(status_code=404, detail="no audio for this meeting")

    # Resolve inside the recordings root and verify containment. The id comes from our
    # own index rather than the caller, but a path check is cheap and this is the one
    # route that turns a request into a filesystem read.
    root = os.path.realpath(settings.recordings_dir)
    path = os.path.realpath(os.path.join(root, det["folder"], det["audio"]))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="audio file is not readable")
    # No filename= on purpose: FileResponse turns that into a
    # Content-Disposition: attachment, and this route exists to be played by an
    # <audio> element, not downloaded.
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/reindex")
def do_reindex() -> dict:
    """Rebuild from disk. The hook an ingest task calls once it has written sidecars."""
    payload = reindex("api")
    c = payload["corpus"]
    return {
        "ok": True,
        "n_meetings": c.get("n_meetings", 0),
        "n_summarised": c.get("n_summarised", 0),
        "n_enriched": c.get("n_enriched", 0),
        "n_flagged": c.get("n_flagged", 0),
        "seconds": _build_secs,
        "indexed_at": c.get("generated_at"),
    }
