"""Co-Worker rail — FastAPI backend.

Routes:
  GET   /api/healthz      liveness + valid/skipped item counts
  GET   /api/inbox        list harvested items (newest first) + malformed-file report
                          ?period=2026W33 / ?source=teams narrow the set
  GET   /api/archive      pruned items, same shape — where trend history lives
  GET   /api/inbox/{id}   fetch a single item by its filename stem
  PATCH /api/inbox/{id}   set triage status (open | done | dismissed)
  GET   /api/doc/{path}   fetch a narrative markdown brief from an inbox subfolder
  GET   /api/brief        the synthesized executive brief (curated, <=10 action items)
                          ?source=email returns that lane's brief instead of the merge
  POST  /api/brief/refresh  queue a re-synthesis (returns immediately)
                          ?source=email refreshes one lane; omit to rebuild all + merge
  GET   /api/brief/status   whether a synthesis run is in flight

The brief is the LANDING VIEW: 147 raw items are not actionable, so a synthesis pass
(local model via the broker — @co-worker-synthesis role, currently gemma3:4b) reduces
them to what actually needs attention this week. Re-synthesize re-summarizes existing
inbox items; it does NOT refresh the harvest. Only the co-work harvest loops (which hold
M365 credentials) can update the underlying inbox/*.json files.

Synthesis runs ONE PASS PER SOURCE, merged into brief.json. A single combined pass over
every unresolved item does not fit the local model's usable context: at ~200 items it
could read about half and the rest were dropped unseen. Per lane the payload is small
enough that every item is read. Each lane also keeps its own brief.<source>.json.
The raw card grid remains available as a second tab. Synthesis writes inbox/brief.json;
this backend only ever reads that file — no model call happens on the read path.

The inbox drop-zone is a directory of JSON files written by an external harvest process
(Claude co-work scheduled tasks reading email + calendar + Teams). Each file is one
structured item; the contract is rails/co-worker/SCHEMA.md.

This backend stays schema-agnostic — unknown fields are forwarded as-is. It does NOT
validate items; that is tools/validate_inbox.py's job. But malformed files are counted
and reported rather than silently swallowed, because a silently-skipped item is worse
than a visible error.

Triage state lives in a sidecar (inbox/.state.json) so harvest files are never mutated:
a rerun can overwrite an item without destroying its triage state. Item ids are
deterministic (see SCHEMA.md), so state re-attaches to the right item across reruns.

The gateway authenticates requests and injects x-platform-user.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from co_worker_app import modelstate
from co_worker_app.atomicio import write_json
from co_worker_app.config import settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("co-worker")

_last_auto_attempt: float = 0.0  # epoch seconds; guards auto-synthesis rate

app = FastAPI(title="Co-Worker", version="0.2.0")

STATE_FILE = ".state.json"
ARCHIVE_DIR = "archive"
BRIEF_FILE = "brief.json"
VALID_STATUS = ("open", "done", "dismissed")
DOC_SUFFIXES = (".md", ".markdown")


def _inbox() -> Path:
    return Path(settings.inbox_dir)


# --- triage state sidecar ---------------------------------------------------


def _state_path() -> Path:
    return _inbox() / STATE_FILE


def _load_state() -> dict[str, str]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(v) in VALID_STATUS}
    except Exception as exc:
        _log.warning("unreadable %s: %s", STATE_FILE, exc)
    return {}


def _save_state(state: dict[str, str]) -> None:
    """Persist triage state. See atomicio: the inbox is often a 9p bind mount, where
    rename-over-existing fails, so the writer degrades rather than erroring out."""
    write_json(_state_path(), state, sort_keys=True)


# --- item reading ----------------------------------------------------------


def _read_item(path: Path, state: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("item must be a JSON object")
    # Inject metadata the frontend can always rely on.
    data.setdefault("_id", path.stem)
    data.setdefault("_file", path.name)
    data.setdefault("_mtime", path.stat().st_mtime)
    data["_status"] = (state or {}).get(path.stem, "open")
    return data


def _item_files(d: Path | None = None) -> list[Path]:
    """Flat glob only — markdown lives in subfolders, archive/ is excluded by design.

    Dotfiles are excluded: `.state.json` matches `*.json` and is valid JSON, so without
    this it gets served as a phantom 46th item. Found by the test suite, not in prod.
    Brief files are excluded for the same reason but are NOT dotfiles — the synthesis
    output lives beside the items and would otherwise render as extra cards. That is
    `brief.json` plus one `brief.<source>.json` per lane, hence the shared predicate.

    Pass a directory to read a different set (e.g. archive/); defaults to the live inbox.
    """
    from co_worker_app.synthesize import is_brief_file

    d = d if d is not None else _inbox()
    if not d.exists():
        return []
    files = [
        p for p in d.glob("*.json")
        if not p.name.startswith(".") and not is_brief_file(p.name)
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _collect(
    d: Path, period: str | None = None, source: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read every item in `d`, returning (items, skipped). Malformed files are reported,
    never silently dropped — a card that vanishes without explanation is the failure mode
    this rail most needs to avoid."""
    state = _load_state()
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for f in _item_files(d):
        try:
            item = _read_item(f, state)
        except Exception as exc:
            _log.warning("skipping %s: %s", f.name, exc)
            skipped.append({"file": f.name, "error": str(exc)})
            continue
        if period is not None and str(item.get("period") or "") != period:
            continue
        if source is not None and str(item.get("source") or "") != source:
            continue
        items.append(item)
    return items, skipped


# --- staleness helpers -------------------------------------------------------


def _inbox_signature() -> tuple[int, float]:
    """(item_count, newest_item_mtime) — identity of the current synthesis input set.

    item_count catches additions and deletions; newest_mtime catches rewrites.
    Stored inside brief.json so the signal survives a restart with no extra state file.
    """
    files = _item_files()
    return len(files), max((p.stat().st_mtime for p in files), default=0.0)


def _brief_is_stale(brief: dict[str, Any]) -> tuple[bool, str]:
    """Has the source set changed since this brief was written?

    Compares against the signature the synthesis pass recorded, NOT the brief
    file's own mtime — that cannot detect deletions or mtime-preserving writes.
    Returns (stale, reason). reason is empty when not stale.
    """
    count, newest = _inbox_signature()
    sig = brief.get("_source_signature")

    if not isinstance(sig, list) or len(sig) != 2:
        return True, "brief predates staleness tracking"

    old_count, old_newest = sig
    if count != old_count:
        return True, f"item count changed ({int(old_count)} -> {count})"
    epsilon = settings.auto_synthesize_mtime_epsilon_s
    if newest > float(old_newest) + epsilon:
        return True, "an item was rewritten after the last synthesis"
    return False, ""


# --- routes ----------------------------------------------------------------


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    """Liveness plus a real integrity signal: how many items failed to parse."""
    valid = 0
    skipped: list[str] = []
    for f in _item_files():
        try:
            json.loads(f.read_text(encoding="utf-8"))
            valid += 1
        except Exception:
            skipped.append(f.name)
    archive = _inbox() / ARCHIVE_DIR
    return {
        "ok": True,
        "app": settings.app_name,
        "inbox_items": valid,
        "skipped": len(skipped),
        "skipped_files": skipped,
        "archived_items": len(list(archive.glob("*.json"))) if archive.exists() else 0,
    }


def _model_slots() -> list[tuple[str, str, str]]:
    """The model slots this rail shows as header chips. Only one: synthesis is a single chat
    pass with no embedder — the brief is built from whole items, not retrieved chunks.

    Reads the same setting synthesis actually sends to the broker, so the chip cannot drift
    from reality the way a hardcoded role would."""
    return [("llm", "Synthesis", settings.synthesis_model)]


@app.get("/api/models")
def models_status() -> dict[str, Any]:
    """Four-state model status for the header chips (missing/cold/warming/loaded), plus the
    item count shown alongside them. Never raises — the header must render with the GPU down."""
    valid = 0
    for f in _item_files():
        try:
            json.loads(f.read_text(encoding="utf-8"))
            valid += 1
        except Exception:  # noqa: BLE001 - malformed files are reported by healthz, not here
            pass
    out = modelstate.resolve(_model_slots())
    out["items"] = valid
    return out


@app.get("/api/inbox")
def inbox_list(
    period: str | None = None,
    source: str | None = None,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """All items, newest-first by mtime, with malformed files reported not hidden.

    `?period=2026W33` narrows to one grouping period. Computing a trend delta otherwise
    means pulling every item and grouping client-side, which gets worse as archive grows.
    `periods` always lists what's available so a caller can drive a picker without a
    second request.
    """
    d = _inbox()
    if not d.exists():
        return {"items": [], "skipped": [], "periods": [], "inbox_dir": str(d)}

    items, skipped = _collect(d, period, source)
    all_periods = sorted({str(i.get("period")) for i in _collect(d)[0] if i.get("period")}, reverse=True)
    return {
        "items": items,
        "skipped": skipped,
        "period": period,
        "source": source,
        "periods": all_periods,
        "inbox_dir": str(d),
    }


@app.get("/api/archive")
def archive_list(
    period: str | None = None,
    source: str | None = None,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Pruned items, same shape as /api/inbox. This is where trend history lives.

    The pruner moves items out of the dashboard window into archive/, so without this
    route that history sits on disk unreachable — you can see this week but never
    compare it to last.
    """
    d = _inbox() / ARCHIVE_DIR
    if not d.exists():
        return {"items": [], "skipped": [], "periods": [], "archive_dir": str(d)}

    items, skipped = _collect(d, period, source)
    all_periods = sorted({str(i.get("period")) for i in _collect(d)[0] if i.get("period")}, reverse=True)
    return {
        "items": items,
        "skipped": skipped,
        "period": period,
        "source": source,
        "periods": all_periods,
        "archive_dir": str(d),
    }


@app.get("/api/brief")
def brief_get(
    source: str | None = Query(default=None, description="Read one lane's brief (email/calendar/teams); omit for the merged brief."),
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """The synthesized executive brief — the landing view.

    Pure file read. `stale` is advisory: the frontend colours the age signal and
    offers a refresh, but a stale brief is still far more useful than 147 cards.

    `?source=email` returns that lane's brief. The merged brief.json is built from all
    of them, so a lane brief is never newer than the merge but is always narrower —
    useful when you want everything in one source rather than the top 10 overall.
    """
    from co_worker_app.synthesize import brief_filename

    if source is not None and ("/" in source or "\\" in source or source.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid source")

    p = _inbox() / brief_filename(source)
    if not p.exists():
        return {
            "exists": False,
            "stale": True,
            "source": source,
            "attention": [],
            "message": "No brief yet — run a synthesis pass to generate one.",
        }
    try:
        brief = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(brief, dict):
            raise ValueError("brief must be a JSON object")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"malformed brief.json: {exc}") from exc

    brief["exists"] = True
    brief["_mtime"] = p.stat().st_mtime
    age_h = (time.time() - p.stat().st_mtime) / 3600
    brief["age_hours"] = round(age_h, 1)
    brief["stale"] = age_h > 12

    stale, reason = _brief_is_stale(brief)
    brief["stale_source"] = stale and settings.auto_synthesize
    brief["stale_reason"] = reason or None

    return brief


@app.post("/api/brief/refresh")
def brief_refresh(
    x_platform_user: str = Header(default="?"),
    auto: bool = Query(default=False, description="True when triggered automatically by staleness detection; applies the cooldown floor."),
    source: str | None = Query(default=None, description="Refresh only this lane. Omit to run every lane and rebuild the merge."),
) -> dict[str, Any]:
    """Queue a synthesis run. Returns immediately — poll /api/brief/status.

    Refuses rather than queues when a run is already in flight: two concurrent
    passes would race on brief.json and waste a model call.

    With no `source`, runs one pass per lane and merges them into brief.json. That is
    several sequential model calls, so it takes proportionally longer than the old
    single pass — the tradeoff for every item actually being read.

    ?auto=1 is set by the frontend's staleness auto-trigger. Manual clicks omit it
    and always bypass the cooldown, so a human is never told to wait.
    """
    global _last_auto_attempt
    from co_worker_app.synthesize import get_status, synthesize_background

    if source is not None and ("/" in source or "\\" in source or source.startswith(".")):
        raise HTTPException(status_code=400, detail="invalid source")

    if auto:
        now = time.time()
        if now - _last_auto_attempt < settings.auto_synthesize_min_interval_s:
            return {"started": False, "reason": "cooldown", **get_status()}
        _last_auto_attempt = now

    started = synthesize_background(_inbox(), source=source)
    if not started:
        raise HTTPException(status_code=409, detail="a synthesis run is already in progress")
    return {"started": True, "source": source, **get_status()}


@app.get("/api/brief/status")
def brief_status(x_platform_user: str = Header(default="?")) -> dict[str, Any]:
    """Whether a synthesis run is in flight, plus the last run's outcome."""
    from co_worker_app.synthesize import get_status

    return get_status()


@app.get("/api/inbox/{item_id}")
def inbox_get(
    item_id: str,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Fetch a single inbox item by its filename stem (no extension)."""
    path = _safe_item_path(item_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="item not found")
    try:
        return _read_item(path, _load_state())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"malformed item: {exc}") from exc


class StatusPatch(BaseModel):
    status: Literal["open", "done", "dismissed"]


@app.patch("/api/inbox/{item_id}")
def inbox_patch(
    item_id: str,
    patch: StatusPatch,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Set triage status. Writes the sidecar, never the harvest file itself."""
    path = _safe_item_path(item_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="item not found")

    state = _load_state()
    if patch.status == "open":
        state.pop(item_id, None)  # absent == open; keeps the sidecar small
    else:
        state[item_id] = patch.status
    try:
        _save_state(state)
    except OSError as exc:
        # The inbox is a bind mount from the Windows host; if it's mounted read-only
        # this is the failure the user needs to see, not a generic 500.
        raise HTTPException(
            status_code=503,
            detail=f"cannot write triage state to {_state_path()}: {exc}",
        ) from exc

    return {"_id": item_id, "_status": patch.status}


@app.get("/api/doc/{doc_path:path}")
def doc_get(
    doc_path: str,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Serve a narrative markdown brief from an inbox subfolder.

    Items carry a `doc` field like "calendar/2026-08-17-week.md"; this makes that
    drill-through real. Returns content as JSON so the frontend can render it inline
    rather than triggering a file download.
    """
    if not doc_path.endswith(DOC_SUFFIXES):
        raise HTTPException(status_code=400, detail="only markdown documents are served")

    base = _inbox().resolve()
    target = (base / doc_path).resolve()
    # Containment check: defeats ../ traversal, absolute paths, and symlink escapes.
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="path outside inbox")
    if target.name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="document not found")

    return {
        "path": doc_path,
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "mtime": target.stat().st_mtime,
    }


def _safe_item_path(item_id: str) -> Path:
    """Reject traversal and hidden files, then resolve inside the inbox."""
    if "/" in item_id or "\\" in item_id or item_id.startswith("."):
        raise HTTPException(status_code=400, detail="invalid item id")
    base = _inbox().resolve()
    p = (base / f"{item_id}.json").resolve()
    if not p.is_relative_to(base) or p.parent != base:
        raise HTTPException(status_code=400, detail="invalid item id")
    return p
