"""Ensures the shared edu_media_core package is importable when the dashboard runs
without an install (mirrors the other apps' bootstrap)."""
import sys as _sys
from pathlib import Path as _Path

_core_src = str(_Path(__file__).resolve().parents[4] / "packages" / "edu-media-core" / "src")
if _core_src not in _sys.path:
    _sys.path.insert(0, _core_src)
