"""Whose Claude subscription may answer an SMB partner question, and how long the rail waits.

The broker answers a chat call from a platform owner's own Claude CLI subscription when the
call's `requester` names an owner and its @role is switched on there (services/broker README,
and "Streaming" for /v1/chat/stream). Here the person waiting is the viewer asking about their
own customer situation, so the viewer is named on the answer role and on nothing else. What
these pin:

* the facade keeps a name only on the answer role (`broker._cli_requester`, which reads
  `config.RAG_MODEL`), buffered and streamed, and leaves the field off the wire when there is
  none;
* `/api/ask`, `/ws/ask` and `/ws/scenario` name the viewer; standalone dev (no user) and the
  boot warm name nobody;
* every scenario pass names the viewer, a prefilled one included (`generate._pass`): the
  broker's CLI path emulates a prefill and answers with its continuation, as Ollama does;
* `/ws/scenario` keeps the socket alive while a pass is silent (`generate._with_heartbeat`),
  through a build longer than the gateway's 600 s read timeout, which is why it is the only
  route that builds a package (the buffered `/api/scenario/generate` is retired);
* the answer chip reads Claude for exactly the viewers whose question would;
* a named call waits the rail's whole broker timeout, which outlasts the broker's CLI timeout
  plus a local answer. The broker's LIVE `cli.timeout_s` is compared with the deployed one
  (compose's SMB_PARTNER_BROKER_TIMEOUT, 1320 s) by the fleet smoke check (maintainer
  tooling, not in the public snapshot); here the code default is compared with the broker's.

Offline (conftest's no-network shim stays under every fake), neutral account names.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from smb_partner import broker, config, generate
from smb_partner.api import create_api

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER, "X-Platform-Admin": "0"}
MSGS = [{"role": "user", "content": "q"}]
PRACTICE = "grow-your-practice"
RETAIL = "retail-chain"

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
    streamed. Replaces conftest's no-network `httpx` for this test only."""
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
    for model in ("@embed", "@gemini-cx-rag", "qwen2.5:3b"):
        broker.chat(model, MSGS, requester=OWNER)
        _drain(model, requester=OWNER)
    assert carried(wire["bodies"]) == ["ABSENT"] * 6


def test_the_guard_follows_the_configured_answer_role(wire, monkeypatch):
    """SMB_PARTNER_RAG_MODEL repoints the answer slot; the name follows it, read at call time."""
    monkeypatch.setattr(config, "RAG_MODEL", "@smb-alt")
    broker.chat("@smb-alt", MSGS, requester=OWNER)
    broker.chat("@smb-partner-rag", MSGS, requester=OWNER)
    assert carried(wire["bodies"]) == [OWNER, "ABSENT"]


def test_the_boot_warm_names_nobody(wire):
    broker.warm(config.RAG_MODEL)
    assert carried(wire["bodies"]) == ["ABSENT"]


# --- the routes --------------------------------------------------------------------------


@pytest.fixture()
def client(fake_broker):
    return TestClient(create_api(), raise_server_exceptions=False)


def _practice_expectation(who) -> list:
    """Per pass of the practice set, in order: `who`, prefilled or not."""
    prefills = [prefill for *_rest, prefill in generate.PASSES_BY_SET["practice"]]
    # Not vacuous: the set has prefilled passes and one that is not, and both are named.
    assert any(prefills) and not all(prefills), prefills
    return [who] * len(prefills)


def _prefilled(stream: dict) -> bool:
    """Whether the call ends in the pass's prefill, a trailing assistant turn."""
    return stream["messages"][-1]["role"] == "assistant"


def test_the_buffered_ask_names_the_viewer(client, fake_broker):
    r = client.post("/api/ask", json={"question": "seats?"}, headers=MINE)
    assert r.status_code == 200, r.text
    assert [c["requester"] for c in fake_broker.chats] == [OWNER]


def test_the_streamed_ask_names_the_viewer(client, fake_broker):
    with client.websocket_connect("/ws/ask", headers=MINE) as ws:
        ws.send_text(json.dumps({"question": "seats?"}))
        while ws.receive_json()["type"] != "done":
            pass
    assert [s["requester"] for s in fake_broker.streams] == [OWNER]


def test_a_package_names_the_viewer_on_every_pass_prefilled_or_not(fake_broker):
    """The prefilled passes carry the name beside their prefill: the broker emulates it on the
    CLI path, so the owner's whole package can reach Claude, not only its one plain pass."""
    run = asyncio.run(generate.generate_package(PRACTICE, {}, requester=OWNER))
    assert run["outputs"]
    assert [s["requester"] for s in fake_broker.streams] == _practice_expectation(OWNER)
    assert [_prefilled(s) for s in fake_broker.streams] == [
        bool(prefill) for *_rest, prefill in generate.PASSES_BY_SET["practice"]]


def test_a_customer_package_is_all_prefilled_and_names_the_viewer_on_every_pass(fake_broker):
    """Every customer-set pass opens on a required prefix, and every one carries the name."""
    asyncio.run(generate.generate_package(RETAIL, {}, requester=OWNER))
    assert fake_broker.streams and all(_prefilled(s) for s in fake_broker.streams)
    assert [s["requester"] for s in fake_broker.streams] == [OWNER] * len(fake_broker.streams)


def test_the_buffered_scenario_route_is_retired(client, fake_broker):
    """It ran four passes inside one buffered request, behind the gateway's 600 s read timeout,
    which one pass waiting up to BROKER_CLI_TIMEOUT for the CLI plus three local ones could
    outlast: the browser saw a gateway error while the build finished. Nothing but tests
    called it (both builders use /ws/scenario), so it is gone, and nothing runs for a caller
    who still tries it."""
    r = client.post("/api/scenario/generate", json={"scenario_id": PRACTICE, "answers": {}},
                    headers=MINE)
    assert r.status_code == 404, r.text
    assert fake_broker.streams == [] and fake_broker.embeds == []


def test_the_scenario_socket_names_the_viewer(client, fake_broker):
    with client.websocket_connect("/ws/scenario", headers=MINE) as ws:
        ws.send_text(json.dumps({"scenario_id": PRACTICE, "answers": {}}))
        while ws.receive_json()["type"] != "package":
            pass
    assert [s["requester"] for s in fake_broker.streams] == _practice_expectation(OWNER)


def test_standalone_dev_names_nobody(client, fake_broker, monkeypatch):
    monkeypatch.setenv("PLATFORM_STANDALONE", "1")
    assert client.post("/api/ask", json={"question": "seats?"}).status_code == 200
    with client.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question": "seats?"}))
        while ws.receive_json()["type"] != "done":
            pass
    with client.websocket_connect("/ws/scenario") as ws:
        ws.send_text(json.dumps({"scenario_id": PRACTICE, "answers": {}}))
        while ws.receive_json()["type"] != "package":
            pass
    named = [c["requester"] for c in fake_broker.chats + fake_broker.streams]
    assert len(named) == 1 + 1 + 4 and set(named) == {None}, named


# --- the scenario socket while a pass is silent --------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def test_a_silent_pass_keeps_the_scenario_socket_alive_with_pings(fake_broker, monkeypatch):
    """A pass naming an owner on a role switched to the CLI can be silent until
    BROKER_CLI_TIMEOUT before its first token or fallback (a cold load did the same), far past
    the ~100 s an idle public WebSocket survives at the edge, and /ws/scenario relays every
    event as a frame. Scaled down: a 0.01 s beat against a 0.2 s silence."""
    def slow_stream(model, messages, **kw):
        async def gen():
            await asyncio.sleep(0.2)
            yield "late"
        return gen()

    monkeypatch.setattr(broker, "chat_stream", slow_stream)
    monkeypatch.setattr(generate, "HEARTBEAT_S", 0.01)
    rec = Recorder()
    asyncio.run(generate.generate_package(PRACTICE, {}, rec, requester=OWNER))
    events = [e for e, _ in rec.events]
    first_token = events.index("token")
    assert events[first_token - 3:first_token] == ["ping"] * 3, events[:first_token + 1]
    assert ("ping", {}) in rec.events


def test_a_package_built_with_no_socket_sends_no_ping(fake_broker, monkeypatch):
    """`generate_package` without an `emit` (no route builds one so since the buffered route
    was retired, but the function keeps the option) has nobody to ping: a silent pass waits."""
    def slow_stream(model, messages, **kw):
        async def gen():
            await asyncio.sleep(0.05)
            yield "late"
        return gen()

    monkeypatch.setattr(broker, "chat_stream", slow_stream)
    monkeypatch.setattr(generate, "HEARTBEAT_S", 0.01)
    package = asyncio.run(generate.generate_package(PRACTICE, {}, requester=OWNER))
    assert "errors" not in package
    assert package["outputs"]["partner_center"] == "late"


#: The gateway's read timeout on a buffered request (its relay client's 600 s), which a package
#: build could outlast once a pass waited on the CLI: why the buffered route was retired.
GATEWAY_READ_S = 600.0
#: How long an idle public WebSocket survives at the edge (Cloudflare cuts one at ~100 s).
EDGE_IDLE_S = 100.0


def test_a_build_longer_than_the_gateways_read_timeout_still_delivers_its_package(
        client, fake_broker, monkeypatch):
    """The socket is the answer to the buffered route's limit. Time is counted in heartbeats:
    the first pass stays silent until the client has received enough pings to cover more than
    600 s of the production heartbeat (31 of 20 s), and then the package arrives whole. A ping
    at least every HEARTBEAT_S also keeps the socket inside the edge's idle cut."""
    beat = generate.HEARTBEAT_S
    assert beat < EDGE_IDLE_S
    needed = int(GATEWAY_READ_S // beat) + 1
    released = threading.Event()
    real = broker.chat_stream
    held: list = []

    def stream(model, messages, **kw):
        gen = real(model, messages, **kw)
        if held:
            return gen
        held.append(model)

        async def silent_first():
            # A safety, not the release: a socket that never pings must fail here, not hang.
            give_up = time.monotonic() + 15
            while not released.is_set() and time.monotonic() < give_up:
                await asyncio.sleep(0.001)
            async for tok in gen:
                yield tok
        return silent_first()

    monkeypatch.setattr(broker, "chat_stream", stream)
    monkeypatch.setattr(generate, "HEARTBEAT_S", 0.002)
    pings = 0
    with client.websocket_connect("/ws/scenario", headers=MINE) as ws:
        ws.send_text(json.dumps({"scenario_id": PRACTICE, "answers": {}}))
        frame = ws.receive_json()
        while frame["type"] not in ("package", "error"):
            if frame["type"] == "ping" and not released.is_set():
                pings += 1
                if pings >= needed:
                    released.set()
            frame = ws.receive_json()
    assert pings >= needed, pings
    assert frame["type"] == "package", frame
    package = frame["package"]
    assert "errors" not in package
    assert all(package["outputs"][key] for key, *_ in generate.PASSES_BY_SET["practice"])
    assert [s["requester"] for s in fake_broker.streams] == _practice_expectation(OWNER)


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
    (SMB_PARTNER_BROKER_TIMEOUT), asserted at the transport so a shorter timeout on either
    path fails."""
    broker.chat(config.RAG_MODEL, MSGS, requester=OWNER)
    _drain(config.RAG_MODEL, requester=OWNER)
    assert wire["timeouts"] == [broker.DEFAULT_TIMEOUT, broker.DEFAULT_TIMEOUT]


def test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer():
    src = Path(broker.__file__).read_text(encoding="utf-8")
    (default,) = re.findall(r'"SMB_PARTNER_BROKER_TIMEOUT", "([0-9.]+)"', src)
    assert float(default) > _broker_cli_timeout_default() + LOCAL_ANSWER_S, default


# --- the chip ----------------------------------------------------------------------------


@pytest.fixture()
def chips(client, fake_broker, monkeypatch):
    cli = {"ready": True, "owners": [OWNER], "roles": ["smb-partner-rag", "embed"], "active": []}
    real = fake_broker.status
    monkeypatch.setattr(broker, "status", lambda: {**real(), "cli": cli})

    def read(headers):
        r = client.get("/api/capabilities", headers=headers)
        assert r.status_code == 200, r.text
        return {m["slot"]: m["backend"] for m in r.json()["models"]}

    read.cli = cli  # type: ignore[attr-defined]
    return read


def test_an_owner_sees_reasoning_on_claude_and_nothing_else(chips):
    """`retrieval` stays local although @embed is in the list: embeddings never reach the CLI."""
    assert chips(MINE) == {"reasoning": "claude", "retrieval": "ollama"}


def test_a_non_owner_sees_ollama(chips):
    assert chips({"X-Platform-User": OTHER})["reasoning"] == "ollama"


def test_a_role_off_the_allowlist_reads_ollama(chips):
    chips.cli["roles"] = ["embed"]
    assert chips(MINE)["reasoning"] == "ollama"
