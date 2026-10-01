"""FIX 4 — /ws/bench must not hang on a failed worker, nor orphan one on a disconnect.

Two failure modes in the same twenty lines:

  * ``worker()`` had no try/except and only ever enqueued ``progress``/``done``, while the
    reader sat on ``await q.get()`` with no timeout. Any exception in the worker meant a socket
    held open forever with the tab spinning — and engine.run raises readily, since ``rc["model"]``
    is a KeyError raised OUTSIDE its own per-config try. One malformed run-config was enough.
  * An early client disconnect broke the reader out of the loop and returned, leaving the
    benchmark task running: it re-embeds the whole corpus once per model, on the GPU the entire
    platform shares.

Both are driven through the real handler with a stub socket, so the queue bridge and the worker
task are the actual ones — and the engine is stubbed, so no GPU, no broker, no ONNX graph.
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import WebSocketDisconnect

from ai_playground import db
from ai_playground.bench import engine
from conftest import StubWS, make_corpus, make_queryset, ws_endpoint


def _request(cid: int, qsid: int) -> dict:
    return {"corpus": cid, "queryset": qsid, "configs": [{"model": "m", "prompting": "none"}]}


def test_a_failing_worker_becomes_an_error_frame(api, con, monkeypatch):
    """The exact defect: engine.run raises, and the socket has to end rather than block."""
    def boom(*args, **kwargs):
        raise KeyError("model")

    monkeypatch.setattr(engine, "run", boom)
    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")
    ws = StubWS(headers={"x-platform-user": "alice"}, inbox=[_request(cid, qsid)])

    # wait_for IS the assertion for the hang: unfixed, this never returns.
    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5))

    assert [f["type"] for f in ws.sent] == ["meta", "error"]
    assert "KeyError" in ws.sent[-1]["message"]
    assert ws.close_code is not None, "the socket was left open"


def test_a_successful_run_still_ends_in_a_done_frame(api, con, monkeypatch):
    """Guards the over-correction: the error path must not swallow the happy one."""
    monkeypatch.setattr(engine, "run", lambda *a, **kw: [{"id": "m", "metrics": {"R@1": 1.0}}])
    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")
    ws = StubWS(headers={"x-platform-user": "alice"}, inbox=[_request(cid, qsid)])

    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5))

    assert [f["type"] for f in ws.sent] == ["meta", "done"]
    assert ws.sent[-1]["results"] == [{"id": "m", "metrics": {"R@1": 1.0}}]


def test_the_run_is_still_recorded_in_the_history(api, con, monkeypatch):
    """add_run moved into the worker thread with its connection; it still has to happen."""
    monkeypatch.setattr(engine, "run", lambda *a, **kw: [{"id": "m", "metrics": {}}])
    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")
    ws = StubWS(headers={"x-platform-user": "alice"}, inbox=[_request(cid, qsid)])

    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5))

    assert [r["corpus_name"] for r in db.list_runs(con, "alice")] == ["Alices Docs"]


def test_a_disconnected_client_does_not_orphan_the_benchmark(api, con, monkeypatch):
    """The tab closes on the first progress frame; the benchmark must not keep the GPU."""
    started, release = threading.Event(), threading.Event()

    def slow_run(con2, chunks, queries, configs, k, progress, *rest):
        progress("m|none|native", "start")
        started.set()
        release.wait(timeout=5)          # bounded: an orphan must not outlive the test run
        return []

    monkeypatch.setattr(engine, "run", slow_run)

    class Dropped(StubWS):
        async def send_json(self, data: dict) -> None:
            await super().send_json(data)
            if data["type"] == "progress":
                raise WebSocketDisconnect(1001)

    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")
    ws = Dropped(headers={"x-platform-user": "alice"}, inbox=[_request(cid, qsid)])

    async def scenario():
        try:
            await asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5)
            # Sampled the instant the handler returns: an un-cancelled worker is still pending
            # here, and asyncio.all_tasks() only reports tasks that are not done.
            return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        finally:
            release.set()
            await asyncio.sleep(0.2)     # let the thread unwind while the loop is still alive

    leftover = asyncio.run(scenario())

    assert started.is_set(), "the benchmark never started, so this proves nothing"
    assert not leftover, "the benchmark outlived the socket — re-embedding a corpus for nobody"
