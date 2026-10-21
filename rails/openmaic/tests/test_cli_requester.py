"""OPENMAIC_CLI_REQUESTER: on a single-user box, the app's generation names the box owner.

The broker serves a /v1/chat or /v1/chat/stream call from a platform owner's own Claude CLI
subscription when the call's `requester` names that owner. The shim's caller is the app
container, which carries no platform identity, so the shim reads no name from a call at all. It
names the configured account, and only while that account is the SOLE user the rail's proxy has
seen within OPENMAIC_CLI_LEASE_S (api/proxy.py `sole_user`). What these pin:

* set, every app call on the reasoning role names exactly that account, buffered and streamed,
  whatever the OpenAI `user` field or a `requester` key say;
* a gateway user calling the shim directly (the gateway relays the mount) is named only when it
  IS that account: another user's call names nobody, whatever else it carries, even while the
  ledger holds only the owner (`_requester_for`);
* a model the app picked off the reasoning role names nobody (`broker._cli_requester`);
* another user seen in the lease, the account not seen, or seen too long ago: nobody;
* the shim token still compose's published fallback: nobody, and the log says why, once;
* the proxy's ledger is fed by requests through the real proxy route (from the gateway; no
  other peer's identity reaches it, tests/test_gateway_peer.py);
* the reasoning chip reads Claude for that account exactly when the shim would name it;
* the stream: a comment heartbeat while the broker is silent (a Claude call can say nothing
  until its first token), the broker's `cli` / `cli_fallback` frames skipped, a
  `cli_interrupted` ending reported as an error rather than a finished answer, and a client that
  leaves mid-wait ends the broker read;
* the broker wait outlasts a Claude call and its local fallback, and so does the proxy's wait
  on the app, whose generating requests wait on that same call.

tests/test_no_requester.py is the other side: unset, nobody is named, ever. Offline, neutral
account names.
"""
from __future__ import annotations

import asyncio
import json
import re
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from openmaic_app import broker
from openmaic_app.api import app as app_module
from openmaic_app.api import llm, proxy
from openmaic_app.config import Settings, settings
# What the loop still runs, sampled inside it, and a run that is the loop's one task on every
# Python (tools/tests/loop_tasks.py; conftest puts tools/tests on sys.path).
from loop_tasks import others, run_alone

OWNER = "owner-one"
OTHER = "other-two"
TOKEN = {"Authorization": "Bearer a-synthetic-service-token"}
BODY = {"model": "@openmaic", "messages": [{"role": "user", "content": "a synthetic lesson"}]}
REPO = Path(__file__).resolve().parents[3]

# The app's own calls (the service token, no platform identity) with every place a body could
# name somebody, all naming OTHER: none of it may be what the shim sends. The first names
# nobody, so a check that failed on every case would show there too.
CALLERS = {
    "nobody": (TOKEN, {}),
    "openai-user": (TOKEN, {"user": OTHER}),
    "requester-key": (TOKEN, {"requester": OTHER}),
}

# Another gateway user calling the shim directly: an X-Platform-User reaches the shim only from
# the gateway (tests/test_gateway_peer.py), so it says whose call this is, whatever else the
# call carries: the service token, or the owner's name in the body.
OTHER_USER = {
    "identity": ({"X-Platform-User": OTHER}, {}),
    "identity-and-token": ({**TOKEN, "X-Platform-User": OTHER}, {}),
    "everywhere": ({**TOKEN, "X-Platform-User": OTHER}, {"user": OWNER, "requester": OWNER}),
}


def content(text: str) -> dict:
    return {"model": "m:1", "message": {"role": "assistant", "content": text}, "done": False}


DONE = {"model": "m:1", "message": {"role": "assistant", "content": ""}, "done": True,
        "done_reason": "stop"}
CLI_LEAD = {"cli": {"backend": "claude", "model": "claude-test"}}
CLI_FALLBACK = {"cli_fallback": {"backend": "ollama", "reason": "timeout"}}
INTERRUPTED = {"error": "the Claude CLI stopped mid-answer (stalled); the text above is "
                        "incomplete",
               "done": True, "done_reason": "cli_interrupted",
               "cli_interrupted": {"reason": "stalled", "chars": 4}}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def box(monkeypatch):
    """A box with OPENMAIC_CLI_REQUESTER=owner-one, an empty ledger and a clock the test moves."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", TOKEN["Authorization"][len("Bearer "):])
    monkeypatch.setattr(settings, "cli_requester", OWNER)
    monkeypatch.setattr(settings, "cli_lease_s", 1800.0)
    monkeypatch.setattr(settings, "llm_model", "@openmaic")
    monkeypatch.setattr(proxy, "_seen", {})
    clock = Clock()
    monkeypatch.setattr(proxy, "_clock", clock)
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    return clock


class Wire:
    """broker.py's two ways out, recorded: `_apost` (buffered) and httpx's stream (streamed)."""

    def __init__(self, monkeypatch) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.frames = [content("ok"), DONE]
        wire = self

        async def fake_apost(path, payload, timeout=None):
            wire.sent.append((path, payload))
            return {"model": "m:1", "message": {"role": "assistant", "content": "ok"},
                    "done": True, "done_reason": "stop"}

        class _Resp:
            status_code = 200

            async def aiter_lines(self):
                for f in wire.frames:
                    yield json.dumps(f)

            async def aread(self):
                return b""

        class _Ctx:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *exc):
                return False

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def stream(self, method, url, json=None, headers=None):  # noqa: A002
                wire.sent.append(("/v1/chat/stream", json))
                return _Ctx()

        monkeypatch.setattr(broker, "_apost", fake_apost)
        monkeypatch.setattr(broker, "httpx", types.SimpleNamespace(
            AsyncClient=_Client, HTTPError=httpx.HTTPError,
            HTTPStatusError=httpx.HTTPStatusError))


@pytest.fixture()
def wire(monkeypatch):
    return Wire(monkeypatch)


def complete(headers=TOKEN, stream=False, **extra) -> httpx.Response:
    r = TestClient(llm.shim).post("/v1/chat/completions", headers=headers,
                                  json={**BODY, **extra, "stream": stream})
    assert r.status_code == 200, r.text
    return r


def named(wire) -> list:
    assert wire.sent, "no call reached the broker client, so nothing was asserted"
    return [payload.get("requester", "ABSENT") for _, payload in wire.sent]


# --- who is named ----------------------------------------------------------------------------

@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_set_and_alone_every_app_call_names_exactly_the_configured_account(wire, caller):
    proxy.note_user(OWNER)
    headers, extra = CALLERS[caller]
    complete(headers, **extra)
    r = complete(headers, stream=True, **extra)
    assert "data: [DONE]" in r.text
    assert [path for path, _ in wire.sent] == ["/v1/chat", "/v1/chat/stream"]
    assert named(wire) == [OWNER, OWNER]


@pytest.mark.parametrize("caller", sorted(OTHER_USER))
def test_another_gateway_user_calling_the_shim_directly_names_nobody(wire, caller):
    """The owner alone in the app, so the ledger would let the name on: the call is still not
    the owner's, and is answered under nobody's name, buffered and streamed."""
    proxy.note_user(OWNER)
    headers, extra = OTHER_USER[caller]
    complete(headers, **extra)
    complete(headers, stream=True, **extra)
    assert named(wire) == ["ABSENT", "ABSENT"]


def test_the_configured_account_calling_the_shim_directly_is_named(wire, box):
    """Its own call, through the gateway, while it is the app's only user; not once anyone else
    has used the app inside the lease."""
    own = {"X-Platform-User": OWNER}
    proxy.note_user(OWNER)
    complete(own)
    complete(own, stream=True)
    box.now += 60
    proxy.note_user(OTHER)
    complete(own)
    assert named(wire) == [OWNER, OWNER, "ABSENT"]


def test_a_gateway_account_named_like_the_app_is_not_the_app(wire):
    """`llm_caller` hands back a kind, not a label: an account called "openmaic-app" is a user,
    and with the owner alone in the app its call names nobody."""
    proxy.note_user(OWNER)
    complete({"X-Platform-User": "openmaic-app"})
    assert named(wire) == ["ABSENT"]


def test_a_model_off_the_reasoning_role_names_nobody(wire):
    proxy.note_user(OWNER)
    for model in ("gemma3:4b", "@chat", "@course-builder"):
        complete(model=model)
        complete(stream=True, model=model)
    assert named(wire) == ["ABSENT"] * 6


def test_another_user_in_the_lease_names_nobody(wire, box):
    proxy.note_user(OWNER)
    box.now += 60
    proxy.note_user(OTHER)
    complete()
    complete(stream=True)
    assert named(wire) == ["ABSENT"] * 2


def test_the_configured_account_unseen_names_nobody(wire):
    complete()
    proxy.note_user(OTHER)
    complete(stream=True)
    assert named(wire) == ["ABSENT"] * 2


def test_the_lease_runs_out_both_ways(wire, box):
    """The account's own visit expires, and so does another user's: each by the same lease."""
    proxy.note_user(OWNER)
    box.now += settings.cli_lease_s + 1
    complete()                                  # the owner has not been seen for too long
    proxy.note_user(OTHER)
    box.now += 1
    proxy.note_user(OWNER)
    complete()                                  # the other user is still inside the lease
    box.now += settings.cli_lease_s
    proxy.note_user(OWNER)
    complete()                                  # their visit is over; the owner is alone
    assert named(wire) == ["ABSENT", "ABSENT", OWNER]


PUBLISHED = {"Authorization": f"Bearer {llm.PUBLISHED_SHIM_TOKEN}"}


def test_with_the_published_token_the_shim_names_nobody(wire, chips, monkeypatch):
    """Compose's fallback token is in a public repo, so any container that can reach this rail
    can call the shim exactly as openmaic-app does, and with the setting honoured its calls would
    ride the owner's name. So the owner, alone in the app, is not named, buffered or streamed,
    and the chip says local."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", llm.PUBLISHED_SHIM_TOKEN)
    monkeypatch.setattr(llm, "_published_token_noted", False)
    proxy.note_user(OWNER)
    complete(PUBLISHED)
    complete(PUBLISHED, stream=True)
    assert named(wire) == ["ABSENT", "ABSENT"]
    assert llm.chip_requester(OWNER) is None
    assert chips(OWNER) == {"reasoning": "ollama", "embed": "ollama"}


def test_the_published_token_is_named_in_the_log_once(wire, monkeypatch, caplog):
    """Once per process: the shim asks on every call and the chip on every poll. The account's
    name stays out of the line."""
    monkeypatch.setattr(llm, "_SHIM_TOKEN", llm.PUBLISHED_SHIM_TOKEN)
    monkeypatch.setattr(llm, "_published_token_noted", False)
    proxy.note_user(OWNER)
    with caplog.at_level("WARNING", logger="openmaic.llm"):
        for _ in range(3):
            complete(PUBLISHED)
        llm.chip_requester(OWNER)
    lines = [r.getMessage() for r in caplog.records if "OPENMAIC_SHIM_TOKEN" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "published default" in lines[0] and llm.PUBLISHED_SHIM_TOKEN in lines[0]
    assert OWNER not in lines[0]


def test_unset_the_published_token_is_not_mentioned(wire, monkeypatch, caplog):
    """A box that never set OPENMAIC_CLI_REQUESTER has nothing to be told."""
    monkeypatch.setattr(settings, "cli_requester", "")
    monkeypatch.setattr(llm, "_SHIM_TOKEN", llm.PUBLISHED_SHIM_TOKEN)
    monkeypatch.setattr(llm, "_published_token_noted", False)
    with caplog.at_level("WARNING", logger="openmaic.llm"):
        complete(PUBLISHED)
    assert named(wire) == ["ABSENT"]
    assert not [r for r in caplog.records if "OPENMAIC_SHIM_TOKEN" in r.getMessage()]


def test_sole_user_is_exact():
    proxy.note_user("  ")
    proxy.note_user(None)
    assert proxy._seen == {}
    assert not proxy.sole_user(OWNER, 1800)
    proxy.note_user(OWNER)
    assert proxy.sole_user(OWNER, 1800) and proxy.sole_user(f" {OWNER} ", 1800)
    assert not proxy.sole_user("", 1800) and not proxy.sole_user(None, 1800)
    assert not proxy.sole_user("Owner-One", 1800)      # a case variant is another account
    assert not proxy.sole_user(OWNER, 0)


# --- the ledger is fed by the proxy ----------------------------------------------------------

class _Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        pass


@pytest.fixture()
def through_the_proxy(monkeypatch, gateway):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=[("content-type", "text/plain")], stream=_Body())

    monkeypatch.setattr(proxy, "_client", httpx.AsyncClient(
        base_url="http://openmaic-app:3000", transport=httpx.MockTransport(handler)))
    # The browser's requests in the frame arrive from the gateway (tests/test_gateway_peer.py
    # holds that no other peer's header reaches the ledger).
    with TestClient(app_module.app, client=(gateway.address, 50000)) as client:
        def visit(user: str, path: str = "/api/app/some/page") -> None:
            r = client.get(path, headers={"X-Platform-User": user, "X-Platform-Admin": "0"})
            assert r.status_code == 200, r.text
        yield visit


def test_a_visit_through_the_proxy_is_what_lets_the_name_on(wire, through_the_proxy):
    complete()                                  # nobody has used the app
    through_the_proxy(OWNER)
    complete()                                  # the owner, alone
    through_the_proxy(OTHER, "/logos/mark.png")  # a root asset counts as a visit too
    complete()                                  # someone else is in the app
    assert named(wire) == ["ABSENT", OWNER, "ABSENT"]


# --- the chip --------------------------------------------------------------------------------

@pytest.fixture()
def chips(monkeypatch, gateway):
    cli = {"ready": True, "owners": [OWNER, OTHER], "roles": ["openmaic"], "active": []}
    monkeypatch.setattr(broker, "roles", lambda: [{"role": "openmaic", "resolved": "gemma3:4b"},
                                                  {"role": "embed", "resolved": "bge-m3"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "gemma3:4b"}, {"name": "bge-m3"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    monkeypatch.setattr(settings, "llm_base_url", "")

    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(app_module, "app_reachable", unreachable)
    client = TestClient(app_module.app, client=(gateway.address, 50000))

    def read(user: str) -> dict:
        r = client.get("/api/capabilities", headers={"X-Platform-User": user})
        assert r.status_code == 200, r.text
        return {m["slot"]: m["backend"] for m in r.json()["models"]}

    return read


def test_the_configured_account_alone_sees_the_reasoning_slot_on_claude(chips):
    proxy.note_user(OWNER)
    assert chips(OWNER) == {"reasoning": "claude", "embed": "ollama"}


def test_another_viewer_sees_ollama_even_as_a_broker_owner(chips):
    """OTHER is a CLI owner at the broker, but the shim never names them."""
    proxy.note_user(OTHER)
    assert chips(OTHER) == {"reasoning": "ollama", "embed": "ollama"}


def test_the_configured_account_not_alone_sees_ollama(chips):
    proxy.note_user(OWNER)
    proxy.note_user(OTHER)
    assert chips(OWNER) == {"reasoning": "ollama", "embed": "ollama"}


def test_unset_the_chip_reads_ollama_for_everyone(chips, monkeypatch):
    monkeypatch.setattr(settings, "cli_requester", "")
    proxy.note_user(OWNER)
    assert chips(OWNER) == {"reasoning": "ollama", "embed": "ollama"}


# --- the stream ------------------------------------------------------------------------------

def _run_sse(fake_stream, monkeypatch, **kw) -> list[str]:
    monkeypatch.setattr(broker, "chat_stream", fake_stream)

    async def run():
        return [e async for e in llm._sse("@openmaic", [], {}, "30m", **kw)]

    return asyncio.run(run())


def _payloads(events: list[str]) -> list[dict]:
    return [json.loads(e[len("data: "):]) for e in events
            if e.startswith("data: ") and e.strip() != "data: [DONE]"]


def test_a_silent_broker_gets_a_heartbeat_and_the_text_is_unchanged(monkeypatch):
    monkeypatch.setattr(llm, "HEARTBEAT_S", 0.05)

    async def slow(model, messages, *, options=None, fmt=None, think=None, keep_alive="30m",
                   requester=None):
        await asyncio.sleep(0.4)
        yield content("Newton")
        yield DONE

    events = _run_sse(slow, monkeypatch)
    assert events[0].startswith("data: ")       # the role chunk goes out before the wait
    first_text = next(i for i, e in enumerate(events) if "Newton" in e)
    pings = [i for i, e in enumerate(events) if e == llm.PING]
    assert pings and all(i < first_text for i in pings), events
    assert llm.PING.startswith(":") and llm.PING.endswith("\n\n")
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in _payloads(events))
    assert text == "Newton"


def test_a_prompt_broker_gets_no_heartbeat(monkeypatch):
    events = _run_sse(_frames_of([content("a"), DONE]), monkeypatch)
    assert llm.PING not in events


def _frames_of(frames):
    async def fake(model, messages, *, options=None, fmt=None, think=None, keep_alive="30m",
                   requester=None):
        for f in frames:
            yield f
    return fake


def test_the_brokers_cli_frames_are_skipped(monkeypatch):
    for lead in ([CLI_LEAD], [CLI_FALLBACK, {"fallback": {"model": "gemma3:4b"}}]):
        events = _run_sse(_frames_of([*lead, content("Newton "), content("said"), DONE]),
                          monkeypatch)
        payloads = _payloads(events)
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in payloads)
        assert text == "Newton said"
        assert payloads[-1]["choices"][0]["finish_reason"] == "stop"


def test_a_cli_interruption_is_an_sse_error_not_a_finished_answer(monkeypatch):
    events = _run_sse(_frames_of([CLI_LEAD, content("part"), INTERRUPTED]), monkeypatch)
    payloads = _payloads(events)
    errors = [p["error"]["message"] for p in payloads if "error" in p]
    assert errors and "stopped mid-answer" in errors[0]
    assert not any(c.get("finish_reason") for p in payloads for c in p.get("choices", []))
    assert events[-1] == "data: [DONE]\n\n"


def test_the_stream_passes_the_shims_name_to_the_broker(monkeypatch):
    seen = {}

    async def fake(model, messages, *, options=None, fmt=None, think=None, keep_alive="30m",
                   requester=None):
        seen["requester"] = requester
        yield DONE

    _run_sse(fake, monkeypatch, requester=OWNER)
    assert seen == {"requester": OWNER}


def test_a_client_that_leaves_mid_wait_ends_the_broker_read(monkeypatch):
    """The read in flight is cancelled and the broker stream unwinds; nothing is left running."""
    monkeypatch.setattr(llm, "HEARTBEAT_S", 0.02)
    state = {"closed": False}

    async def silent(model, messages, *, options=None, fmt=None, think=None, keep_alive="30m",
                     requester=None):
        try:
            await asyncio.sleep(30)
            yield DONE
        finally:
            state["closed"] = True

    monkeypatch.setattr(broker, "chat_stream", silent)

    async def run():
        gen = llm._sse("@openmaic", [], {}, "30m")
        first = await gen.__anext__()           # the role chunk
        ping = await gen.__anext__()            # the broker is silent
        await gen.aclose()                      # the client went away
        pending = others()
        return first, ping, pending

    first, ping, pending = run_alone(run(), 10)
    assert first.startswith("data: ") and ping == llm.PING
    assert state["closed"] and pending == []


def test_an_anyio_cancellation_mid_wait_still_ends_the_broker_read(monkeypatch):
    """How Starlette ends the response of a client that hung up: an anyio cancel scope, whose
    cancellation is delivered again at every await until the task leaves it. The read in flight
    must still be ended and the broker stream closed, not left running behind a RuntimeError."""
    import anyio

    monkeypatch.setattr(llm, "HEARTBEAT_S", 30.0)
    state = {"closed": False}

    async def silent(model, messages, *, options=None, fmt=None, think=None, keep_alive="30m",
                     requester=None):
        try:
            await asyncio.sleep(30)
            yield DONE
        finally:
            # Unwinding takes a few turns of the loop, as closing a real HTTP response does;
            # an unshielded wait for it is cancelled again before it ends.
            for _ in range(5):
                await asyncio.sleep(0)
            state["closed"] = True

    monkeypatch.setattr(broker, "chat_stream", silent)

    async def run():
        gen = llm._sse("@openmaic", [], {}, "30m")
        await gen.__anext__()                   # the role chunk
        with anyio.move_on_after(0.1):
            await gen.__anext__()               # waits on the silent broker; cancelled here
        await gen.aclose()
        return others()

    pending = run_alone(run(), 10)
    assert state["closed"] and pending == []


# --- the wait --------------------------------------------------------------------------------

def test_the_broker_wait_outlasts_a_cli_call_and_its_local_fallback(monkeypatch):
    """BROKER_CLI_TIMEOUT (300 s on the owner's box) plus a local answer (120 s): the code
    default clears it, compose raises it to the platform's broker ceiling, and the calls use it.
    The live comparison against the broker's cli.timeout_s is the maintainers' smoke sweep's."""
    monkeypatch.delenv("OPENMAIC_BROKER_TIMEOUT", raising=False)
    assert Settings(_env_file=None).broker_timeout == 600.0
    assert broker.DEFAULT_TIMEOUT == settings.broker_timeout
    for compose in COMPOSE_FILES:
        if not compose.is_file():
            pytest.skip(f"no {compose.name} beside this rail (an extracted rail)")
        found = re.findall(r"^ +OPENMAIC_BROKER_TIMEOUT: \$\{OPENMAIC_BROKER_TIMEOUT:-"
                           r"([0-9.]+)\}\s*$", compose.read_text(encoding="utf-8"), re.M)
        assert found == ["1320"], (compose.name, found)


#: BROKER_CLI_TIMEOUT on the owner's box, and what a local answer after it may take (the figure
#: tools/rail_smoke.py's CLI_LOCAL_ANSWER_S allows).
CLI_TIMEOUT_S, LOCAL_ANSWER_S = 300.0, 120.0


def test_the_app_wait_outlasts_a_cli_call_and_its_local_fallback(monkeypatch):
    """The proxy's wait on the app. A generating request in the app waits on the shim, and the
    shim on a broker call that may wait out the Claude CLI and then a local answer. At 300 s it
    equalled the owner's BROKER_CLI_TIMEOUT, so the proxy cut the request as the broker fell
    back. The code default and compose's both clear the two together, and the proxy's client
    uses the setting."""
    monkeypatch.delenv("OPENMAIC_APP_TIMEOUT", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.app_timeout > CLI_TIMEOUT_S + LOCAL_ANSWER_S
    assert fresh.app_timeout == 1320.0
    monkeypatch.setattr(proxy, "_client", None)
    try:
        assert proxy.get_client().timeout.read == settings.app_timeout
    finally:
        asyncio.run(proxy.close_client())
    for compose in COMPOSE_FILES:
        if not compose.is_file():
            pytest.skip(f"no {compose.name} beside this rail (an extracted rail)")
        found = re.findall(r"^ +OPENMAIC_APP_TIMEOUT: \$\{OPENMAIC_APP_TIMEOUT:-"
                           r"([0-9.]+)\}\s*$", compose.read_text(encoding="utf-8"), re.M)
        assert found == ["1320"], (compose.name, found)


COMPOSE_FILES = (REPO / "deploy" / "docker-compose.yml",
                 REPO / "deploy" / "installer" / "docker-compose.installer.yml")


def test_compose_hands_the_setting_to_the_rail():
    """Compose passes a container only what its `environment:` names, so a deploy/.env value
    compose does not list would change nothing. Blank by default: nobody is named."""
    for compose in COMPOSE_FILES:
        if not compose.is_file():
            pytest.skip(f"no {compose.name} beside this rail (an extracted rail)")
        found = re.findall(r"^ +OPENMAIC_CLI_REQUESTER: \$\{OPENMAIC_CLI_REQUESTER:-\}\s*$",
                           compose.read_text(encoding="utf-8"), re.M)
        assert len(found) == 1, compose.name


def test_the_published_token_is_the_one_compose_falls_back_to():
    """The guard compares against a literal, and compose holds the real fallback, once for the
    rail and once inside the app's OLLAMA_API_KEY. Drifted apart, the guard would watch for a
    token nobody runs with and let the one that ships through."""
    for compose in COMPOSE_FILES:
        if not compose.is_file():
            pytest.skip(f"no {compose.name} beside this rail (an extracted rail)")
        found = re.findall(r"\$\{OPENMAIC_SHIM_TOKEN:-([^}]*)\}",
                           compose.read_text(encoding="utf-8"))
        assert found == [llm.PUBLISHED_SHIM_TOKEN] * 2, (compose.name, found)
