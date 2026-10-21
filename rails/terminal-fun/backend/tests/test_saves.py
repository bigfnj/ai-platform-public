"""Per-user save/resume store: Crawl (HOME-relative) + NetHack (shared dir, fun-name-namespaced)."""
from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest

from terminal_fun_app import saves
from terminal_fun_app.config import settings


def tree(root: Path) -> dict[str, bytes | None]:
    """Everything under `root`: each path, POSIX-spelled, to its bytes (a directory to None)."""
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    nh = tmp_path / "nethack_save"
    nh.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", str(data))
    monkeypatch.setattr(settings, "nethack_save_dir", str(nh))
    return {"data": data, "nh": nh, "tmp": tmp_path}


def test_assign_name_fun_stable_and_unique(dirs):
    a = saves.assign_name("alice")
    assert a in saves.FANTASY_NAMES
    assert a == saves.assign_name("alice")                 # stable for a returning player
    b = saves.assign_name("bob")
    assert b in saves.FANTASY_NAMES and b != a             # reserved unique across users
    assert saves.nethack_extra_argv("alice") == ["-u", a]


def test_crawl_capture_then_restore(dirs):
    home = dirs["tmp"] / "home1"
    (home / ".crawl" / "saves").mkdir(parents=True)
    (home / ".crawl" / "saves" / "game.cs").write_text("SAVEDATA")
    assert not saves.has_save("alice", "crawl")
    saves.capture("alice", "crawl", str(home))
    assert saves.has_save("alice", "crawl")
    home2 = dirs["tmp"] / "home2"; home2.mkdir()
    saves.restore("alice", "crawl", str(home2))
    assert (home2 / ".crawl" / "saves" / "game.cs").read_text() == "SAVEDATA"


def test_nethack_namespaced_capture_and_restore(dirs):
    nh = dirs["nh"]
    name, other = saves.assign_name("alice"), saves.assign_name("bob")
    (nh / f"10001{name}.gz").write_text("NHSAVE")          # alice's save (real Debian format)
    (nh / f"10001{name}.0").write_text("lock")             # a stray lock file
    (nh / f"10001{other}.gz").write_text("someone-else")   # a different user's save
    home = dirs["tmp"] / "h"; home.mkdir()

    saves.capture("alice", "nethack", str(home))
    assert saves.has_save("alice", "nethack")
    assert not (nh / f"10001{name}.gz").exists()           # captured out of the shared dir
    assert not (nh / f"10001{name}.0").exists()            # lock cleared too
    assert (nh / f"10001{other}.gz").exists()              # the other user's save is untouched

    saves.restore("alice", "nethack", str(home))
    assert (nh / f"10001{name}.gz").read_text() == "NHSAVE"


def test_nethack_name_is_anchored_not_substring(dirs):
    name = saves.assign_name("alice")
    nh = dirs["nh"]
    (nh / f"10001{name}extra.gz").write_text("NOT ALICE")  # name is only a prefix here
    home = dirs["tmp"] / "h2"; home.mkdir()
    saves.capture("alice", "nethack", str(home))
    assert not saves.has_save("alice", "nethack")          # nothing matched
    assert (nh / f"10001{name}extra.gz").exists()          # left untouched


def test_list_and_discard(dirs):
    home = dirs["tmp"] / "hh"
    (home / ".crawl").mkdir(parents=True)
    (home / ".crawl" / "x").write_text("y")
    saves.capture("alice", "crawl", str(home))
    assert saves.list_saves("alice") == ["crawl"]
    saves.discard("alice", "crawl")
    assert not saves.has_save("alice", "crawl") and saves.list_saves("alice") == []


# ==============================================================================================
# A copy that fails partway never costs the player the save they have
# ==============================================================================================
#
# The disk fills from the second file copied (conftest's full_disk), so each failure is partway
# through, never before anything was written. A full /tmp (terminal-fun's tmpfs is sized) is
# what made the restore's reachable.

def _played(home: Path, tag: bytes) -> Path:
    """A Crawl HOME as a game leaves it: a save, a morgue file and an options file."""
    d = home / ".crawl"
    (d / "saves").mkdir(parents=True)
    (d / "morgue").mkdir()
    (d / "saves" / "Thrain.cs").write_bytes(b"SAVE " + tag + bytes(range(256)))
    (d / "morgue" / "morgue-Thrain.txt").write_bytes(b"MORGUE " + tag)
    (d / "init.txt").write_bytes(b"OPTIONS " + tag)
    return home


def test_a_crawl_restore_that_fails_midway_says_so_and_leaves_no_tree(dirs, full_disk):
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "played", b"one")))
    store = dirs["data"] / "saves" / "alice" / "crawl"
    stored = tree(store)
    session = dirs["tmp"] / "session"
    session.mkdir()
    hits = full_disk(lambda p: not p.is_relative_to(dirs["data"]))     # /tmp is full
    assert saves.restore("alice", "crawl", str(session)) is False
    assert len(hits) >= 2, "the disk did not fill partway through the copy"
    assert sorted(p.name for p in session.iterdir()) == []
    assert [p.name for p in dirs["tmp"].iterdir() if p.name.startswith(".crawl.")] == []
    assert tree(store) == stored


def test_a_crawl_capture_that_fails_midway_keeps_the_stored_save_whole(dirs, full_disk):
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
    store = dirs["data"] / "saves" / "alice" / "crawl"
    stored = tree(store)
    second = _played(dirs["tmp"] / "second", b"two")
    hits = full_disk(lambda p: p.is_relative_to(dirs["data"]))
    ok = saves.capture("alice", "crawl", str(second))
    assert len(hits) >= 2, "the disk did not fill partway through the copy"
    assert tree(store) == stored, "the stored save was half-replaced"
    assert ok is False


def test_a_nethack_capture_that_fails_midway_keeps_the_stored_save_whole(dirs, full_disk):
    nh, name = dirs["nh"], saves.assign_name("alice")
    home = dirs["tmp"] / "h"
    home.mkdir()
    (nh / f"10001{name}.gz").write_bytes(b"OLD ONE")
    (nh / f"10002{name}.gz").write_bytes(b"OLD TWO")
    saves.capture("alice", "nethack", str(home))
    store = dirs["data"] / "saves" / "alice"
    stored = tree(store)
    (nh / f"10001{name}.gz").write_bytes(b"NEW ONE")
    (nh / f"10002{name}.gz").write_bytes(b"NEW TWO")
    hits = full_disk(lambda p: p.is_relative_to(dirs["data"]))
    ok = saves.capture("alice", "nethack", str(home))
    assert len(hits) >= 2, "the disk did not fill partway through the copy"
    assert tree(store) == stored, "the stored save was half-replaced"
    assert ok is False


def test_a_nethack_restore_that_fails_midway_says_so_and_leaves_none_of_its_files(dirs, full_disk):
    nh, name, other = dirs["nh"], saves.assign_name("alice"), saves.assign_name("bob")
    store = dirs["data"] / "saves" / "alice" / "nethack"
    store.mkdir(parents=True)
    (store / f"10001{name}.gz").write_bytes(b"SAVE ONE")
    (store / f"10002{name}.gz").write_bytes(b"SAVE TWO")
    (nh / f"10001{other}.gz").write_bytes(b"bob's")
    # Resolved: on Linux the restore reaches the shared dir through its descriptor (_shared_dir).
    hits = full_disk(lambda p: p.resolve().is_relative_to(nh.resolve()))
    assert saves.restore("alice", "nethack", str(dirs["tmp"])) is False
    assert len(hits) >= 2, "the disk did not fill partway through the copy"
    assert sorted(p.name for p in nh.iterdir()) == [f"10001{other}.gz"]


def test_a_restore_and_a_capture_that_succeed_say_so_and_round_trip(dirs):
    first = _played(dirs["tmp"] / "first", b"one")
    assert saves.capture("alice", "crawl", str(first)) is True
    session = dirs["tmp"] / "session"
    session.mkdir()
    assert saves.restore("alice", "crawl", str(session)) is True
    assert tree(session / ".crawl") == tree(first / ".crawl")
    # The game plays on: a file changes, one goes, one is new. The next capture is the new tree,
    # with nothing of the old one and nothing left beside it.
    (session / ".crawl" / "saves" / "Thrain.cs").write_bytes(b"PLAYED ON")
    (session / ".crawl" / "init.txt").unlink()
    (session / ".crawl" / "morgue" / "morgue-Wren.txt").write_bytes(b"A SECOND MORGUE")
    assert saves.capture("alice", "crawl", str(session)) is True
    store = dirs["data"] / "saves" / "alice" / "crawl"
    assert sorted(p.name for p in store.iterdir()) == ["dot_crawl"]
    assert tree(store / "dot_crawl") == tree(session / ".crawl")
    # Nothing to restore, and nothing to capture, are successes too.
    assert saves.restore("bob", "crawl", str(dirs["tmp"] / "bob")) is True
    assert saves.capture("bob", "crawl", str(dirs["tmp"] / "bob")) is True


# ==============================================================================================
# a swap Windows refuses for a moment, while something holds a file of the directory open
# ==============================================================================================

def refusing(monkeypatch, which: str, times: int | None) -> dict[str, list]:
    """os.rename as Windows answers it while something (an on-access scanner, which opens a file
    moments after it is written) holds a file of the directory open. The rename `which` names,
    "aside" (the stored tree moved to its `.old-*` name) or "in" (the new copy moved onto the
    stored name), raises PermissionError ([WinError 5] Access is denied) the first `times` times
    it is asked (None: every time), and is the real rename after that; every other rename is the
    real one. The wait after a stand-in refusal is recorded and not slept, so no test waits.
    Returns the record: each attempt at `which`, "refused" or "renamed", and each wait.

    This box's own scanner refuses real renames now and then too. Those are not recorded, and
    the wait after one is a real one, so the swap outlasts it as it would in a session and the
    record holds the stand-in's refusals alone."""
    real_rename, real_sleep = os.rename, time.sleep
    seen: dict[str, list] = {"attempts": [], "slept": []}
    pending: list[bool] = []             # a stand-in refusal whose wait has not come yet

    def kind(src) -> str:
        name = Path(src).name
        return "in" if ".new-" in name else "back" if ".old-" in name else "aside"

    def rename(src, dst, *a, **kw):
        pending.clear()
        if kind(src) == which and (times is None or seen["attempts"].count("refused") < times):
            seen["attempts"].append("refused")
            pending.append(True)
            raise PermissionError(13, "Access is denied", str(src))
        real_rename(src, dst, *a, **kw)
        if kind(src) == which:
            seen["attempts"].append("renamed")

    def sleep(seconds):
        if pending:
            pending.clear()
            seen["slept"].append(seconds)
        else:
            real_sleep(seconds)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(time, "sleep", sleep)
    return seen


def _beside(directory: Path) -> list[str]:
    """What a swap leaves beside its tree: its `.new-*` copy, or the `.old-*` it moved aside."""
    return sorted(p.name for p in directory.iterdir() if ".new-" in p.name or ".old-" in p.name)


@pytest.mark.parametrize("which", ["aside", "in"])
@pytest.mark.parametrize("refusals", ["one", "three", "as many as the bound allows"])
def test_a_refused_store_swap_is_asked_again_within_its_bound_and_takes_effect(
        dirs, monkeypatch, which, refusals):
    """Windows refuses to rename a directory while anything holds a file in it open, and an
    on-access scanner opens a file moments after it is written: on the dev box a capture's swap
    was refused now and then, [WinError 5], and the capture failed (a test of a session's capture
    went red about one run in thirty). Each rename of the swap is asked again after each wait of
    _RENAME_RETRIES, so a refusal that ends within the bound costs only the wait, and the
    session's play is stored."""
    # raising=False: on the code before the retry this fails on the refusal itself, the
    # capture False, rather than on the switch being missing.
    monkeypatch.setattr(saves, "_RETRY_RENAME", True, raising=False)
    refusals = {"one": 1, "three": 3}.get(refusals) or len(saves._RENAME_RETRIES)
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
    second = _played(dirs["tmp"] / "second", b"two")
    seen = refusing(monkeypatch, which, refusals)
    assert saves.capture("alice", "crawl", str(second)) is True
    assert seen["attempts"] == ["refused"] * refusals + ["renamed"]
    assert seen["slept"] == list(saves._RENAME_RETRIES[:refusals])
    store = dirs["data"] / "saves" / "alice" / "crawl"
    assert tree(store / "dot_crawl") == tree(second / ".crawl")
    assert _beside(store) == []


def test_a_restore_whose_swap_is_refused_for_a_moment_still_restores(dirs, monkeypatch):
    """The same swap brings the stored save into a session (_replace_tree onto `.crawl`)."""
    monkeypatch.setattr(saves, "_RETRY_RENAME", True, raising=False)
    first = _played(dirs["tmp"] / "first", b"one")
    saves.capture("alice", "crawl", str(first))
    session = dirs["tmp"] / "session"
    session.mkdir()
    seen = refusing(monkeypatch, "in", 2)
    assert saves.restore("alice", "crawl", str(session)) is True
    assert seen["attempts"] == ["refused", "refused", "renamed"]
    assert seen["slept"] == list(saves._RENAME_RETRIES[:2])
    assert tree(session / ".crawl") == tree(first / ".crawl")
    assert _beside(dirs["tmp"]) == []


@pytest.mark.parametrize("which", ["aside", "in"])
def test_a_store_swap_refused_past_its_bound_gives_up_and_keeps_the_save(dirs, monkeypatch, which):
    """A refusal that outlasts the bound fails the capture, as every refusal did before: one
    attempt after each wait and one more, then the PermissionError ends it, False, the stored
    save byte for byte as it was and nothing left beside it (a refused move onto the stored name
    puts the stored tree back first). The bound is short, under a second of waiting in all, so
    a swap the box refuses for good costs a session's end no more than that."""
    monkeypatch.setattr(saves, "_RETRY_RENAME", True)
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
    store = dirs["data"] / "saves" / "alice" / "crawl"
    stored = tree(store)
    seen = refusing(monkeypatch, which, None)
    assert saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "second", b"two"))) is False
    assert seen["attempts"] == ["refused"] * (len(saves._RENAME_RETRIES) + 1)
    assert seen["slept"] == list(saves._RENAME_RETRIES)
    assert 0 < sum(seen["slept"]) < 1.0 and len(seen["attempts"]) <= 10
    assert tree(store) == stored
    assert _beside(store) == []


def test_only_a_refusal_is_asked_again(dirs, monkeypatch):
    """Any other error of a rename (here a full disk's) fails the capture at once and unwaited: a
    retry is for a holder that lets go, and nothing else does."""
    monkeypatch.setattr(saves, "_RETRY_RENAME", True)
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
    store = dirs["data"] / "saves" / "alice" / "crawl"
    stored = tree(store)
    real = os.rename
    attempts: list[str] = []

    def rename(src, dst, *a, **kw):
        if Path(src).name == "dot_crawl":
            attempts.append(str(src))
            raise OSError(errno.ENOSPC, "No space left on device", str(src))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "rename", rename)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    assert saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "second", b"two"))) is False
    assert len(attempts) == 1 and slept == []
    assert tree(store) == stored


def test_the_swap_is_asked_again_on_windows_alone(dirs, monkeypatch):
    """Linux renames a directory whatever holds its files, so a PermissionError there is a real
    one (the backend may not write the store) and fails the capture at once, as it always did:
    _RETRY_RENAME is Windows' alone, and with it off one refusal is the end."""
    assert saves._RETRY_RENAME is (os.name == "nt")
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
    store = dirs["data"] / "saves" / "alice" / "crawl"
    stored = tree(store)
    monkeypatch.setattr(saves, "_RETRY_RENAME", False)
    seen = refusing(monkeypatch, "aside", 1)
    assert saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "second", b"two"))) is False
    assert seen["attempts"] == ["refused"] and seen["slept"] == []
    assert tree(store) == stored


# ==============================================================================================
# _share_with_game — making a restored save writable by the second uid
# ==============================================================================================

class TestShareWithGame:
    """Modes travel with a copied save: copytree and copy2 both preserve them. So a tree
    captured before the uid split, or under any umask that clears the group bit, arrives
    read-only for the uid that now has to play it — NetHack cannot lock its save, Crawl
    refuses to write, and it presents as "resume is broken" rather than as a permissions bug,
    because the save is plainly sitting there.

    Two layers, split for one reason. The CHOICE of bits is asserted on _shared_mode, a pure
    function taking an explicit starting mode — because a real file on Windows is already 0666
    and os.chmod there honours only the write bit, so "adds group write" and "adds world
    write" both read as true whatever the code does, and the test proves nothing on the
    machine it usually runs on. The TRAVERSAL and the failure handling are asserted against
    real paths with os.chmod recorded rather than applied.
    """

    def _record(self, monkeypatch):
        seen: dict[str, int] = {}
        monkeypatch.setattr(saves.os, "chmod", lambda p, m: seen.__setitem__(str(p), m))
        return seen

    def test_adds_group_read_write_to_a_file(self):
        """0600 -> 0660. Asserted on the pure function with an explicit starting mode, because
        a real file on this box starts at 0666 and would satisfy the assertion no matter what
        the code did."""
        assert saves._shared_mode(0o600, is_dir=False) == 0o660

    def test_does_not_add_execute_to_a_file(self):
        assert saves._shared_mode(0o600, is_dir=False) & stat.S_IXGRP == 0

    def test_adds_group_execute_to_a_directory(self):
        """0700 -> 0770. Without +x the game cannot traverse into the directory, so read and
        write on the files inside it are worth nothing."""
        assert saves._shared_mode(0o700, is_dir=True) == 0o770

    def test_never_adds_world_bits(self):
        """The point of the split is that a third uid cannot read a player's save. Widening
        the world nibble here would hand it back to exactly the process being contained."""
        assert saves._shared_mode(0o600, is_dir=False) & stat.S_IRWXO == 0
        assert saves._shared_mode(0o700, is_dir=True) & stat.S_IRWXO == 0

    def test_preserves_bits_already_set(self):
        assert saves._shared_mode(0o755, is_dir=True) == 0o775
        assert saves._shared_mode(0o644, is_dir=False) == 0o664

    def test_leaves_an_already_shared_mode_alone(self):
        assert saves._shared_mode(0o660, is_dir=False) == 0o660

    def test_strips_setuid_and_setgid_from_the_type_bits_it_is_handed(self):
        """st_mode carries the file-type bits (S_IFREG/S_IFDIR) as well as the permissions.
        Passing the whole thing through to chmod would be an error on some platforms and
        nonsense on all of them."""
        assert saves._shared_mode(stat.S_IFREG | 0o600, is_dir=False) == 0o660

    def test_recurses_through_the_whole_tree(self, tmp_path, monkeypatch):
        """Crawl's save is a directory tree. Fixing only the top level leaves everything the
        game actually opens unreachable."""
        seen = self._record(monkeypatch)
        root = tmp_path / ".crawl"
        (root / "saves").mkdir(parents=True)
        (root / "saves" / "Thrain.cs").write_bytes(b"x")
        (root / "init.txt").write_bytes(b"x")
        saves._share_with_game(root)
        assert set(seen) == {str(root), str(root / "saves"),
                             str(root / "saves" / "Thrain.cs"), str(root / "init.txt")}

    def test_end_to_end_widens_no_world_bit(self, tmp_path, monkeypatch):
        """The same guarantee as test_never_adds_world_bits, but through the real traversal.
        Compared against what was already on the file rather than asserted absent: whatever
        the umask left is not this function's business, and on Windows write_bytes produces
        0666. What must hold is that this call adds nothing in that nibble."""
        seen = self._record(monkeypatch)
        f = tmp_path / "save.gz"
        f.write_bytes(b"x")
        before = stat.S_IMODE(f.stat().st_mode)
        saves._share_with_game(f)
        assert seen[str(f)] & stat.S_IRWXO == before & stat.S_IRWXO

    def test_applies_the_computed_mode_and_nothing_else(self, tmp_path, monkeypatch):
        """The wiring between the pure function and the syscall, pinned with a sentinel.

        Comparing against a real recomputation cannot pin it here: every path in tmp_path on
        this box is already 0666/0777, so _shared_mode is the identity and a call site that
        skipped it entirely would agree with one that used it."""
        seen = self._record(monkeypatch)
        monkeypatch.setattr(saves, "_shared_mode", lambda mode, is_dir: 0o1234)
        f = tmp_path / "save.gz"
        f.write_bytes(b"x")
        saves._share_with_game(f)
        assert seen[str(f)] == 0o1234

    def test_tells_shared_mode_which_paths_are_directories(self, tmp_path, monkeypatch):
        """is_dir is what decides group +x, and getting it wrong is invisible on a box where
        everything is already 0777. Encoded into the sentinel so each path can be checked."""
        seen = self._record(monkeypatch)
        monkeypatch.setattr(saves, "_shared_mode",
                            lambda mode, is_dir: 0o2000 if is_dir else 0o1000)
        root = tmp_path / ".crawl"
        (root / "saves").mkdir(parents=True)
        (root / "saves" / "Thrain.cs").write_bytes(b"x")
        saves._share_with_game(root)
        assert seen == {str(root): 0o2000,
                        str(root / "saves"): 0o2000,
                        str(root / "saves" / "Thrain.cs"): 0o1000}

    def test_a_chmod_failure_is_swallowed(self, tmp_path, monkeypatch):
        """Best-effort, like everything else in this module: on an un-split image none of it
        is needed, and a chmod that fails must never be why a game will not start."""
        def refuse(*_a):
            raise PermissionError("read-only mount")
        monkeypatch.setattr(saves.os, "chmod", refuse)
        f = tmp_path / "save.gz"
        f.write_bytes(b"x")
        saves._share_with_game(f)          # must not raise

    def test_a_missing_path_is_not_an_error(self, tmp_path, monkeypatch):
        self._record(monkeypatch)
        saves._share_with_game(tmp_path / "nope")

    def test_restore_shares_the_nethack_save_it_just_copied(self, tmp_path, monkeypatch):
        """The integration that matters: restore() is the only thing standing between a stored
        save and a game that has to open it as another uid."""
        shared: list[str] = []
        monkeypatch.setattr(saves, "_share_with_game", lambda p: shared.append(p.name))
        monkeypatch.setattr(saves.settings, "data_dir", str(tmp_path / "data"))
        monkeypatch.setattr(saves.settings, "nethack_save_dir", str(tmp_path / "nh"))
        name = saves.assign_name("player1")
        store = saves._store("player1", "nethack")
        store.mkdir(parents=True)
        (store / f"10001{name}.gz").write_bytes(b"save")
        saves.restore("player1", "nethack", str(tmp_path / "home"))
        assert shared == [f"10001{name}.gz"]

    def test_restore_shares_the_crawl_tree_it_just_copied(self, tmp_path, monkeypatch):
        shared: list[str] = []
        monkeypatch.setattr(saves, "_share_with_game", lambda p: shared.append(p.name))
        monkeypatch.setattr(saves.settings, "data_dir", str(tmp_path / "data"))
        dot = saves._store("player1", "crawl") / "dot_crawl"
        dot.mkdir(parents=True)
        (dot / "init.txt").write_bytes(b"x")
        home = tmp_path / "home"
        home.mkdir()
        saves.restore("player1", "crawl", str(home))
        assert shared == [".crawl"]


# ==============================================================================================
# Nothing follows a link the game could have left
# ==============================================================================================
#
# Every game runs as the one game uid, which can write every session HOME and NetHack's shared
# dir; saves.py runs as the uid that owns /data. A link a player plants (`.crawl/loot -> another
# player's stored save`) must never be read, written or chmodded through. An unprivileged Windows
# shell cannot make a symlink (WinError 1314), so most tests here stand a real file in for the
# link's target and make Path.is_symlink() answer True for it (pretend_links), or use a hard link,
# which any user can make, for "a name in the shared dir that leads to a file outside it". The
# test with real symlinks, at the end, is defined where this process can make one.

SECRET = b"ANOTHER PLAYER'S SAVE"


def pretend_links(monkeypatch, *paths: Path) -> list[Path]:
    """Path.is_symlink() answers True for `paths`, and is asked: returns each path it answered
    for, the witness that the code under test looked before it acted.

    A path is matched as the file it names, however it is spelled. On Linux the code reaches
    NetHack's shared dir through its descriptor (saves._shared_dir), so a file planted at
    `nh / name` is asked about as /proc/self/fd/<fd>/name. Matched by its spelling alone, the
    stand-in never answered there and the link's "target" was stored (CI, 2026-10-17), while
    saves.py, asked about a real link that way, says it is one (the real links at the end of
    this file)."""
    real = Path.is_symlink
    fake = [Path(p) for p in paths]
    asked: list[Path] = []

    def names_one(self) -> bool:
        for p in fake:
            if os.path.normcase(str(self)) == os.path.normcase(str(p)):
                return True
            try:
                if os.path.samefile(self, p):
                    return True
            except OSError:                  # either is gone, or never was
                pass
        return False

    def is_symlink(self):
        if names_one(self):
            asked.append(self)
            return True
        return real(self)
    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    return asked


def holds_secret(root: Path) -> list[str]:
    """Every regular file under `root` whose bytes are SECRET."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink() and p.read_bytes() == SECRET)


def test_a_crawl_capture_copies_links_as_links_and_follows_none(dirs, monkeypatch):
    """The copytree path's choices, which a platform without descriptor walks takes (Windows),
    pinned on any platform by taking it here: copytree copies a link it lists as a link
    (symlinks=True), and each file goes through copy2 with follow_symlinks=False, so one that
    became a link after copytree listed it is copied as the link too."""
    monkeypatch.setattr(saves, "_BY_DESCRIPTOR", False)
    real_tree, real_copy2 = shutil.copytree, shutil.copy2
    tree_kw: dict = {}
    follows: list[bool] = []

    def copytree(src, dst, *a, **k):
        tree_kw.update(k)
        return real_tree(src, dst, *a, **k)

    def copy2(src, dst, *a, **k):
        follows.append(k.get("follow_symlinks", True))
        return real_copy2(src, dst, *a, **k)
    monkeypatch.setattr(shutil, "copytree", copytree)
    monkeypatch.setattr(shutil, "copy2", copy2)
    home = _played(dirs["tmp"] / "played", b"one")
    assert saves.capture("alice", "crawl", str(home)) is True
    assert tree_kw.get("symlinks") is True, "copytree follows every link it lists"
    assert follows and not any(follows), f"a file copy follows a link: {follows}"
    assert tree(dirs["data"] / "saves" / "alice" / "crawl" / "dot_crawl") == tree(home / ".crawl")


def test_the_copy_walks_by_descriptor_exactly_where_the_platform_can(dirs, monkeypatch):
    """_copy_of takes the descriptor walk where _BY_DESCRIPTOR says the platform has one, and
    copytree only where it has none. Both stand in here, so this runs anywhere; the walk itself
    is shown at the end of this file, on Linux."""
    calls: list[tuple] = []
    monkeypatch.setattr(saves, "_copy_tree_by_descriptor",
                        lambda src, dst: calls.append(("descriptor", src, dst)))
    monkeypatch.setattr(shutil, "copytree", lambda src, dst, **_k: calls.append(("copytree", src, dst)))
    src, new = dirs["tmp"] / "played" / ".crawl", dirs["tmp"] / "new"
    for walks, way in ((True, "descriptor"), (False, "copytree")):
        calls.clear()
        monkeypatch.setattr(saves, "_BY_DESCRIPTOR", walks)
        saves._copy_of(src)(new)
        assert calls == [(way, src, new)]


def test_a_crawl_capture_never_stores_what_a_linked_crawl_dir_points_at(dirs, monkeypatch):
    home = dirs["tmp"] / "played"
    (home / ".crawl").mkdir(parents=True)
    (home / ".crawl" / "Thrain.cs").write_bytes(SECRET)          # the link's target stands here
    asked = pretend_links(monkeypatch, home / ".crawl")
    ok = saves.capture("alice", "crawl", str(home))
    store = dirs["data"] / "saves" / "alice"
    assert not store.exists() or holds_secret(store) == [], "the link's target was stored"
    assert asked, "capture never asked whether .crawl is a link"
    assert ok is False


def test_share_with_game_never_chmods_through_a_link(tmp_path, monkeypatch):
    seen: dict[str, int] = {}
    monkeypatch.setattr(saves.os, "chmod", lambda p, m: seen.__setitem__(str(p), m))
    root = tmp_path / ".crawl"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "Thrain.cs").write_bytes(b"x")
    (root / "saves" / "loot").write_bytes(SECRET)
    pretend_links(monkeypatch, root / "saves" / "loot")
    saves._share_with_game(root)
    assert str(root / "saves" / "loot") not in seen, "chmod went through a link"
    assert set(seen) == {str(root), str(root / "saves"), str(root / "saves" / "Thrain.cs")}


def test_share_with_game_leaves_a_linked_root_alone(tmp_path, monkeypatch):
    seen: dict[str, int] = {}
    monkeypatch.setattr(saves.os, "chmod", lambda p, m: seen.__setitem__(str(p), m))
    root = tmp_path / ".crawl"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "Thrain.cs").write_bytes(SECRET)
    pretend_links(monkeypatch, root)
    saves._share_with_game(root)
    assert seen == {}, f"chmod went through a linked .crawl: {sorted(seen)}"


def test_a_nethack_capture_never_copies_a_link_and_clears_it(dirs, monkeypatch):
    nh, name = dirs["nh"], saves.assign_name("alice")
    (nh / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
    (nh / f"10002{name}.gz").write_bytes(SECRET)                 # a link a player left
    pretend_links(monkeypatch, nh / f"10002{name}.gz")
    home = dirs["tmp"] / "h"
    home.mkdir()
    assert saves.capture("alice", "nethack", str(home)) is True
    store = dirs["data"] / "saves" / "alice" / "nethack"
    assert holds_secret(store) == [], "the link's target was stored"
    assert tree(store) == {f"10001{name}.gz": b"ALICE'S SAVE"}
    assert sorted(p.name for p in nh.iterdir()) == [], "the planted link was left in the shared dir"


def test_a_nethack_restore_never_writes_through_a_name_left_in_the_shared_dir(dirs):
    """A hard link stands in for the planted symlink: writing to the name writes the file
    outside, which is the hazard. Any user can make one; Windows needs no privilege for it."""
    nh, name = dirs["nh"], saves.assign_name("alice")
    store = dirs["data"] / "saves" / "alice" / "nethack"
    store.mkdir(parents=True)
    (store / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
    outside = dirs["tmp"] / "outside.gz"
    outside.write_bytes(SECRET)
    os.link(outside, nh / f"10001{name}.gz")
    assert saves.restore("alice", "nethack", str(dirs["tmp"])) is True
    assert outside.read_bytes() == SECRET, "the restore wrote through the name, into the file"
    assert (nh / f"10001{name}.gz").read_bytes() == b"ALICE'S SAVE"
    assert sorted(p.name for p in nh.iterdir()) == [f"10001{name}.gz"], "a temp file was left"


@pytest.mark.parametrize("op", ["restore", "capture", "abandon", "discard"])
def test_the_shared_dir_is_opened_along_its_path_without_following_a_link(dirs, monkeypatch, op):
    """NetHack's shared dir sits in a directory the game can write (its playground, which it
    must), so the game can swap the dir, or a parent of it, for a link. Where directories can be
    pinned (_PINNED_DIRS: Linux, so the image), every restore, capture and clean-up opens the dir
    from the root one component at a time, each below the root with O_NOFOLLOW, and a component
    that is a link ends it before anything in the dir is read, written or cleared. os.open is
    stood in for here, answering ELOOP for the dir's parent only when asked not to follow, as
    Linux does, so this runs anywhere; the real links are at the end of this file, on Linux.

    Only the walk's own opens are stood in for: the root, and each component opened below a
    descriptor the stand-in handed out. Every other open is the real one. saves.os IS os, so a
    stand-in for every open reached shutil.rmtree too, which on Linux walks by descriptor:
    discard empties the store with it first, and fstat on a made-up descriptor failed it with
    EBADF (CI, 2026-10-17). On Windows rmtree opens nothing through os.open."""
    nh, name = dirs["nh"], saves.assign_name("alice")
    store = dirs["data"] / "saves" / "alice" / "nethack"
    store.mkdir(parents=True)
    (store / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
    (nh / f"10001{name}.gz").write_bytes(b"ALICE'S GAME")
    before = (tree(store), tree(nh))
    parts = Path(os.path.abspath(nh)).parts
    nofollow = getattr(os, "O_NOFOLLOW", 0x20000)
    opened: list[tuple[str, bool, bool]] = []
    real_open, real_close = os.open, os.close
    handed: set[int] = set()

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        if not (dir_fd in handed or (dir_fd is None and str(path) == parts[0])):
            return real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append((str(path), bool(flags & nofollow), dir_fd is not None))
        if str(path) == parts[-2] and flags & nofollow:
            raise OSError(errno.ELOOP, "Too many levels of symbolic links", str(path))
        handed.add(10_000 + len(opened))
        return 10_000 + len(opened)

    def fake_close(fd):
        if fd not in handed:
            real_close(fd)
    with monkeypatch.context() as m:
        m.setattr(saves, "_PINNED_DIRS", True)
        m.setattr(saves.os, "O_NOFOLLOW", nofollow, raising=False)
        m.setattr(saves.os, "O_DIRECTORY", getattr(os, "O_DIRECTORY", 0x10000), raising=False)
        m.setattr(saves.os, "open", fake_open)
        m.setattr(saves.os, "close", fake_close)
        if op == "restore":
            assert saves.restore("alice", "nethack", str(dirs["tmp"])) is False
        elif op == "capture":
            assert saves.capture("alice", "nethack", str(dirs["tmp"])) is False
        elif op == "abandon":
            saves.abandon("alice", "nethack")
        else:
            saves.discard("alice", "nethack")
    assert opened == [(parts[0], False, False)] + [(p, True, True) for p in parts[1:-1]]
    # Nothing in the dir was read into the store, written or cleared (discard empties the store,
    # which is its job).
    assert tree(nh) == before[1]
    assert tree(store) == ({} if op == "discard" else before[0])


def test_a_crawl_restore_copies_nothing_into_the_session_home_until_it_is_whole(dirs, full_disk):
    """The HOME is the game's to write: a staging directory made inside it could be swapped for
    one holding links where the copy's files go. So every file is copied outside it, and the
    finished tree is renamed in."""
    saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "played", b"one")))
    session = dirs["tmp"] / "session"
    session.mkdir()
    copies = full_disk(lambda p: True, nth=10**9)                # records, never fills
    assert saves.restore("alice", "crawl", str(session)) is True
    assert copies, "no copy was seen, so this proves nothing"
    inside = [p for p in copies if p.is_relative_to(session)]
    assert inside == [], f"copied straight into the session HOME: {inside}"
    assert tree(session / ".crawl") == tree(dirs["tmp"] / "played" / ".crawl")
    assert sorted(p.name for p in dirs["tmp"].iterdir()
                  if p.name.startswith(".crawl.")) == [], "the staging directory was left"


def _can_symlink() -> bool:
    import tempfile
    d = Path(tempfile.mkdtemp())
    try:
        os.symlink(d / "target", d / "link")
        return True
    except OSError:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


# Defined only where this process can make a symlink (Linux and the image, not an unprivileged
# Windows shell). Not a skip, on purpose: the rail's mutation harnesses refuse a baseline with a
# skipped test, so a skip here would stop them on every such box. The tests above pin the same
# choices on every platform; this one shows them on real links.
if _can_symlink():
    def test_real_links_are_stored_as_links_and_never_followed(dirs, monkeypatch):
        elsewhere = dirs["tmp"] / "elsewhere"
        (elsewhere / "dir").mkdir(parents=True)
        (elsewhere / "secret.cs").write_bytes(SECRET)
        (elsewhere / "dir" / "inner.cs").write_bytes(SECRET)
        home = _played(dirs["tmp"] / "played", b"one")
        os.symlink(elsewhere / "secret.cs", home / ".crawl" / "saves" / "loot.cs")
        os.symlink(elsewhere / "dir", home / ".crawl" / "morgue" / "more", target_is_directory=True)
        assert saves.capture("alice", "crawl", str(home)) is True
        dot = dirs["data"] / "saves" / "alice" / "crawl" / "dot_crawl"
        assert holds_secret(dot) == [], "a link's target was stored"
        assert (dot / "saves" / "loot.cs").is_symlink() and (dot / "morgue" / "more").is_symlink()
        played = home / ".crawl"
        assert (dot / "saves" / "Thrain.cs").read_bytes() == (played / "saves" / "Thrain.cs").read_bytes()
        assert (dot / "init.txt").read_bytes() == b"OPTIONS one"
        # Restored, the links are links again, and nothing chmods through them.
        chmodded: list[str] = []
        real_chmod = os.chmod

        def chmod(p, m):
            chmodded.append(str(p))
            real_chmod(p, m)
        monkeypatch.setattr(saves.os, "chmod", chmod)
        session = dirs["tmp"] / "session"
        session.mkdir()
        before = (elsewhere / "secret.cs").stat().st_mode
        assert saves.restore("alice", "crawl", str(session)) is True
        assert (session / ".crawl" / "saves" / "loot.cs").is_symlink()
        assert holds_secret(session) == []
        links = {str(session / ".crawl" / "saves" / "loot.cs"), str(session / ".crawl" / "morgue" / "more")}
        assert links.isdisjoint(chmodded), "chmod went through a restored link"
        assert (elsewhere / "secret.cs").stat().st_mode == before
        # A .crawl that is itself a link is never captured.
        other = dirs["tmp"] / "other"
        other.mkdir()
        os.symlink(elsewhere, other / ".crawl", target_is_directory=True)
        stored = tree(dot)
        assert saves.capture("alice", "crawl", str(other)) is False
        assert tree(dot) == stored


# Linux only: a walk by directory descriptor needs os.fwalk and opens relative to a directory's
# descriptor, which Windows does not have, so there the copy is copytree's (pinned above on every
# platform). Defined only on Linux rather than skipped elsewhere, for the reason given above
# _can_symlink(). CI runs this file on Linux (.github/workflows/private-suite.yml), and so can
# anyone with the image's toolchain.
if sys.platform == "linux":
    class _Listing(list):
        """A directory's entries, read whole, handed over as os.scandir hands them: iterable,
        and a context manager."""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def close(self):
            pass

    def _swap_for_a_link(directory: Path, target: Path, aside: Path) -> None:
        os.rename(directory, aside)
        os.symlink(target, directory, target_is_directory=True)

    @pytest.mark.parametrize("when", ["once-listed", "once-entered"])
    def test_a_directory_swapped_for_a_link_mid_walk_is_never_followed(dirs, monkeypatch, when):
        """The game swaps `.crawl/saves` for a link to a directory it may not read, while the
        capture is partway through `.crawl`: `once-listed`, after the walk has listed `.crawl`
        and before it looks at what is there; `once-entered`, after the copy has made what the
        listing holds and before the walk goes into it. copytree, which opens each directory by
        name, stored what the link points at in the first case."""
        assert saves._BY_DESCRIPTOR, "Linux walks by directory descriptor"
        elsewhere = dirs["tmp"] / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "secret.cs").write_bytes(SECRET)
        home = _played(dirs["tmp"] / "played", b"one")
        crawl = home / ".crawl"
        swapped: list[str] = []

        def swap() -> None:
            _swap_for_a_link(crawl / "saves", elsewhere, dirs["tmp"] / "saves-aside")
            swapped.append(when)

        if when == "once-listed":
            real_scandir, top = os.scandir, os.stat(crawl)

            def scandir(target="."):
                it = real_scandir(target)
                entries = _Listing(it)
                it.close()
                here = os.fstat(target) if isinstance(target, int) else os.stat(target)
                if not swapped and os.path.samestat(here, top):
                    swap()
                return entries
            monkeypatch.setattr(os, "scandir", scandir)
        else:
            real_fwalk = os.fwalk

            def fwalk(*a, **k):
                walk = real_fwalk(*a, **k)
                yield next(walk)
                swap()
                yield from walk
            monkeypatch.setattr(os, "fwalk", fwalk)

        assert saves.capture("alice", "crawl", str(home)) is True
        assert swapped == [when], "the directory was never swapped mid-walk, so this proves nothing"
        dot = dirs["data"] / "saves" / "alice" / "crawl" / "dot_crawl"
        assert holds_secret(dot) == [], "the walk followed the link into what it points at"
        # The link copied as a link (seen before the walk looked), or the directory it listed,
        # made and left empty (swapped after).
        stored = dot / "saves"
        assert stored.is_symlink() or (stored.is_dir() and not any(stored.iterdir()))
        assert (dot / "init.txt").read_bytes() == b"OPTIONS one"
        assert (dot / "morgue" / "morgue-Thrain.txt").read_bytes() == b"MORGUE one"

    def test_a_crawl_dir_swapped_for_a_link_after_capture_looked_is_not_copied(dirs, monkeypatch):
        """capture() refuses a `.crawl` that is a link, but the game can swap it for one just
        after it looked. The walk opens `.crawl` itself with O_NOFOLLOW, so the copy fails and
        the stored save stays as it was; copytree followed the link and stored its target."""
        elsewhere = dirs["tmp"] / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "secret.cs").write_bytes(SECRET)
        saves.capture("alice", "crawl", str(_played(dirs["tmp"] / "first", b"one")))
        dot = dirs["data"] / "saves" / "alice" / "crawl" / "dot_crawl"
        stored = tree(dot)
        crawl = _played(dirs["tmp"] / "second", b"two") / ".crawl"
        real = Path.is_symlink
        looked: list[Path] = []

        def is_symlink(self):
            answer = real(self)
            if self == crawl and not looked:
                looked.append(self)
                _swap_for_a_link(crawl, elsewhere, dirs["tmp"] / "crawl-aside")
            return answer
        monkeypatch.setattr(Path, "is_symlink", is_symlink)
        ok = saves.capture("alice", "crawl", str(crawl.parent))
        assert looked, "capture never asked whether .crawl is a link"
        assert holds_secret(dot) == [], "the link's target was stored"
        assert tree(dot) == stored
        assert ok is False

    def _alices_nethack_store(dirs) -> tuple[Path, str]:
        name = saves.assign_name("alice")
        store = dirs["data"] / "saves" / "alice" / "nethack"
        store.mkdir(parents=True)
        (store / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
        return store, name

    @pytest.mark.parametrize("swapped", ["save", "nethack"])
    def test_a_nethack_restore_never_writes_through_a_link_on_the_shared_dirs_path(
            dirs, monkeypatch, swapped):
        """The game swaps the shared dir (`save`), or its playground (`nethack`), for a link to a
        directory only the backend can write. The restore opens the dir along its whole path
        without following a link, so it refuses; the old one wrote this owner's save there, as
        the uid that owns /data."""
        playground = dirs["tmp"] / "games" / "nethack"
        (playground / "save").mkdir(parents=True)
        monkeypatch.setattr(settings, "nethack_save_dir", str(playground / "save"))
        store, name = _alices_nethack_store(dirs)
        target = dirs["data"] / "elsewhere"
        (target / "save").mkdir(parents=True)
        moved = playground / "save" if swapped == "save" else playground
        _swap_for_a_link(moved, target, dirs["tmp"] / "aside")
        assert saves.restore("alice", "nethack", str(dirs["tmp"])) is False
        assert tree(target) == {"save": None}, "the restore wrote through the link"
        assert tree(store) == {f"10001{name}.gz": b"ALICE'S SAVE"}

    def test_a_shared_dir_swapped_while_a_restore_holds_it_is_not_followed(dirs, monkeypatch):
        """The swap after the restore has opened the dir: what it writes goes into the directory
        it opened (now moved aside), never through the link onto its path."""
        nh = dirs["nh"]
        store, name = _alices_nethack_store(dirs)
        target = dirs["data"] / "elsewhere"
        target.mkdir()
        aside = dirs["tmp"] / "aside"
        real_place = saves._place_file

        def place(src, dest):
            if not aside.exists():
                _swap_for_a_link(nh, target, aside)
            return real_place(src, dest)
        monkeypatch.setattr(saves, "_place_file", place)
        assert saves.restore("alice", "nethack", str(dirs["tmp"])) is True
        assert aside.exists(), "the shared dir was never swapped, so this proves nothing"
        assert tree(target) == {}, "the restore followed the link swapped onto the shared dir"
        assert tree(aside) == {f"10001{name}.gz": b"ALICE'S SAVE"}

    def test_nothing_reads_or_clears_through_a_shared_dir_swapped_for_a_link(dirs):
        """Capture, abandon and discard each refuse a shared dir that is now a link. The old
        capture stored what the link pointed at under this owner's name, and cleared it there."""
        nh, name = dirs["nh"], saves.assign_name("alice")
        (nh / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
        assert saves.capture("alice", "nethack", str(dirs["tmp"])) is True
        store = dirs["data"] / "saves" / "alice" / "nethack"
        stored = tree(store)
        target = dirs["data"] / "elsewhere"
        target.mkdir()
        (target / f"10002{name}.gz").write_bytes(SECRET)    # this owner's name, backend-only
        _swap_for_a_link(nh, target, dirs["tmp"] / "aside")
        assert saves.capture("alice", "nethack", str(dirs["tmp"])) is False
        assert tree(store) == stored, "what the link points at was stored"
        saves.abandon("alice", "nethack")
        saves.discard("alice", "nethack")
        assert tree(target) == {f"10002{name}.gz": SECRET}, "a file was cleared through the link"

    def test_a_real_link_left_in_the_shared_dir_is_cleared_and_never_stored(dirs):
        """test_a_nethack_capture_never_copies_a_link_and_clears_it, on a real link: a player
        leaves one under this owner's name in the shared dir, to a file only the backend can
        read. The capture reaches the dir through its descriptor (/proc/self/fd/<fd>), stores
        the owner's save and neither the link nor what it points at, and clears the link; the
        file behind it is untouched."""
        nh, name = dirs["nh"], saves.assign_name("alice")
        (nh / f"10001{name}.gz").write_bytes(b"ALICE'S SAVE")
        outside = dirs["data"] / "elsewhere.gz"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(SECRET)
        os.symlink(outside, nh / f"10002{name}.gz")
        assert saves.capture("alice", "nethack", str(dirs["tmp"])) is True
        store = dirs["data"] / "saves" / "alice" / "nethack"
        assert holds_secret(store) == [], "the link's target was stored"
        assert tree(store) == {f"10001{name}.gz": b"ALICE'S SAVE"}
        assert sorted(p.name for p in nh.iterdir()) == [], "the planted link was left in the shared dir"
        assert outside.read_bytes() == SECRET
