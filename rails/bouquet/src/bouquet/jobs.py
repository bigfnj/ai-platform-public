"""A tiny in-process job store for the long broker calls.

Vision identification and report writing can each take a minute or more when the
27B model cold-loads. Held open as a single HTTP request, that trips Cloudflare's
~100s edge timeout (524) on the public URL. So the slow work runs as a background
job and the client polls a fast status endpoint instead — every request stays short.

Single uvicorn worker + single tenant, so a process-local dict guarded by a lock is
enough; nothing here needs to survive a restart (an in-flight job just fails and the
florist retries). Finished jobs are pruned by age/count so the dict can't grow without
bound.
"""

from __future__ import annotations

import threading
import time
import uuid

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

_MAX_AGE_S = 3600.0     # forget a job an hour after it was created
_MAX_JOBS = 200         # …and never keep more than this many around


def _prune_locked(now: float) -> None:
    stale = [k for k, j in _JOBS.items() if now - j["created"] > _MAX_AGE_S]
    for k in stale:
        _JOBS.pop(k, None)
    if len(_JOBS) > _MAX_JOBS:
        for k in sorted(_JOBS, key=lambda k: _JOBS[k]["created"])[:len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(k, None)


def create() -> str:
    now = time.time()
    jid = uuid.uuid4().hex
    with _LOCK:
        _prune_locked(now)
        _JOBS[jid] = {"status": "running", "result": None, "error": None, "created": now}
    return jid


def finish(jid: str, *, result: dict | None = None, error: str | None = None) -> None:
    with _LOCK:
        j = _JOBS.get(jid)
        if j is not None:
            j["status"] = "error" if error else "done"
            j["result"] = result
            j["error"] = error


def get(jid: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(jid)
        return dict(j) if j else None
