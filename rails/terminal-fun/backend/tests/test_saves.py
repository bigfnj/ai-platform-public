"""Per-user save/resume store: Crawl (HOME-relative) + NetHack (shared dir, fun-name-namespaced)."""
from __future__ import annotations

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
