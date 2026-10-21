"""The Claude CLI on a STREAMED chat (/v1/chat/stream): token by token for an owner's @role, a
fallback before the first token, an interruption after it, and a disconnect that takes the CLI's
whole process tree.

WHO may reach the CLI is test_cli_routing's subject and is unchanged here: the stream asks the
same `cli_decision`, through the same slot, re-decision and hung-up checks. What is new is the
stream's own contract with a rail:

  * a failure BEFORE the first content frame is a fallback, exactly as on /v1/chat. The rail reads
    a leading `cli_fallback` frame (no `message.content`, so every reader skips it; never keyed
    "error", on which every reader raises), then the local model's stream;
  * a failure AFTER it cannot be taken back, because the rail has already shown the text. The
    stream ends with an `error` line marked `cli_interrupted`, and no local answer starts: a
    second, different answer appended to half of the first would read as one answer;
  * a caller that disconnects mid-stream takes the CLI's process tree with it, through the real
    app, on both ASGI spec versions Starlette distinguishes.

Settings are REAL `BrokerSettings`, for the reason test_cli_routing gives. Role names are the
neutral ones the rest of the broker's suites use.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import cli_backend
from app.broker import Broker, ClientGone
from app.cli_backend import CliUnavailable
from app.config import BrokerSettings

OWNERS = "owner-one,owner-two"
ROLES = "iep,edu"
SWITCHES = "cli-roles.json"
_Q = [{"role": "user", "content": "q"}]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_ambient_cli_env(monkeypatch):
    """A BROKER_CLI_* variable in the developer's own environment would silently change what
    these tests assert."""
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_EXE", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES",
                "BROKER_CLI_OAUTH_TOKEN", "BROKER_CLI_OAUTH_TOKEN_FILE", "BROKER_CLI_WORKDIR",
                "BROKER_CLI_MODEL", "BROKER_CLI_ROLES_FILE", "BROKER_CLI_CONCURRENCY",
                "BROKER_CLI_TIMEOUT", "BROKER_CLI_TEMP_ROOT"):
        monkeypatch.delenv(key, raising=False)


_REAL_KILLPG = cli_backend._killpg


@pytest.fixture(autouse=True)
def group_kills(monkeypatch):
    """POSIX group kills are recorded, not sent, unless a test starts REAL children and takes
    `real_group_kill` (as in cli_backend_fakes): a fake process's made-up pid must never reach a
    real killpg."""
    calls: list[int] = []
    monkeypatch.setattr(cli_backend, "_killpg", calls.append)
    return calls


@pytest.fixture
def real_group_kill(monkeypatch, group_kills):
    monkeypatch.setattr(cli_backend, "_killpg", _REAL_KILLPG)


def _settings(tmp_path, **kw) -> BrokerSettings:
    exe = tmp_path / "claude.exe"
    if not exe.exists():
        exe.write_bytes(b"")
    kw.setdefault("cli_enabled", True)
    kw.setdefault("cli_exe", str(exe))
    kw.setdefault("cli_owners", OWNERS)
    kw.setdefault("cli_roles", ROLES)
    kw.setdefault("cli_workdir", str(tmp_path))
    return BrokerSettings(**kw)


def _broker(tmp_path, **kw) -> Broker:
    return Broker(_settings(tmp_path, **kw))


def _frame(text: str, model: str = "claude-cli") -> dict:
    return {"model": model, "created_at": "2026-10-13T00:00:00.000000Z",
            "message": {"role": "assistant", "content": text}, "done": False}


_USAGE = {"backend": "claude", "input_tokens": 9, "output_tokens": 3}
_DONE = {"model": "claude-cli", "created_at": "2026-10-13T00:00:01.000000Z",
         "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
         "cli": _USAGE}
_CLI_OK = {"model": "claude-cli", "message": {"role": "assistant", "content": "from cli"},
           "done": True, "done_reason": "stop", "cli": _USAGE}
_LEAD = {"cli": {"backend": "claude", "model": "claude-cli"}}


def _streaming(tmp_path, *, texts=("Hel", "lo"), raises_at=None, exc=None, chat_raises=None,
               **kw):
    """A Broker whose Ollama and CLI are both fakes. The CLI's `chat_stream` yields one content
    frame per text and then the done frame, raising `exc` once `raises_at` frames are out; its
    `chat` (the buffered client) answers "from cli" or raises `chat_raises`. `seen` records
    what ran, what the GPU gate held while the CLI streamed, and the CLI block while the local
    fallback streamed."""
    b = _broker(tmp_path, **kw)
    seen: dict = {"ollama": 0, "cli_stream": 0, "cli_chat": 0, "evicted": 0, "gate": [],
                  "cli_kw": [], "ollama_messages": [], "at_local": []}

    async def _cli_stream(messages, *, options=None, think=None):
        seen["cli_stream"] += 1
        seen["cli_kw"].append({"options": options, "think": think})
        for i, text in enumerate(texts):
            if raises_at == i:
                raise exc
            seen["gate"].append(b.gate.jobs())
            yield _frame(text)
        if raises_at == len(texts):
            raise exc
        yield copy.deepcopy(_DONE)

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        seen["cli_chat"] += 1
        seen["chat_format"] = format
        seen["chat_messages"] = copy.deepcopy(messages)
        if chat_raises is not None:
            raise chat_raises
        return copy.deepcopy(_CLI_OK)

    async def _ollama_stream(model, messages, **_kw):
        seen["ollama"] += 1
        seen["ollama_messages"].append(copy.deepcopy(messages))
        state = b.cli_state()
        seen["at_local"].append({"active": state["active"],
                                 "waiting": state["concurrency"]["waiting"],
                                 "slots_free": b._cli_slots._value})
        yield {"model": model, "message": {"role": "assistant", "content": "from "}, "done": False}
        yield {"model": model, "message": {"role": "assistant", "content": "ollama"},
               "done": False}
        yield {"model": model, "message": {"role": "assistant", "content": ""}, "done": True}

    async def _route(ref, *, substitute):
        return None, "local-model", None

    async def _evict(keep=None):
        seen["evicted"] += 1
        return []

    b.ollama = SimpleNamespace(chat_stream=_ollama_stream)
    b.route = _route
    b._evict_other_heavy = _evict
    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat, chat_stream=_cli_stream)
    return b, seen


async def _drain(agen) -> list[dict]:
    return [f async for f in agen]


def _stream(b, messages=None, model="@iep", **kw) -> list[dict]:
    return _run(_drain(b.chat_stream(model, copy.deepcopy(messages or _Q), **kw)))


def _text(frames) -> str:
    return "".join(((f.get("message") or {}).get("content") or "") for f in frames)


def _contents(frames) -> list[str]:
    return [f["message"]["content"] for f in frames if (f.get("message") or {}).get("content")]


# --- served by the CLI, token by token ---------------------------------------------------------

def test_an_owner_stream_is_served_token_by_token_by_the_cli(tmp_path):
    """The lead frame says who answered before the first token (a rail can label the bubble),
    the text arrives as the CLI writes it, and the done frame carries the CLI's usage, the way a
    buffered answer's `cli` block does. No GPU gate is held at any point, and Ollama never runs."""
    b, seen = _streaming(tmp_path)
    frames = _stream(b, requester="owner-one")
    assert frames[0] == _LEAD
    assert _contents(frames) == ["Hel", "lo"]
    assert frames[-1]["done"] is True and frames[-1]["cli"] == _USAGE and len(frames) == 4
    assert seen["ollama"] == 0 and seen["evicted"] == 0
    assert seen["gate"] == [[], []], "the CLI stream held the GPU gate"
    assert not any("error" in f or "cli_fallback" in f for f in frames)
    state = b.cli_state()
    assert state["calls"] == 1 and state["fallbacks"]["count"] == 0
    assert state["last_by_role"]["@iep"]["ok"] is True
    assert state["active"] == []


def test_options_and_think_are_handed_to_the_streaming_client(tmp_path):
    """So the done frame can report what the CLI could not honour (`cli.dropped`)."""
    b, seen = _streaming(tmp_path)
    _stream(b, requester="owner-one", options={"temperature": 0.2}, think=False)
    assert seen["cli_kw"] == [{"options": {"temperature": 0.2}, "think": False}]


def test_a_non_owner_stream_never_reaches_the_cli_and_carries_no_cli_keys(tmp_path):
    b, seen = _streaming(tmp_path)
    frames = _stream(b, requester="someone-else")
    assert _text(frames) == "from ollama" and seen["cli_stream"] == 0
    assert not any("cli" in f or "cli_fallback" in f for f in frames)
    assert b.cli_state()["refusals"] == {"not_owner": 1}


def test_background_streaming_work_never_reaches_the_cli(tmp_path):
    b, seen = _streaming(tmp_path)
    frames = _stream(b)
    assert _text(frames) == "from ollama" and seen["cli_stream"] == 0
    assert b.cli_state()["refusals"] == {"no_requester": 1}


# --- a failure before the first token: a fallback, as on /v1/chat -----------------------------

def test_a_failure_before_the_first_token_falls_back_transparently(tmp_path):
    """Nothing reached the rail yet, so the local model's whole answer can replace it. Announced
    by a leading frame no reader shows (no message.content) and none raises on (no "error")."""
    b, seen = _streaming(tmp_path, raises_at=0, exc=CliUnavailable("rate_limited", "usage limit"))
    frames = _stream(b, requester="owner-one")
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "rate_limited"}}
    assert _text(frames) == "from ollama" and frames[-1]["done"] is True
    assert not any("error" in f for f in frames)
    assert not any("cli" in f for f in frames)
    state = b.cli_state()
    assert state["fallbacks"] == {"count": 1, "last": {"requested": "@iep",
                                                       "reason": "rate_limited",
                                                       "detail": "usage limit"}}
    assert state["last_by_role"]["@iep"]["reason"] == "rate_limited"
    assert state["interruptions"]["count"] == 0
    assert (seen["cli_stream"], seen["ollama"]) == (1, 1)
    assert seen["ollama_messages"] == [_Q]


@pytest.mark.parametrize("exc", [ValueError("embedded null byte"), TypeError("bad usage"),
                                 NotImplementedError("selector loop"), KeyError("event")],
                         ids=["ValueError", "TypeError", "NotImplementedError", "KeyError"])
def test_any_exception_before_the_first_token_falls_back(tmp_path, exc):
    """"Every failure is a fallback" holds for the stream too, not only for the failures the
    client anticipated."""
    b, seen = _streaming(tmp_path, raises_at=0, exc=exc)
    frames = _stream(b, requester="owner-one")
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": type(exc).__name__}}
    assert _text(frames) == "from ollama" and seen["ollama"] == 1


def test_the_slot_is_back_before_the_local_fallback_streams(tmp_path):
    """A call that fell back never holds a CLI slot, or shows as running on the subscription,
    while Ollama answers it."""
    b, seen = _streaming(tmp_path, raises_at=0, exc=CliUnavailable("timeout", "no text"),
                         cli_concurrency=1)
    _stream(b, requester="owner-one")
    assert seen["at_local"] == [{"active": [], "waiting": 0, "slots_free": 1}]


def test_a_delegated_role_falls_back_to_its_upstream_and_never_sends_it_the_requester(tmp_path):
    """The CLI fails, the role is delegated: the remote box streams the answer, after the same
    leading marker. `requester` names an owner of THIS box and is never forwarded."""
    b, seen = _streaming(tmp_path, raises_at=0, exc=CliUnavailable("timeout", "no text"))
    heard: list = []

    class _Up:
        name = "remote"

        async def chat_stream(self, model, messages, **kw):
            heard.append(kw)
            yield {"model": model, "message": {"role": "assistant", "content": "from remote"},
                   "done": False}
            yield {"model": model, "done": True}

    async def _route(ref, *, substitute):
        return _Up(), "remote-model", None

    b.route = _route
    frames = _stream(b, requester="owner-one")
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "timeout"}}
    assert _text(frames) == "from remote" and seen["ollama"] == 0
    assert heard and all("requester" not in kw and "client_gone" not in kw for kw in heard)


# --- a failure after the first token: an interruption, never a second answer --------------------

#: Text a model might stream: rail content, which the broker keeps out of its log and status.
_SAID = "Wombats dig"


def test_a_failure_after_the_first_token_ends_the_stream_with_an_error(tmp_path, capsys):
    """The rail has shown the first words already. A local answer appended to them would read as
    one answer that changed its mind, so none starts: the stream ends with a line every reader
    raises on (`error`), marked `cli_interrupted` so a rail can say what happened. Counted as an
    interruption, never as a fallback, and the role's record says `interrupted`."""
    b, seen = _streaming(tmp_path, texts=(_SAID, " deep"), raises_at=1,
                         exc=CliUnavailable("stalled", "nothing for 60s once the answer began"))
    frames = _stream(b, requester="owner-one")
    assert frames[0] == _LEAD and _contents(frames) == [_SAID]
    last = frames[-1]
    assert last["done"] is True and last["done_reason"] == "cli_interrupted"
    assert last["cli_interrupted"] == {"reason": "stalled", "chars": len(_SAID)}
    assert "stopped mid-answer (stalled)" in last["error"] and "incomplete" in last["error"]
    assert seen["ollama"] == 0
    state = b.cli_state()
    assert state["interruptions"] == {"count": 1, "last": {"requested": "@iep",
                                                           "reason": "stalled",
                                                           "chars": len(_SAID)}}
    assert state["fallbacks"] == {"count": 0, "last": None}
    assert state["calls"] == 0
    entry = state["last_by_role"]["@iep"]
    assert entry == {"ok": False, "at": entry["at"], "reason": "interrupted", "cause": "stalled",
                     "requester": "owner-one", "model": ""}
    assert state["active"] == []
    log = capsys.readouterr().err
    assert "CLI INTERRUPTED: @iep" in log and "CLI FALLBACK" not in log
    for where in (log, json.dumps(state)):
        assert _SAID not in where


def test_an_exception_after_the_first_token_is_an_interruption_whatever_its_type(tmp_path):
    b, seen = _streaming(tmp_path, raises_at=2, exc=RuntimeError("transport"))
    frames = _stream(b, requester="owner-one")
    assert frames[-1]["cli_interrupted"] == {"reason": "RuntimeError", "chars": 5}
    assert seen["ollama"] == 0


# --- format and images: the buffered client, inside the stream ----------------------------------

@pytest.mark.parametrize("fmt", ["json", {"type": "object"}], ids=["json", "schema"])
def test_a_format_goes_to_the_buffered_client_inside_the_stream(tmp_path, fmt):
    """A JSON answer is validated whole (bare json) or enforced by the CLI (a schema), so it is
    asked for buffered and sent as one content frame. Nothing is yielded before it succeeds, so
    every failure of it is still a transparent fallback."""
    b, seen = _streaming(tmp_path)
    frames = _stream(b, requester="owner-one", format=fmt)
    assert (seen["cli_chat"], seen["cli_stream"]) == (1, 0) and seen["chat_format"] == fmt
    assert frames[0] == _LEAD and _contents(frames) == ["from cli"]
    assert frames[-1]["done"] is True and frames[-1]["cli"] == _USAGE and len(frames) == 3


def test_images_go_to_the_buffered_client_inside_the_stream(tmp_path):
    msgs = [{"role": "user", "content": "what is this", "images": ["iVBORw0KGgo="]}]
    b, seen = _streaming(tmp_path)
    frames = _stream(b, msgs, requester="owner-one")
    assert (seen["cli_chat"], seen["cli_stream"]) == (1, 0)
    assert seen["chat_messages"] == msgs and _contents(frames) == ["from cli"]


def test_a_failed_buffered_call_inside_the_stream_falls_back(tmp_path):
    b, seen = _streaming(tmp_path, chat_raises=CliUnavailable("bad_json", "prose"))
    frames = _stream(b, requester="owner-one", format="json")
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "bad_json"}}
    assert _text(frames) == "from ollama"


def test_a_prefill_the_cli_path_cannot_emulate_falls_back_before_anything_is_spawned(
        tmp_path, monkeypatch):
    """Through the REAL client: an assistant turn last that is not plain text (here one carrying
    an image) cannot be emulated, so the call is refused before a process starts, and the local
    model continues it as it was put."""
    spawned: list = []

    async def fake_exec(*argv, **_kw):
        spawned.append(argv)
        raise AssertionError("a process was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    b, seen = _streaming(tmp_path)
    real = Broker._cli_client.__get__(b)
    b._cli_client = real
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "{", "images": ["iVBORw0KGgo="]}]
    frames = _stream(b, msgs, requester="owner-one")
    assert spawned == []
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "prefill_unsupported"}}
    assert seen["ollama_messages"] == [msgs]


#: The Scenario Builder's "next move" pass, as smb-partner-enablement sends it.
_PREFILLED = [{"role": "system", "content": "You coach partners."},
              {"role": "user", "content": "q"},
              {"role": "assistant", "content": "Your next move: "}]


def _transcript_cli(*deltas: str) -> str:
    """A stand-in for `claude -p` that writes a partial-message transcript, one text delta per
    entry and the result line joining them, then exits."""
    lines = [json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": d}}})
        for d in deltas]
    lines.append(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                             "result": "".join(deltas),
                             "usage": {"input_tokens": 5, "output_tokens": 3}}))
    return f"import sys\nsys.stdout.write({chr(10).join(lines) + chr(10)!r})\n"


def _real_client(b, monkeypatch, script: str) -> dict:
    """`b`'s CLI calls run the REAL client, spawning `script`; returns what the client asked of
    the argv (its system prompt)."""
    seen: dict = {}

    def build_argv(self, system, **_kw):
        seen["system"] = system
        return [sys.executable, "-c", script]

    monkeypatch.setattr(cli_backend.ClaudeCliClient, "build_argv", build_argv)
    b._cli_client = Broker._cli_client.__get__(b)
    return seen


def test_a_streamed_prefill_reaches_the_cli_and_the_rail_gets_the_continuation(tmp_path,
                                                                               monkeypatch):
    """The Scenario Builder joins `prefill + answer`, as Ollama's continuation reads. Through the
    REAL client: the CLI is asked to begin with the opening, the opening it writes back is held
    and never sent, and the lead frame comes only with the first words after it."""
    b, seen = _streaming(tmp_path)
    asked = _real_client(b, monkeypatch, _transcript_cli("Your next", " move: Lead", " with it."))
    frames = _stream(b, copy.deepcopy(_PREFILLED), requester="owner-one")
    assert frames[0] == _LEAD
    assert _contents(frames) == ["Lead", " with it."]
    assert frames[-1]["done"] is True and seen["ollama"] == 0
    assert "Your next move:" in asked["system"] and asked["system"].startswith("You coach")
    state = b.cli_state()
    assert state["calls"] == 1 and state["fallbacks"]["count"] == 0


def test_a_streamed_reply_that_ignores_the_prefill_falls_back_with_nothing_shown(
        tmp_path, monkeypatch, capsys):
    """The CLI answered afresh. Nothing of it reached the rail (no lead frame, no content), so
    the local model's continuation replaces it whole, announced as an ordinary fallback,
    `prefill_mismatch`, and Ollama is sent the turn to continue, as asked. Neither text is in
    the log or on status."""
    b, seen = _streaming(tmp_path)
    _real_client(b, monkeypatch, _transcript_cli("Sure! ", "Here is the move."))
    frames = _stream(b, copy.deepcopy(_PREFILLED), requester="owner-one")
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "prefill_mismatch"}}
    assert not any("cli" in f for f in frames)
    assert _text(frames) == "from ollama" and seen["ollama_messages"] == [_PREFILLED]
    state = b.cli_state()
    assert state["fallbacks"]["count"] == 1
    assert state["fallbacks"]["last"]["reason"] == "prefill_mismatch"
    assert state["last_by_role"]["@iep"]["reason"] == "prefill_mismatch"
    assert state["interruptions"]["count"] == 0
    log = capsys.readouterr().err
    for where in (log, json.dumps(state)):
        assert "Sure!" not in where and "Your next move" not in where


# --- the slot, the re-decision and a caller who hung up: the buffered rules, unchanged -------

def test_a_stream_that_cannot_get_a_slot_in_time_falls_back_busy(tmp_path):
    b, seen = _streaming(tmp_path, cli_concurrency=1, cli_timeout=0.3)

    async def scenario():
        await b._cli_slots.acquire()            # somebody else's call holds the only slot
        try:
            t0 = time.monotonic()
            frames = await asyncio.wait_for(
                _drain(b.chat_stream("@iep", _Q, requester="owner-one")), 10)
            return frames, time.monotonic() - t0
        finally:
            b._cli_slots.release()

    frames, took = _run(scenario())
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "busy"}}
    assert seen["cli_stream"] == 0 and took < 3


def test_a_queued_stream_is_decided_again_when_it_gets_its_slot(tmp_path):
    b, seen = _streaming(tmp_path, cli_concurrency=1, cli_timeout=30,
                         cli_roles_file=str(tmp_path / SWITCHES))
    b.settings.set_cli_role("chat", True)

    async def scenario():
        await b._cli_slots.acquire()
        call = asyncio.ensure_future(_drain(b.chat_stream("@chat", _Q, requester="owner-one")))
        for _ in range(100):
            if b.cli_state()["concurrency"]["waiting"] == 1:
                break
            await asyncio.sleep(0.02)
        b.settings.set_cli_role("chat", False)
        b._cli_slots.release()
        return await asyncio.wait_for(call, 10)

    frames = _run(scenario())
    assert frames[0] == {"cli_fallback": {"backend": "ollama", "reason": "role_not_eligible"}}
    assert seen["cli_stream"] == 0


def test_a_stream_whose_caller_hung_up_while_it_waited_yields_nothing(tmp_path):
    b, seen = _streaming(tmp_path, cli_concurrency=1, cli_timeout=30)
    gone = {"now": False}
    got: list = []

    async def client_gone():
        return gone["now"]

    async def scenario():
        await b._cli_slots.acquire()

        async def consume():
            async for f in b.chat_stream("@iep", _Q, requester="owner-one",
                                         client_gone=client_gone):
                got.append(f)

        call = asyncio.ensure_future(consume())
        await asyncio.sleep(0.2)
        gone["now"] = True
        b._cli_slots.release()
        with pytest.raises(ClientGone):
            await asyncio.wait_for(call, 10)

    _run(scenario())
    state = b.cli_state()
    assert got == [] and seen["cli_stream"] == 0 and seen["ollama"] == 0
    assert state["refusals"].get("client_gone") == 1 and state["fallbacks"]["count"] == 0
    assert state["active"] == [] and b._cli_slots._value == 1


def test_a_failed_stream_whose_caller_hung_up_skips_the_local_fallback(tmp_path, capsys):
    b, seen = _streaming(tmp_path, raises_at=0, exc=CliUnavailable("timeout", "no text"))
    asked: list = []

    async def client_gone():
        asked.append(1)
        return len(asked) > 1               # there for the slot, gone once the CLI failed

    with pytest.raises(ClientGone):
        _stream(b, requester="owner-one", client_gone=client_gone)
    state = b.cli_state()
    assert seen["ollama"] == 0 and state["fallbacks"] == {"count": 0, "last": None}
    assert state["last_by_role"]["@iep"]["reason"] == "timeout"
    assert "CLI FALLBACK" not in capsys.readouterr().err


def test_a_caller_who_hangs_up_mid_stream_stops_it_on_the_next_idle_tick(tmp_path, monkeypatch):
    """Between frames the broker asks `client_gone` every tick, so a stream waiting on a slow
    CLI notices a caller who left without waiting for the next frame (Starlette under ASGI 2.4
    notices only at the next write). Counted with the refusals, as a hung-up buffered call is."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.05)
    b = _broker(tmp_path)
    closed: list = []
    gone = {"now": False}

    async def _cli_stream(messages, *, options=None, think=None):
        try:
            yield _frame("Hel")
            gone["now"] = True
            await asyncio.sleep(30)          # a CLI that has gone quiet
            yield _frame("lo")
        finally:
            closed.append(1)

    async def client_gone():
        return gone["now"]

    b._cli_client = lambda **_kw: SimpleNamespace(chat_stream=_cli_stream)
    got: list = []

    async def scenario():
        with pytest.raises(ClientGone):
            async for f in b.chat_stream("@iep", _Q, requester="owner-one",
                                         client_gone=client_gone):
                got.append(f)

    t0 = time.monotonic()
    _run(asyncio.wait_for(scenario(), 10))
    assert time.monotonic() - t0 < 5
    assert _contents(got) == ["Hel"] and closed == [1]
    state = b.cli_state()
    assert state["refusals"].get("client_gone") == 1 and state["active"] == []
    assert state["interruptions"]["count"] == 0 and state["fallbacks"]["count"] == 0


@pytest.mark.parametrize("gone", [True, False], ids=["hung-up", "shutting-down"])
def test_a_cancelled_stream_counts_as_a_hang_up_only_when_the_connection_says_so(
        tmp_path, monkeypatch, gone):
    """A cancellation is how Starlette delivers a hang-up under ASGI 2.3, and also how a server
    shutting down stops a request in flight. The broker asks the connection once, after the
    cancellation: only a caller that has gone is counted as `client_gone`. The tick is set far
    out, so the cancellation is the only thing that can end the stream."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    b = _broker(tmp_path)

    async def _cli_stream(messages, *, options=None, think=None):
        yield _frame("Hel")
        await asyncio.sleep(30)              # mid-answer when the cancellation comes
        yield _frame("lo")

    cancelled = {"yet": False}

    async def client_gone():
        # There for the slot; once the cancellation is sent, gone or still there.
        return gone and cancelled["yet"]

    b._cli_client = lambda **_kw: SimpleNamespace(chat_stream=_cli_stream)
    got: list = []

    async def scenario():
        async def consume():
            async for f in b.chat_stream("@iep", _Q, requester="owner-one",
                                         client_gone=client_gone):
                got.append(f)

        task = asyncio.ensure_future(consume())
        assert await _until(lambda: len(got) >= 2, 5)
        cancelled["yet"] = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)

    _run(scenario())
    state = b.cli_state()
    assert _contents(got) == ["Hel"]
    assert state["refusals"] == ({"client_gone": 1} if gone else {}), state["refusals"]
    assert state["active"] == [] and b._cli_slots._value == b._cli_limit
    assert state["interruptions"]["count"] == 0 and state["fallbacks"]["count"] == 0


@pytest.mark.parametrize("gone", [True, False], ids=["hung-up", "still-there"])
def test_a_closed_stream_counts_as_a_hang_up_only_when_the_connection_says_so(
        tmp_path, monkeypatch, gone):
    """The route closes the broker's stream (GeneratorExit at the frame it was suspended on) when
    the stream ended in Starlette's send(): a write that failed under ASGI 2.4, or a 2.3
    cancellation that landed there. An in-process caller that stops reading closes it too. The
    broker asks the connection once, as after a cancellation: only a caller that has gone is
    counted as `client_gone`, and the close is never an interruption or a fallback."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    b = _broker(tmp_path)

    async def _cli_stream(messages, *, options=None, think=None):
        yield _frame("Hel")
        await asyncio.sleep(30)              # never reached: the close comes at the yield
        yield _frame("lo")

    closing = {"yet": False}

    async def client_gone():
        return gone and closing["yet"]

    b._cli_client = lambda **_kw: SimpleNamespace(chat_stream=_cli_stream)

    async def scenario():
        agen = b.chat_stream("@iep", _Q, requester="owner-one", client_gone=client_gone)
        got = [await agen.__anext__() for _ in range(2)]
        closing["yet"] = True
        await asyncio.wait_for(agen.aclose(), 10)
        return got

    got = _run(scenario())
    state = b.cli_state()
    assert _contents(got) == ["Hel"]
    assert state["refusals"] == ({"client_gone": 1} if gone else {}), state["refusals"]
    assert state["active"] == [] and b._cli_slots._value == b._cli_limit
    assert state["interruptions"]["count"] == 0 and state["fallbacks"]["count"] == 0


# --- visibility ---------------------------------------------------------------------------------

def test_an_in_flight_stream_is_active_until_its_last_frame(tmp_path):
    """The CLI takes no GPU gate, so `active` is the only place an owner's stream shows. It is
    there, marked as a stream, from the first frame to the last, and gone once the stream ends."""
    b, _seen = _streaming(tmp_path)

    async def scenario():
        agen = b.chat_stream("@iep", _Q, requester="owner-one")
        seen = []
        async for f in agen:
            seen.append((f, [dict(e) for e in b.cli_state()["active"]]))
        return seen, b.cli_state()["active"]

    seen, after = _run(scenario())
    assert len(seen) == 4
    for _frame_, active in seen:
        [entry] = active
        assert entry["stream"] is True and entry["role"] == "@iep"
        assert entry["requester"] == "owner-one"
    assert seen[-1][0]["done"] is True
    assert after == []


def test_status_reports_streaming_and_interruptions_as_copies(tmp_path):
    """`streams` says this broker's CLI path streams (what the code carries, ready or not);
    `interruptions` counts streams cut after their first token. Handed out as copies, like the
    rest of the block."""
    off = _broker(tmp_path, cli_enabled=False)
    assert off.cli_state()["streams"] is True and off.cli_summary()["streams"] is True
    b, _seen = _streaming(tmp_path, raises_at=1, exc=CliUnavailable("stalled", "quiet"))
    _stream(b, requester="owner-one")
    handed = b.cli_state()
    handed["interruptions"]["last"]["chars"] = 999
    handed["interruptions"]["count"] = 0
    fresh = b.cli_state()["interruptions"]
    assert fresh == {"count": 1, "last": {"requested": "@iep", "reason": "stalled", "chars": 3}}


# --- the route -----------------------------------------------------------------------------------

def _ndjson(body: bytes | str) -> list[dict]:
    text = body.decode() if isinstance(body, bytes) else body
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def stream_app(tmp_path, monkeypatch):
    """The real broker app over a `_streaming` broker; returns (client, broker, seen)."""
    from app.main import app

    def build(auth_token="shared-secret"):
        b, seen = _streaming(tmp_path, tokens_file=str(tmp_path / "tokens.json"),
                             roles_file=str(tmp_path / "roles.json"), auth_token=auth_token)
        monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
        monkeypatch.setattr(app.state, "broker", b, raising=False)
        monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
        return TestClient(app), b, seen

    return build


_OWNER_ASKS = {"model": "@iep", "messages": _Q, "requester": "owner-one"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("credential", ["shared", "named-full"])
def test_the_stream_route_honours_a_full_scope_requester(stream_app, credential):
    client, b, seen = stream_app()
    token = "shared-secret" if credential == "shared" else b.settings.add_token("gw", "full")[0]
    r = client.post("/v1/chat/stream", json=_OWNER_ASKS, headers=_bearer(token))
    assert r.status_code == 200, r.text
    frames = _ndjson(r.content)
    assert frames[0] == _LEAD and _text(frames) == "Hello"
    assert (seen["cli_stream"], seen["ollama"]) == (1, 0)


def test_an_inference_token_cannot_name_an_owner_onto_a_streamed_cli(stream_app):
    client, b, seen = stream_app()
    token, _row = b.settings.add_token("studio", "inference")
    r = client.post("/v1/chat/stream", json=_OWNER_ASKS, headers=_bearer(token))
    assert r.status_code == 200, r.text
    frames = _ndjson(r.content)
    assert _text(frames) == "from ollama" and not any("cli_fallback" in f for f in frames)
    assert (seen["cli_stream"], seen["ollama"]) == (0, 1)
    assert b.cli_state()["refusals"] == {"no_requester": 1}


def test_an_open_broker_takes_a_streamed_requester_from_any_caller(stream_app):
    client, _b, seen = stream_app(auth_token="")
    r = client.post("/v1/chat/stream", json=_OWNER_ASKS)
    assert r.status_code == 200 and _text(_ndjson(r.content)) == "Hello"
    assert seen["cli_stream"] == 1


def test_the_stream_route_ends_without_an_error_line_for_a_caller_who_hung_up(tmp_path,
                                                                              monkeypatch):
    """Nobody is reading, so nothing is written: an error line here would be the broker
    reporting a failure of its own for a call the caller ended."""
    from app.main import app
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="")

    async def chat_stream(model, messages, **kw):
        raise ClientGone("the caller hung up while this call waited for a CLI slot")
        yield {}                                                    # pragma: no cover

    b.chat_stream = chat_stream
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    r = TestClient(app).post("/v1/chat/stream", json=_OWNER_ASKS)
    assert r.status_code == 200 and r.content == b""


async def _asgi_post(app, payload: dict, *, spec: str, gone: asyncio.Event,
                       sent: list, path: str = "/v1/chat/stream", send=None) -> None:
    """POST through the raw ASGI interface, announcing `spec` as the ASGI spec version (uvicorn
    reports 2.3; Starlette streams differently from 2.4 on). `receive` answers http.disconnect
    once `gone` is set and never before; every message the app sends is appended to `sent`,
    unless the test brings a `send` of its own (a write that blocks, or one that fails)."""
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def recorded(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 11500),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    await app(scope, receive, send or recorded)


@pytest.mark.parametrize("hung_up", [True, False], ids=["hung-up", "still-there"])
def test_the_stream_route_hands_the_broker_the_connections_own_answer(tmp_path, monkeypatch,
                                                                      hung_up):
    from app.main import app
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="")
    heard: list = []

    async def chat_stream(model, messages, **kw):
        heard.append(await kw["client_gone"]())
        yield {"message": {"role": "assistant", "content": "ok"}, "done": True}

    b.chat_stream = chat_stream
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)

    async def scenario():
        gone = asyncio.Event()
        if hung_up:
            gone.set()
        sent: list = []
        task = asyncio.ensure_future(_asgi_post(app, _OWNER_ASKS, spec="2.4", gone=gone,
                                                  sent=sent))
        await asyncio.wait_for(task, 10)
        return sent

    sent = _run(scenario())
    assert heard == [hung_up]
    assert sent[0]["status"] == 200


# --- a real process tree, through the real app ----------------------------------------------------

def _alive(pid: int) -> bool:
    """Is `pid` still running? (cli_backend_fakes' helper: a Linux zombie counts as dead.)"""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def _kill_leftover(pid: int | None) -> None:
    if pid is None or not _alive(pid):
        return
    if sys.platform == "win32":
        import subprocess
        # /T: a venv's python.exe is a launcher whose child is the interpreter itself.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGKILL)


_HELLO = json.dumps({"type": "stream_event", "event": {
    "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}})


def _fake_cli(pidfile: Path, *, delta: bool) -> str:
    """A stand-in for `claude -p`: starts a grandchild that sleeps (the node child the real CLI
    runs, which is what keeps generating and billing), writes its pid, prints one text delta
    when asked to, then hangs without closing stdout."""
    return ("import subprocess, sys, time\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
            + (f"print({_HELLO!r}, flush=True)\n" if delta else "")
            + "time.sleep(120)\n")


def _real_tree_broker(tmp_path, monkeypatch, script: str, **kw) -> Broker:
    """The real app's broker, its CLI calls running the REAL client, which spawns `script`. Each
    call passes the script its own number (0, 1, ...) as its one argument."""
    from app.main import app
    # The broker's own TEMP: a call whose child inherited it would write there, never into the
    # developer's.
    (tmp_path / "broker-temp").mkdir(exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(tmp_path / "broker-temp"))
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="", **kw)
    calls = iter(range(1000))
    monkeypatch.setattr(cli_backend.ClaudeCliClient, "build_argv",
                        lambda self, *a, **k: [sys.executable, "-c", script, str(next(calls))])

    async def _route(ref, *, substitute):
        raise AssertionError("a local answer started")

    b.route = _route
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    return b


async def _until(check, limit: float) -> bool:
    deadline = time.monotonic() + limit
    while not check():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.parametrize("spec,shape", [("2.3", "text"), ("2.4", "text"), ("2.3", "json"),
                                        ("2.4", "json")],
                         ids=["asgi-2.3-text", "asgi-2.4-text", "asgi-2.3-json", "asgi-2.4-json"])
def test_a_caller_who_disconnects_mid_stream_takes_the_cli_tree_with_it(
        tmp_path, monkeypatch, real_group_kill, spec, shape):
    """REAL processes through the real app. The caller disconnects once the CLI is running (for
    a text stream, after its first token reached the wire). Under ASGI 2.3, which uvicorn
    reports, Starlette cancels the stream through anyio, and anyio re-delivers that cancellation
    at every await, the kill's own spawn of taskkill included: the kill must survive it
    (cli_backend._reap). The json shape is the buffered client inside a stream. Under 2.4
    Starlette never listens for the disconnect, so the broker's idle tick (client_gone) is what
    notices, for the buffered call too (Broker._while_there): it used to run on, unwatched,
    until the CLI answered or cli_timeout ran out. Either way the CLI's grandchild, the one that
    keeps generating, is dead within a tick or two, nothing is left running or holding a slot,
    and the hang-up is counted as `client_gone` however it was delivered (under 2.3 it used to
    be counted only when the tick noticed, which it never does there)."""
    import app.broker as broker_mod
    from app.main import app
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.2)
    pidfile = tmp_path / "gc.pid"
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli(pidfile, delta=shape == "text"))
    payload = dict(_OWNER_ASKS, **({"format": "json"} if shape == "json" else {}))
    gc_pid = None

    async def scenario():
        nonlocal gc_pid
        gone = asyncio.Event()
        sent: list = []
        task = asyncio.ensure_future(_asgi_post(app, payload, spec=spec, gone=gone, sent=sent))

        def ready() -> bool:
            if task.done() or not pidfile.exists() or not pidfile.read_text():
                return task.done()
            if shape == "json":
                return True
            return any(b'"Hello"' in m.get("body", b"") for m in sent)

        assert await _until(ready, 30), "the stand-in CLI never started"
        assert not task.done(), f"the stream ended before the disconnect: {sent}"
        gc_pid = int(pidfile.read_text())
        t0 = time.monotonic()
        gone.set()
        try:
            await asyncio.wait_for(task, 15)
        except asyncio.TimeoutError:
            return False, time.monotonic() - t0, sent      # the stream never noticed
        died = await _until(lambda: not _alive(gc_pid), 15)
        took = time.monotonic() - t0
        await asyncio.sleep(0.2)
        return died, took, sent

    try:
        died, took, sent = _run(scenario())
        assert died, "the CLI's grandchild outlived the disconnect"
        assert took < 5, f"the tree took {took:.1f}s to die after the disconnect"
        assert not any(b'"error"' in m.get("body", b"") for m in sent)
        state = b.cli_state()
        assert state["active"] == [] and b._cli_slots._value == b._cli_limit
        assert state["refusals"] == {"client_gone": 1}, state["refusals"]
    finally:
        if gc_pid is None and pidfile.exists() and pidfile.read_text():
            gc_pid = int(pidfile.read_text())
        _kill_leftover(gc_pid)


def test_closing_the_stream_takes_the_cli_tree_with_it(tmp_path, monkeypatch, real_group_kill):
    """A consumer that stops reading and closes the broker's generator (the route's own exit, an
    in-process caller) closes the client's, whose `finally` kills the tree."""
    pidfile = tmp_path / "gc.pid"
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli(pidfile, delta=True))
    gc_pid = None

    async def scenario():
        nonlocal gc_pid
        agen = b.chat_stream("@iep", _Q, requester="owner-one")
        got = [await asyncio.wait_for(agen.__anext__(), 30) for _ in range(2)]
        gc_pid = int(pidfile.read_text())
        await agen.aclose()
        died = await _until(lambda: not _alive(gc_pid), 20)
        return got, died

    try:
        got, died = _run(scenario())
        assert got[0] == _LEAD and _contents(got) == ["Hello"]
        assert died, "the CLI's grandchild outlived the closed stream"
        assert b.cli_state()["active"] == [] and b._cli_slots._value == b._cli_limit
    finally:
        _kill_leftover(gc_pid)


def _fake_cli_flowing(pidfile: Path) -> str:
    """`_fake_cli` whose answer keeps coming, a text delta every 50 ms: the stream is always
    writing, so no idle tick ever has a reason to ask the connection."""
    return ("import subprocess, sys, time\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
            "for _ in range(2400):\n"
            f"    print({_HELLO!r}, flush=True)\n"
            "    time.sleep(0.05)\n")


@pytest.fixture
def no_collector():
    """The cyclic garbage collector off for the test: whatever closes the stream has to be the
    route, not a collection that happens to run. Back on (if it was) afterwards."""
    import gc
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def test_a_stream_cancelled_while_its_write_is_stuck_takes_the_cli_tree_with_it(
        tmp_path, monkeypatch, real_group_kill, no_collector):
    """REAL processes through the real app, ASGI 2.3. The caller stopped reading, so the
    server's write buffer is full and Starlette is waiting in send(), and then it hangs up:
    Starlette's cancellation lands in send(), outside the body generator. `async for` does not
    close a generator it stops iterating, so the generator, the broker's stream inside it and
    the CLI's tree under that stayed suspended until a collection finalized them (with the
    collector off, the grandchild was still alive 10 s after the disconnect), and the hang-up
    was never counted. The route closes its body on every exit now (ClosingStreamingResponse),
    before the request ends: the slot is back by the time the server hears the request is done.
    The tick is set far out, so only the route can stop the tree.

    Real uvicorn 0.54 does not land the cancellation in send() (its send returns at once on a
    connection that has gone, and anyio does not cancel a task whose wake-up is already due);
    this send models a server that keeps it blocked, which is what the route must not depend
    on."""
    import app.broker as broker_mod
    from app.main import app
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    pidfile = tmp_path / "gc.pid"
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli(pidfile, delta=True))
    gc_pid = None

    async def scenario():
        nonlocal gc_pid
        gone = asyncio.Event()
        stuck = asyncio.Event()
        sent: list = []

        async def send(message):
            sent.append(message)
            if message.get("body"):
                stuck.set()
                await asyncio.Event().wait()   # a full write buffer that never drains

        task = asyncio.ensure_future(_asgi_post(app, _OWNER_ASKS, spec="2.3", gone=gone,
                                                sent=sent, send=send))
        assert await _until(lambda: task.done() or stuck.is_set(), 30), "the stream never wrote"
        assert not task.done(), f"the stream ended before the disconnect: {sent}"
        gc_pid = int(pidfile.read_text())
        t0 = time.monotonic()
        gone.set()
        await asyncio.wait_for(task, 15)
        ended = (b.cli_state()["active"], b._cli_slots._value)
        died = await _until(lambda: not _alive(gc_pid), 10)
        return died, time.monotonic() - t0, ended

    try:
        died, took, ended = _run(scenario())
        assert died, "the CLI's grandchild outlived the disconnect"
        assert took < 5, f"the tree took {took:.1f}s to die after the disconnect"
        assert ended == ([], b._cli_limit), f"the request ended before the CLI call: {ended}"
        state = b.cli_state()
        assert state["active"] == [] and b._cli_slots._value == b._cli_limit
        assert state["refusals"] == {"client_gone": 1}, state["refusals"]
    finally:
        _kill_leftover(gc_pid)


def test_a_hang_up_only_a_failed_write_noticed_takes_the_cli_tree_and_is_counted(
        tmp_path, monkeypatch, real_group_kill, no_collector):
    """REAL processes through the real app, ASGI 2.4, the answer still flowing. Starlette does
    not listen for a disconnect under 2.4; the first anyone hears of it is a write that fails
    (an OSError, which Starlette turns into ClientDisconnect), and the tick never asks because
    frames keep coming. That failure is raised in send(), outside the body generator, so the
    generator used to be closed only by its finalizer, after the request had ended, and a close
    was never counted as `client_gone`. The route closes it before the request ends now, and
    the broker asks the connection whether that close was a hang-up. The server here does what
    uvicorn does with an exception after the response started: it lets it go."""
    import app.broker as broker_mod
    from app.main import app
    from starlette.requests import ClientDisconnect
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    pidfile = tmp_path / "gc.pid"
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli_flowing(pidfile))
    gc_pid = None

    async def scenario():
        nonlocal gc_pid
        gone = asyncio.Event()
        sent: list = []
        raised: list[str] = []

        async def send(message):
            if gone.is_set() and message["type"] == "http.response.body":
                raise ConnectionResetError("the caller went away")   # ASGI 2.4: an OSError
            sent.append(message)

        async def served():
            try:
                await _asgi_post(app, _OWNER_ASKS, spec="2.4", gone=gone, sent=sent, send=send)
            except ClientDisconnect as exc:
                raised.append(type(exc).__name__)

        task = asyncio.ensure_future(served())

        def flowing() -> bool:
            return task.done() or sum(b'"Hello"' in m.get("body", b"") for m in sent) >= 3

        assert await _until(flowing, 30), "the stand-in CLI never started"
        assert not task.done(), f"the stream ended before the disconnect: {sent}"
        gc_pid = int(pidfile.read_text())
        t0 = time.monotonic()
        gone.set()
        await asyncio.wait_for(task, 15)
        ended = (b.cli_state()["active"], b._cli_slots._value)
        died = await _until(lambda: not _alive(gc_pid), 10)
        return died, time.monotonic() - t0, raised, ended

    try:
        died, took, raised, ended = _run(scenario())
        assert raised == ["ClientDisconnect"]
        assert died, "the CLI's grandchild outlived the disconnect"
        assert took < 5, f"the tree took {took:.1f}s to die after the disconnect"
        assert ended == ([], b._cli_limit), f"the request ended before the CLI call: {ended}"
        state = b.cli_state()
        assert state["active"] == [] and b._cli_slots._value == b._cli_limit
        assert state["refusals"] == {"client_gone": 1}, state["refusals"]
    finally:
        _kill_leftover(gc_pid)


def test_the_response_closes_its_body_through_the_cancellation_that_ended_it(no_collector):
    """ClosingStreamingResponse closes its body in a shielded scope. The cancellation that ended
    the stream in send() (here an enclosing anyio scope's, as a server shutting down delivers
    one) is still in force when the close runs, and anyio delivers it again at every await, so
    a body whose cleanup awaits anything (a CLI's reap, the GPU gate's release) would have it
    cut off at the first one."""
    import anyio
    from app.streaming import ClosingStreamingResponse
    cleaned: list[str] = []

    async def body():
        try:
            yield b"one"
            yield b"two"
        finally:
            await asyncio.sleep(0.01)
            cleaned.append("cleaned up")

    async def scenario():
        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message.get("body"):
                await asyncio.Event().wait()     # a write that never completes

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}}
        with anyio.move_on_after(0.2):
            await ClosingStreamingResponse(body(), media_type="text/plain")(scope, receive, send)
        return list(cleaned)

    assert _run(scenario()) == ["cleaned up"]


# --- a reader that stops reading: every send is bounded (added 2026-10-14) ----------------------

@pytest.mark.parametrize("spec", ["2.3", "2.4"], ids=["asgi-2.3", "asgi-2.4"])
def test_a_stream_whose_reader_stopped_takes_the_cli_tree_with_it(
        tmp_path, monkeypatch, capsys, real_group_kill, no_collector, spec):
    """REAL processes through the real app. The caller keeps its connection open and stops
    reading, so the server's write buffer fills and Starlette's send() blocks, and nothing else
    happens: no disconnect, no failed write. Every deadline the stream has runs while it waits
    for its NEXT frame (the CLI's idle bound, the broker's tick), never while it is suspended
    behind a send, so the stream held its CLI slot for as long as the caller liked (the request
    here never ended). Each send is bounded now: past the bound the stream ends, the tree dies,
    and the slot is back by the time the request ends. Not a hang-up (the connection never said
    it had gone), so not counted as one."""
    import app.broker as broker_mod
    import app.streaming as streaming
    from app.main import app
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    monkeypatch.setattr(streaming, "SEND_TIMEOUT_S", 0.5)
    pidfile = tmp_path / "gc.pid"
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli(pidfile, delta=True))
    gc_pid = None

    async def scenario():
        nonlocal gc_pid
        never = asyncio.Event()             # the caller never hangs up
        stuck = asyncio.Event()
        sent: list = []

        async def send(message):
            sent.append(message)
            if message.get("body"):
                stuck.set()
                await asyncio.Event().wait()   # a write buffer the caller never drains

        async def served():
            try:
                await _asgi_post(app, _OWNER_ASKS, spec=spec, gone=never, sent=sent, send=send)
            except Exception as exc:  # noqa: BLE001 -- uvicorn would log it and let it go
                return type(exc).__name__
            return None

        task = asyncio.ensure_future(served())
        assert await _until(lambda: task.done() or stuck.is_set(), 30), "the stream never wrote"
        gc_pid = int(pidfile.read_text())
        t0 = time.monotonic()
        try:
            raised = await asyncio.wait_for(task, 15)
        except asyncio.TimeoutError:
            return False, None, None, None, sent     # the stream sat behind the send
        took = time.monotonic() - t0
        ended = (b.cli_state()["active"], b._cli_slots._value)
        died = await _until(lambda: not _alive(gc_pid), 10)
        return died, took, raised, ended, sent

    try:
        died, took, raised, ended, sent = _run(scenario())
        assert took is not None, "the request never ended: the stream sat behind its send"
        assert took < 5, f"the stream took {took:.1f}s to end behind its send"
        assert raised is None, f"the app raised {raised} to the server"
        assert ended == ([], b._cli_limit), f"the request ended before the CLI call: {ended}"
        assert died, "the CLI's grandchild outlived the closed stream"
        state = b.cli_state()
        assert state["refusals"] == {}, state["refusals"]
        bodies = [m for m in sent if m["type"] == "http.response.body"]
        assert len(bodies) == 1 and bodies[0]["more_body"], "a stalled stream was completed"
        assert "a send waited 0.5s for the client to read" in capsys.readouterr().err
    finally:
        _kill_leftover(gc_pid)


async def _respond(body, send, spec: str) -> None:
    """Run a ClosingStreamingResponse over `body` with `send`, announcing ASGI `spec`."""
    from app.streaming import ClosingStreamingResponse

    async def receive():
        await asyncio.Event().wait()            # the caller never hangs up

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec},
             "path": "/v1/chat/stream"}
    await ClosingStreamingResponse(body, media_type="text/plain")(scope, receive, send)


@pytest.mark.parametrize("spec", ["2.3", "2.4"], ids=["asgi-2.3", "asgi-2.4"])
def test_a_send_past_the_bound_ends_the_stream_and_closes_its_body(monkeypatch, capsys, spec):
    """The bound itself: the second write never completes, so past SEND_TIMEOUT_S the response
    returns (raising nothing to the server, which closes an incomplete response's connection),
    with its body closed and one log line naming the bound."""
    import anyio
    import app.streaming as streaming
    monkeypatch.setattr(streaming, "SEND_TIMEOUT_S", 0.3)
    cleaned: list[str] = []
    sent: list = []

    async def body():
        try:
            while True:
                yield b"frame"
        finally:
            await asyncio.sleep(0.01)
            cleaned.append("cleaned up")

    async def send(message):
        sent.append(message)
        if len(sent) == 3:
            await asyncio.Event().wait()         # the caller stopped reading
        # Every other write yields, as a server's does, and a stream that writes on past the
        # stalled one is stopped here: a send that never suspended would keep the loop from
        # ever reaching the bound below, and the list would grow until memory ran out.
        assert len(sent) < 50, "the stream wrote on past the send that stalled"
        await asyncio.sleep(0)

    async def scenario():
        began = time.monotonic()
        with anyio.fail_after(5):
            await _respond(body(), send, spec)
        return time.monotonic() - began

    took = _run(scenario())
    assert 0.25 < took < 3, took
    assert cleaned == ["cleaned up"]
    assert [m.get("body") for m in sent[1:]] == [b"frame", b"frame"]
    assert capsys.readouterr().err.count("a send waited 0.3s for the client to read") == 1


@pytest.mark.parametrize("spec", ["2.3", "2.4"], ids=["asgi-2.3", "asgi-2.4"])
def test_a_slow_reader_is_not_cut_off(monkeypatch, capsys, spec):
    """The bound is per send. A reader that takes 0.3 s over every write, against a bound of
    0.5 s, reads the whole answer, though the stream lasts far longer than the bound."""
    import app.streaming as streaming
    monkeypatch.setattr(streaming, "SEND_TIMEOUT_S", 0.5)
    sent: list = []

    async def body():
        for n in range(6):
            yield f"frame {n}".encode()

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(0.3)
        sent.append(message)

    began = time.monotonic()
    _run(_respond(body(), send, spec))
    assert time.monotonic() - began > 1.5
    assert [m.get("body") for m in sent[1:]] == [f"frame {n}".encode() for n in range(6)] + [b""]
    assert sent[-1]["more_body"] is False
    assert "a send waited" not in capsys.readouterr().err


class _MemoryTransport(asyncio.Transport):
    """What uvicorn's protocol writes to, held in memory: no socket, so nothing fills a real
    buffer. A client that stopped reading is the protocol's pause_writing(), which asyncio calls
    once a transport holds more than its high-water mark."""

    def __init__(self) -> None:
        super().__init__()
        self.written = bytearray()
        self.closed = False

    def get_extra_info(self, name, default=None):
        return {"sockname": ("127.0.0.1", 11500),
                "peername": ("127.0.0.1", 50000)}.get(name, default)

    def write(self, data) -> None:
        self.written += data

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass


async def _through_uvicorn(http: str, asgi_app, request: bytes, *, stall: bool):
    """One request through uvicorn's own protocol class for `http` (its h11 or httptools
    implementation, what `uvicorn --http` picks), over a _MemoryTransport. With `stall`, the
    transport fills once the response's body has begun, as a client that stopped reading leaves
    it. Returns the transport once the request's task has ended."""
    from uvicorn.config import Config
    from uvicorn.protocols.http.h11_impl import H11Protocol
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
    from uvicorn.server import ServerState

    config = Config(app=asgi_app, http=http, lifespan="off", log_config=None)
    config.load()
    state = ServerState()
    protocol = (H11Protocol if http == "h11" else HttpToolsProtocol)(
        config=config, server_state=state, app_state={})
    transport = _MemoryTransport()
    protocol.connection_made(transport)
    protocol.data_received(request)
    assert await _until(lambda: b"\r\n\r\n" in transport.written, 10), "no response began"
    if stall:
        assert await _until(lambda: transport.written.count(b"\n") > 3, 10), "no body was sent"
        protocol.pause_writing()
    await asyncio.wait_for(asyncio.gather(*list(state.tasks)), 10)
    return transport


@pytest.mark.parametrize("http", ["h11", "httptools"])
def test_a_stream_the_bound_closed_leaves_no_error_in_uvicorn_s_log(tmp_path, monkeypatch,
                                                                    capsys, caplog, http):
    """uvicorn's own protocol code runs the real app. When the bound closed a stream whose reader
    had stopped, uvicorn logged `ASGI callable returned without completing response.` at ERROR
    beside the broker's line, which reads as a crash in the broker's log. The broker's own line
    is a WARNING now, the only line, and uvicorn still closes the connection. A response some
    other code left unfinished, in the next request on the same loop, still gets uvicorn's
    ERROR: only a stream the bound closed is spared it."""
    import logging

    import app.streaming as streaming
    from app.main import app
    monkeypatch.setattr(streaming, "SEND_TIMEOUT_S", 0.3)
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="")

    async def chat_stream(model, messages, **kw):
        while True:                                     # an answer that never ends
            yield {"message": {"role": "assistant", "content": "x" * 64}, "done": False}
            await asyncio.sleep(0)

    b.chat_stream = chat_stream
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    body = json.dumps(_OWNER_ASKS).encode()
    asked = (b"POST /v1/chat/stream HTTP/1.1\r\nHost: broker\r\nContent-Type: application/json"
             b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

    async def unfinished(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    async def scenario():
        cut = await _through_uvicorn(http, app, asked, stall=True)
        cut_errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        caplog.clear()
        other = await _through_uvicorn(http, unfinished, b"GET / HTTP/1.1\r\nHost: b\r\n\r\n",
                                       stall=False)
        return cut, cut_errors, other

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        cut, cut_errors, other = _run(scenario())
        other_errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert cut_errors == [], f"uvicorn logged at ERROR for a stream the bound closed: {cut_errors}"
    assert cut.closed, "uvicorn left the connection of the closed stream open"
    err = capsys.readouterr().err
    assert err.count("[broker] WARNING: /v1/chat/stream: a send waited 0.3s for the client to "
                     "read; the stream was closed") == 1, err
    assert other_errors == ["ASGI callable returned without completing response."], other_errors
    assert other.closed


def test_the_shipped_send_bound_is_the_cli_s_own_idle_minute():
    """A minute: a reader slower than about 0.8 KiB/s with 48 KiB waiting for it is cut off, a
    stalled one holds its slot or the gate for at most that long. The CLI's idle bound is the
    same minute from the other end."""
    import app.streaming as streaming
    assert streaming.SEND_TIMEOUT_S == cli_backend._STREAM_IDLE_S == 60.0


def _fake_cli_numbered(pids: Path) -> str:
    """`_fake_cli` for more than one call: each run writes what the real CLI leaves under its
    TEMP (the scratchpad of a session) and that TEMP's path to `<n>.temp`, then its grandchild's
    pid to `<n>.gc` under `pids`, `n` being the call's number (its one argument), prints one
    text delta, then hangs."""
    return ("import os, subprocess, sys, time\n"
            "scratch = os.path.join(os.environ['TEMP'], 'claude', 'C--work', 'session', "
            "'scratchpad')\n"
            "os.makedirs(scratch, exist_ok=True)\n"
            f"open(os.path.join({str(pids)!r}, sys.argv[1] + '.temp'), 'w')"
            ".write(os.environ['TEMP'])\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open(os.path.join({str(pids)!r}, sys.argv[1] + '.gc'), 'w').write(str(gc.pid))\n"
            f"print({_HELLO!r}, flush=True)\n"
            "time.sleep(120)\n")


def _gc_pid(pids: Path, n: int) -> int | None:
    """Call `n`'s grandchild, once its stand-in CLI has written it."""
    f = pids / f"{n}.gc"
    text = f.read_text() if f.exists() else ""
    return int(text) if text else None


def _temp_of(pids: Path, n: int) -> Path:
    """The TEMP call `n`'s stand-in CLI was given (it writes this before its grandchild's pid)."""
    return Path((pids / f"{n}.temp").read_text())


async def _first(agen, n: int) -> list[dict]:
    return [await agen.__anext__() for _ in range(n)]


@pytest.mark.parametrize("shape", ["text", "json"])
def test_a_hung_up_streams_slot_comes_free_only_once_its_tree_is_dead(
        tmp_path, monkeypatch, real_group_kill, shape):
    """REAL processes through the real app, with ONE CLI slot: a stream whose caller disconnects
    under ASGI 2.3 (Starlette cancels it), and a second call already queued for the slot. The
    second call's CLI must not start while any of the first one's tree is alive.

    The slot used to come back in the cancellation itself, while the task that finishes the
    process-tree kill was still running taskkill: the next-frame task of a text stream
    (Broker._watch did not wait for it once its own task was being cancelled), or for a `format`
    the reap behind a bare `asyncio.shield`, whose wait anyio's next delivery ended. For that
    moment two trees ran under BROKER_CLI_CONCURRENCY=1. The tick is set far out, so the only
    thing that can notice the hang-up is Starlette's cancellation, and that hang-up is counted."""
    import app.broker as broker_mod
    from app.main import app
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 60.0)
    pids = tmp_path / "pids"
    pids.mkdir()
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli_numbered(pids), cli_concurrency=1)
    real_spawn = cli_backend.ClaudeCliClient._spawn
    procs: list = []
    alive_at_spawn: list[list[bool]] = []

    async def spawn(self, argv):
        # The moment this call's tree is about to start: is anything of an earlier call's alive?
        # The launcher counts until the broker has seen it exit.
        alive_at_spawn.append([
            p.returncode is None or bool((gc := _gc_pid(pids, n)) and _alive(gc))
            for n, p in enumerate(procs)])
        # (the process, the call's temp directory); a bare process before 2026-10-14
        spawned = await real_spawn(self, argv)
        procs.append(spawned[0] if isinstance(spawned, tuple) else spawned)
        return spawned

    monkeypatch.setattr(cli_backend.ClaudeCliClient, "_spawn", spawn)
    payload = dict(_OWNER_ASKS, **({"format": "json"} if shape == "json" else {}))

    async def scenario():
        gone = asyncio.Event()
        sent: list = []
        first = asyncio.ensure_future(_asgi_post(app, payload, spec="2.3", gone=gone, sent=sent))

        def running() -> bool:
            if first.done() or _gc_pid(pids, 0) is None:
                return first.done()
            return shape == "json" or any(b'"Hello"' in m.get("body", b"") for m in sent)

        assert await _until(running, 30), "the stand-in CLI never started"
        assert not first.done(), f"the first stream ended before the disconnect: {sent}"
        second = b.chat_stream("@iep", _Q, requester="owner-one")
        head = asyncio.ensure_future(_first(second, 2))
        assert await _until(lambda: b.cli_state()["concurrency"]["waiting"] == 1, 10)
        gone.set()
        await asyncio.wait_for(first, 30)
        got = await asyncio.wait_for(head, 30)
        await second.aclose()
        died = await _until(lambda: not any(
            _alive(gc) for n in range(2) if (gc := _gc_pid(pids, n))), 20)
        return got, died

    try:
        got, died = _run(scenario())
        assert alive_at_spawn == [[], [False]], (
            f"the second call's CLI started while the first one's tree was alive: "
            f"{alive_at_spawn}")
        assert got[0] == _LEAD and _contents(got) == ["Hello"]
        assert died, "a CLI grandchild outlived its call"
        state = b.cli_state()
        assert state["active"] == [] and b._cli_slots._value == 1
        assert state["refusals"] == {"client_gone": 1}, state["refusals"]
        # The hung-up stream's temp directory went with its tree, and the closed one's too.
        left = [t for t in (_temp_of(pids, 0), _temp_of(pids, 1)) if t.exists()]
        assert left == [], f"a call's temp directory outlived it: {left}"
    finally:
        for n in range(2):
            _kill_leftover(_gc_pid(pids, n))
        for p in procs:
            _kill_leftover(p.pid if p.returncode is None else None)


def test_a_buffered_call_whose_caller_disconnects_takes_the_cli_tree_with_it(
        tmp_path, monkeypatch, real_group_kill):
    """/v1/chat through the real app, REAL processes. A plain request never hears a disconnect
    (Starlette cancels nothing for one, under either spec), so the CLI call used to run on for
    nobody until it answered or cli_timeout ran out, holding its slot. Now the broker asks the
    connection every tick while the CLI answers (Broker._while_there): the tree is dead within a
    tick or two, the slot is back, the hang-up is counted, and the 499 goes to nobody."""
    import app.broker as broker_mod
    from app.main import app
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.2)
    pids = tmp_path / "pids"
    pids.mkdir()
    b = _real_tree_broker(tmp_path, monkeypatch, _fake_cli_numbered(pids))

    async def scenario():
        gone = asyncio.Event()
        sent: list = []
        task = asyncio.ensure_future(_asgi_post(app, _OWNER_ASKS, spec="2.3", gone=gone,
                                                sent=sent, path="/v1/chat"))
        assert await _until(lambda: task.done() or _gc_pid(pids, 0) is not None, 30), (
            "the stand-in CLI never started")
        assert not task.done(), f"the call ended before the disconnect: {sent}"
        gc_pid = _gc_pid(pids, 0)
        t0 = time.monotonic()
        gone.set()
        try:
            await asyncio.wait_for(task, 15)
        except asyncio.TimeoutError:
            return False, time.monotonic() - t0, sent      # the call never noticed
        died = await _until(lambda: not _alive(gc_pid), 15)
        return died, time.monotonic() - t0, sent

    try:
        died, took, sent = _run(scenario())
        assert died, "the CLI's grandchild outlived the disconnect"
        assert took < 5, f"the tree took {took:.1f}s to die after the disconnect"
        assert sent[0]["status"] == 499
        state = b.cli_state()
        assert state["active"] == [] and b._cli_slots._value == b._cli_limit
        assert state["refusals"] == {"client_gone": 1}, state["refusals"]
        assert state["fallbacks"]["count"] == 0
        # Gone by the time the request ended, with the tree: the caller who left owns no file.
        assert not _temp_of(pids, 0).exists(), "the call's temp directory outlived its caller"
    finally:
        _kill_leftover(_gc_pid(pids, 0))
