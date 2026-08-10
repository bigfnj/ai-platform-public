"""On-disk job library: ID-stamped per-job directories + self-contained zips.

Layout (outside the repo, default D:\\edu-suite-library, override EDU_LIBRARY_DIR):

    <workflow>/<YYYY-MM-DD>__<name-slug>__<shortid>/
        input/       uploaded originals
        work/        intermediates (prunable)
        output/      deliverables
        output.zip   self-contained bundle (the download)
        job.json     metadata
"""
from __future__ import annotations

import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path


def library_dir() -> Path:
    return Path(os.getenv("EDU_LIBRARY_DIR", "D:/edu-suite-library"))


def db_path() -> Path:
    """Where the job/event SQLite DB lives. Defaults alongside the library, but is
    overridable via EDU_DB_PATH so hosting can keep it OFF a Windows→Linux Docker
    bind mount: SQLite WAL's shared-memory index can't be mapped there by a second
    process, so the per-job worker subprocess crashes on open and every job fails.
    In the container this points at a named volume (real ext4)."""
    p = os.getenv("EDU_DB_PATH")
    return Path(p) if p else library_dir() / "library.db"


def new_job_id() -> str:
    return uuid.uuid4().hex[:8]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return (s or "job")[:40]


def bundle_basename(name: str, created_at: float | None = None) -> str:
    """Human-readable, on-disk-safe base name shared by the download zip AND the
    bundle's root HTML: '<Name> - MM-DD-YYYY · h.mm am' (workstation-local time, no
    seconds, 12-hour). Avoids every character Windows forbids in a filename
    (< > : " / \\ | ? *) — the name is stripped of them and the timestamp uses a
    middle dot + period instead of '|' and ':'."""
    nm = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", (name or "bundle").strip())
    nm = re.sub(r"\s+", " ", nm).strip() or "bundle"
    dt = datetime.fromtimestamp(created_at) if created_at else datetime.now()
    hour = dt.strftime("%I").lstrip("0") or "12"
    stamp = f"{dt.strftime('%m-%d-%Y')} · {hour}.{dt.strftime('%M')} {dt.strftime('%p').lower()}"
    return f"{nm} - {stamp}"


def make_job_dir(workflow: str, name: str, job_id: str) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    d = library_dir() / workflow / f"{date}__{slugify(name)}__{job_id}"
    for sub in ("input", "work", "output"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def write_job_meta(job_dir: Path, meta: dict) -> None:
    (job_dir / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8")


def bundle_zip(job_dir: Path) -> Path:
    """Zip the output/ tree into output.zip (self-contained; relative paths so it
    opens offline). Returns the zip path."""
    out_dir = job_dir / "output"
    zip_path = job_dir / "output.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out_dir).as_posix())
    return zip_path
