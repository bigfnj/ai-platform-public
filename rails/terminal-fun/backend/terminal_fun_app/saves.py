"""Per-user save/resume for the two games with real, resumable state: NetHack + Dungeon Crawl.

A game session runs in an ephemeral sandbox HOME that is wiped on exit, so we RESTORE a stored
save into the fresh session before launch and CAPTURE it back after the session ends. Saves are
owner-scoped under ``<data_dir>/saves/<owner>/<item_id>/``.

Two save shapes:

* **Crawl** saves HOME-relative (``$HOME/.crawl``). We seed/capture that whole subtree in the
  sandbox HOME.
* **NetHack** (Debian) saves into a SHARED system dir, every game's to write
  (``/var/games/nethack/save``, files named ``<uid><plname>.gz``), keyed by the player NAME. Every
  session runs as the same sandbox user, so we give each owner a **unique, reserved fun name** (via
  ``-u``); the save file then carries that name, and capture/restore matches only that owner's file.

No failure here ever breaks a game: restore() and capture() swallow it and return False. What a
failure must never do is cost the player the save they already have. So a copy is made beside its
destination and only a whole one takes its place (_replace_tree), and a session whose restore
failed is never captured (main.ws_terminal): what it ends with is a new game or a partial tree,
not the player's save.

Nothing here follows a link the game could have left. Every game runs as the one game uid, which
can write every session HOME and NetHack's shared dir, while this module runs as the uid that
owns /data: a link followed would read, write or chmod whatever it points at, with that uid. Where
the platform has descriptor-relative calls (Linux, so the image), that holds for a link swapped in
while the work is under way too: a Crawl tree is copied by directory descriptor (_copy_of), and
NetHack's shared dir is opened along its whole path without following a link and worked on
through that descriptor (_shared_dir).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import random
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
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
    """A filesystem-safe owner id. An identity-less caller shares the 'anon' bucket.

    The API is fail-closed now, so an un-gated request never reaches here at all — it is a 401
    before the route body runs. What still arrives without a name is a STANDALONE dev run,
    where identity yields None (and the websocket yields ""), and those need a stable bucket
    or every restart would start a fresh game. The legacy "?" literal is still folded in so any
    save written under the old ungated default keeps resolving to the same place.
    """
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


def _nh_files(directory: Path, pat: re.Pattern[str], *, links: bool = False) -> list[Path]:
    """The files in `directory` whose names `pat` matches; a link only when `links`, to remove it.

    The shared dir is every game's to write, so a name in it may be a link a player left there,
    and the backend reads as the uid that owns /data: copied or counted, a link would bring in
    whatever it points at. Removing one removes the link, not its target.
    """
    if not directory.is_dir():
        return []
    return [p for p in directory.iterdir()
            if pat.match(p.name) and (links if p.is_symlink() else p.is_file())]


# -- capture / restore / query --------------------------------------------------------------------

#: Windows refuses to rename a directory while anything holds a file in it open (PermissionError,
#: [WinError 5] Access is denied), and an on-access scanner opens a file moments after it is
#: written. On the dev box, where Defender, Digital Guardian, BeyondTrust and Tanium all run, a
#: rename of _replace_tree was refused in about one capture in thirty, each refusal over within
#: 9 ms, and each such capture failed. So on Windows each of its renames is asked again after
#: each of these waits, 0.63 s in all, and the refusal that outlasts them fails the swap as every
#: refusal did. Linux renames such a directory, and a PermissionError there is a real one (the
#: backend may not write the store), so there a rename is asked once (_RETRY_RENAME).
_RENAME_RETRIES: tuple[float, ...] = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
_RETRY_RENAME: bool = os.name == "nt"


def _rename(src: Path, dst: Path) -> None:
    """os.rename, asked again while Windows refuses it (_RENAME_RETRIES, where _RETRY_RENAME).
    A refused rename moved nothing, so asking again is safe, and a rename acts on the name,
    never on what a link there points at. Any other error, and the refusal that outlasts the
    bound, is raised."""
    for wait in _RENAME_RETRIES if _RETRY_RENAME else ():
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            time.sleep(wait)
    os.rename(src, dst)


def _replace_tree(dst: Path, fill: Callable[[Path], object], stage: Path | None = None) -> None:
    """Make `dst` the directory `fill` fills, or leave `dst` exactly as it was.

    `fill` writes into a fresh directory in `stage` (`dst`'s parent unless given; it must be on
    the same filesystem, so the swap is two renames), and only once it has returned does that
    directory take `dst`'s place. A failure partway (a full /tmp, a file that cannot be read)
    removes the partial copy and raises, `dst` untouched. A `dst` whose parent the game can
    write (a session HOME) needs a `stage` it cannot: there it could swap the fresh directory
    for one of its own, holding links where the copy's files go, and copy2 writes through a link.

    What it replaced copied straight over `dst`, an rmtree and then a copytree. A capture that
    failed partway left the player's stored save half-replaced. And copytree carries on past a
    file it cannot copy and raises only at the end, so a restore that failed left a partial tree
    in the session HOME, which the session's capture then stored in place of the whole save.

    Each rename is _rename's, asked again while Windows refuses it for a moment.

    Not covered: the process killed between the two renames leaves `dst` absent and its previous
    copy beside it as `<name>.old-*`, which nothing reads back.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix=f"{dst.name}.new-", dir=stage or dst.parent))
    old = new.with_name(new.name.replace(".new-", ".old-", 1))
    try:
        fill(new)
        if os.path.lexists(dst):
            _rename(dst, old)
        try:
            _rename(new, dst)
        except BaseException:
            if os.path.lexists(old):
                _rename(old, dst)
            raise
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _copy_file(src, dst) -> object:
    """copytree's copy function: a file that became a link after copytree listed it is copied as
    the link too, not followed."""
    return shutil.copy2(src, dst, follow_symlinks=False)


#: Whether this platform can walk a tree by directory descriptor: os.fwalk, and open, stat and
#: readlink relative to a directory's descriptor without following a link. Linux can, and so
#: can the image; Windows has none of it, and copies with copytree (_copy_of).
_BY_DESCRIPTOR: bool = (hasattr(os, "fwalk") and hasattr(os, "O_NOFOLLOW")
                        and hasattr(os, "O_DIRECTORY")
                        and {os.open, os.stat, os.readlink} <= os.supports_dir_fd
                        and os.stat in os.supports_follow_symlinks)


def _copy_file_at(dir_fd: int, name: str, dst: Path) -> None:
    """Copy the regular file `name`, in the directory open at `dir_fd`, to the new file `dst`,
    with its mode and times. Opened with O_NOFOLLOW: a link there now fails the copy, and so does
    anything that is not a regular file (O_NONBLOCK, so a fifo cannot hold the open)."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with open(fd, "rb") as inp:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise shutil.SpecialFileError(f"{dst.name} is not a regular file")
        # A path string, so the file object names it, as copy2's own copy does.
        with open(str(dst), "xb") as out:
            shutil.copyfileobj(inp, out)
    os.chmod(dst, stat.S_IMODE(st.st_mode))
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))


def _copy_tree_by_descriptor(src: Path, dst: Path) -> None:
    """What copytree(src, dst, symlinks=True, dirs_exist_ok=True) makes of the tree at `src`,
    into the existing directory `dst`, without opening anything in `src` through a link.

    `src` itself is opened with O_NOFOLLOW, so a `src` swapped for a link fails the copy rather
    than handing over an empty tree. os.fwalk opens each directory under it from its parent's
    descriptor and enters one only if it is still the directory it listed. Each entry is looked
    at relative to its directory's descriptor: a link is copied as a link, a directory is made
    and entered, a file is opened with O_NOFOLLOW (_copy_file_at), and anything else (a fifo, a
    device) fails the copy, as copy2 fails on one. Modes and times are set last, as copytree
    sets a directory's after its contents.
    """
    made: list[tuple[Path, os.stat_result]] = []
    top = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        made.append((dst, os.fstat(top)))
        for dirpath, dirnames, filenames, dir_fd in os.fwalk(".", dir_fd=top):
            here = dst / dirpath
            entered: set[str] = set()
            for name in [*dirnames, *filenames]:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    os.symlink(os.readlink(name, dir_fd=dir_fd), here / name)
                elif stat.S_ISDIR(st.st_mode):
                    os.mkdir(here / name)
                    made.append((here / name, st))
                    entered.add(name)
                elif stat.S_ISREG(st.st_mode):
                    _copy_file_at(dir_fd, name, here / name)
                else:
                    raise shutil.SpecialFileError(f"{name} is not a file, a directory or a link")
            # fwalk lists a link to a directory among the directories; it is copied above, as a
            # link, and never entered.
            dirnames[:] = [d for d in dirnames if d in entered]
    finally:
        os.close(top)
    for path, st in reversed(made):
        os.chmod(path, stat.S_IMODE(st.st_mode))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _copy_of(src: Path) -> Callable[[Path], object]:
    """A `fill` for _replace_tree: the tree at `src`, its links copied as links, never followed.

    A session HOME is the game's to write, and the backend reads it as the uid that owns /data.
    Followed, a link a player planted in `.crawl` (to another player's stored save, or anything
    else this uid can read) had its target copied into the player's own store, and the next
    restore handed it to the game. A link stored as a link points nowhere the game can reach.

    Where the platform walks by directory descriptor (_BY_DESCRIPTOR: Linux, so the image), the
    copy does (_copy_tree_by_descriptor), so a directory swapped for a link while the copy is
    partway through the tree is never followed either. copytree, which a Windows box still uses,
    opens each directory by name, and there such a directory is followed.
    """
    if _BY_DESCRIPTOR:
        return lambda new: _copy_tree_by_descriptor(src, new)
    return lambda new: shutil.copytree(src, new, symlinks=True, dirs_exist_ok=True,
                                       copy_function=_copy_file)


#: Whether a directory held by its descriptor can be reached by a path: /proc/self/fd/<fd> names
#: the directory the descriptor holds, whatever is moved onto the path it was opened by. Linux,
#: so the image; not Windows.
_PINNED_DIRS: bool = _BY_DESCRIPTOR and os.path.isdir("/proc/self/fd")


def _open_dir_nofollow(path: Path) -> int:
    """A descriptor on the directory `path`, opened from its root one component at a time, each
    below the root with O_NOFOLLOW: a link anywhere on the path fails the open (ELOOP), and
    nothing on it is followed."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = inner
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def _shared_dir(make: bool = False) -> Iterator[Path]:
    """NetHack's shared dir for the length of the `with`, as the path every read and write in it
    goes through.

    The game can write the dir's parent, and must: NetHack keeps its lock, level and bones files
    in its playground, /var/games/nethack (deploy/Dockerfile), and /var/games is the game's too.
    So it can swap the dir, or a parent of it, for a link, and the backend would then place this
    owner's save, read it back and clear this owner's files wherever the link points, as the uid
    that owns /data. Where directories can be pinned (_PINNED_DIRS), the dir is opened by
    descriptor along its whole path (_open_dir_nofollow), so a link on it fails the open, and the
    path handed out is /proc/self/fd/<that descriptor>: the directory that was opened, whatever
    is moved onto its path afterwards. Elsewhere (Windows) it is the dir's own path.

    `make` creates the dir first, as a restore always has (the image makes it; a dev run may not).
    """
    nh = _nh_dir()
    if make:
        nh.mkdir(parents=True, exist_ok=True)
    if not _PINNED_DIRS:
        yield nh
        return
    fd = _open_dir_nofollow(nh)
    try:
        yield Path(f"/proc/self/fd/{fd}")
    finally:
        os.close(fd)


def _place_file(src: Path, dest: Path) -> None:
    """Copy `src` to `dest` without ever opening `dest`.

    NetHack's shared dir is every game's to write, so `dest` may be a link a player left there,
    and a copy onto it writes through it: this owner's save, written by the uid that owns /data,
    wherever the link points. The bytes go into a new file of our own beside it instead, made
    exclusively and written through its descriptor, which is then renamed over `dest`: a rename
    replaces a link, never its target.
    """
    fd, tmp = tempfile.mkstemp(prefix=f"{dest.name}.", suffix=".part", dir=dest.parent)
    try:
        # The opener hands open() the descriptor mkstemp made, so the file object names `tmp`.
        with open(tmp, "wb", opener=lambda _path, _flags: fd) as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _remove(path: Path) -> None:
    """Best-effort removal of a file or a directory tree this module put somewhere."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _shared_mode(mode: int, is_dir: bool) -> int:
    """`mode` plus the group bits the game uid needs, and nothing else.

    A pure function so the CHOICE OF BITS can be asserted directly. Going through a real file
    cannot do that: os.chmod on Windows honours only the write bit, and a file created there
    already carries 0666 — so "adds group write" and "adds world write" both look true whether
    or not the code does either, and the test proves nothing on the machine it usually runs on.

    Group read+write on everything, group EXECUTE only on directories. +x on a directory is
    traversal, without which read/write on the files inside is worthless; +x on a save file is
    meaningless and would leave unexplained executables under /var/games for the next person
    auditing the image. World bits are never touched: keeping one player's save out of another
    process's reach is the entire point of the uid split, and re-widening it here would undo
    that in the one place that looks like housekeeping.
    """
    extra = stat.S_IRGRP | stat.S_IWGRP | (stat.S_IXGRP if is_dir else 0)
    return stat.S_IMODE(mode) | extra


def _share_with_game(path: Path) -> None:
    """Add group read/write to a save this function just handed to the game.

    The backend and the game are different uids in the deployed image (deploy/Dockerfile) and
    share exactly one group. Modes TRAVEL with a copied save — copytree and copy2 both
    preserve them — so a tree captured before the split, or under any umask that clears the
    group bit, arrives read-only for the process that now has to play it. NetHack would fail
    to lock its save and Crawl would refuse to write; both look like "resume is broken", not
    like a permissions bug, because the save is plainly there.

    Group-only, and never the world bits: another player's session must stay unreachable.

    Best-effort and silent, in keeping with the rest of this module. On an image without the
    split the game IS this uid and none of it is needed, and a chmod that fails must never be
    the reason a game will not start.

    A link is never chmodded: chmod follows it, and its target is wherever the player pointed it,
    with the backend's uid behind the call (a file only that uid can read, given to the group).
    """
    if path.is_symlink():
        return
    targets = [path, *path.rglob("*")] if path.is_dir() else [path]
    for p in targets:
        if p.is_symlink():
            continue
        try:
            os.chmod(p, _shared_mode(p.stat().st_mode, p.is_dir()))
        except OSError:
            pass


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


def restore(user: str | None, item_id: str, home: str) -> bool:
    """Seed a fresh session with this owner's stored save (if any) before the game launches.

    True when the session now holds the stored save, or there was none to hold. False on any
    failure, and then the session holds none of it: no `.crawl`, none of this restore's files in
    NetHack's shared dir. The caller must not capture such a session (main.ws_terminal).
    """
    placed: list[Path] = []
    held = contextlib.ExitStack()        # NetHack's shared dir, open until the end (_shared_dir)
    try:
        if item_id == "crawl":
            saved = _store(user, "crawl") / "dot_crawl"
            if saved.is_dir():
                dest = Path(home) / ".crawl"
                # Staged beside the HOME, not in it: the HOME is the game's to write, and its
                # parent (/tmp) is sticky, so a directory the backend makes there is its own.
                _replace_tree(dest, _copy_of(saved), stage=Path(home).parent)
                placed.append(dest)
                _share_with_game(dest)
        elif item_id == "nethack":
            name = assign_name(user)
            nh = held.enter_context(_shared_dir(make=True))
            for f in _nh_files(_store(user, "nethack"), _nh_save_re(name)):
                dest = nh / f.name
                placed.append(dest)          # removed again if a later file fails
                _place_file(f, dest)         # a partial copy never reaches dest
                _share_with_game(dest)
        return True
    except Exception:  # noqa: BLE001 -- any failure is a failed restore, and says so
        for p in placed:
            _remove(p)
        return False
    finally:
        held.close()


def capture(user: str | None, item_id: str, home: str) -> bool:
    """Persist this owner's save after the session ends (before the sandbox HOME is wiped).

    Only a whole copy replaces the stored save (_replace_tree), so a capture that fails partway
    returns False with the stored save exactly as it was. True when the save was stored, or the
    session left none to store.
    """
    held = contextlib.ExitStack()        # NetHack's shared dir, open until the end (_shared_dir)
    try:
        if item_id == "crawl":
            src = Path(home) / ".crawl"
            if src.is_symlink():
                return False         # not a tree the game wrote: following it stores its target
            if src.is_dir():
                _replace_tree(_store(user, "crawl") / "dot_crawl", _copy_of(src))
        elif item_id == "nethack":
            name = assign_name(user)
            nh = held.enter_context(_shared_dir())
            files = _nh_files(nh, _nh_save_re(name))            # never a link
            if files:
                def fill(new: Path) -> None:
                    for f in files:
                        _copy_file(f, new / f.name)
                # A fresh snapshot: what the store held (an old name's orphans too) goes in the swap.
                _replace_tree(_store(user, "nethack"), fill)
            # Clear ALL of this owner's files (save, stray locks, any link) out of the shared dir.
            for f in _nh_files(nh, _nh_any_re(name), links=True):
                f.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 -- any failure is a failed capture, and says so
        return False
    finally:
        held.close()


def abandon(user: str | None, item_id: str) -> None:
    """End a session whose restore failed, without capturing it: the stored save is not touched.

    Only what the session left outside its HOME goes, which is NetHack's files under this
    owner's name in the shared dir (a new game's save, written as the session hangs up). Left
    there, they would sit under the name the next restore writes. Crawl's are in the HOME, which
    the caller deletes.
    """
    if item_id == "nethack":
        with contextlib.suppress(Exception), _shared_dir() as nh:
            for f in _nh_files(nh, _nh_any_re(assign_name(user)), links=True):
                _remove(f)


def discard(user: str | None, item_id: str) -> None:
    """Delete this owner's stored save for a game (frontend 'reset' / 'start fresh').

    Clearing this owner's files from NetHack's shared dir is best-effort, as each unlink always
    was: a dir that cannot be opened safely (_shared_dir) is left alone, the store already gone.
    """
    shutil.rmtree(_store(user, item_id), ignore_errors=True)
    if item_id == "nethack":
        with contextlib.suppress(OSError), _shared_dir() as nh:
            for f in _nh_files(nh, _nh_any_re(assign_name(user)), links=True):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass
