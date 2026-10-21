"""An image upload's decode runs off the event loop, two at a time, and two stores of the same
bytes do not trip over each other.

`upload_image` is async, and until 2026-10-14 it called `images.store` (decode, transpose,
thumbnail, optimised PNG encode: ~0.7 s for a 40 MP JPEG) on the event loop, so every other
request the rail served, the editor's job polls included, waited behind it. Here the store is
held open mid-decode while a poll is sent: the poll must finish while the store is still
working. On the old code the held store blocked the loop itself, so the poll could only be
answered after the store let go.

Moving it to the threadpool (2026-10-14) let stores run at once, which they never had (the delta
audit, 2026-10-15): with no bound, as many decodes of a 40 MP image (~320 MB each) as the pool
has tokens, and every store of the same bytes wrote and renamed one fixed `<id>.png.tmp`, so a
double drop or two tabs gave the user a 500. Two stores run at once now, a third waits for a slot
holding no thread, and each store writes a temp of its own.
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from watch_builder import config, images
from watch_builder.api import app as app_module
from watch_builder.api.app import app

USER = {"X-Platform-User": "alice", "X-Platform-Admin": "0"}


def _png(red: int = 255) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (red, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_an_image_upload_does_not_stall_a_concurrent_poll(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    at: dict[str, float] = {}
    real = images.store

    def held_store(owner, raw, *, name):
        entered.set()
        release.wait(3)                 # the decode, held open
        at["store_done"] = time.monotonic()
        return real(owner, raw, name=name)

    monkeypatch.setattr(images, "store", held_store)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://watch") as client:
            upload = asyncio.create_task(client.post(
                "/api/images", headers=USER, files={"file": ("red.png", _png(), "image/png")}))
            assert await asyncio.to_thread(entered.wait, 5), "the upload never reached store"
            poll = await client.get("/api/health", headers=USER)
            at["poll_done"] = time.monotonic()
            release.set()
            return poll, await upload

    poll, upload = asyncio.run(asyncio.wait_for(run(), 20))
    assert poll.status_code == 200 and upload.status_code == 200, upload.text
    assert at["poll_done"] < at["store_done"], "the poll waited for the decode to finish"
    assert upload.json()["width"] == 64


def test_a_third_upload_waits_for_a_slot_while_a_poll_is_answered(monkeypatch, tmp_path):
    """Three uploads at once: two stores run, the third waits on the route's limiter (no thread
    of its own yet), and a poll sent meanwhile is answered. With no bound all three decode at
    once, as up to the threadpool's 40 could."""
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    release, lock = threading.Event(), threading.Lock()
    running = [0]
    seen: list[int] = []                # how many stores were running, at each one's start
    real = images.store

    def held_store(owner, raw, *, name):
        with lock:
            running[0] += 1
            seen.append(running[0])
        try:
            assert release.wait(10), "the test never released the stores"
            return real(owner, raw, name=name)
        finally:
            with lock:
                running[0] -= 1

    monkeypatch.setattr(images, "store", held_store)
    limiter = getattr(app_module, "_STORE_LIMITER", None)

    def queued() -> int:
        return limiter.statistics().tasks_waiting if limiter is not None else 0

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://watch") as client:
            uploads = [asyncio.create_task(client.post(
                "/api/images", headers=USER,
                files={"file": (f"{i}.png", _png(200 + i), "image/png")})) for i in range(3)]
            # Watched from the loop: two stores in and one upload queued on the limiter, or a
            # third store in (the defect). A thread hop here could queue behind the stores.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(seen) < 3 and not (
                    len(seen) == 2 and queued() == 1):
                await asyncio.sleep(0.01)
            started, waiting = len(seen), queued()
            try:
                poll = await client.get("/api/health", headers=USER)
                still_waiting = queued()
            finally:
                release.set()
            return started, waiting, poll, still_waiting, await asyncio.gather(*uploads)

    started, waiting, poll, still_waiting, done = asyncio.run(asyncio.wait_for(run(), 30))
    assert (started, waiting) == (2, 1), f"{started} stores started, {waiting} waiting"
    assert poll.status_code == 200 and still_waiting == 1, "the poll was not answered meanwhile"
    assert [r.status_code for r in done] == [200, 200, 200], [r.text for r in done]
    assert max(seen) == 2, f"three stores ran at once: {seen}"


def test_stores_of_the_same_bytes_at_once_all_succeed(monkeypatch, tmp_path):
    """Four stores of one image, each held after writing its temp until all four have, then
    renamed one after another: the old shared `<id>.png.tmp` was gone after the first rename,
    and the other three raised FileNotFoundError (a 500). One after another because renames
    released together all opened the shared temp before any moved it, and on Windows then all
    "succeeded"; the audit's repro met the interleavings that fail."""
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    meet, serial = threading.Barrier(4, timeout=10), threading.Lock()
    real_write, real_replace = Path.write_bytes, os.replace

    def write_then_meet(self, data):
        n = real_write(self, data)
        if self.name.endswith(".png.tmp"):
            meet.wait()                 # every store's temp is written; none renamed yet
        return n

    def one_rename_at_a_time(src, dst):
        with serial:
            return real_replace(src, dst)

    monkeypatch.setattr(Path, "write_bytes", write_then_meet)
    monkeypatch.setattr(os, "replace", one_rename_at_a_time)
    raw, errors, ids = _png(), [], []

    def one():
        try:
            ids.append(images.store("alice", raw, name="red.png").id)
        except Exception as exc:  # noqa: BLE001 -- each one is the finding
            errors.append(repr(exc))

    threads = [threading.Thread(target=one) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert errors == [] and len(ids) == 4 and len(set(ids)) == 1, errors
    left = sorted(p.name for p in (tmp_path / "alice" / "images").iterdir())
    assert left == [f"{ids[0]}.json", f"{ids[0]}.png"], left
    assert [r["id"] for r in images.listing("alice")] == [ids[0]]
    assert Image.open(io.BytesIO(images.raw_png("alice", ids[0]))).size == (64, 48)


@pytest.mark.parametrize("fails", ["write", "rename"])
def test_a_store_that_cannot_write_leaves_no_temp_behind(monkeypatch, tmp_path, fails):
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    real_write = Path.write_bytes

    def broken_write(self, data):
        if self.name.endswith(".tmp"):
            real_write(self, data[: len(data) // 2])
            raise OSError(28, "No space left on device")
        return real_write(self, data)

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    if fails == "write":
        monkeypatch.setattr(Path, "write_bytes", broken_write)
    else:
        monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        images.store("alice", _png(), name="red.png")
    assert list((tmp_path / "alice" / "images").iterdir()) == []


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
def test_a_refused_rename_is_tried_again_on_windows_only(monkeypatch, tmp_path, windows):
    """Windows refuses a rename onto a file someone holds open (another store's rename, a
    scanner reading what was just written), and lets it through once they let go. On POSIX a
    refusal is a real one."""
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(images, "_WINDOWS", windows, raising=False)
    real_replace, refusals = os.replace, [2]

    def held_open(src, dst):
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", held_open)
    if windows:
        rec = images.store("alice", _png(), name="red.png")
        assert images.raw_png("alice", rec.id) and refusals == [0]
    else:
        with pytest.raises(PermissionError):
            images.store("alice", _png(), name="red.png")
        assert refusals == [1], "a POSIX refusal was tried again"
    assert not [p for p in (tmp_path / "alice" / "images").iterdir() if p.name.endswith(".tmp")]


def test_a_record_write_that_fails_keeps_the_previous_record(monkeypatch, tmp_path):
    """The same image again, its record cut off half way (a full disk): the record already
    there must survive. Written in place, the half was left in `<id>.json`, which `listing`
    skips, so the image left the list."""
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    first = images.store("alice", _png(), name="first.png")
    real_text, real_bytes = Path.write_text, Path.write_bytes

    def half(self, data, real, *a, **k):
        if ".json" in self.name:
            real(self, data[: len(data) // 2], *a, **k)
            raise OSError(28, "No space left on device")
        return real(self, data, *a, **k)

    monkeypatch.setattr(Path, "write_text", lambda self, d, *a, **k: half(self, d, real_text, *a, **k))
    monkeypatch.setattr(Path, "write_bytes", lambda self, d: half(self, d, real_bytes))
    with pytest.raises(OSError):
        images.store("alice", _png(), name="again.png")
    assert [(r["id"], r["name"]) for r in images.listing("alice")] == [(first.id, "first.png")]
    assert not [p for p in (tmp_path / "alice" / "images").iterdir() if p.name.endswith(".tmp")]
