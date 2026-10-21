"""Driving the Pebble SDK from inside this rail's own container.

The SDK lives in the rail's image rather than being reached through the host's Docker socket.
That costs about 2 GB of image and buys two things the socket approach cannot: the rail stays
an independent deployable (the rule docs/RAIL_CONTRACT.md opens with), and it is not a
privilege-escalation path.

WHAT THIS MODULE REFUSES TO PRETEND
-----------------------------------
The Pebble SDK has no Windows build. On the Windows host, standalone dev cannot compile at
all. So `available()` is a real question with a real "no", and everything here is written so
that a missing toolchain produces `build_available: false` in /api/capabilities rather than a
stack trace on every build. Design, validation and the layout audit all work without it,
which is most of the rail.

That distinction matters more than it looks. An unavailable toolchain is a THIRD outcome, not
a degraded version of "built and found nothing wrong", and collapsing the two would let a
frontend report a clean build on a box that never compiled anything.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from watch_builder.config import settings

# Emulator boot plus install plus screenshot. Serial per platform, which is why the API takes
# a platform list and the editor asks for one at a time while you iterate.
_SETTLE_SECONDS = 2.0

# One render at a time across the process (render()): each ends by stopping every emulator,
# which would cut a second render's emulators out from under it.
_render_lock = threading.Lock()


@dataclass
class BuildResult:
    ok: bool
    platforms: list[str]
    pbw: Path | None = None
    duration_s: float = 0.0
    log: str = ""
    # Present only when ok is False. Kept separate from `log` so a caller can show the error
    # without showing 300 lines of waf output.
    error: str | None = None


@dataclass
class RenderResult:
    ok: bool
    # platform -> PNG bytes AS THE WATCH SHOWS IT. `pebble screenshot` applies the sunlight
    # correction by default, so this is already the on-wrist appearance and must NOT be run
    # through a correction table again.
    shots: dict[str, bytes] = field(default_factory=dict)
    # platform -> PNG bytes of the RAW framebuffer (`--no-correction`). Useful for seeing the
    # literal palette colours a design specified; not what anybody's wrist shows.
    raw_shots: dict[str, bytes] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0


def pebble_bin() -> str | None:
    """The `pebble` executable, or None when the SDK is not installed here."""
    return shutil.which("pebble")


def available() -> bool:
    """Can this process actually compile a watchface?

    Both halves are required. `pebble` on PATH without an SDK produces a tool that reports a
    version and then fails every build; an SDK directory with no tool is equally useless.
    """
    if pebble_bin() is None:
        return False
    return Path(settings.sdk_root, "SDKs").is_dir()


def sdk_version() -> str | None:
    """The active SDK version, or None if it cannot be determined."""
    exe = pebble_bin()
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # "Pebble Tool v5.0.40 (active SDK: v4.33.1)"
    marker = "active SDK: v"
    if marker in out:
        return out.split(marker, 1)[1].split(")", 1)[0].strip()
    return out.strip() or None


def _run(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, check=False, env=env)


def write_project(files: dict[str, str], root: Path) -> None:
    """Materialise a generated project on disk.

    Newline is forced to "\\n" because the SDK's waf scripts and the ARM toolchain are run on
    Linux, and a CRLF `wscript` is a syntax error that reads as a broken SDK.
    """
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def build(root: Path) -> BuildResult:
    """`pebble build` in a prepared project directory."""
    started = time.monotonic()
    if not available():
        return BuildResult(ok=False, platforms=[], error="sdk-unavailable",
                           log="No Pebble SDK in this environment. The SDK has no Windows "
                               "build; design and audit work, compiling does not.")
    exe = pebble_bin()
    assert exe is not None
    try:
        proc = _run([exe, "build"], root, settings.build_timeout)
    except subprocess.TimeoutExpired:
        return BuildResult(ok=False, platforms=[], error="timeout",
                           duration_s=time.monotonic() - started,
                           log=f"build exceeded {settings.build_timeout}s")

    log = (proc.stdout or "") + (proc.stderr or "")
    duration = time.monotonic() - started

    pbws = sorted((root / "build").glob("*.pbw")) if (root / "build").is_dir() else []
    if proc.returncode != 0 or not pbws:
        return BuildResult(ok=False, platforms=[], duration_s=duration, log=log,
                           error=_first_compiler_error(log) or "build-failed")

    built = sorted(p.name for p in (root / "build").iterdir()
                   if p.is_dir() and (p / "pebble-app.bin").exists())
    return BuildResult(ok=True, platforms=built, pbw=pbws[0], duration_s=duration, log=log)


def _first_compiler_error(log: str) -> str | None:
    """The first line that actually names a problem.

    Waf prints a lot and puts the useful line in the middle. A caller showing the tail of the
    log usually shows the summary rather than the cause.
    """
    for line in log.splitlines():
        low = line.lower()
        if "error:" in low or "undeclared" in low:
            return line.strip()[:400]
    return None


def render(root: Path, platforms: list[str], *, want_raw: bool = True) -> RenderResult:
    """Install on each emulator and screenshot it.

    TWO screenshots per platform, and the distinction matters.

    `pebble screenshot` applies the SUNLIGHT CORRECTION by default. Measured: a face filled
    with GColorRed screenshots as #E35462, not #FF0000, and the other three primaries match
    their corrected values exactly too. So the default output is already what a wrist sees.

    This module previously captured only that and the API then ran it through a correction
    table a second time, labelling the result "sunlight-corrected" and defaulting the UI to
    it. Every colour judged on that view was double-corrected.

    `--no-correction` gives the raw framebuffer. Both are captured because they answer
    different questions: the corrected one is what the watch shows, the raw one is which
    palette colours the design actually specified.

    Serial rather than parallel. Each emulator is a QEMU process and the tool talks to it
    over a fixed local socket per platform, so two installs racing on the same platform
    collide; across platforms it would work but would put seven QEMU instances on the box at
    once, which on a machine also running a GPU broker is not a trade worth making for a few
    seconds.

    THE EMULATORS STOP WHEN IT ENDS, however it ends (`kill_emulators` in a `finally`). `pebble
    install --emulator` boots a QEMU emulator per platform and leaves it running after the
    command returns, and nothing stopped them until 2026-10-14, so every platform ever rendered
    kept a QEMU process for the container's life beside the GPU broker. Killed rather than kept
    warm on an idle timer, because a long-lived emulator is the measured failure here: installs
    against one wedge it with `[Errno 111] Connection refused` (docs/DESIGN.md, "The default
    target was two watches"), and the reliable state is a fresh one. The price is a cold boot
    per build, for one platform by default (emery). Renders take `_render_lock`, one at a time:
    the kill would otherwise cut another render's emulators mid-capture, and two renders on one
    platform already collided on its socket.
    """
    started = time.monotonic()
    result = RenderResult(ok=False)
    if not available():
        result.errors = {p: "sdk-unavailable" for p in platforms}
        return result

    exe = pebble_bin()
    assert exe is not None
    shots_dir = root / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    with _render_lock:
        try:
            for plat in platforms:
                target = shots_dir / f"{plat}.png"
                # Stale file removed first: `pebble screenshot` failing leaves the previous
                # image in place, and re-measuring an old screenshot is how a broken layout
                # reports as clean.
                target.unlink(missing_ok=True)
                try:
                    inst = _run([exe, "install", "--emulator", plat], root,
                                settings.render_timeout)
                    if inst.returncode != 0:
                        result.errors[plat] = _tail(inst.stdout, inst.stderr)
                        continue
                    time.sleep(_SETTLE_SECONDS)
                    shot = _run([exe, "screenshot", "--no-open", "--emulator", plat,
                                 f"shots/{plat}.png"], root, settings.render_timeout)
                    if shot.returncode != 0 or not target.exists():
                        result.errors[plat] = _tail(shot.stdout, shot.stderr)
                        continue
                    result.shots[plat] = target.read_bytes()

                    if want_raw:
                        # A second capture with the correction off. Failing this is not
                        # fatal: the on-watch image is the one that matters and a missing raw
                        # view is a lost comparison rather than a broken render.
                        raw_target = shots_dir / f"{plat}-raw.png"
                        raw_target.unlink(missing_ok=True)
                        raw = _run([exe, "screenshot", "--no-open", "--no-correction",
                                    "--emulator", plat, f"shots/{plat}-raw.png"],
                                   root, settings.render_timeout)
                        if raw.returncode == 0 and raw_target.exists():
                            result.raw_shots[plat] = raw_target.read_bytes()
                except subprocess.TimeoutExpired:
                    result.errors[plat] = f"timeout after {settings.render_timeout}s"
        finally:
            kill_emulators()

    result.duration_s = time.monotonic() - started
    result.ok = bool(result.shots)
    return result


def _tail(stdout: str | None, stderr: str | None, limit: int = 300) -> str:
    text = ((stdout or "") + (stderr or "")).strip()
    return text[-limit:] if text else "no output"


def kill_emulators() -> None:
    """Best-effort cleanup: `pebble kill`, which stops every emulator and its phone simulator.
    render() calls it when it ends, success or not.

    NOT `pebble wipe`. Measured on the Pebble knowledge-base container: wipe left every one
    of the seven emulators answering `[Errno 111] Connection refused`, and the documented
    recovery (kill then wipe again) made it worse. Recreating the container was the only
    reliable reset, which a rail cannot do to itself. So this kills and stops there.
    """
    exe = pebble_bin()
    if exe is None:
        return
    try:
        subprocess.run([exe, "kill"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
