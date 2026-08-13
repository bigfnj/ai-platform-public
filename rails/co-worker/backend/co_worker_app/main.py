"""Co-Worker rail — FastAPI backend.

Routes:
  GET  /api/healthz      liveness
  GET  /api/inbox        list harvested items from the inbox drop-zone (newest first)
  GET  /api/inbox/{id}   fetch a single item by its filename stem

The inbox drop-zone is a directory of JSON files written by an external harvest
process (a Claude co-work session reading email + calendar). Each file is one
structured item; the schema is defined by the harvest process and visualized by
the frontend. This backend is schema-agnostic — it reads and forwards whatever
is in the directory.

The gateway authenticates requests and injects x-platform-user.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from co_worker_app.config import settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("co-worker")

app = FastAPI(title="Co-Worker", version="0.1.0")


def _inbox() -> Path:
    return Path(settings.inbox_dir)


def _read_item(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("item must be a JSON object")
    # Inject metadata the frontend can always rely on.
    data.setdefault("_id", path.stem)
    data.setdefault("_file", path.name)
    data.setdefault("_mtime", path.stat().st_mtime)
    return data


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    inbox = _inbox()
    count = len(list(inbox.glob("*.json"))) if inbox.exists() else 0
    return {"ok": True, "app": settings.app_name, "inbox_items": count}


@app.get("/api/inbox")
def inbox_list(
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Return all inbox items, sorted newest-first by file mtime."""
    d = _inbox()
    if not d.exists():
        return {"items": [], "inbox_dir": str(d)}

    items: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            items.append(_read_item(f))
        except Exception as exc:
            _log.warning("skipping %s: %s", f.name, exc)

    return {"items": items, "inbox_dir": str(d)}


@app.get("/api/inbox/{item_id}")
def inbox_get(
    item_id: str,
    x_platform_user: str = Header(default="?"),
) -> dict[str, Any]:
    """Fetch a single inbox item by its filename stem (no extension)."""
    # Sanitize: no path traversal
    if "/" in item_id or "\\" in item_id or item_id.startswith("."):
        raise HTTPException(status_code=400, detail="invalid item id")
    path = _inbox() / f"{item_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="item not found")
    try:
        return _read_item(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"malformed item: {exc}") from exc
