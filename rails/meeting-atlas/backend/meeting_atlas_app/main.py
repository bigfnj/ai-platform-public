"""Meeting Atlas backend — FastAPI.

Run (dev): uvicorn meeting_atlas_app.main:app --app-dir rails/meeting-atlas/backend --port 8740

Routes:
  GET  /api/healthz              liveness + whether the recordings mount is visible
  GET  /api/meetings             the index (every meeting, light records + roll-up data)
  GET  /api/meetings/{id}        one meeting's transcript and parsed summary
  GET  /api/meetings/{id}/audio  the recording's audio, range-capable
  POST /api/reindex              rebuild from disk — the hook an ingest task calls (admin)

The gateway sits in front and authenticates every request, so this backend is never
directly reachable by a browser. It is reachable by anything else on the pod network,
which is why every route also requires the identity header the gateway sets — see
identity.py and the app-level dependency below.

WHY THE INDEX IS IN MEMORY
--------------------------
The recordings directory is a Windows path bind-mounted through Podman/Hyper-V, which
means 9p, and 9p rejects rename-over-an-existing-file — the trap that silently broke
every co-worker triage write once its state file existed. Rather than get that right,
this backend never writes to the mount at all. Indexing ~250 segments per meeting is
milliseconds; there is nothing here worth persisting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from meeting_atlas_app.config import MeetingAtlasSettings
from meeting_atlas_app.identity import identity, require_admin
from meeting_atlas_app.indexer import build_index

# Without basicConfig the root logger keeps uvicorn's WARNING default and no handler, so
# every _log.info() here is discarded -- including "indexed N meeting(s)", the one line
# that tells an operator the scan ran at all. terminal-fun and co-worker both set this.
logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("meeting-atlas")

settings = MeetingAtlasSettings()

# Identity is required app-wide. The module docstring above says the gateway authenticates
# every request and this backend is never directly reachable by a browser — true of the
# browser, and it was the whole of the defence. Nothing here checked, so any sibling
# container on the pod network could read the corpus: /api/meetings/{id} returns the full
# transcript of a meeting and /api/meetings/{id}/audio streams the recording. An app-level
# dependency rather than five per-route ones, so the gate is not something a new route has
# to remember to opt into.
#
# Docs go with it: FastAPI's /docs and /openapi.json bypass app-level dependencies, so
# leaving them on would publish the route list to the caller we just refused.
app = FastAPI(title="Meeting Atlas", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(identity)])
# lifespan is attached after definition: it lives beside reindex(), which it calls, and
# Python resolves that name at call time rather than at def time.

# --- the index ---------------------------------------------------------------
# One writer at a time; readers get whatever snapshot is current. The payload is
# replaced wholesale rather than mutated, so a reader mid-request keeps a coherent
# view without holding the lock.
_lock = threading.Lock()          # guards the snapshot swap (held briefly)
_build_lock = threading.RLock()   # serialises the walk itself (held for seconds)
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
    """Rebuild the index. Serialised: only one walk of the tree runs at a time."""
    global _index, _built_at, _build_secs, _fingerprint
    # _build_lock serialises the WORK; _lock guards the swap. Separate locks because the walk
    # can take seconds and readers must not block on it.
    with _build_lock:
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
    """Re-index on read when the mount looks changed and the interval has elapsed.

    The guard is evaluated under _build_lock rather than before it. Read first and two
    concurrent requests both saw a stale _built_at, both decided to rebuild, and both walked
    the entire tree -- the second one's work thrown away. Re-checking inside the lock means
    the loser sees the winner's fresh timestamp and returns immediately.
    """
    if settings.autoreindex_seconds <= 0:
        return
    if time.time() - _built_at < settings.autoreindex_seconds:
        return
    with _build_lock:
        if time.time() - _built_at < settings.autoreindex_seconds:
            return
        if _mount_fingerprint() == _fingerprint:
            return
    reindex("mount changed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warn about missing mounts, then index OFF the event loop.

    Two changes from the `@app.on_event("startup")` this replaces. on_event is deprecated in
    this FastAPI (iep and workstation already moved), and a SYNC startup handler is invoked
    directly on the loop -- so the full tree walk, plus the SQLite copy when a Meetily DB is
    configured, blocked the rail from answering /api/healthz until it finished. Empty here
    today; a few hundred meetings and it is a readiness stall with nothing in the log.
    """
    if not os.path.isdir(settings.recordings_dir):
        _log.warning("recordings dir %s is not visible — the rail will render an "
                     "empty state until it is mounted", settings.recordings_dir)
    if settings.meetily_db and not os.path.isfile(settings.meetily_db):
        _log.warning("meetily db %s is not visible — meeting titles will fall back "
                     "to the folder's auto-generated name", settings.meetily_db)
    await asyncio.to_thread(reindex, "startup")
    yield


app.router.lifespan_context = lifespan


# --- routes ------------------------------------------------------------------

@app.get("/api/healthz")
def healthz() -> dict:
    """Liveness plus the corpus counters.

    Refreshes on the same terms as /api/meetings. It used to skip _maybe_refresh(), so health
    could report n_meetings: 0 against a stale snapshot while a read one second later returned
    a dozen — a health route that disagrees with the data is worse than none, and it is exactly
    the stale-read failure RC015 exists to catch (which does not fire here, because this rail
    declares no status_route).

    n_summarised / n_enriched / n_flagged were reachable only by triggering an ADMIN reindex.
    They are the counters that say whether ingestion is actually landing, which is the question
    while the rail is empty, so they belong on the read-only route.
    """
    _maybe_refresh()
    c = _index["corpus"]
    mounted = os.path.isdir(settings.recordings_dir)
    db_set = bool(settings.meetily_db)
    db_mounted = db_set and os.path.isfile(settings.meetily_db)
    return {
        "ok": True,
        "app": settings.app_name,
        "recordings_dir": settings.recordings_dir,
        "recordings_mounted": mounted,
        "meetily_db": settings.meetily_db or None,
        "meetily_db_mounted": db_mounted,
        # Configured but not mounted is a silent-failure shape: titles quietly fall back to the
        # folder's auto-generated name and nothing else says why. Surface it as its own flag.
        "meetily_db_misconfigured": db_set and not db_mounted,
        "display_tz": settings.display_tz,
        "n_meetings": c.get("n_meetings", 0),
        "n_summarised": c.get("n_summarised", 0),
        "n_enriched": c.get("n_enriched", 0),
        "n_flagged": c.get("n_flagged", 0),
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
    #
    # Derive the type from the extension rather than hardcoding video/mp4: the filename comes
    # from the meeting's metadata.json and only the FALLBACK is literally audio.mp4, so a .wav
    # or .m4a recording was being served with a type the browser will not play. That surfaces
    # as "the player is broken" long after anyone remembers this line.
    ext = os.path.splitext(path)[1].lower()
    media_type = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".wav": "audio/wav",
                  ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/opus",
                  ".webm": "audio/webm", ".flac": "audio/flac"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.post("/api/reindex")
def do_reindex(_=Depends(require_admin)) -> dict:
    """Rebuild from disk. The hook an ingest task calls once it has written sidecars.

    Admin-only, not merely identified: this walks the whole recordings mount and swaps the
    index out from under every in-flight reader, so anyone who can call it can pin the
    backend to a full rebuild on repeat. The ingest task is a platform job and fires with
    admin headers; a human browsing the rail has no reason to reach it.
    """
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
