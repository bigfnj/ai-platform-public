"""Suite-level, content-addressed translation cache shared by every app.

One JSON store keyed by ``(model, system-prompt, content)`` so an identical request is
never re-run — across workflows, apps, and process restarts. The location is
env-overridable via ``EDU_CACHE_DIR``; in the container it points at a persistent volume
so the cache survives rebuilds. This module owns the mechanics;
``translate.translate_cached`` and ``broker_media.translate_cached`` default to it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_SEP = "\x1f"  # unit separator — won't appear in prompts/content


def content_hash(text: str) -> str:
    """Stable sha256 hex of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_dir() -> Path:
    """The shared cache directory. Override with EDU_CACHE_DIR (a volume in the container)."""
    return Path(os.getenv("EDU_CACHE_DIR") or (Path.home() / ".edu-suite-cache"))


def translations_path() -> Path:
    return cache_dir() / "translations.json"


def make_key(model: str, system_prompt: str, content: str) -> str:
    """Content-addressed key: same (model, system prompt, input) -> same key, so an
    identical request shares its cached result no matter which app made it."""
    return content_hash(_SEP.join((model or "", system_prompt or "", content or "")))


def load(path: str | Path) -> dict:
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(path: str | Path, data: dict) -> None:
    """Write the store atomically: temp file in the same directory, then replace.

    The whole cache is rewritten on every miss, so a crash, a container kill or a full disk
    mid-write would otherwise leave a truncated file behind — and ``load`` treats a
    JSONDecodeError as NO CACHE AT ALL, silently discarding every translation in it (real
    GPU time). Same temp-then-replace shape as the broker's roles/disabled overlays.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The temp name carries the pid. A FIXED ".tmp" is atomic within one process and races
    # across two: the web process and the per-job run_job.py subprocess both hold a cache
    # against the same /cache/translations.json, so two concurrent savers opened the same
    # temp in mode "w" and one replace()d what the other was still writing. Rare, because the
    # queue serializes jobs — but load() reads a torn file as NO CACHE AT ALL, so the failure
    # is the silent loss of every cached translation, which is real GPU time.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        # A failed write must not leave its scratch file behind: the name is pid-scoped, so
        # nothing would ever reclaim it.
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def clear(path: str | Path | None = None) -> None:
    Path(path or translations_path()).unlink(missing_ok=True)
