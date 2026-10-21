"""The terminal socket's own bookkeeping: what it stores from a resize frame, what it does with a
session that failed to start, and how it counts sessions against the per-user cap.

Driven through the real ws_terminal handler with a stub socket and a FAKE PtySession (the real
one is Linux-only; its own lifecycle is pinned in test_pty_lifecycle.py). The fake records
every construction, start, resize and close, which is what each assertion reads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from terminal_fun_app import main, saves
from terminal_fun_app.config import settings

USER = "owner-one"


class FakeSession:
    made: list["FakeSession"] = []
    fail_start = False
    on_make = None          # called after each construction (a test's hook)

    def __init__(self, argv, term, cols, rows, env_extra=None, home=None):
        self.argv, self.cols, self.rows, self.home = list(argv), cols, rows, home
        self.resized: list[tuple[int, int]] = []
        self.started = self.closed = False
        self._done = asyncio.Event()
        FakeSession.made.append(self)
        if FakeSession.on_make:
            FakeSession.on_make()

    async def start(self):
        if FakeSession.fail_start:
            raise FileNotFoundError("no such game in this image")
        self.started = True

    def resize(self, cols, rows):
        self.resized.append((cols, rows))

    def write(self, data):
        pass

    async def read(self):
        await self._done.wait()
        return b""

    async def close(self):
        self.closed = True
        self._done.set()


class StubWS:
    """Enough of a Starlette WebSocket for ws_terminal. `frames` are delivered in order, then a
    disconnect; `hold` (an asyncio.Event) delays that disconnect until it is set."""

    def __init__(self, frames=(), hold: asyncio.Event | None = None, query=None,
                 hold_timeout: float | None = None):
        self.headers = {"x-platform-user": USER}
        self.query_params = dict(query or {"cols": "80", "rows": "24"})
        self.frames = list(frames)
        self.hold = hold
        self.hold_timeout = hold_timeout
        self.accepted = False
        self.close_code = None
        self.texts: list[str] = []
        self.sent: list[bytes] = []

    async def accept(self):
        await asyncio.sleep(0)          # a real accept() awaits: this is the race's window
        self.accepted = True

    async def close(self, code=1000):
        if self.close_code is None:
            self.close_code = code

    async def receive(self):
        if self.frames:
            await asyncio.sleep(0)
            return {"type": "websocket.receive", "bytes": self.frames.pop(0)}
        if self.hold is not None:
            try:
                await asyncio.wait_for(self.hold.wait(), self.hold_timeout)
            except asyncio.TimeoutError:
                pass
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, data):
        self.sent.append(bytes(data))

    async def send_text(self, text):
        self.texts.append(text)


def frame(tag: int, obj) -> bytes:
    return bytes([tag]) + json.dumps(obj).encode()


@pytest.fixture(autouse=True)
def fake_pty(monkeypatch, tmp_path):
    """The handler imports terminal_fun_app.pty_session lazily; hand it the fake."""
    FakeSession.made = []
    FakeSession.fail_start = False
    FakeSession.on_make = None
    monkeypatch.setitem(sys.modules, "terminal_fun_app.pty_session",
                        types.SimpleNamespace(PtySession=FakeSession))
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "nethack_save_dir", str(tmp_path / "nh"))
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    main._sessions.clear()
    yield
    main._sessions.clear()


def run(ws, item="asciiquarium"):
    asyncio.run(asyncio.wait_for(main.ws_terminal(ws, item), timeout=10))


def test_a_resize_frame_is_clamped_before_it_is_stored_or_applied():
    ws = StubWS(frames=[frame(main._RESIZE, {"cols": 70000, "rows": 24})])
    run(ws)
    (s,) = FakeSession.made
    assert s.resized == [(500, 24)]


def test_the_next_launch_gets_the_clamped_size_not_the_poison():
    """The leak's trigger was the NEXT start() reading the stored 70000. A tunable toy's apply
    frame relaunches with the stored dims, so that is the launch to look at."""
    hold = asyncio.Event()
    # Disconnect only once the relaunch has happened; disconnecting with the apply still in
    # flight ends the session instead, which is correct but not what this test is about.
    FakeSession.on_make = lambda: hold.set() if len(FakeSession.made) == 2 else None
    ws = StubWS(frames=[frame(main._RESIZE, {"cols": 70000, "rows": 99999}),
                        frame(main._APPLY, {"color": "red"})], hold=hold)
    run(ws, item="cmatrix")
    assert len(FakeSession.made) == 2, "the apply frame did not relaunch"
    relaunched = FakeSession.made[1]
    assert (relaunched.cols, relaunched.rows) == (500, 500)


def test_a_session_that_fails_to_start_is_closed_and_releases_its_slot():
    FakeSession.fail_start = True
    ws = StubWS()
    run(ws)
    (s,) = FakeSession.made
    assert s.closed, "the failed session was never closed"
    assert USER not in main._sessions, "the failed session kept its place against the cap"
    assert any("failed to start" in t for t in ws.texts)


@pytest.mark.parametrize("body", [["not", "an", "object"], [], 0, "x"])
def test_a_non_object_apply_frame_changes_nothing(body):
    """A falsy one ([] or 0) is the case that mattered: validate_params reads it as "no values"
    and answers the defaults, so it relaunched the toy with its tuning reset.

    The disconnect is held until a relaunch happens or 0.5 s pass: delivered at once it would
    end the session before any relaunch could, and this would pass whatever the handler did."""
    hold = asyncio.Event()
    FakeSession.on_make = lambda: hold.set() if len(FakeSession.made) == 2 else None
    ws = StubWS(frames=[frame(main._APPLY, body)], hold=hold, hold_timeout=0.5)
    run(ws, item="cmatrix")
    assert len(FakeSession.made) == 1, f"{body!r} was taken as new settings and relaunched"


def test_concurrent_handshakes_cannot_pass_the_cap_together(monkeypatch):
    """Two tabs opened at once by one user, cap 1. The check used to sit before accept(), which
    awaits, so both read a count of 0 and both were admitted."""
    monkeypatch.setattr(settings, "max_sessions_per_user", 1)

    async def scenario():
        hold = asyncio.Event()
        first, second = StubWS(hold=hold), StubWS(hold=hold)
        t1 = asyncio.create_task(main.ws_terminal(first, "asciiquarium"))
        t2 = asyncio.create_task(main.ws_terminal(second, "asciiquarium"))
        for _ in range(50):                      # let both handshakes reach their decision
            await asyncio.sleep(0)
            if first.close_code == 4409 or second.close_code == 4409:
                break
        refused = [w.close_code for w in (first, second)].count(4409)
        hold.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=10)
        return refused, len(FakeSession.made)

    refused, launched = asyncio.run(scenario())
    assert (refused, launched) == (1, 1)
    assert USER not in main._sessions, "the admitted session did not release its slot"


def test_sequential_sessions_still_get_in():
    """Guards the over-correction: the cap counts LIVE sessions, not handshakes ever made."""
    for _ in range(settings.max_sessions_per_user + 2):
        ws = StubWS()
        run(ws)
        assert ws.close_code != 4409
    assert len(FakeSession.made) == settings.max_sessions_per_user + 2


# ==============================================================================================
# A session whose restore failed is never captured
# ==============================================================================================
#
# The defect: restore() swallowed a failure partway through its copy (a full /tmp), and the
# session's capture then replaced the stored save with what the session held, the partial tree
# and whatever the new game wrote. The disk here fills from the second file copied anywhere but
# the save volume (conftest's full_disk), so only the restore into the session HOME fails.

KEY = bytes([main._IN]) + b"x"
NOTICE = b"could not be loaded"


def tree(root: Path) -> dict[str, bytes | None]:
    """Everything under `root`: each path, POSIX-spelled, to its bytes (a directory to None)."""
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def stored_crawl() -> Path:
    """The player's stored Crawl save, as an earlier session's capture left it. Returns the store."""
    dot = Path(settings.data_dir) / "saves" / USER / "crawl" / "dot_crawl"
    (dot / "saves").mkdir(parents=True)
    (dot / "morgue").mkdir()
    (dot / "saves" / "Thrain.cs").write_bytes(b"THE PLAYER'S SAVE" + bytes(range(256)))
    (dot / "morgue" / "morgue-Thrain.txt").write_bytes(b"A MORGUE")
    (dot / "init.txt").write_bytes(b"OPTIONS")
    return dot.parent


def off_the_volume(path: Path) -> bool:
    return not path.is_relative_to(Path(settings.data_dir))


def the_game_writes(rel: str, data: bytes) -> None:
    """What the game does once it runs: write `rel` in its session's HOME."""
    def write():
        p = Path(FakeSession.made[-1].home) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    FakeSession.on_make = write


def audit_events(caplog) -> list[str]:
    return [json.loads(r.getMessage())["event"] for r in caplog.records if r.name == "terminal-fun"]


def test_a_restore_that_fails_midway_leaves_the_stored_save_byte_for_byte(full_disk, caplog):
    caplog.set_level(logging.INFO, logger="terminal-fun")
    store = stored_crawl()
    before = tree(store)
    hits = full_disk(off_the_volume)
    the_game_writes(".crawl/saves/Wren.cs", b"A NEW GAME")
    run(StubWS(frames=[KEY]), item="crawl")
    assert len(hits) >= 2, "the disk did not fill partway through the restore"
    assert len(FakeSession.made) == 1, "the new game never ran, so this proves nothing"
    assert tree(store) == before, "the session's capture replaced the save it could not load"
    assert USER not in main._sessions
    assert audit_events(caplog) == ["start", "restore_failed", "end"]


def test_a_capture_that_fails_at_the_session_s_end_keeps_the_save_and_says_so(full_disk, caplog):
    caplog.set_level(logging.INFO, logger="terminal-fun")
    store = stored_crawl()
    before = tree(store)
    hits = full_disk(lambda p: not off_the_volume(p))     # the volume fills, not /tmp
    the_game_writes(".crawl/saves/Thrain.cs", b"PLAYED ON")
    run(StubWS(), item="crawl")
    assert len(hits) >= 2, "the disk did not fill partway through the capture"
    assert tree(store) == before, "the stored save was half-replaced"
    assert audit_events(caplog) == ["start", "capture_failed", "end"]


def test_a_restore_that_raises_is_a_failed_restore_too(monkeypatch):
    """main's own guard: anything but True from restore() is a failure, an exception included.

    The calls are the witness: no capture, and the session abandoned. The stored save's bytes
    alone were not one: a capture that runs and fails leaves them as they were too. On Windows
    the capture's swap is now and then refused (the store's rename, PermissionError), so under
    the harness case for this guard the old test passed 5 runs in 300, and SURVIVED once."""
    store = stored_crawl()
    before = tree(store)

    def broken(*_a):
        raise RuntimeError("not an OSError")
    captured: list[tuple] = []
    abandoned: list[tuple] = []
    monkeypatch.setattr(saves, "restore", broken)
    monkeypatch.setattr(saves, "capture", lambda *a: captured.append(a) or True)
    monkeypatch.setattr(saves, "abandon", lambda *a: abandoned.append(a))
    the_game_writes(".crawl/saves/Wren.cs", b"A NEW GAME")
    run(StubWS(frames=[KEY]), item="crawl")
    assert len(FakeSession.made) == 1
    assert captured == [], "a session whose restore raised was captured"
    assert abandoned == [(USER, "crawl")], "the session was not ended as a failed restore"
    assert tree(store) == before            # and nothing else wrote the stored save


def test_the_player_is_told_once_in_the_terminal_and_the_game_waits_for_a_key(full_disk):
    stored_crawl()
    full_disk(off_the_volume)
    ws = StubWS(frames=[KEY])
    told_at_launch: list[int] = []
    FakeSession.on_make = lambda: told_at_launch.append(sum(NOTICE in b for b in ws.sent))
    run(ws, item="crawl")
    notices = [b for b in ws.sent if NOTICE in b]
    assert len(notices) == 1, f"told {len(notices)} times"
    assert notices[0][0] == main._IN, "the notice went to the status line, which a live page hides"
    assert b"kept safe" in notices[0]
    assert told_at_launch == [1], "the game started before the player was told"


def test_a_player_who_leaves_at_the_notice_starts_no_game(full_disk):
    store = stored_crawl()
    before = tree(store)
    full_disk(off_the_volume)
    run(StubWS(), item="crawl")             # no key: the socket closes at the notice
    assert FakeSession.made == [], "the new game started without the player's key"
    assert tree(store) == before
    assert USER not in main._sessions


def test_a_failed_nethack_restore_keeps_the_save_and_leaves_the_shared_dir_clean(full_disk):
    """NetHack plays out of a shared dir, not the HOME: what the new game saved as it hung up
    must not stay there under the name the next restore writes."""
    name = saves.assign_name(USER)
    store = Path(settings.data_dir) / "saves" / USER / "nethack"
    store.mkdir(parents=True)
    (store / f"10001{name}.gz").write_bytes(b"THE PLAYER'S SAVE")
    (store / f"10002{name}.gz").write_bytes(b"ITS SECOND FILE")
    before = tree(store)
    nh = Path(settings.nethack_save_dir)
    hits = full_disk(off_the_volume)

    def hang_up_save():
        (nh / f"10001{name}.gz").write_bytes(b"A NEW GAME, SAVED AS IT HUNG UP")
    FakeSession.on_make = hang_up_save
    run(StubWS(frames=[KEY]), item="nethack")
    assert len(hits) >= 2, "the disk did not fill partway through the restore"
    assert tree(store) == before
    assert sorted(p.name for p in nh.iterdir()) == []


def test_a_restored_session_is_still_captured_with_no_notice_and_no_key():
    """Guards the over-correction: only a FAILED restore skips the capture."""
    store = stored_crawl()
    the_game_writes(".crawl/saves/Thrain.cs", b"PLAYED ON")
    ws = StubWS()                           # no key: none is asked for
    run(ws, item="crawl")
    assert len(FakeSession.made) == 1
    assert (store / "dot_crawl" / "saves" / "Thrain.cs").read_bytes() == b"PLAYED ON"
    assert (store / "dot_crawl" / "init.txt").read_bytes() == b"OPTIONS"
    assert not any(NOTICE in b for b in ws.sent)
