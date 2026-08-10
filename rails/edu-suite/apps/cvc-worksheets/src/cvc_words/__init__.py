"""Ensures the shared edu_media_core package is importable when this app runs
via `python cli.py` (which does not install packages). Harmless when
edu-media-core is already installed in the environment (e.g. via `uv sync`)."""
import sys as _sys
from pathlib import Path as _Path

_core_src = str(_Path(__file__).resolve().parents[4] / "packages" / "edu-media-core" / "src")
if _core_src not in _sys.path:
    _sys.path.insert(0, _core_src)
