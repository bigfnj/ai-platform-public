"""Whose Claude subscription may answer the RAG demo, and how long the rail waits for it.

The broker answers a chat call from a platform owner's own Claude CLI subscription when the
call's `requester` names an owner and its @role is switched on there (services/broker README,
and "Streaming" for /v1/chat/stream). Here the person waiting is the viewer asking a question
over a corpus they can read, so the viewer is named on the RAG demo's own generation role and
on nothing else. What these pin:

* the facade keeps a name only on the generation role (`broker._cli_requester`), buffered and
  streamed, and leaves the field off the wire when there is none;
* `/api/rag/ask` and `/ws/rag`'s local branch name the viewer; the NIM branch never reaches the
  broker; standalone dev (no user) and the Embedding Lab's draft (`bench/describe.py`, on the
  shared `@chat` class role) name nobody;
* `/ws/rag` keeps the socket alive while a CLI call is silent (`_stream_with_heartbeat`) and
  says who answered (`backend` frames, from the broker's leading `cli` / `cli_fallback`);
* the generation chip reads Claude for exactly the viewers whose question would;
* a named call waits the rail's whole broker timeout, which outlasts the broker's CLI timeout
  plus a local answer. The broker's LIVE `cli.timeout_s` is compared with it by the fleet smoke
  check (maintainer tooling, not in the public snapshot), from a registry its own tests hold
  equal to this rail's source; here it is compared with the broker's code default.

Offline, neutral account names.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_playground import broker, config, corpora, nim
from ai_playground.api import app as appmod
from ai_playground.bench import describe
from conftest import StubWS, ws_endpoint

OWNER = "owner-one"
OTHER = "other-two"
MSGS = [{"role": "user", "content": "q"}]
HITS = [{"source": "a.md", "score": 0.9, "text": "context"}]

#: What a local answer may take after a CLI failure: a cold load and one reply. The figure
#: tools/rail_smoke.py's CLI_LOCAL_ANSWER_S allows.
LOCAL_ANSWER_S = 120.0


def carried(bodies) -> list:
    assert bodies, "no call reached the facade, so nothing was asserted"
    return [b.get("requester", "ABSENT") for b in bodies]


# --- the facade --------------------------------------------------------------------------


@pytest.fixture()
def wire(monkeypatch):
    """Every buffered /v1/chat body the facade sends, and the timeout it sends it with."""
    sent: list[tuple[dict, float]] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "ok"}}

    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002 - httpx's name
        sent.append((json, timeout))
        return _Resp()

    monkeypatch.setattr(broker.httpx, "post", fake_post)
    return sent


class _Recording(httpx.AsyncBaseTransport):
    """Answers every streamed request with one content frame and records its JSON body."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, content=b'{"message":{"content":"ok"}}\n{"done":true}\n')


@pytest.fixture()
def stream_wire(monkeypatch):
    """(recorded stream bodies, AsyncClient timeouts) for the real chat_stream."""
    transport = _Recording()
    timeouts: list = []

    class _Httpx:
        HTTPError = httpx.HTTPError
        HTTPStatusError = httpx.HTTPStatusError
        # The buffered path's call, looked up at call time, so `wire` still records it.
        post = staticmethod(lambda *a, **kw: httpx.post(*a, **kw))

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 - mirrors httpx
            timeouts.append(kwargs.get("timeout"))
            return httpx.AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(broker, "httpx", _Httpx)
    return transport.bodies, timeouts


def _drain(model, **kw) -> list[str]:
    async def go() -> list[str]:
        return [t async for t in broker.chat_stream(model, MSGS, **kw)]
    return asyncio.run(go())


def test_the_generation_role_carries_the_requester(wire):
    broker.chat(None, MSGS, requester=OWNER)
    broker.chat(broker.DEFAULT_MODEL, MSGS, requester=OWNER)
    assert carried([b for b, _ in wire]) == [OWNER, OWNER]


def test_no_requester_means_none_on_the_wire(wire):
    broker.chat(None, MSGS)
    broker.chat(None, MSGS, requester="")
    assert carried([b for b, _ in wire]) == ["ABSENT", "ABSENT"]


def test_a_foreign_role_never_carries_the_name(wire):
    """`@chat` is the Embedding Lab's draft role, a class role other rails share."""
    for model in ("@chat", "@another-rail", "nemotron-3-nano:4b"):
        broker.chat(model, MSGS, requester=OWNER)
    assert carried([b for b, _ in wire]) == ["ABSENT"] * 3


def test_the_stream_carries_the_requester_on_its_own_role_only(stream_wire):
    bodies, _ = stream_wire
    assert _drain(None, requester=OWNER) == ["ok"]
    _drain("@chat", requester=OWNER)
    _drain(None)
    assert carried(bodies) == [OWNER, "ABSENT", "ABSENT"]


# --- the routes --------------------------------------------------------------------------


@pytest.fixture()
def calls(api, monkeypatch):
    """The real app's broker calls, recorded: `chat` and `chat_stream` with their kwargs."""
    seen: dict[str, list[dict]] = {"chat": [], "stream": [], "nim": []}

    def fake_chat(model, messages, **kw):
        seen["chat"].append({"model": model, **kw})
        return "an answer [1]"

    async def fake_stream(model, messages, **kw):
        seen["stream"].append({"model": model, **kw})
        yield "tok"

    async def fake_nim(messages, *, max_tokens=800):
        seen["nim"].append({})
        yield "cloud"

    monkeypatch.setattr(broker, "chat", fake_chat)
    monkeypatch.setattr(broker, "chat_stream", fake_stream)
    monkeypatch.setattr(nim, "chat_stream", fake_nim)
    monkeypatch.setattr(corpora, "retrieve", lambda cid, q, k, owner: HITS)
    return seen


def _ws(api, headers: dict, backend: str = "local") -> list[dict]:
    ws = StubWS(headers=headers, inbox=[{"question": "what is NIM?", "corpus": 1,
                                         "backend": backend}])
    asyncio.run(asyncio.wait_for(ws_endpoint(api, "/ws/rag")(ws), timeout=5))
    return ws.sent


def test_the_buffered_ask_names_the_viewer(api, calls):
    r = TestClient(api).post("/api/rag/ask", json={"question": "what?", "corpus": 1},
                             headers={"X-Platform-User": OWNER})
    assert r.status_code == 200, r.text
    assert [c.get("requester") for c in calls["chat"]] == [OWNER]
    assert calls["chat"][0]["model"] == config.CHAT_MODEL


def test_the_streamed_ask_names_the_viewer(api, calls):
    frames = _ws(api, {"x-platform-user": OWNER})
    assert [f["type"] for f in frames] == ["sources", "token", "done"], frames
    assert [c.get("requester") for c in calls["stream"]] == [OWNER]
    assert calls["stream"][0]["model"] == config.CHAT_MODEL


def test_a_nim_question_never_reaches_the_broker(api, calls, monkeypatch):
    monkeypatch.setattr(nim, "API_KEY", "fake-key-never-sent")
    frames = _ws(api, {"x-platform-user": OWNER}, backend="nim")
    assert [f["type"] for f in frames] == ["sources", "token", "done"], frames
    assert calls["nim"] and calls["stream"] == [] and calls["chat"] == []


def test_standalone_dev_names_nobody(api, calls, monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", True)
    r = TestClient(api).post("/api/rag/ask", json={"question": "what?", "corpus": 1})
    assert r.status_code == 200, r.text
    frames = _ws(api, {})
    assert [f["type"] for f in frames] == ["sources", "token", "done"], frames
    assert [c.get("requester") for c in calls["chat"] + calls["stream"]] == [None, None]


def test_the_embedding_lab_draft_names_nobody(monkeypatch):
    """A background-ish admin helper on a shared class role: it never carries a name."""
    sent: list[dict] = []

    def fake_post(path, payload, timeout=None):
        sent.append(payload)
        return {"message": {"content": "A small model that finds the right passage quickly."}}

    monkeypatch.setattr(broker, "_post", fake_post)
    monkeypatch.setattr(describe, "_search_repo", lambda name: None)
    describe.describe("bge-small", "bge")
    assert carried(sent) == ["ABSENT"]


# --- the socket while Claude thinks ----------------------------------------------------


def test_the_socket_says_who_answered_before_the_first_token(api, calls, monkeypatch):
    async def claude_stream(model, messages, *, on_backend=None, **kw):
        calls["stream"].append(kw)
        on_backend({"backend": "claude", "model": "claude-haiku-5-5"})
        yield "From "
        yield "Claude."

    monkeypatch.setattr(broker, "chat_stream", claude_stream)
    frames = _ws(api, {"x-platform-user": OWNER})
    assert [f["type"] for f in frames] == ["sources", "backend", "token", "token", "done"]
    assert frames[1] == {"type": "backend", "backend": "claude", "model": "claude-haiku-5-5"}


def test_a_fallback_says_the_local_model_answered(api, calls, monkeypatch):
    async def fell_back(model, messages, *, on_backend=None, **kw):
        on_backend({"backend": "ollama", "reason": "busy"})
        yield "local"

    monkeypatch.setattr(broker, "chat_stream", fell_back)
    frames = _ws(api, {"x-platform-user": OWNER})
    assert frames[1:] == [{"type": "backend", "backend": "ollama", "reason": "busy"},
                          {"type": "token", "text": "local"}, {"type": "done"}]


def test_a_silent_wait_keeps_the_socket_alive_with_pings(api, calls, monkeypatch):
    """A CLI call can be silent until BROKER_CLI_TIMEOUT before its first token or fallback, far
    past the ~100 s an idle public WebSocket survives at the edge. Scaled down: a 0.01 s beat
    against a 0.2 s silence must send pings between the sources and the first token."""
    async def slow(model, messages, **kw):
        await asyncio.sleep(0.2)
        yield "late"

    monkeypatch.setattr(broker, "chat_stream", slow)
    monkeypatch.setattr(appmod, "HEARTBEAT_S", 0.01)
    frames = _ws(api, {"x-platform-user": OWNER})
    types = [f["type"] for f in frames]
    assert types[0] == "sources" and types[-2:] == ["token", "done"], types
    assert set(types[1:-2]) == {"ping"} and len(types[1:-2]) >= 3, types


def test_an_interrupted_answer_ends_in_an_error_frame_after_its_tokens(api, calls, monkeypatch):
    async def interrupted(model, messages, **kw):
        yield "Half an "
        raise broker.BrokerError("the Claude CLI stopped mid-answer (stalled); the text above "
                                 "is incomplete")

    monkeypatch.setattr(broker, "chat_stream", interrupted)
    frames = _ws(api, {"x-platform-user": OWNER})
    assert [f["type"] for f in frames] == ["sources", "token", "error"], frames
    assert "incomplete" in frames[-1]["message"]


# --- the wait ----------------------------------------------------------------------------


def _broker_cli_timeout_default() -> float:
    """BROKER_CLI_TIMEOUT's code default, read from the broker's settings in this tree."""
    for parent in Path(__file__).resolve().parents:
        cfg = parent / "services" / "broker" / "app" / "config.py"
        if cfg.is_file():
            found = re.findall(r"^    cli_timeout: float = ([0-9.]+)$",
                               cfg.read_text(encoding="utf-8").replace("\r\n", "\n"), re.M)
            assert len(found) == 1, found
            return float(found[0])
    pytest.skip("no broker in this tree to read BROKER_CLI_TIMEOUT's default from")


def test_a_named_call_waits_the_whole_broker_timeout(wire, stream_wire):
    """Every CLI failure falls back inside the same broker request, so a named call must wait
    out BROKER_CLI_TIMEOUT plus a local answer. Both paths wait DEFAULT_TIMEOUT
    (AI_PLAYGROUND_BROKER_TIMEOUT; compose passes none, so the code default runs), asserted at
    the transport so a shorter timeout on either path fails."""
    broker.chat(None, MSGS, requester=OWNER)
    _drain(None, requester=OWNER)
    assert [t for _, t in wire] == [broker.DEFAULT_TIMEOUT]
    assert stream_wire[1] == [broker.DEFAULT_TIMEOUT]


def test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer():
    src = Path(broker.__file__).read_text(encoding="utf-8")
    (default,) = re.findall(r'"AI_PLAYGROUND_BROKER_TIMEOUT", "([0-9.]+)"', src)
    assert float(default) > _broker_cli_timeout_default() + LOCAL_ANSWER_S, default


# --- the chip ----------------------------------------------------------------------------


@pytest.fixture()
def chips(api, monkeypatch):
    cli = {"ready": True, "owners": [OWNER], "roles": ["ai-playground", "chat"], "active": []}
    monkeypatch.setattr(broker, "roles", lambda: [
        {"role": "ai-playground", "resolved": "nemotron-3-nano:4b"},
        {"role": "embed", "resolved": "bge-m3"}, {"role": "chat", "resolved": "qwen3:8b"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "nemotron-3-nano:4b"},
                                                   {"name": "bge-m3:latest"},
                                                   {"name": "qwen3:8b"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    client = TestClient(api)

    def read(headers):
        r = client.get("/api/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        return {m["slot"]: m["backend"] for m in r.json()["models"]}

    read.cli = cli  # type: ignore[attr-defined]
    return read


def test_an_owner_sees_generation_on_claude_and_nothing_else(chips):
    """`describe` stays local although its @chat role is switched on: the draft names nobody,
    so its chip must not claim Claude."""
    assert chips({"X-Platform-User": OWNER}) == {"generation": "claude", "embed": "ollama",
                                                 "describe": "ollama"}


def test_a_non_owner_sees_ollama(chips):
    assert chips({"X-Platform-User": OTHER})["generation"] == "ollama"


def test_a_role_off_the_allowlist_reads_ollama(chips):
    chips.cli["roles"] = ["chat"]
    assert chips({"X-Platform-User": OWNER})["generation"] == "ollama"
