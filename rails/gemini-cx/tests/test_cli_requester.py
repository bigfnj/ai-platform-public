"""Whose Claude subscription may answer a Gemini CX question, and how long the rail waits.

The broker answers a chat call from a platform owner's own Claude CLI subscription when the
call's `requester` names an owner and its @role is switched on there (services/broker README,
and "Streaming" for /v1/chat/stream). Here the person waiting is the viewer asking a deck or
free-text question over the shipped corpus, so the viewer is named on the answer role and on
nothing else. What these pin:

* the facade keeps a name only on the answer role (`broker._cli_requester`, which reads
  `config.RAG_MODEL`), buffered and streamed, and leaves the field off the wire when there is
  none;
* `/api/ask` and `/ws/ask` name the viewer; standalone dev (no user) and the boot warm name
  nobody;
* the answer chip, on `/api/capabilities` and on `/api/health`, reads Claude for exactly the
  viewers whose question would;
* a named call waits the rail's whole broker timeout, which outlasts the broker's CLI timeout
  plus a local answer. The broker's LIVE `cli.timeout_s` is compared with the deployed one
  (compose's GEMINI_CX_BROKER_TIMEOUT, 1320 s) by the fleet smoke check (maintainer tooling,
  not in the public snapshot); here the code default is compared with the broker's.

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

from gemini_cx import broker, config, store, voice
from gemini_cx.api import create_api

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER, "X-Platform-Admin": "0"}
MSGS = [{"role": "user", "content": "q"}]

#: What a local answer may take after a CLI failure: a cold load and one reply. The figure
#: tools/rail_smoke.py's CLI_LOCAL_ANSWER_S allows.
LOCAL_ANSWER_S = 120.0


def carried(bodies) -> list:
    assert bodies, "no call reached the facade, so nothing was asserted"
    return [b.get("requester", "ABSENT") for b in bodies]


# --- the facade --------------------------------------------------------------------------


class _Recording(httpx.AsyncBaseTransport):
    def __init__(self, bodies: list) -> None:
        self.bodies = bodies

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, content=b'{"message":{"content":"ok"}}\n{"done":true}\n')


@pytest.fixture()
def wire(monkeypatch):
    """Every body the real facade sends, with the timeout it sends it under, buffered or
    streamed. Nothing leaves the process."""
    seen: dict[str, list] = {"bodies": [], "timeouts": []}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "ok"}}

    class _Httpx:
        HTTPError = httpx.HTTPError
        HTTPStatusError = httpx.HTTPStatusError

        @staticmethod
        def post(url, json=None, timeout=None, headers=None):  # noqa: A002 - httpx's name
            seen["bodies"].append(json)
            seen["timeouts"].append(timeout)
            return _Resp()

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 - mirrors httpx
            seen["timeouts"].append(kwargs.get("timeout"))
            return httpx.AsyncClient(transport=_Recording(seen["bodies"]), **kwargs)

    monkeypatch.setattr(broker, "httpx", _Httpx)
    return seen


def _drain(model, **kw) -> list[str]:
    async def go() -> list[str]:
        return [t async for t in broker.chat_stream(model, MSGS, **kw)]
    return asyncio.run(go())


def test_the_answer_role_carries_the_requester(wire):
    broker.chat(config.RAG_MODEL, MSGS, requester=OWNER)
    assert _drain(config.RAG_MODEL, requester=OWNER) == ["ok"]
    assert carried(wire["bodies"]) == [OWNER, OWNER]


def test_no_requester_means_none_on_the_wire(wire):
    broker.chat(config.RAG_MODEL, MSGS)
    _drain(config.RAG_MODEL, requester="")
    assert carried(wire["bodies"]) == ["ABSENT", "ABSENT"]


def test_a_foreign_role_never_carries_the_name(wire):
    for model in ("@embed", "@smb-partner-rag", "qwen3:4b"):
        broker.chat(model, MSGS, requester=OWNER)
        _drain(model, requester=OWNER)
    assert carried(wire["bodies"]) == ["ABSENT"] * 6


def test_the_guard_follows_the_configured_answer_role(wire, monkeypatch):
    """GEMINI_CX_RAG_MODEL repoints the answer slot; the name follows it, read at call time."""
    monkeypatch.setattr(config, "RAG_MODEL", "@gecx-alt")
    broker.chat("@gecx-alt", MSGS, requester=OWNER)
    broker.chat("@gemini-cx-rag", MSGS, requester=OWNER)
    assert carried(wire["bodies"]) == [OWNER, "ABSENT"]


def test_the_boot_warm_names_nobody(wire):
    broker.warm(config.RAG_MODEL)
    assert carried(wire["bodies"]) == ["ABSENT"]


# --- the routes and the chip ---------------------------------------------------------------


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """The real app over a throwaway index, with the broker's reads and calls faked. `calls`
    records each chat call's model and requester."""
    calls: list[dict] = []
    cli = {"ready": True, "owners": [OWNER], "roles": ["gemini-cx-rag", "embed"], "active": []}

    def chat(model, messages, **kw):
        calls.append({"model": model, "requester": kw.get("requester"), "streamed": False})
        return "GECX pricing is not published [1]."

    async def chat_stream(model, messages, **kw):
        calls.append({"model": model, "requester": kw.get("requester"), "streamed": True})
        yield "GECX "
        yield "answer."

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    monkeypatch.setattr(broker, "chat", chat)
    monkeypatch.setattr(broker, "chat_stream", chat_stream)
    monkeypatch.setattr(broker, "embed", lambda texts, *, model: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "gemini-cx-rag", "resolved": "qwen3:4b"},
                                                  {"role": "embed", "resolved": "bge-m3"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "qwen3:4b"}, {"name": "bge-m3:latest"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    monkeypatch.setattr(broker, "media_enabled", lambda: False)
    monkeypatch.setattr(broker, "warm", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(voice, "_probe", None)
    client = TestClient(create_api(), raise_server_exceptions=False)
    client.calls = calls  # type: ignore[attr-defined]
    client.cli = cli      # type: ignore[attr-defined]
    yield client
    store.reload_matrix()


def test_the_buffered_ask_names_the_viewer(app):
    r = app.post("/api/ask", json={"question": "is it priced?"}, headers=MINE)
    assert r.status_code == 200, r.text
    assert app.calls == [{"model": config.RAG_MODEL, "requester": OWNER, "streamed": False}]


def test_the_streamed_ask_names_the_viewer(app):
    with app.websocket_connect("/ws/ask", headers=MINE) as ws:
        ws.send_text(json.dumps({"question": "is it priced?"}))
        while ws.receive_json()["type"] != "done":
            pass
    assert app.calls == [{"model": config.RAG_MODEL, "requester": OWNER, "streamed": True}]


def test_standalone_dev_names_nobody(app, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert app.post("/api/ask", json={"question": "is it priced?"}).status_code == 200
    with app.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question": "is it priced?"}))
        while ws.receive_json()["type"] != "done":
            pass
    assert [c["requester"] for c in app.calls] == [None, None]


def _backends(models: list[dict]) -> dict[str, str]:
    return {m["slot"]: m["backend"] for m in models}


def test_an_owner_sees_reasoning_on_claude_and_nothing_else(app):
    """`retrieval` stays local although @embed is in the list: embeddings never reach the CLI."""
    r = app.get("/api/capabilities", headers=MINE)
    assert r.status_code == 200, r.text
    assert _backends(r.json()["models"]) == {"reasoning": "claude", "retrieval": "ollama"}


def test_the_health_chips_judge_the_same_viewer(app):
    mine = app.get("/api/health", headers=MINE).json()["models"]["models"]
    theirs = app.get("/api/health", headers={"X-Platform-User": OTHER}).json()["models"]["models"]
    assert _backends(mine)["reasoning"] == "claude"
    assert _backends(theirs)["reasoning"] == "ollama"


def test_a_non_owner_sees_ollama(app):
    r = app.get("/api/capabilities", headers={"X-Platform-User": OTHER})
    assert _backends(r.json()["models"])["reasoning"] == "ollama"


def test_a_role_off_the_allowlist_reads_ollama(app):
    app.cli["roles"] = ["embed"]
    r = app.get("/api/capabilities", headers=MINE)
    assert _backends(r.json()["models"])["reasoning"] == "ollama"


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


def test_a_named_call_waits_the_whole_broker_timeout(wire):
    """Every CLI failure falls back inside the same broker request, so a named call must wait
    out BROKER_CLI_TIMEOUT plus a local answer. Both paths wait DEFAULT_TIMEOUT
    (GEMINI_CX_BROKER_TIMEOUT), asserted at the transport so a shorter timeout on either path
    fails."""
    broker.chat(config.RAG_MODEL, MSGS, requester=OWNER)
    _drain(config.RAG_MODEL, requester=OWNER)
    assert wire["timeouts"] == [broker.DEFAULT_TIMEOUT, broker.DEFAULT_TIMEOUT]


def test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer():
    src = Path(broker.__file__).read_text(encoding="utf-8")
    (default,) = re.findall(r'"GEMINI_CX_BROKER_TIMEOUT", "([0-9.]+)"', src)
    assert float(default) > _broker_cli_timeout_default() + LOCAL_ANSWER_S, default
