"""The refresh-on-read gate: one walk of a quiet mount per interval, and one rebuild per change.

`_built_at` moved only when the index was rebuilt, so once a mount had been quiet for
MEETING_ATLAS_AUTOREINDEX_SECONDS every read re-ran the fingerprint walk (a listdir plus one
stat per meeting folder, over 9p, under the build lock) for as long as nothing changed. The
audit's probe, 250 folders: 100 reads, 100 listdirs and 25,000 getmtimes. And the rebuild ran
after the lock was released, so the double-rebuild guard its docstring promised did not exist.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from meeting_atlas_app import main

USER = {"X-Platform-User": "owner-one", "X-Platform-Admin": "0"}
FOLDERS = 40


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def mount(tmp_path, monkeypatch):
    root = tmp_path / "recordings"
    for i in range(FOLDERS):
        (root / f"Meeting {i:03d}").mkdir(parents=True)
    monkeypatch.setattr(main.settings, "recordings_dir", str(root))
    monkeypatch.setattr(main.settings, "meetily_db", "")
    monkeypatch.setattr(main.settings, "autoreindex_seconds", 300)
    return root


@pytest.fixture()
def counted(monkeypatch):
    """How many fingerprint walks and rebuilds the reads below cause."""
    n = {"walks": 0, "rebuilds": 0}
    real_fp, real_build = main._mount_fingerprint, main.build_index

    def fp():
        n["walks"] += 1
        return real_fp()

    def build(*a, **kw):
        n["rebuilds"] += 1
        return real_build(*a, **kw)

    monkeypatch.setattr(main, "_mount_fingerprint", fp)
    monkeypatch.setattr(main, "build_index", build)
    return n


def _age_everything(seconds: float) -> None:
    """Pretend the last rebuild and the last look were `seconds` ago."""
    main._built_at -= seconds
    main._checked_at -= seconds


def test_a_quiet_mount_is_walked_once_per_interval_not_once_per_read(mount, counted):
    with TestClient(main.app) as c:
        counted.update(walks=0, rebuilds=0)
        _age_everything(301)
        for _ in range(100):
            assert c.get("/api/meetings", headers=USER).status_code == 200
        assert counted == {"walks": 1, "rebuilds": 0}
        # And the next interval pays exactly one more.
        _age_everything(301)
        for _ in range(10):
            c.get("/api/healthz", headers=USER)
        assert counted == {"walks": 2, "rebuilds": 0}


def test_a_changed_mount_is_still_picked_up(mount, counted):
    with TestClient(main.app) as c:
        assert c.get("/api/healthz", headers=USER).json()["n_meetings"] == 0  # folders, no transcripts
        counted.update(walks=0, rebuilds=0)
        (mount / "Meeting 999").mkdir()
        _age_everything(301)
        c.get("/api/meetings", headers=USER)
        assert counted["rebuilds"] == 1
        # The rebuild restarted the interval: the reads after it neither walk nor rebuild.
        before = dict(counted)
        for _ in range(20):
            c.get("/api/meetings", headers=USER)
        assert counted == before


def test_inside_the_interval_nothing_is_walked(mount, counted):
    with TestClient(main.app) as c:
        counted.update(walks=0, rebuilds=0)
        for _ in range(50):
            c.get("/api/meetings", headers=USER)
        assert counted == {"walks": 0, "rebuilds": 0}


def _meetily(path):
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, "
                "folder_path TEXT)")
    con.execute("CREATE TABLE summary_processes (meeting_id TEXT, status TEXT, result TEXT, "
                "processing_time REAL)")
    con.commit()
    con.close()
    return path


def _write_summary(path):
    """What Meetily does after a recording ends: a row in the database, no folder touched."""
    import json
    import os
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute("INSERT INTO summary_processes VALUES (?, 'completed', ?, 2.0)",
                ("mtg-1", json.dumps({"markdown": "# A summary written later\n" + "x" * 5000})))
    con.commit()
    con.close()
    later = os.path.getmtime(path) + 5
    os.utime(path, (later, later))


def test_a_summary_written_only_into_the_database_is_indexed_at_the_next_check(
        mount, counted, tmp_path, monkeypatch):
    db = _meetily(tmp_path / "meetily.db")
    monkeypatch.setattr(main.settings, "meetily_db", str(db))
    with TestClient(main.app) as c:
        counted.update(walks=0, rebuilds=0)
        _write_summary(db)                   # no recording folder changes
        _age_everything(301)
        c.get("/api/meetings", headers=USER)
        assert counted["rebuilds"] == 1, "a database-only change was never picked up"


def test_an_unchanged_database_triggers_no_rebuild(mount, counted, tmp_path, monkeypatch):
    """The control: the stamp must not differ from itself, or every check would rebuild."""
    db = _meetily(tmp_path / "meetily.db")
    monkeypatch.setattr(main.settings, "meetily_db", str(db))
    with TestClient(main.app) as c:
        counted.update(walks=0, rebuilds=0)
        _age_everything(301)
        c.get("/api/meetings", headers=USER)
        assert counted == {"walks": 1, "rebuilds": 0}


def test_the_rebuild_runs_under_the_build_lock(mount, monkeypatch):
    """The re-check and the rebuild must be one critical section, or a second reader can take
    the lock in between, see the old timestamp and rebuild the whole tree again."""
    held: list[bool] = []
    real = main.reindex

    def watching(reason="manual"):
        # RLock._is_owned: True when THIS thread holds it. The call from inside _maybe_refresh
        # is the one under test; reindex's own acquire nests on the RLock.
        held.append(main._build_lock._is_owned())
        return real(reason)

    with TestClient(main.app) as c:
        monkeypatch.setattr(main, "reindex", watching)
        (mount / "Meeting 998").mkdir()
        _age_everything(301)
        c.get("/api/meetings", headers=USER)
    assert held == [True]


def test_two_readers_after_a_change_rebuild_once(mount, counted, monkeypatch):
    real_build = main.build_index

    with TestClient(main.app) as c:
        counted.update(walks=0, rebuilds=0)

        def slow_build(*a, **kw):
            time.sleep(0.3)
            return real_build(*a, **kw)

        monkeypatch.setattr(main, "build_index", slow_build)
        (mount / "Meeting 997").mkdir()
        _age_everything(301)
        rebuilds: list[float] = []
        orig = main.reindex

        def counting(reason="manual"):
            rebuilds.append(time.time())
            return orig(reason)

        monkeypatch.setattr(main, "reindex", counting)
        threads = [threading.Thread(target=main._maybe_refresh) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(rebuilds) == 1
        assert c.get("/api/healthz", headers=USER).status_code == 200


def test_healthz_reports_a_wal_database_whose_log_is_not_mounted(mount, tmp_path, monkeypatch):
    import sqlite3

    live = tmp_path / "live.sqlite"
    con = sqlite3.connect(str(live))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, "
                "folder_path TEXT)")
    con.commit()
    try:
        mounted = tmp_path / "meetily.db"
        mounted.write_bytes(live.read_bytes())
        monkeypatch.setattr(main.settings, "meetily_db", str(mounted))
        with TestClient(main.app) as c:
            h = c.get("/api/healthz", headers=USER).json()
        assert h["meetily_db_mounted"] is True
        assert h["meetily_wal_unseen"] is True
        monkeypatch.setattr(main.settings, "meetily_db", str(live))
        with TestClient(main.app) as c:
            assert c.get("/api/healthz", headers=USER).json()["meetily_wal_unseen"] is False
    finally:
        con.close()
