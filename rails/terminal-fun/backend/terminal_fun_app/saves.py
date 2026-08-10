"""Per-user save/resume for the two games with real, resumable state: NetHack + Dungeon Crawl.

A game session runs in an ephemeral sandbox HOME that is wiped on exit, so we RESTORE a stored
save into the fresh session before launch and CAPTURE it back after the session ends. Saves are
owner-scoped under ``<data_dir>/saves/<owner>/<item_id>/``.

Two save shapes:

* **Crawl** saves HOME-relative (``$HOME/.crawl``). We seed/capture that whole subtree in the
  sandbox HOME.
* **NetHack** (Debian) saves into a SHARED, world-writable system dir
  (``/var/games/nethack/save``, files named ``<uid><plname>.gz``), keyed by the player NAME. Every
  session runs as the same sandbox user, so we give each owner a **unique, reserved fun name** (via
  ``-u``); the save file then carries that name, and capture/restore matches only that owner's file.

All operations are best-effort: any failure is swallowed so a save hiccup never breaks a game.
"""
from __future__ import annotations

import hashlib
import random
import re
import shutil
import threading
from pathlib import Path

from terminal_fun_app.config import settings

SAVEABLE: frozenset[str] = frozenset({"nethack", "crawl"})

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")

# A pool of fun fantasy adventurer names. Each owner is assigned one, reserved so two users never
# share a name (which would collide in NetHack's shared save dir). Deterministic per owner, so a
# returning player keeps the same name. Grow this list before it runs low relative to your users.
FANTASY_NAMES: tuple[str, ...] = (
    "Thrain", "Kaelen", "Vessa", "Brynn", "Fenwick", "Draven", "Sable", "Corwin",
    "Garrick", "Thorne", "Isolde", "Bramble", "Ember", "Ashryn", "Talon", "Wren",
    "Cael", "Doran", "Elowen", "Fable", "Grimm", "Halcyon", "Indra", "Kestrel",
    "Lorne", "Maeve", "Orin", "Quillon", "Rowan", "Sorrel", "Tamsin", "Ulric",
    "Vale", "Wraith", "Xara", "Yarrow", "Zephyr", "Alaric", "Brisa", "Caspian",
    "Eira", "Fingal", "Gwyn", "Halen", "Idris", "Jasper", "Kira", "Lucan",
    "Mabon", "Nova", "Orsen", "Piper", "Quorra", "Runa", "Silas", "Tovin",
    "Ursa", "Vane", "Willa", "Xander", "Yorick", "Zinnia", "Bastian", "Delphine",
)

_names_lock = threading.Lock()


def is_saveable(item_id: str) -> bool:
    return item_id in SAVEABLE


def _nh_dir() -> Path:
    """NetHack's shared system save dir (Debian compile-time default; env-overridable)."""
    return Path(settings.nethack_save_dir)


def _owner_key(user: str | None) -> str:
    """A filesystem-safe owner id. Un-gated/dev callers ('?'/empty) share the 'anon' bucket."""
    u = (user or "").strip()
    if not u or u == "?":
        return "anon"
    return _SAFE.sub("_", u)[:64]


def _store(user: str | None, item_id: str) -> Path:
    return Path(settings.data_dir) / "saves" / _owner_key(user) / item_id


# -- fun-name registry (NetHack player name, reserved unique per owner) ---------------------------

def _names_dir() -> Path:
    return Path(settings.data_dir) / "names"


def _fallback_name(user: str | None) -> str:
    """A guaranteed-unique but unglamorous name, used only if the fun-name pool is exhausted."""
    return "nh" + hashlib.sha1(_owner_key(user).encode()).hexdigest()[:8]


def assign_name(user: str | None) -> str:
    """This owner's NetHack character name — a fun fantasy name, reserved so it's unique across
    users (NetHack's shared save dir is keyed by name). Stable for a returning player."""
    key = _owner_key(user)
    d = _names_dir()
    with _names_lock:
        d.mkdir(parents=True, exist_ok=True)
        mine = d / key
        if mine.is_file():
            got = mine.read_text(encoding="utf-8").strip()
            if got:
                return got
        taken = set()
        for p in d.iterdir():
            if p.is_file():
                taken.add(p.read_text(encoding="utf-8").strip())
        # Deterministic per-owner shuffle, so each user gets a stable, distinctive name.
        pool = list(FANTASY_NAMES)
        random.Random(int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)).shuffle(pool)
        chosen = next((n for n in pool if n not in taken), _fallback_name(user))
        mine.write_text(chosen, encoding="utf-8")
        return chosen


def nethack_name(user: str | None) -> str:
    return assign_name(user)


def nethack_extra_argv(user: str | None) -> list[str]:
    """`-u <name>`: run NetHack under this owner's reserved fun name."""
    return ["-u", assign_name(user)]


def _nh_save_re(name: str) -> re.Pattern[str]:
    # A NetHack save file: <uid><name> with an optional letter extension (Debian gzips -> .gz).
    # Anchored so "Bryn" never matches "Brynn"; a letter ext (.gz) is the save vs numeric .0 locks.
    return re.compile(rf"^\d*{re.escape(name)}(\.[A-Za-z]\w*)?$")


def _nh_any_re(name: str) -> re.Pattern[str]:
    # Anything this owner's game wrote (save + any stray lock/level files) — for cleanup.
    return re.compile(rf"^\d*{re.escape(name)}(\..*)?$")


def _nh_files(directory: Path, pat: re.Pattern[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return [p for p in directory.iterdir() if p.is_file() and pat.match(p.name)]


# -- capture / restore / query --------------------------------------------------------------------

def _copytree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=False)


def has_save(user: str | None, item_id: str) -> bool:
    if item_id == "nethack":
        # Name-aware: a stored file must match this owner's CURRENT name (so an old name's orphan
        # doesn't show a false "resume").
        return bool(_nh_files(_store(user, "nethack"), _nh_save_re(assign_name(user))))
    store = _store(user, item_id)
    return store.is_dir() and any(store.iterdir())


def list_saves(user: str | None) -> list[str]:
    """Which saveable games this owner currently has a stored save for (drives the Resume UI)."""
    return [g for g in sorted(SAVEABLE) if has_save(user, g)]


def restore(user: str | None, item_id: str, home: str) -> None:
    """Seed a fresh session with this owner's stored save (if any) before the game launches."""
    try:
        if item_id == "crawl":
            store = _store(user, "crawl")
            if (store / "dot_crawl").is_dir():
                _copytree(store / "dot_crawl", Path(home) / ".crawl")
        elif item_id == "nethack":
            name = assign_name(user)
            nh = _nh_dir()
            nh.mkdir(parents=True, exist_ok=True)
            for f in _nh_files(_store(user, "nethack"), _nh_save_re(name)):
                shutil.copy2(f, nh / f.name)
    except OSError:
        pass


def capture(user: str | None, item_id: str, home: str) -> None:
    """Persist this owner's save after the session ends (before the sandbox HOME is wiped)."""
    try:
        if item_id == "crawl":
            src = Path(home) / ".crawl"
            if src.is_dir():
                _copytree(src, _store(user, "crawl") / "dot_crawl")
        elif item_id == "nethack":
            name = assign_name(user)
            nh = _nh_dir()
            store = _store(user, "nethack")
            files = _nh_files(nh, _nh_save_re(name))
            if files:
                store.mkdir(parents=True, exist_ok=True)
                for old in store.iterdir():          # fresh snapshot: drop prior (incl. old-name orphans)
                    if old.is_file():
                        old.unlink()
                for f in files:
                    shutil.copy2(f, store / f.name)
            # Clear ALL of this owner's files (save + any stray locks) out of the shared dir.
            for f in _nh_files(nh, _nh_any_re(name)):
                f.unlink(missing_ok=True)
    except OSError:
        pass


def discard(user: str | None, item_id: str) -> None:
    """Delete this owner's stored save for a game (frontend 'reset' / 'start fresh')."""
    shutil.rmtree(_store(user, item_id), ignore_errors=True)
    if item_id == "nethack":
        for f in _nh_files(_nh_dir(), _nh_any_re(assign_name(user))):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
