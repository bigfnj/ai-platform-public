"""The NVIDIA budget on the EXPENSIVE door: ``/ws/rag`` with ``backend: "nim"``.

The 1-token probe was rate limited; the real generation path was not. Each question there is a
streamed answer of up to 800 tokens on the deployment's key, and the 2026-10-08 audit drove 50
of them from one caller with 0 refusals. The probe's limiter was also keyed on X-Platform-User,
which a sibling container talking to the rail directly can set to anything: ten probes with the
name rotated per call were ten 200s.

So two budgets, and each test below holds one of them:
  * per caller, so one person cannot spend everybody's allowance;
  * one ceiling for the whole rail, keyed on nothing the caller sends, charged by every door
    (answers, the toggle's probe, the weekly check), so rotating the name buys nothing.

Both are charged BEFORE the work, so a refused question costs neither an NVIDIA call nor the
shared GPU's retrieval embed. Hermetic: nim.chat_stream and corpora.retrieve are counters, and
``no_outbound`` (via ``api``) fails anything that would really leave the process.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ai_playground import broker, config, corpora, nim
from ai_playground.api import app as appmod
from conftest import StubWS, ws_endpoint


@pytest.fixture
def rag(api, monkeypatch):
    """The real /ws/rag handler with NVIDIA, the broker stream and retrieval all counted."""
    calls = {"nim": 0, "local": 0, "retrieve": 0, "probe": 0}

    async def fake_nim_stream(messages, *, max_tokens=800):
        calls["nim"] += 1
        yield "tok"

    async def fake_local_stream(model, messages, options=None, requester=None, on_backend=None):
        calls["local"] += 1
        yield "tok"

    def fake_retrieve(cid, q, k, owner):
        calls["retrieve"] += 1
        return [{"source": "a.md", "score": 0.9, "text": "context"}]

    async def fake_probe():
        calls["probe"] += 1

    monkeypatch.setattr(nim, "API_KEY", "fake-key-never-sent")
    monkeypatch.setattr(nim, "chat_stream", fake_nim_stream)
    monkeypatch.setattr(nim, "probe", fake_probe)
    monkeypatch.setattr(broker, "chat_stream", fake_local_stream)
    monkeypatch.setattr(corpora, "retrieve", fake_retrieve)
    ep = ws_endpoint(api, "/ws/rag")

    def ask(user: str, backend: str = "nim", req: dict | None = None) -> list[dict]:
        frame = req if req is not None else {"question": "what is NIM?", "corpus": 1,
                                             "backend": backend}
        ws = StubWS(headers={"x-platform-user": user}, inbox=[frame])
        asyncio.run(asyncio.wait_for(ep(ws), timeout=5))
        return ws.sent

    return ask, calls, TestClient(api)


def _types(frames: list[dict]) -> list[str]:
    return [f["type"] for f in frames]


def test_one_caller_is_stopped_at_their_own_budget(rag, monkeypatch):
    """The audit's exact probe, shrunk: past the per-caller limit the answer is refused."""
    ask, calls, _ = rag
    monkeypatch.setattr(appmod._NIM_CHAT_LIMIT, "limit", 3)
    for _ in range(3):
        assert _types(ask("owner-one")) == ["sources", "token", "done"]
    refused = ask("owner-one")
    assert _types(refused) == ["error"], refused
    assert "NVIDIA NIM answers for you" in refused[0]["message"], refused
    assert "Retry in" in refused[0]["message"], "a refusal must say when to come back"
    assert calls["nim"] == 3, "the refused question still reached NVIDIA"


def test_a_refused_question_does_no_work_at_all(rag, monkeypatch):
    """Charged before retrieval, so the refusal does not even embed the question on the GPU."""
    ask, calls, _ = rag
    monkeypatch.setattr(appmod._NIM_CHAT_LIMIT, "limit", 1)
    ask("owner-one")
    ask("owner-one")
    assert calls == {"nim": 1, "local": 0, "retrieve": 1, "probe": 0}


def test_another_caller_keeps_their_own_allowance(rag, monkeypatch):
    ask, calls, _ = rag
    monkeypatch.setattr(appmod._NIM_CHAT_LIMIT, "limit", 1)
    ask("owner-one")
    assert _types(ask("owner-one")) == ["error"]
    assert _types(ask("bob")) == ["sources", "token", "done"]
    assert calls["nim"] == 2


def test_rotating_the_name_does_not_get_past_the_rail_wide_ceiling(rag, monkeypatch):
    """The hole a per-caller key cannot close on its own: a fresh name per question."""
    ask, calls, _ = rag
    monkeypatch.setattr(appmod._NIM_CHAT_LIMIT, "limit", 5)
    monkeypatch.setattr(appmod._NIM_GLOBAL_LIMIT, "limit", 4)
    answered = refused = 0
    for i in range(10):
        frames = ask(f"sibling-{i}")
        if _types(frames) == ["sources", "token", "done"]:
            answered += 1
        else:
            assert _types(frames) == ["error"], frames
            assert "for this whole rail" in frames[0]["message"], frames
            refused += 1
    assert (answered, refused) == (4, 6)
    assert calls["nim"] == 4


def test_the_probe_draws_on_the_same_ceiling(rag, monkeypatch):
    """Ten probes with the name rotated per call were ten 200s. They now share the ceiling with
    the answers, so the toggle cannot be the way around it."""
    ask, calls, client = rag
    monkeypatch.setattr(appmod._NIM_GLOBAL_LIMIT, "limit", 3)
    ask("owner-one")                                    # one answer spends one
    codes = [client.post("/api/nim/probe", headers={"X-Platform-User": f"sibling-{i}"}).status_code
             for i in range(4)]
    assert codes == [200, 200, 429, 429], codes
    assert calls["probe"] == 2 and calls["nim"] == 1


def test_the_weekly_check_draws_on_the_same_ceiling(rag, monkeypatch):
    ask, calls, client = rag
    monkeypatch.setattr(appmod._NIM_GLOBAL_LIMIT, "limit", 1)
    ask("owner-one")
    resp = client.post("/api/maintenance/nim-check",
                       headers={"X-Platform-User": "system", "X-Platform-Admin": "1"})
    assert resp.status_code == 429, resp.text
    assert calls["probe"] == 0


def test_local_generation_is_not_charged(rag, monkeypatch):
    """The budget is NVIDIA's, not the demo's: the local GPU path stays unmetered."""
    ask, calls, _ = rag
    monkeypatch.setattr(appmod._NIM_CHAT_LIMIT, "limit", 1)
    monkeypatch.setattr(appmod._NIM_GLOBAL_LIMIT, "limit", 1)
    for _ in range(5):
        assert _types(ask("owner-one", backend="local")) == ["sources", "token", "done"]
    assert calls["local"] == 5 and calls["nim"] == 0
    assert _types(ask("owner-one")) == ["sources", "token", "done"], "local answers spent it"


def test_the_budgets_are_configurable_and_process_wide():
    """Discoverable next to the other rail knobs, and module-level so an app rebuild cannot
    refill a bucket."""
    assert appmod._NIM_CHAT_LIMIT.limit == config.NIM_CHAT_LIMIT >= 1
    assert appmod._NIM_GLOBAL_LIMIT.limit == config.NIM_GLOBAL_LIMIT >= 1
    assert config.NIM_CHAT_WINDOW_SECONDS > 0 and config.NIM_GLOBAL_WINDOW_SECONDS > 0
    assert config.NIM_GLOBAL_LIMIT >= config.NIM_CHAT_LIMIT, \
        "a ceiling below one caller's allowance makes the per-caller budget meaningless"


@pytest.mark.parametrize("frame, why", [
    (["not", "an", "object"], "expected a JSON object"),
    ({"question": "q", "corpus": "abc", "backend": "local"}, "corpus must be a number"),
])
def test_a_malformed_request_gets_an_error_frame_not_a_dead_socket(rag, frame, why):
    """Parsed inside the try now: these used to raise between accept() and the handler's try,
    so the socket closed with no frame and the demo waited on nothing."""
    ask, calls, _ = rag
    frames = ask("owner-one", req=frame)
    assert frames == [{"type": "error", "message": why}]
    assert calls["retrieve"] == 0
