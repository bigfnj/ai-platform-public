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

import pytest
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

    runs = db.list_runs(con, "alice")
    assert [r["corpus_name"] for r in runs] == ["Alices Docs"]
    # No top-K is archived any more: the engine never read it, so storing the control's value
    # made two identical runs look different in the history.
    assert runs[0]["k"] is None


def test_a_disconnected_client_does_not_orphan_the_benchmark(api, con, monkeypatch):
    """The tab closes on the first progress frame; the benchmark must not keep the GPU."""
    started, release = threading.Event(), threading.Event()

    def slow_run(con2, chunks, queries, configs, progress=None, **kw):
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


# --- the THREAD, not just the task --------------------------------------------------------
#
# The test above samples asyncio tasks, and a task is the half that was already fine. The run
# itself is a worker thread, which a cancelled task does not stop: the audit's probe closed the
# socket on the first progress frame and watched 5 of 5 configs re-embed the corpus afterwards,
# then the run land in the history. These drive the REAL engine.run over a counting embedder.

class _CountingEmb:
    """Stands in for a model re-embedding the corpus on the GPU. The first embed blocks until
    the handler has given up on its client (the cancel event is set), so whether the NEXT config
    starts is decided by the cancel path and not by how fast the reader happened to react."""
    provider = "broker"

    def __init__(self, embeds: list, gate: dict) -> None:
        self.embeds, self.gate = embeds, gate

    def embed_batch(self, texts, is_query=False):
        self.embeds.append(1)
        if len(self.embeds) == 1 and self.gate.get("hold_first"):
            self.gate["cancel"].wait(timeout=5)
        return [[1.0, 0.0] for _ in texts]

    def embed(self, text, is_query=True):
        return [1.0, 0.0]


def _real_engine_over_counters(monkeypatch, *, hold_first: bool = False):
    """engine.run, unmodified, with the registry answering every model id with a counter.
    `hold_first` makes the first embed wait for the cancel event (the socket test only)."""
    from ai_playground.bench import registry
    embeds: list = []
    gate: dict = {"hold_first": hold_first}
    finished = threading.Event()
    real_run = engine.run

    def spy(*args, **kwargs):
        gate["cancel"] = kwargs.get("cancel")
        try:
            return real_run(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(engine, "run", spy)
    monkeypatch.setattr(registry, "get_spec",
                        lambda con, mid: {"id": mid, "label": mid, "provider": "broker"})
    monkeypatch.setattr(registry, "build_embedder",
                        lambda con, mid, p, d: _CountingEmb(embeds, gate))
    return embeds, gate, finished


def test_a_closed_socket_stops_the_benchmark_thread_between_configs(api, con, monkeypatch):
    embeds, gate, finished = _real_engine_over_counters(monkeypatch, hold_first=True)
    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")

    class Dropped(StubWS):
        async def send_json(self, data: dict) -> None:
            await super().send_json(data)
            if data["type"] == "progress":
                raise WebSocketDisconnect(1001)        # the tab is gone

    req = {"corpus": cid, "queryset": qsid,
           "configs": [{"model": f"m{i}", "prompting": "none"} for i in range(5)]}
    ws = Dropped(headers={"x-platform-user": "alice"}, inbox=[req])
    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5))

    assert finished.wait(timeout=15), "the benchmark thread never finished"
    assert gate["cancel"] is not None and gate["cancel"].is_set(), "nothing told the thread"
    assert len(embeds) == 1, f"{len(embeds)} of 5 configs re-embedded the corpus for nobody"
    assert db.list_runs(con, "alice") == [], "a run nobody saw was written into the history"


def test_a_cancelled_engine_stops_before_the_next_config(monkeypatch):
    """The engine half on its own: set between configs, the next one never starts."""
    embeds, _, _ = _real_engine_over_counters(monkeypatch)
    cancel = threading.Event()
    started: list[str] = []

    def progress(cfg_id: str, phase: str) -> None:
        started.append(f"{cfg_id}:{phase}")
        if phase == "done":
            cancel.set()

    with pytest.raises(engine.Cancelled):
        engine.run(None, [{"source": "a.md", "text": "t"}], [{"q": "q", "targets": ["a.md"]}],
                   [{"model": "m0"}, {"model": "m1"}], progress=progress, cancel=cancel)
    assert started == ["m0|model|native:start", "m0|model|native:done"]
    assert len(embeds) == 1


def test_a_cancelled_engine_starts_no_rerank_row(monkeypatch):
    """The reranked row re-scores every query on the CPU; it is a step of its own and has to
    honour the same event."""
    from ai_playground.bench import registry
    embeds, _, _ = _real_engine_over_counters(monkeypatch)

    class Rerank:
        def score(self, query, docs):
            raise AssertionError("a cancelled run still reranked")

    monkeypatch.setattr(registry, "build_reranker", lambda con, rid: Rerank())
    cancel = threading.Event()

    def progress(cfg_id: str, phase: str) -> None:
        if phase == "done":
            cancel.set()

    with pytest.raises(engine.Cancelled):
        engine.run(None, [{"source": "a.md", "text": "t"}], [{"q": "q", "targets": ["a.md"]}],
                   [{"model": "m0"}], progress=progress, reranker_id="rr", cancel=cancel)


def test_an_uncancelled_run_is_unchanged(monkeypatch):
    """Guards the over-correction: with the event never set, every config still runs."""
    embeds, _, _ = _real_engine_over_counters(monkeypatch)
    rows = engine.run(None, [{"source": "a.md", "text": "t"}], [{"q": "q", "targets": ["a.md"]}],
                      [{"model": f"m{i}"} for i in range(3)], cancel=threading.Event())
    assert [r["model"] for r in rows] == ["m0", "m1", "m2"] and len(embeds) == 3
    assert all("metrics" in r for r in rows), rows


def test_a_malformed_bench_request_gets_an_error_frame(api, con):
    """Parsed inside the handler's try now, like /ws/rag."""
    ws = StubWS(headers={"x-platform-user": "alice"},
                inbox=[{"corpus": "abc", "queryset": 1, "configs": [{"model": "m"}]}])
    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/bench")(ws), timeout=5))
    assert ws.sent == [{"type": "error",
                        "message": "corpus, queryset and rerank_depth must be numbers"}]
