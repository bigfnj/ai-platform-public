"""Platform theming: a palette (color family) + mode (light/dark/system), set as a
platform-wide default by an admin and optionally overridden per user.

Effective theme = the user's override where present, else the platform default. The
frontend applies it as ``data-palette`` + ``data-theme`` on <html>; every token-driven
surface recolors from there.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from platform_gateway_app.models import Setting, User, UserTheme

PALETTES = [
    "indigo", "slate", "graphite", "evergreen", "ocean", "ember", "plum", "rose", "gold",
    # theme-inspired palettes (VS Code built-ins + famous community themes)
    "monokai", "solarized", "dracula", "nord", "tokyo", "onedark", "nightowl",
    "gruvbox", "catppuccin", "github", "abyss", "kimbie",
]
MODES = ["light", "dark", "system"]
DEFAULT_PALETTE = "indigo"
DEFAULT_MODE = "system"


def _get(db: OrmSession, key: str, fallback: str) -> str:
    row = db.get(Setting, key)
    return row.value if row else fallback


def _set(db: OrmSession, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def platform_default(db: OrmSession) -> dict:
    return {"palette": _get(db, "default_palette", DEFAULT_PALETTE),
            "mode": _get(db, "default_mode", DEFAULT_MODE)}


def set_platform_default(db: OrmSession, palette: str, mode: str) -> None:
    if palette not in PALETTES:
        raise ValueError(f"unknown palette: {palette}")
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    _set(db, "default_palette", palette)
    _set(db, "default_mode", mode)
    db.commit()


def user_override(db: OrmSession, user: User) -> dict | None:
    row = db.get(UserTheme, user.id)
    if not row or (row.palette is None and row.mode is None):
        return None
    return {"palette": row.palette, "mode": row.mode}


def set_user_override(db: OrmSession, user: User,
                      palette: str | None, mode: str | None, clear: bool = False) -> None:
    """Partial update: pass only the field(s) to change. ``clear`` (or both None with no
    existing row) removes the override entirely so the user follows the platform default."""
    row = db.get(UserTheme, user.id)
    if clear:
        if row:
            db.delete(row)
        db.commit()
        return
    if palette is not None and palette not in PALETTES:
        raise ValueError(f"unknown palette: {palette}")
    if mode is not None and mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if row is None:
        db.add(UserTheme(user_id=user.id, palette=palette, mode=mode))
    else:
        if palette is not None:
            row.palette = palette
        if mode is not None:
            row.mode = mode
    db.commit()


def effective(db: OrmSession, user: User) -> dict:
    default = platform_default(db)
    override = user_override(db, user)
    o = override or {}
    return {
        "palette": o.get("palette") or default["palette"],
        "mode": o.get("mode") or default["mode"],
        "default": default,
        "override": override,
        "palettes": PALETTES,
        "modes": MODES,
    }
