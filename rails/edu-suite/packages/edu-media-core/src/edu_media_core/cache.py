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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear(path: str | Path | None = None) -> None:
    Path(path or translations_path()).unlink(missing_ok=True)
