"""Owner-scoped design storage: one JSON file per design.

No database. A design document is a few kilobytes of JSON, there is no relational query this
rail wants to make, and a rail that needs a database needs a volume, a migration story and a
backup story. Files on a volume have all three for free.

Ownership is enforced on the READ path by construction rather than by a filter: a design
lives under its owner's directory, so a caller who asks for someone else's id gets a path
that does not exist under their own root. There is no query that could accidentally omit a
`WHERE owner = ?`.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid as uuidlib
from pathlib import Path

from watch_builder.config import settings

# A username reaches the filesystem here, so it is encoded rather than trusted. The gateway
# already authenticated it, but "authenticated" is not "safe as a path component": a name
# containing a separator or ".." would escape the owner's directory, and the encoding removes
# the question rather than answering it.
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _owner_dir(owner: str | None) -> Path:
    # owner=None is the standalone/unscoped case. It gets its own directory rather than the
    # root, so a standalone dev session cannot see or overwrite a real user's designs when
    # the same volume is later mounted by a gateway-fronted container.
    key = "_standalone" if not owner else _SAFE.sub("_", owner)[:64]
    return Path(settings.data_dir) / key


def _path(owner: str | None, design_id: str) -> Path:
    return _owner_dir(owner) / f"{_SAFE.sub('_', design_id)}.json"


def new_id() -> str:
    return uuidlib.uuid4().hex[:16]


def save(owner: str | None, design_id: str, doc: dict, *, name: str) -> dict:
    """Write a design, atomically.

    Atomic because the frontend autosaves while someone is dragging an element, and a partial
    write at that moment would be a corrupt document rather than a stale one. os.replace is
    atomic on both Windows and Linux, which matters because this rail's tests run on the
    Windows host and its container does not.
    """
    directory = _owner_dir(owner)
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(owner, design_id)

    record = {
        "id": design_id,
        "name": name,
        "updated": time.time(),
        "design": doc,
    }
    if path.exists():
        try:
            record["created"] = json.loads(path.read_text(encoding="utf-8")).get("created")
        except (OSError, ValueError):
            pass
    record.setdefault("created", record["updated"])

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return record


def load(owner: str | None, design_id: str) -> dict | None:
    path = _path(owner, design_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def delete(owner: str | None, design_id: str) -> bool:
    path = _path(owner, design_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def listing(owner: str | None) -> list[dict]:
    """Every design this owner has, newest first. Summary fields only."""
    directory = _owner_dir(owner)
    if not directory.is_dir():
        return []
    rows: list[dict] = []
    for path in directory.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt file is skipped rather than failing the list. One bad document must
            # not make the rail unusable, and the file is still on disk to be inspected.
            continue
        design = rec.get("design") or {}
        rows.append({
            "id": rec.get("id") or path.stem,
            "name": rec.get("name") or "(untitled)",
            "updated": rec.get("updated") or 0,
            "created": rec.get("created") or 0,
            "platforms": design.get("platforms") or [],
            "elements": len(design.get("elements") or []),
        })
    return sorted(rows, key=lambda r: -float(r["updated"]))
