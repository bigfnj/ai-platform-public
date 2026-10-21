"""A render stops the emulators it started, however it ends, and renders run one at a time.

`pebble install --emulator` boots a QEMU emulator per platform and leaves it running after the
command returns. Until 2026-10-14 nothing called `sdk.kill_emulators()` (docs/DESIGN.md named it
as the recovery step), so every platform ever rendered kept a QEMU process for the container's
life. These drive the real `render()` over a fake `pebble` (subprocess.run replaced), which
records each command and writes the screenshot a real one would.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from watch_builder import sdk

PNG = b"\x89PNG\r\n\x1a\n-a-synthetic-screenshot"


class FakePebble:
    """subprocess.run for `pebble`: records (thread, args) per call; a screenshot writes its file.
    `hold` names an install that blocks until `release` is set."""

    def __init__(self, hold: str | None = None, fail: dict | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.hold = hold
        self.fail = fail or {}
        self.release = threading.Event()
        self.holding = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, args, cwd=None, **_kw):
        cmd = list(args[1:])
        with self._lock:
            self.calls.append((threading.current_thread().name, cmd))
        if cmd[0] in self.fail:
            raise self.fail[cmd[0]]
        if cmd[0] == "install" and cmd[-1] == self.hold:
            self.holding.set()
            assert self.release.wait(10), "the test never released the held install"
        if cmd[0] == "screenshot":
            Path(cwd, cmd[-1]).write_bytes(PNG)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def commands(self, thread: str | None = None) -> list[str]:
        return [c[0] for t, c in self.calls if thread is None or t == thread]


@pytest.fixture
def pebble(monkeypatch):
    def install(fake: FakePebble) -> FakePebble:
        monkeypatch.setattr(sdk, "available", lambda: True)
        monkeypatch.setattr(sdk, "pebble_bin", lambda: "pebble")
        monkeypatch.setattr(sdk, "_SETTLE_SECONDS", 0.0)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return install


def test_a_render_stops_the_emulators_once_it_has_its_screenshots(pebble, tmp_path):
    fake = pebble(FakePebble())
    result = sdk.render(tmp_path, ["emery", "gabbro"])
    assert result.ok and set(result.shots) == {"emery", "gabbro"}
    assert fake.commands() == ["install", "screenshot", "screenshot",
                               "install", "screenshot", "screenshot", "kill"]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("pebble", 1),
                                     OSError("the emulator socket went away")])
def test_a_render_that_fails_still_stops_the_emulators(pebble, tmp_path, failure):
    """A timed-out install is an error the render reports; an unexpected raise escapes it. The
    emulators stop either way."""
    fake = pebble(FakePebble(fail={"install": failure}))
    if isinstance(failure, subprocess.TimeoutExpired):
        result = sdk.render(tmp_path, ["emery"])
        assert not result.ok and "timeout" in result.errors["emery"]
    else:
        with pytest.raises(OSError):
            sdk.render(tmp_path, ["emery"])
    assert fake.commands()[-1] == "kill" and fake.commands().count("kill") == 1


def test_two_renders_run_one_after_the_other(pebble, tmp_path):
    """The kill that ends one render must not land mid-way through another's capture."""
    fake = pebble(FakePebble(hold="emery"))
    first = threading.Thread(target=sdk.render, args=(tmp_path / "a", ["emery"]), name="first")
    second = threading.Thread(target=sdk.render, args=(tmp_path / "b", ["gabbro"]),
                              name="second")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first.start()
    assert fake.holding.wait(5)
    second.start()
    time.sleep(0.2)                     # room for the second to start, were it allowed to
    started_early = fake.commands("second")
    fake.release.set()
    first.join(10)
    second.join(10)
    assert started_early == [], "the second render ran while the first held its emulator"
    order = [t for t, _ in fake.calls]
    assert order == ["first"] * 4 + ["second"] * 4, fake.calls
    assert fake.commands("first")[-1] == "kill" and fake.commands("second")[-1] == "kill"
