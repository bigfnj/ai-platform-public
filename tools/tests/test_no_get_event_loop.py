"""No app code asks asyncio for its loop with get_event_loop().

Every caller in this tree runs inside a running loop (a FastAPI handler, or a task one started),
and there `asyncio.get_running_loop()` is the call that says so: it returns that loop, and raises
when there is none instead of quietly handing back some other loop. get_event_loop() is
deprecated outside a running loop and its behaviour there has changed across Python releases, so
a call that works only because a loop happens to be running is one refactor away from a different
answer. Three request handlers of one rail made it (found 2026-10-13); this keeps a fourth from
arriving unnoticed.

Every tracked .py under the trees that hold app code is scanned, comments and docstrings included:
a docstring that tells the reader to call it is advice to do the deprecated thing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TREES = ("apps", "packages", "rails", "services")
PRUNE = {".git", "node_modules", "__pycache__", "dist", ".venv", ".test-logs"}
# Built from parts, so this file never matches the pattern it enforces (tools/ is not scanned
# either; the split keeps it that way if it ever is).
_CALL = re.compile(r"\bget_event_" + r"loop\s*\(")
# Hundreds of modules in either tree, public or private; a scan that found a handful saw nothing.
MIN_FILES = 150


def _python_files() -> list[Path]:
    """Every tracked .py under TREES: `git ls-files` in a checkout, a pruned walk in a built tree
    that is not its own git top level (the public snapshot is checked that way too). `-z`, read
    as UTF-8: without it git quotes a path holding a non-ASCII character, and the quoted form
    names no file, so that file was skipped in silence."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO:
            ls = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", *TREES],
                                capture_output=True, text=True, encoding="utf-8", timeout=60,
                                check=True)
            return [REPO / p for p in ls.stdout.split("\0") if p.endswith(".py")]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for tree in TREES:
        for root, dirs, files in os.walk(REPO / tree):
            dirs[:] = [d for d in dirs if d not in PRUNE]
            out += [Path(root) / f for f in files if f.endswith(".py")]
    return out


def calls(text: str) -> list[int]:
    """Line numbers in `text` that call get_event_loop()."""
    return [n for n, line in enumerate(text.splitlines(), 1) if _CALL.search(line)]


def test_the_pattern_tells_the_deprecated_call_from_its_neighbours():
    name = "get_event_" + "loop"
    sample = "\n".join([
        f"loop = asyncio.{name}()",                     # 1: the call
        "loop = asyncio.get_running_loop()",             # 2: the replacement
        f"policy = asyncio.{name}_policy()",             # 3: a different function
        f"    via asyncio.{name} ().run_in_executor",    # 4: spaced, still the call
    ])
    assert calls(sample) == [1, 4]


def test_a_tracked_file_with_a_non_ascii_name_is_scanned(tmp_path, monkeypatch):
    """git quotes such a path unless given -z (core.quotepath, forced on here whatever this box's
    git config says), and the quoted form names no file."""
    repo = tmp_path.resolve()
    f = repo / "rails" / "demo" / "señal.py"
    f.parent.mkdir(parents=True)
    f.write_text("loop = asyncio.get_event_" + "loop()\n", encoding="utf-8")
    for key, value in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "core.quotepath"),
                       ("GIT_CONFIG_VALUE_0", "true")):
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(repo), "add", "--", "rails"], check=True, timeout=60)
    monkeypatch.setattr(sys.modules[__name__], "REPO", repo)
    files = _python_files()
    assert f in files, files
    assert calls(f.read_text(encoding="utf-8")) == [1]


def test_no_app_code_calls_get_event_loop():
    files = [f for f in _python_files() if f.is_file()]
    assert len(files) >= MIN_FILES, f"scanned only {len(files)} files under {TREES}"
    hits = []
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        hits += [f"{rel}:{n}" for n in calls(f.read_text(encoding="utf-8", errors="replace"))]
    assert not hits, ("asyncio.get_running_loop() states the running loop these callers assume, "
                      f"and fails loudly without one; get_event_loop() is called at: {hits}")
