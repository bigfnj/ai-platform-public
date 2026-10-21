"""What a test's event loop still has running: the check every hang-up and heartbeat test makes.

A wrapper that keeps a broker read in a task of its own must end that task when its client
leaves, and the tests that pin it ask the loop what is still running once the wrapper is done.
They had a copy each of the same two lines, and ran their scenario as
`asyncio.run(asyncio.wait_for(scenario(), 10))`. Five rails' suites take this module instead:

    from loop_tasks import others, run_alone

    async def scenario():
        ...                         # drive the wrapper until the client has gone
        return others()             # sampled HERE, inside the loop
    assert run_alone(scenario()) == []

* `run_alone(coro, timeout)` runs `coro` to completion under asyncio.run, bounded by `timeout`
  seconds, as the loop's ONE task, on every Python. `asyncio.wait_for` cannot be that before
  3.12: it runs its argument in a task of its own, so the task asyncio.run made is still
  waiting inside wait_for when the scenario samples the loop, and the sample listed it. pytest
  printed it as `<Task finished ... coro=<wait_for() done ...>` because it reprs the list after
  asyncio.run has returned, by which time it had finished. 3.12 rebuilt wait_for on
  asyncio.timeout, which awaits in the caller's own task, so a box on 3.14 never saw it, and CI
  on 3.11 failed 27 tests in five rails on it (2026-10-17). `asyncio.timeout` (3.11+) is what
  bounds the run here, so it is one task on every version this tree supports.
* `others()` is every task on the running loop that has not finished, but the one asking. It is
  sampled where it is called, and that has to be inside the loop: asyncio.run cancels each task
  still pending and runs the loop until every one has finished before it returns, so a list
  filtered for unfinished tasks after asyncio.run returned is empty whatever the code under test
  left running. Nor does it wait for the loop to settle: a read cancelled and not awaited is
  still pending when the wrapper returns, and that is the defect these tests are there to catch.

Stdlib only. tools/tests/test_loop_tasks.py holds both halves, a task truly left running among
them, and finds any suite that samples the loop from inside wait_for again.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def others() -> list[asyncio.Task]:
    """Every task on the running loop that has not finished, except the one calling."""
    me = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


def run_alone(coro: Coroutine[Any, Any, T], timeout: float = 10) -> T:
    """`asyncio.run(coro)`, failing with TimeoutError after `timeout` seconds, with `coro` run
    in the loop's one task (not in a task of its own, as wait_for runs it before 3.12)."""
    async def bounded() -> T:
        async with asyncio.timeout(timeout):
            return await coro
    return asyncio.run(bounded())
