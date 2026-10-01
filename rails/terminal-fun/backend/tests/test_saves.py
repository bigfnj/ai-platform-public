"""Per-user save/resume store: Crawl (HOME-relative) + NetHack (shared dir, fun-name-namespaced)."""
from __future__ import annotations

import os
import stat

import pytest

from terminal_fun_app import saves
from terminal_fun_app.config import settings


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
