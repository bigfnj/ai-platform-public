"""tools/tests/loop_tasks.py: the pending-task check five rails' hang-up tests share.

Its tests here are the controls for the change that moved those suites onto it (2026-10-19):
a task truly left running, or cancelled and not yet ended, still fails the check, so the move
made the check honest on Python 3.11 without making it blind. Two more pin why it is built the
way it is: before 3.12, wait_for ran the scenario in a task of its own, which the sample saw;
and a list filtered for unfinished tasks once asyncio.run has returned is empty whatever was
left running. The last finds a test that samples the loop from inside wait_for again, which
passes on a 3.12+ box and fails only in CI.
"""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loop_tasks import others, run_alone

REPO = Path(__file__).resolve().parents[2]


async def _forever() -> None:
    await asyncio.Event().wait()


def test_a_task_left_running_is_listed():
    """The control: what the check is for still fails it."""
    async def scenario():
        left = asyncio.ensure_future(_forever())
        await asyncio.sleep(0)               # it has started, and is waiting
        return left, others()

    left, pending = run_alone(scenario())
    assert pending == [left]


def test_a_read_cancelled_and_not_awaited_is_still_listed():
    """A wrapper that cancels its read and returns without waiting for it leaves the read
    running past its own return. The check is taken there, not after the loop has settled,
    so it says so."""
    async def scenario():
        read = asyncio.ensure_future(_forever())
        await asyncio.sleep(0)
        read.cancel()                        # asked to stop; not yet stopped
        return read, others()

    read, pending = run_alone(scenario())
    assert pending == [read]


def test_the_scenario_is_the_loops_one_task():
    async def scenario():
        return others(), len(asyncio.all_tasks())

    assert run_alone(scenario()) == ([], 1)


def test_a_task_that_finished_is_not_listed():
    async def scenario():
        done = asyncio.ensure_future(asyncio.sleep(0))
        await done
        return others()

    assert run_alone(scenario()) == []


def test_the_run_is_bounded():
    began = time.monotonic()
    with pytest.raises(TimeoutError):
        run_alone(asyncio.sleep(30), 0.05)
    assert time.monotonic() - began < 10


def test_the_value_comes_back():
    async def scenario():
        await asyncio.sleep(0)
        return "the answer"

    assert run_alone(scenario(), 5) == "the answer"


def test_before_3_12_wait_for_runs_the_scenario_in_a_task_of_its_own():
    """Why run_alone exists: the sample saw the task asyncio.run made, still waiting inside
    wait_for. Measured on both sides of the change, so the docstring's claim is held, not
    remembered: one extra task before 3.12, none from 3.12 on."""
    async def scenario():
        return others()

    seen = asyncio.run(asyncio.wait_for(scenario(), 10))
    assert len(seen) == (1 if sys.version_info < (3, 12) else 0), seen


def test_a_list_filtered_after_asyncio_run_returns_is_empty_whatever_was_left():
    """Why the sample is taken inside the loop: asyncio.run cancels each task still pending and
    runs the loop until every one has finished, so filtering afterwards proves nothing."""
    async def scenario():
        left = asyncio.ensure_future(_forever())
        await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()], left

    sampled, left = asyncio.run(scenario())
    assert sampled == [left]                             # inside: the task left running
    assert [t for t in sampled if not t.done()] == []    # after: nothing, though it was


# --- no test samples the loop from inside wait_for -------------------------------------------

TREES = ("apps", "packages", "rails", "services")
PRUNE = {".git", "node_modules", "__pycache__", "dist", ".venv", ".test-logs"}


def _test_files() -> list[Path]:
    """Every tracked test module under TREES: `git ls-files` in a checkout, a pruned walk in a
    built tree that is not its own git top level (the public snapshot)."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO:
            ls = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", *TREES],
                                capture_output=True, text=True, encoding="utf-8", timeout=60,
                                check=True)
            return [REPO / p for p in ls.stdout.split("\0")
                    if Path(p).name.startswith("test_") and p.endswith(".py")]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for tree in TREES:
        for root, dirs, files in os.walk(REPO / tree):
            dirs[:] = [d for d in dirs if d not in PRUNE]
            out += [Path(root) / f for f in files if f.startswith("test_") and f.endswith(".py")]
    return out


def _is_call(node: ast.AST, owner: str | None, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if owner is None:
        return isinstance(f, ast.Name) and f.id == name
    return (isinstance(f, ast.Attribute) and f.attr == name
            and isinstance(f.value, ast.Name) and f.value.id == owner)


def samples_inside_wait_for(source: str) -> list[str]:
    """The functions of `source` (module-level, and methods of its classes: a test, or a helper
    several tests share, as one suite's `_leave` was) that sample the loop's tasks and run their
    scenario as asyncio.run(asyncio.wait_for(...)). A sample is a call of asyncio.all_tasks(),
    of loop_tasks' others(), or of a module-level helper of the file's own that calls
    all_tasks() (each suite had one, `_others`, before loop_tasks)."""
    tree = ast.parse(source)
    samplers = {"others"} | {
        fn.name for fn in tree.body
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_call(n, "asyncio", "all_tasks") for n in ast.walk(fn))}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)] + [
        m for c in tree.body if isinstance(c, ast.ClassDef)
        for m in c.body if isinstance(m, ast.FunctionDef)]
    found = []
    for fn in functions:
        nodes = list(ast.walk(fn))
        samples = any(_is_call(n, "asyncio", "all_tasks")
                      or any(_is_call(n, None, s) for s in samplers) for n in nodes)
        wrapped = any(_is_call(n, "asyncio", "run") and n.args
                      and _is_call(n.args[0], "asyncio", "wait_for") for n in nodes)
        if samples and wrapped:
            found.append(fn.name)
    return found


def test_the_scan_tells_a_sample_inside_wait_for_from_one_that_is_not():
    sample = "\n".join([
        "def test_inside():",
        "    async def run():",
        "        return others()",
        "    assert asyncio.run(asyncio.wait_for(run(), 10)) == []",
        "def test_alone():",
        "    async def run():",
        "        return others()",
        "    assert run_alone(run()) == []",
        "def test_raw_inside():",
        "    async def run():",
        "        return [t for t in asyncio.all_tasks()]",
        "    asyncio.run(asyncio.wait_for(run(), 10))",
        "def test_wait_for_inside_the_scenario():",
        "    async def run():",
        "        await asyncio.wait_for(work(), 5)",
        "        return others()",
        "    asyncio.run(run())",
        "def test_no_sample():",
        "    asyncio.run(asyncio.wait_for(work(), 5))",
        "def _mine():",
        "    return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]",
        "def test_own_helper_inside():",
        "    async def run():",
        "        return _mine()",
        "    asyncio.run(asyncio.wait_for(run(), 10))",
        "def _shared_by_tests(ws):",
        "    async def run():",
        "        return others()",
        "    return asyncio.run(asyncio.wait_for(run(), 10))",
        "class TestInAClass:",
        "    def test_method_inside(self):",
        "        async def run():",
        "            return others()",
        "        asyncio.run(asyncio.wait_for(run(), 10))",
    ])
    assert samples_inside_wait_for(sample) == ["test_inside", "test_raw_inside",
                                               "test_own_helper_inside", "_shared_by_tests",
                                               "test_method_inside"]


def test_no_test_samples_the_loop_from_inside_wait_for():
    files = _test_files()
    offenders = {str(f.relative_to(REPO)): names for f in files
                 if (names := samples_inside_wait_for(f.read_text(encoding="utf-8")))}
    assert offenders == {}, (
        "these sample the loop's tasks inside asyncio.wait_for, which before Python 3.12 runs "
        "the scenario in a task of its own that the sample then lists; run it with "
        f"loop_tasks.run_alone instead: {offenders}")
    # A scan that found no module taking loop_tasks saw nothing. (Six do here; fewer in the
    # public snapshot, which withholds a rail of theirs.)
    assert any("from loop_tasks import" in f.read_text(encoding="utf-8") for f in files)
