"""Co-Worker rail — FastAPI backend.

Routes:
  GET   /api/healthz      liveness + valid/skipped item counts
  GET   /api/inbox        list harvested items (newest first) + malformed-file report
  GET   /api/inbox/{id}   fetch a single item by its filename stem
  PATCH /api/inbox/{id}   set triage status (open | done | dismissed)
  GET   /api/doc/{path}   fetch a narrative markdown brief from an inbox subfolder

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
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from co_worker_app.config import settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("co-worker")

app = FastAPI(title="Co-Worker", version="0.2.0")

STATE_FILE = ".state.json"
ARCHIVE_DIR = "archive"
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
    """Atomic write — a torn state file would lose every triage decision at once."""
    d = _inbox()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, str(_state_path()))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


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


def _item_files() -> list[Path]:
    """Flat glob only — markdown lives in subfolders, archive/ is excluded by design.

    Dotfiles are excluded: `.state.json` matches `*.json` and is valid JSON, so without
    this it gets served as a phantom 46th item. Found by the test suite, not in prod.
    """
    d = _inbox()
    if not d.exists():
        return []
    files = [p for p in d.glob("*.json") if not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


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


@app.get("/api/inbox")
def inbox_list(
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """All items, newest-first by mtime, with malformed files reported not hidden."""
    d = _inbox()
    if not d.exists():
        return {"items": [], "skipped": [], "inbox_dir": str(d)}

    state = _load_state()
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for f in _item_files():
        try:
            items.append(_read_item(f, state))
        except Exception as exc:
            _log.warning("skipping %s: %s", f.name, exc)
            skipped.append({"file": f.name, "error": str(exc)})

    return {"items": items, "skipped": skipped, "inbox_dir": str(d)}


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
