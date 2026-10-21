"""WHO may reach the Claude CLI backend, and what happens when it cannot serve.

This file is the licence condition in test form. Anthropic does not permit routing requests
"through Free, Pro, or Max plan credentials on behalf of their users", so the question
`cli_decision` answers -- is the person waiting on this answer the subscriber? -- is not a
preference that can be relaxed later for convenience. Three of its answers matter most and
each has a test that fails loudly:

  * **no requester at all** is background work (the scheduler, a container-start warm). Nobody
    is waiting on it and nobody consented to it, so it must never reach a personal
    subscription. This is the default for every caller that has not been taught to pass one,
    which is why it has to be refused rather than treated as "probably the owner".
  * **a requester who is not an owner** is the forbidden case outright.
  * **a concrete model name instead of an @role** is the hole the BACKLOG already records for
    upstream delegation, where any inference-scoped token can address a registered box
    directly. Here the same hole would spend the owner's money, so only an admin-set role is
    eligible.

Settings are REAL `BrokerSettings`, not stubs, for the reason test_role_fallback gives about
`disabled()`: a test that stubbed `cli_owner_set` would still pass if production stopped
reading the setting at all.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.broker import ROLE_RAIL, Broker, ClientGone
from app.cli_backend import CliUnavailable
from app.config import DEFAULT_ROLES, BrokerSettings, CliRolesFileError, RolesFileError

# Two platform accounts of the same subscriber: the broker holds one Claude credential, so every
# BROKER_CLI_OWNERS entry is one person's own account (README, "The Claude CLI backend").
OWNERS = "owner-one,owner-two"
ROLES = "iep,edu"
# BROKER_CLI_MODELS' default, in its order.
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_ambient_cli_env(monkeypatch):
    """A BROKER_CLI_* variable in the developer's own environment would silently change what
    these tests assert. Cleared so the kwargs below are the only source of truth."""
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_EXE", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES",
                "BROKER_CLI_OAUTH_TOKEN", "BROKER_CLI_OAUTH_TOKEN_FILE", "BROKER_CLI_WORKDIR",
                "BROKER_CLI_MODEL", "BROKER_CLI_ROLES_FILE", "BROKER_CLI_CONCURRENCY",
                "BROKER_CLI_TIMEOUT", "BROKER_CLI_TEMP_ROOT"):
        monkeypatch.delenv(key, raising=False)


def _settings(tmp_path, **kw) -> BrokerSettings:
    exe = tmp_path / "claude.exe"
    if not exe.exists():
        exe.write_bytes(b"")
    kw.setdefault("cli_enabled", True)
    kw.setdefault("cli_exe", str(exe))
    kw.setdefault("cli_owners", OWNERS)
    kw.setdefault("cli_roles", ROLES)
    # A real, empty directory: an UNSET workdir is a blocker of its own (workdir_unset).
    kw.setdefault("cli_workdir", str(tmp_path))
    return BrokerSettings(**kw)


def _broker(tmp_path, **kw) -> Broker:
    return Broker(_settings(tmp_path, **kw))


# --- the predicate ---------------------------------------------------------

def test_an_owner_asking_an_eligible_role_is_allowed(tmp_path):
    assert _broker(tmp_path).cli_decision("@iep", "owner-one") == (True, "ok")


def test_the_second_owner_account_is_allowed(tmp_path):
    """A subscriber may have more than one platform account, and lists every one: the second
    account is allowed as the first is, whether or not it is an admin (the check is the list)."""
    assert _broker(tmp_path).cli_decision("@iep", "owner-two")[0] is True


def test_a_case_variant_of_an_owner_is_not_the_owner(tmp_path):
    """The licence hole this closes: gateway usernames used to be unique case-SENSITIVELY, so
    `Owner-One` could be a second account beside `owner-one`, and a lowercased match let it
    spend the owner's subscription. The gateway forwards the stored spelling; a different
    spelling is a different account."""
    assert _broker(tmp_path).cli_decision("@iep", "Owner-One") == (False, "not_owner")


def test_an_owner_list_entry_is_matched_as_written_not_folded(tmp_path):
    """The other side of the same comparison. Folding the configured list would make
    `owner-one` match an owner written `Owner-One`, the same hole from the other end."""
    b = _broker(tmp_path, cli_owners="Owner-One")
    assert b.cli_decision("@iep", "owner-one") == (False, "not_owner")
    assert b.cli_decision("@iep", " Owner-One ") == (True, "ok")


def test_a_case_variant_of_an_eligible_role_is_not_eligible(tmp_path):
    """Roles are case-sensitive on the local path (roles() has no "IEP"), so `@IEP` would be
    served by the CLI and then, on a CLI failure, fall back into UnknownRoleError."""
    b = _broker(tmp_path)
    assert b.cli_decision("@IEP", "owner-one") == (False, "role_not_eligible")
    assert b.cli_decision("@iep", "owner-one") == (True, "ok")


def test_background_work_with_no_requester_is_refused(tmp_path):
    """The scheduler's nightly passes and the container-start warms arrive here. Nobody is
    waiting on them and nobody consented to them paying a personal subscription."""
    assert _broker(tmp_path).cli_decision("@iep", None) == (False, "no_requester")


def test_a_blank_requester_is_refused_like_a_missing_one(tmp_path):
    """Same predicate the rails' identity gate needed: a blank header is not a user."""
    assert _broker(tmp_path).cli_decision("@iep", "   ") == (False, "no_requester")


def test_another_user_is_refused(tmp_path):
    assert _broker(tmp_path).cli_decision("@iep", "someone-else") == (False, "not_owner")


def test_an_ineligible_role_is_refused(tmp_path):
    """Turning this on for IEP drafting must not also send the finance fraud scan off-box."""
    assert _broker(tmp_path).cli_decision("@finance-fraud", "owner-one") == (
        False, "role_not_eligible")


def test_a_concrete_model_name_is_refused(tmp_path):
    """`route()` lets a caller name `upstream::model` directly and the backlog records that
    as a widening. The same hole here would let any inference-scoped token spend the owner's
    subscription, so eligibility keys on an admin-set role only."""
    assert _broker(tmp_path).cli_decision("mistral-small3.2:24b", "owner-one") == (
        False, "not_a_role")


def test_disabled_refuses_even_for_the_owner(tmp_path):
    assert _broker(tmp_path, cli_enabled=False).cli_decision("@iep", "owner-one") == (
        False, "disabled")


def test_no_owners_configured_means_nobody(tmp_path):
    """Enabled with an empty owner list is the whole feature off, not everyone allowed."""
    assert _broker(tmp_path, cli_owners="").cli_decision("@iep", "owner-one") == (
        False, "no_owners_configured")


def test_no_roles_configured_means_nothing_is_eligible(tmp_path):
    assert _broker(tmp_path, cli_roles="").cli_decision("@iep", "owner-one") == (
        False, "role_not_eligible")


def test_an_unreadable_token_file_refuses_rather_than_calling(tmp_path):
    """Degrading to the ambient login would mean a service account quietly using whatever
    credential happened to be lying around."""
    ok, why = _broker(tmp_path, cli_oauth_token_file=str(tmp_path / "nope.txt")).cli_decision(
        "@iep", "owner-one")
    assert (ok, why) == (False, "token_unreadable")


def test_a_missing_executable_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr("app.broker.resolve_cli_exe", lambda _c: None)
    assert _broker(tmp_path).cli_decision("@iep", "owner-one") == (False, "not_installed")


# --- the token file --------------------------------------------------------
#
# The README tells the operator to run `claude setup-token` in PowerShell, and PowerShell
# 5.1's `>` and Out-File write UTF-16LE. Reading those as UTF-8 raises UnicodeDecodeError,
# which is a ValueError and escaped the `except OSError` this used to have -- out of
# cli_state(), which Broker.status() calls OUTSIDE its "must never raise" guard. So a
# mistyped encoding took /v1/status to a 500 and every chat to a 502: the exact opposite of
# the quiet degradation the asymmetry with _resolve_auth_token_file exists to provide.

def _with_token_bytes(tmp_path, raw: bytes):
    p = tmp_path / "token.txt"
    p.write_bytes(raw)
    return _broker(tmp_path, cli_oauth_token_file=str(p))


def test_a_utf8_token_file_is_read(tmp_path):
    b = _with_token_bytes(tmp_path, b"sk-ant-oat01-abc\r\n")
    assert b.settings.cli_token() == ("sk-ant-oat01-abc", "")


def test_a_utf16_token_file_from_powershell_is_read_not_fatal(tmp_path):
    b = _with_token_bytes(tmp_path, "sk-ant-oat01-abc\r\n".encode("utf-16"))
    token, problem = b.settings.cli_token()
    assert (token, problem) == ("sk-ant-oat01-abc", "")


def test_status_never_raises_on_an_undecodable_token_file(tmp_path):
    """Binary rubbish must degrade to a blocker, not a 500 on the status endpoint."""
    b = _with_token_bytes(tmp_path, b"\xff\xfe\x00\x01\x02\x80\x81")
    state = b.cli_state()
    assert state["ready"] is False
    assert "token_unreadable" in state["blockers"]


def test_a_token_with_embedded_nuls_is_rejected(tmp_path):
    """UTF-16 with no BOM decodes as UTF-8 into 's\\x00k\\x00...', survives .strip(), and
    reaches create_subprocess_exec as an env value with embedded NULs."""
    b = _with_token_bytes(tmp_path, "sk-abc".encode("utf-16-le")[:-1] + b"\x00\x00")
    token, problem = b.settings.cli_token()
    assert "\x00" not in token
    assert token == "sk-abc" or problem


# --- status ----------------------------------------------------------------

def test_status_is_ready_when_fully_configured(tmp_path):
    state = _broker(tmp_path).cli_state()
    assert state["ready"] is True
    assert state["blockers"] == []
    assert state["owners"] == ["owner-one", "owner-two"]
    assert state["roles"] == ["edu", "iep"]


def test_status_names_every_blocker_not_just_the_first(tmp_path):
    """Fixing one of three and seeing no change is the same dead end one step further on."""
    state = _broker(tmp_path, cli_enabled=False, cli_owners="", cli_roles="").cli_state()
    assert set(state["blockers"]) >= {"disabled", "no_owners_configured", "no_roles_configured"}
    assert state["ready"] is False


def test_status_reports_a_missing_workdir(tmp_path):
    """The workdir exists to keep a stray CLAUDE.md out of a rail's prompt. Configured but
    absent means the CLI would run in the broker's cwd instead -- the repo root."""
    state = _broker(tmp_path, cli_workdir=str(tmp_path / "gone")).cli_state()
    assert "workdir_missing" in state["blockers"]


def test_status_reports_an_unreadable_token_file(tmp_path):
    state = _broker(tmp_path, cli_oauth_token_file=str(tmp_path / "nope.txt")).cli_state()
    assert "token_unreadable" in state["blockers"]
    assert state["token_problem"]


def test_status_is_inert_by_default(tmp_path, monkeypatch):
    """Nothing configured must read as off, not as broken."""
    state = Broker(BrokerSettings()).cli_state()
    assert state["enabled"] is False
    assert state["ready"] is False
    assert "disabled" in state["blockers"]


# --- dispatch --------------------------------------------------------------

def _dispatchable(tmp_path, cli_result=None, cli_raises=None, **kw):
    """A Broker whose Ollama and CLI are both fakes, recording which one served."""
    b = _broker(tmp_path, **kw)
    seen: dict = {"ollama": 0, "cli": 0, "evicted": 0}

    async def _ollama_chat(model, messages, **_kw):
        seen["ollama"] += 1
        return {"model": model, "message": {"role": "assistant", "content": "from ollama"}}

    async def _route(ref, *, substitute):
        return None, "local-model", None

    async def _evict(keep=None):
        seen["evicted"] += 1
        return []

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        seen["cli"] += 1
        if cli_raises is not None:
            raise cli_raises
        return cli_result

    b.ollama = SimpleNamespace(chat=_ollama_chat)
    b.route = _route
    b._evict_other_heavy = _evict
    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    return b, seen


_CLI_OK = {"model": "claude-cli", "message": {"role": "assistant", "content": "from cli"},
           "done": True, "cli": {"backend": "claude", "input_tokens": 9, "output_tokens": 3}}


def test_an_owner_request_is_served_by_the_cli(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert out["message"]["content"] == "from cli"
    assert (seen["cli"], seen["ollama"]) == (1, 0)


def test_the_cli_path_takes_no_gpu_gate_and_evicts_nothing(tmp_path):
    """It runs no GPU work. Holding the single local slot would serialise local work behind
    something that cannot contend with it -- the same reasoning as a delegated role."""
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert seen["evicted"] == 0
    assert b.gate.jobs() == []


def test_a_non_owner_never_reaches_the_cli(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="someone-else"))
    assert out["message"]["content"] == "from ollama"
    assert seen["cli"] == 0


def test_background_work_never_reaches_the_cli(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}]))
    assert seen["cli"] == 0
    assert seen["ollama"] == 1


def test_a_cli_failure_falls_back_to_ollama_and_says_so(tmp_path):
    """The property that makes this safe to switch on: the worst case is latency."""
    b, seen = _dispatchable(tmp_path, cli_raises=CliUnavailable("rate_limited", "usage limit"))
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert out["message"]["content"] == "from ollama"
    assert out["cli_fallback"] == {"backend": "ollama", "reason": "rate_limited"}
    assert (seen["cli"], seen["ollama"]) == (1, 1)


def test_a_fallback_is_counted_and_kept_for_the_status_surface(tmp_path):
    """A job lives for seconds; a counter is what an operator can still see afterwards."""
    b, _seen = _dispatchable(tmp_path, cli_raises=CliUnavailable("timeout", "no answer"))
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    state = b.cli_state()
    assert state["fallbacks"]["count"] == 1
    assert state["fallbacks"]["last"]["reason"] == "timeout"
    assert state["fallbacks"]["last"]["requested"] == "@iep"


def test_a_served_call_is_counted(tmp_path):
    b, _seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert b.cli_state()["calls"] == 1


def test_any_exception_falls_back_not_just_the_expected_one(tmp_path):
    """"Every failure is a fallback" is the property that makes this safe to switch on, and a
    guarantee that holds only for anticipated errors is not a guarantee. A ValueError from an
    env value with embedded NULs, a NotImplementedError from a selector loop under --reload,
    a TypeError from a malformed usage block: each used to escape as a 502 on a rail that
    worked the day before."""
    b, seen = _dispatchable(tmp_path, cli_raises=ValueError("embedded null byte"))
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert out["message"]["content"] == "from ollama"
    assert out["cli_fallback"]["reason"] == "ValueError"
    assert seen["ollama"] == 1


def test_a_refusal_is_counted_by_reason(tmp_path):
    """"It says ready and nothing changed" is the likeliest complaint, and `blockers` cannot
    answer it: a mistyped role and a rail that never passes a requester both look exactly
    like "nobody has asked yet"."""
    b, _seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}]))
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="stranger"))
    _run(b.chat("@finance-fraud", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert b.cli_state()["refusals"] == {
        "no_requester": 1, "not_owner": 1, "role_not_eligible": 1}


def test_an_unset_workdir_refuses_the_call_and_says_so(tmp_path):
    """Unset, the CLI ran in the broker's cwd (the repo root) and read this repo's CLAUDE.md
    into every rail's prompt. It is configuration the owner must give, not a default."""
    b = _broker(tmp_path, cli_workdir="")
    assert b.cli_decision("@iep", "owner-one") == (False, "workdir_unset")
    assert "workdir_unset" in b.cli_state()["blockers"]
    assert b.cli_state()["ready"] is False


def test_a_blank_workdir_is_unset_not_the_current_directory(tmp_path):
    b = _broker(tmp_path, cli_workdir="   ")
    assert b.cli_decision("@iep", "owner-one") == (False, "workdir_unset")


def test_a_missing_workdir_refuses_the_call_not_just_the_status(tmp_path):
    """These were two hand-written lists and had already drifted: status said ready=false
    while the decision still routed the call, which then died in the subprocess and was
    reported as `not_installed` -- three different stories about one bad setting."""
    b = _broker(tmp_path, cli_workdir=str(tmp_path / "gone"))
    assert b.cli_decision("@iep", "owner-one") == (False, "workdir_missing")
    assert "workdir_missing" in b.cli_state()["blockers"]


def test_invalid_settings_json_refuses_the_call(tmp_path):
    """`claude -p` silently ignores a settings blob that fails validation, so a mangled value
    would quietly stop excluding the owner's CLAUDE.md with nothing to notice."""
    b = _broker(tmp_path, cli_settings_json="{not json")
    assert b.cli_decision("@iep", "owner-one") == (False, "settings_json_invalid")
    assert "settings_json_invalid" in b.cli_state()["blockers"]


def test_options_and_think_are_handed_to_the_cli(tmp_path):
    """So it can report what it could not honour, rather than discarding it silently."""
    b = _broker(tmp_path)
    got: dict = {}

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        got.update(options=options, think=think)
        return _CLI_OK

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}],
                options={"temperature": 0.3}, think=False, requester="owner-one"))
    assert got["options"] == {"temperature": 0.3}
    assert got["think"] is False


def test_no_cli_fallback_key_on_an_ordinary_local_call(tmp_path):
    """Or the field decays into decoration nobody reads."""
    b, _seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="nobody"))
    assert "cli_fallback" not in out


def test_the_format_is_handed_to_the_cli(tmp_path):
    """edu-suite and IEP both ask for JSON. Dropping it here would hand the rail prose."""
    b = _broker(tmp_path)
    got: dict = {}

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        got["format"] = format
        return _CLI_OK

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], format="json",
                requester="owner-one"))
    assert got["format"] == "json"


# --- a call carrying images takes the same road ----------------------------------------------
#
# Images reach the CLI now (over stream-json, see test_cli_backend). Nothing about WHO may send
# them there changed: the same decision, the same slot, the same accounting, and a failure falls
# back to the local model with the images still on the messages.

# A PNG header and filler: the broker reads an image's first bytes only.
_PNG_B64 = base64.b64encode(bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
                            + b"IHDR-and-the-rest").decode("ascii")


def _img_q():
    return [{"role": "system", "content": "You read recipes."},
            {"role": "user", "content": "what is this", "images": [_PNG_B64]}]


#: Both shapes of call, for the tests below that must hold for either.
_SHAPES = {"text": lambda: [{"role": "user", "content": "q"}], "image": _img_q}


def _recording(tmp_path, *, cli_raises=None, **kw):
    """`_dispatchable`, keeping the messages each backend was HANDED."""
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK, cli_raises=cli_raises, **kw)
    seen["cli_messages"], seen["ollama_messages"] = [], []
    cli_chat = b._cli_client().chat
    ollama_chat = b.ollama.chat

    async def _cli(messages, **k):
        seen["cli_messages"].append(copy.deepcopy(messages))
        return await cli_chat(messages, **k)

    async def _ollama(model, messages, **k):
        seen["ollama_messages"].append(copy.deepcopy(messages))
        return await ollama_chat(model, messages, **k)

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli)
    b.ollama = SimpleNamespace(chat=_ollama)
    return b, seen


def test_an_owner_image_call_reaches_the_cli_with_its_images(tmp_path):
    b, seen = _recording(tmp_path)
    out = _run(b.chat("@iep", _img_q(), requester="owner-one"))
    assert out["message"]["content"] == "from cli"
    assert seen["cli_messages"] == [_img_q()] and seen["ollama"] == 0
    state = b.cli_state()
    assert state["calls"] == 1 and state["fallbacks"]["count"] == 0
    assert state["last_by_role"]["@iep"]["ok"] is True
    assert state["last_by_role"]["@iep"]["requester"] == "owner-one"


@pytest.mark.parametrize("requester,why", [(None, "no_requester"), ("someone-else", "not_owner")],
                         ids=["background", "not-an-owner"])
def test_an_image_call_is_decided_exactly_as_a_text_call(tmp_path, requester, why):
    """Images change what the CLI can carry, never who may use it."""
    b, seen = _recording(tmp_path)
    out = _run(b.chat("@iep", _img_q(), requester=requester))
    assert out["message"]["content"] == "from ollama"
    assert seen["cli"] == 0 and seen["ollama_messages"] == [_img_q()]
    assert b.cli_state()["refusals"] == {why: 1}


def test_an_image_the_cli_will_not_send_falls_back_with_the_images_intact(tmp_path):
    """`vision_unsupported` is now a particular image (a BMP, past a cap), and its fallback is
    counted like any other: the local vision model gets the question exactly as it was put."""
    b, seen = _recording(tmp_path, cli_raises=CliUnavailable(
        "vision_unsupported", "an image that is not PNG, JPEG, GIF or WebP"))
    out = _run(b.chat("@iep", _img_q(), requester="owner-one"))
    assert out["message"]["content"] == "from ollama"
    assert out["cli_fallback"] == {"backend": "ollama", "reason": "vision_unsupported"}
    assert seen["ollama_messages"] == [_img_q()]
    state = b.cli_state()
    assert state["fallbacks"] == {"count": 1, "last": {
        "requested": "@iep", "reason": "vision_unsupported",
        "detail": "an image that is not PNG, JPEG, GIF or WebP"}}
    assert state["last_by_role"]["@iep"]["reason"] == "vision_unsupported"


class _StreamProc:
    """Just enough of an asyncio subprocess for the REAL ClaudeCliClient: hands out `out` on
    stdout, keeps what it was given on stdin, exits with `code` (0 unless told)."""

    def __init__(self, out: bytes, code: int = 0) -> None:
        self.returncode, self.pid, self.stdin_seen, self._code = None, 4242, b"", code
        proc = self

        class _In:
            def write(self, data):
                proc.stdin_seen += data

            async def drain(self):
                return None

            def close(self):
                return None

        class _Out:
            def __init__(self, data: bytes) -> None:
                self.data = data

            async def read(self, n=-1):
                chunk, self.data = self.data[:n], self.data[n:]
                return chunk

        self.stdin, self.stdout, self.stderr = _In(), _Out(out), _Out(b"")

    async def wait(self):
        self.returncode = self._code
        return self._code

    def kill(self):
        self.returncode = -9


@pytest.mark.parametrize("served", [True, False], ids=["answered", "no-result-line"])
def test_a_dispatched_image_call_runs_the_real_client_over_stream_json(tmp_path, monkeypatch,
                                                                       served):
    """End to end through Broker.chat with the real client and a fake process: the image call
    is sent as stream-json, and a transcript that never reaches its result line falls back to
    the local model, images and all, counted as a fallback."""
    lines = [{"type": "system", "subtype": "init"},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "a pie"}]}}]
    if served:
        lines.append({"type": "result", "is_error": False, "result": "a pie",
                      "usage": {"input_tokens": 5, "output_tokens": 2}})
    proc = _StreamProc(("\n".join(json.dumps(x) for x in lines) + "\n").encode())
    ran: dict = {}

    async def fake_exec(*argv, **kw):
        ran["argv"] = list(argv)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    b = _broker(tmp_path)
    got: list = []

    async def _ollama(model, messages, **_k):
        got.append(copy.deepcopy(messages))
        return {"model": model, "message": {"role": "assistant", "content": "from ollama"}}

    async def _route(ref, *, substitute):
        return None, "local-model", None

    async def _evict(keep=None):
        return []

    b.ollama, b.route, b._evict_other_heavy = SimpleNamespace(chat=_ollama), _route, _evict
    out = _run(b.chat("@iep", _img_q(), requester="owner-one"))
    assert ran["argv"][ran["argv"].index("--input-format") + 1] == "stream-json"
    line = json.loads(proc.stdin_seen)
    assert [blk["type"] for blk in line["message"]["content"]] == ["image", "text"]
    state = b.cli_state()
    if served:
        assert out["message"]["content"] == "a pie" and got == []
        assert out["cli"]["input_tokens"] == 5 and state["calls"] == 1
    else:
        assert out["message"]["content"] == "from ollama" and got == [_img_q()]
        assert out["cli_fallback"] == {"backend": "ollama", "reason": "bad_stream"}
        assert state["fallbacks"]["count"] == 1
        assert state["last_by_role"]["@iep"]["reason"] == "bad_stream"


def _over_real_client(tmp_path, monkeypatch, proc, **kw):
    """A broker whose CLI calls run the REAL client against `proc`, with Ollama faked. Returns
    (broker, ran, got): `ran["argv"]` once a process was started, `got` the messages the local
    model was handed."""
    ran: dict = {}

    async def fake_exec(*argv, **_kw):
        ran["argv"] = list(argv)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    b = _broker(tmp_path, **kw)
    got: list = []

    async def _ollama(model, messages, **_k):
        got.append(copy.deepcopy(messages))
        return {"model": model, "message": {"role": "assistant", "content": "from ollama"}}

    async def _route(ref, *, substitute):
        return None, "local-model", None

    async def _evict(keep=None):
        return []

    b.ollama, b.route, b._evict_other_heavy = SimpleNamespace(chat=_ollama), _route, _evict
    return b, ran, got


def _stream(*records) -> bytes:
    return ("\n".join(json.dumps(x) for x in records) + "\n").encode()


def test_the_cli_block_says_the_cli_path_sends_images(tmp_path, monkeypatch):
    """`vision` in the CLI block, on /v1/status (cli_state, reported whole) and on the Rails
    tab's read (cli_summary), is what a gateway can check before a vision role is switched on:
    a broker from before images over stream-json refused every image call and sends no such
    key. It says what the code carries, so it is true with the backend switched off as well,
    and it is checked against the code: an owner's image call reaches the CLI as an image."""
    off = _broker(tmp_path, cli_enabled=False)
    assert off.cli_state()["ready"] is False
    assert off.cli_state()["vision"] is True and off.cli_summary()["vision"] is True
    proc = _StreamProc(_stream({"type": "system", "subtype": "init"},
                               {"type": "result", "is_error": False, "result": "a pie"}))
    b, ran, got = _over_real_client(tmp_path, monkeypatch, proc)
    out = _run(b.chat("@iep", _img_q(), requester="owner-one"))
    sent = ("argv" in ran and out["message"]["content"] == "a pie" and got == []
            and [blk["type"] for blk in json.loads(proc.stdin_seen)["message"]["content"]]
            == ["image", "text"])
    assert b.cli_state()["vision"] is sent is True
    assert b.cli_summary()["vision"] is True


#: What a model might say about a rail's picture: rail content, which the broker keeps out of its
#: log and its status block.
_DESCRIBED = "The worksheet shows a pupil's answers to questions 1 to 4"


@pytest.mark.parametrize("records,lines", [
    ([{"type": "system", "subtype": "init", "cwd": "C:/svc/work", "session_id": "s-77"},
      {"type": "assistant", "message": {"content": [{"type": "text", "text": _DESCRIBED}]}}], 2),
    ([{"type": "system", "subtype": "init", "cwd": "C:/svc/work", "session_id": "s-77"},
      {"type": "assistant", "message": {"content": [{"type": "text", "text": _DESCRIBED}]}},
      {"type": "result", "is_error": False, "result": _DESCRIBED}], 3),
], ids=["killed-before-the-result-line", "success-result-line"])
def test_a_failed_image_call_quotes_no_transcript_line_anywhere(tmp_path, monkeypatch, capsys,
                                                               records, lines):
    """Exit 1 with stderr empty: the CLI killed from outside after the assistant line, or a
    result line that reads as success on a nonzero exit. Either way the call falls back, and
    the model's description of the picture reaches none of the service log's CLI FALLBACK line,
    last_by_role, last_by_requester or cli.fallbacks.last on /v1/status. What is recorded is
    a fixed sentence with the exit code and the transcript's line count."""
    b, ran, got = _over_real_client(tmp_path, monkeypatch, _StreamProc(_stream(*records), 1))
    out = _run(b.chat("@iep", _img_q(), requester="owner-one"))
    assert "argv" in ran
    assert out["message"]["content"] == "from ollama" and got == [_img_q()]
    assert out["cli_fallback"] == {"backend": "ollama", "reason": "failed"}
    detail = (f"the CLI exited with code 1 and printed no error sentence; its transcript "
              f"({lines} lines) is not recorded")
    state = b.cli_state()
    assert state["fallbacks"] == {"count": 1, "last": {"requested": "@iep", "reason": "failed",
                                                       "detail": detail}}
    assert state["last_by_role"]["@iep"]["reason"] == "failed"
    log = capsys.readouterr().err
    assert f"CLI FALLBACK: @iep -> local (failed): {detail}" in log
    for where in (log, json.dumps(state), json.dumps(b.cli_summary())):
        for leaked in ("worksheet", "pupil", "C:/svc/work", "s-77"):
            assert leaked not in where


@pytest.mark.parametrize("turn", ["user", "assistant"])
def test_an_image_on_a_user_turn_reaches_the_cli_and_on_an_assistant_turn_falls_back(
        tmp_path, monkeypatch, turn):
    """Through Broker.chat and the real client. A user turn's image is sent, from an earlier
    turn too. An assistant turn's image is refused (vision_unsupported) before any process
    starts, because the CLI gets one user message and the picture would arrive as the asker's
    own; the local model answers with the conversation exactly as it was put."""
    msgs = [{"role": "user", "content": "what is this"}, {"role": "assistant", "content": "a pie"},
            {"role": "user", "content": "and the crust?"}]
    msgs[0 if turn == "user" else 1]["images"] = [_PNG_B64]
    proc = _StreamProc(_stream({"type": "system", "subtype": "init"},
                               {"type": "result", "is_error": False, "result": "shortcrust"}))
    b, ran, got = _over_real_client(tmp_path, monkeypatch, proc)
    out = _run(b.chat("@iep", copy.deepcopy(msgs), requester="owner-one"))
    state = b.cli_state()
    if turn == "user":
        assert out["message"]["content"] == "shortcrust" and got == []
        blocks = json.loads(proc.stdin_seen)["message"]["content"]
        assert [blk["type"] for blk in blocks] == ["image", "text", "text", "text"]
        assert blocks[0]["source"]["data"] == _PNG_B64
        assert state["calls"] == 1
    else:
        assert "argv" not in ran and proc.stdin_seen == b""
        assert out["message"]["content"] == "from ollama" and got == [msgs]
        assert out["cli_fallback"] == {"backend": "ollama", "reason": "vision_unsupported"}
        assert state["last_by_role"]["@iep"]["reason"] == "vision_unsupported"
        assert "assistant" in state["fallbacks"]["last"]["detail"]


def test_image_and_text_calls_share_one_concurrency_limit(tmp_path):
    """An image call is one more claude.exe, so it takes a slot like any other: with the limit
    at two, a text call and an image call running leave the next image call waiting."""
    b, seen = _dispatchable(tmp_path, cli_concurrency=2)

    async def scenario():
        gates = _held_cli(b, calls=3)
        calls = [asyncio.create_task(b.chat("@iep", msgs, requester="owner-one"))
                 for msgs in (_SHAPES["text"](), _img_q(), _img_q())]
        await _parked(gates[0][0], calls[0])
        await _parked(gates[1][0], calls[1])
        await asyncio.sleep(0.2)
        third_started = gates[2][0].is_set()
        state = b.cli_state()
        gates[0][1].set()
        await _parked(gates[2][0], calls[2])
        gates[1][1].set()
        gates[2][1].set()
        await asyncio.wait_for(asyncio.gather(*calls), 10)
        return third_started, state

    third_started, state = _run(scenario())
    assert third_started is False, "a third CLI call ran with the limit at two"
    assert len(state["active"]) == 2
    assert state["concurrency"] == {"limit": 2, "waiting": 1}
    assert seen["ollama"] == 0


# --- in-flight visibility --------------------------------------------------
#
# The CLI takes no GPU gate, so a call to it never appears in status()["jobs"]. These cover the
# `active` and `last_by_role` blocks that make it visible instead. The fake CLI below PARKS
# until released, because an entry that only exists while a call is running can only be
# observed from a second task during that window; asserting before and after alone would pass
# for a broker that never registered anything.

def _held_cli(b, calls=1, *, result=_CLI_OK, raises=None):
    """Swap in a CLI fake whose Nth call sets its own `started` and then waits on its own
    `release`. Returns that list of (started, release) pairs. Call it INSIDE the running loop:
    each test's asyncio.run is a fresh loop, and an Event belongs to the loop it first awaits."""
    gates = [(asyncio.Event(), asyncio.Event()) for _ in range(calls)]
    order = iter(gates)

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        started, release = next(order)
        started.set()
        await release.wait()
        if raises is not None:
            raise raises
        return result

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    return gates


async def _parked(started, call):
    """Wait until the fake CLI has been entered. Bounded, and it also stops early when the
    call finishes WITHOUT reaching the CLI: a broker that refused the call would otherwise
    leave this waiting for ever, turning a failing test into a hung suite. The mutation
    harness found exactly that on its case-sensitive owner match."""
    entered = asyncio.ensure_future(started.wait())
    await asyncio.wait({entered, call}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
    if not entered.done():
        entered.cancel()
        raise AssertionError("the call never reached the CLI fake (refused, or stuck before it)")


def _active_during(b, model="@iep", requester="owner-one"):
    """`cli_state()["active"]` as seen WHILE one CLI call is parked inside the fake."""
    async def scenario():
        [(started, release)] = _held_cli(b)
        call = asyncio.create_task(
            b.chat(model, [{"role": "user", "content": "q"}], requester=requester))
        await _parked(started, call)
        seen = b.cli_state()["active"]
        release.set()
        await call
        return seen

    return _run(scenario())


def test_an_in_flight_cli_call_is_visible_and_gone_once_served(tmp_path):
    """The UI otherwise reads "idle" for the whole of an owner's call, because the CLI never
    holds the gate that status()["jobs"] is built from. And an entry that outlives its call is
    worse than none: it shows the subscription being spent when nothing is running."""
    b, _seen = _dispatchable(tmp_path)

    async def scenario():
        [(started, release)] = _held_cli(b)
        call = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(started, call)
        during = b.cli_state()["active"]
        release.set()
        return during, await call

    t0 = time.time()
    during, out = _run(scenario())
    t1 = time.time()
    assert out["message"]["content"] == "from cli"
    [entry] = during
    # The shared contract the gateway and the chips build against: exactly these four keys.
    assert set(entry) == {"role", "rail", "since", "requester"}
    assert entry["role"] == "@iep"
    assert isinstance(entry["since"], float) and t0 <= entry["since"] <= t1
    assert b.cli_state()["active"] == []


def test_an_active_entry_is_gone_after_a_fallback_and_before_ollama_runs(tmp_path):
    """A failed CLI call becomes a LOCAL job, which the gate's queue then shows. If the CLI
    entry were still present while Ollama ran, the one call would be on screen twice."""
    b, seen = _dispatchable(tmp_path)
    active_while_local: list = []

    async def _ollama_chat(model, messages, **_kw):
        seen["ollama"] += 1
        active_while_local.append(b.cli_state()["active"])
        return {"model": model, "message": {"role": "assistant", "content": "from ollama"}}

    b.ollama = SimpleNamespace(chat=_ollama_chat)

    async def scenario():
        [(started, release)] = _held_cli(b, raises=CliUnavailable("timeout", "no answer"))
        call = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(started, call)
        during = b.cli_state()["active"]
        release.set()
        return during, await call

    during, out = _run(scenario())
    assert len(during) == 1
    assert out["cli_fallback"]["reason"] == "timeout"
    assert active_while_local == [[]]
    assert b.cli_state()["active"] == []


def test_an_active_entry_is_gone_after_the_call_is_cancelled(tmp_path):
    """Cancellation (a shutdown, an outer timeout) is a BaseException, so the `except
    Exception` that turns every failure into a fallback never sees it. Removal written into
    the success and fallback paths would pass both tests above and leak here, leaving the
    owner's call shown as running for ever. A cancel is also not an outcome: it is neither
    a served call nor a fallback, so it must not be recorded as either."""
    b, seen = _dispatchable(tmp_path)

    async def scenario():
        [(started, _release)] = _held_cli(b)
        call = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(started, call)
        during = b.cli_state()["active"]
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return during

    during = _run(scenario())
    assert len(during) == 1
    state = b.cli_state()
    assert state["active"] == []
    assert "@iep" not in state["last_by_role"]
    assert state["fallbacks"]["count"] == 0
    assert seen["ollama"] == 0


def test_overlapping_calls_for_one_role_each_keep_their_own_entry(tmp_path):
    """Keyed per call, not per role. Two @iep calls in flight are two entries, and the first
    to finish must take its own entry with it rather than its neighbour's."""
    b, _seen = _dispatchable(tmp_path)

    async def scenario():
        (s1, r1), (s2, r2) = _held_cli(b, calls=2)
        c1 = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s1, c1)
        c2 = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-two"))
        await _parked(s2, c2)
        both = b.cli_state()["active"]
        r1.set()
        await c1
        left = b.cli_state()["active"]
        r2.set()
        await c2
        return both, left

    both, left = _run(scenario())
    assert [e["requester"] for e in both] == ["owner-one", "owner-two"]
    assert [e["requester"] for e in left] == ["owner-two"]
    assert b.cli_state()["active"] == []


def test_the_active_entry_names_the_requester_as_it_was_matched(tmp_path):
    """Stripped, because the owner check strips, and NOT case-folded, because it is exact.
    Folding here would file the call under a name the licence check never accepted, and the
    gateway, which matches exactly too, would hide the owner's own call from them."""
    b, _seen = _dispatchable(tmp_path, cli_owners="Owner-One")
    [entry] = _active_during(b, requester="  Owner-One ")
    assert entry["requester"] == "Owner-One"


def test_the_active_entry_names_the_rail_from_the_role(tmp_path):
    """From the same ROLE_RAIL map a queued GPU job's `source` comes from, so a CLI call and a
    local job for one rail cannot be labelled two different ways. None for a role that
    belongs to no rail (a generic class like @chat), rather than a guess."""
    b, _seen = _dispatchable(tmp_path, cli_roles="iep,edu,chat")
    [iep] = _active_during(b, "@iep")
    [edu] = _active_during(b, "@edu")
    [generic] = _active_during(b, "@chat")
    assert iep["rail"] == ROLE_RAIL["iep"] == "IEP"
    assert edu["rail"] == ROLE_RAIL["edu"]
    assert generic["rail"] is None


def test_last_by_role_records_each_outcome_for_that_role(tmp_path):
    """`fallbacks.last` is one slot for the whole backend that only failures write. It cannot
    say that @iep has recovered since, and the next @edu failure overwrites it, so an IEP chip
    reading it would show a stale failure or another rail's. Per role, every outcome
    overwrites the previous one for that role and no other: a success clears a failure."""
    b, _seen = _dispatchable(tmp_path)
    outcomes = iter([_CLI_OK, CliUnavailable("rate_limited", "usage limit"), _CLI_OK,
                     CliUnavailable("timeout", "no answer")])

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    seen = []
    for _ in range(3):
        _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        seen.append(b.cli_state()["last_by_role"]["@iep"])
    _run(b.chat("@edu", [{"role": "user", "content": "q"}], requester="owner-one"))
    final = b.cli_state()["last_by_role"]

    assert [e["ok"] for e in seen] == [True, False, True]
    assert seen[1]["reason"] == "rate_limited"
    # The slug only. `detail` is the CLI's own error text and this block is meant for the UI.
    # The shared contract the gateway builds against: exactly these keys. `model` is the
    # `--model` the call passed, "" here (no switch file, no BROKER_CLI_MODEL: none).
    assert set(seen[1]) == {"ok", "at", "reason", "requester", "model"}
    assert set(seen[0]) == set(seen[2]) == {"ok", "at", "requester", "model"}
    assert seen[0]["at"] <= seen[1]["at"] <= seen[2]["at"]
    assert final["@iep"]["ok"] is True
    assert final["@edu"] == {"ok": False, "at": final["@edu"]["at"], "reason": "timeout",
                             "requester": "owner-one", "model": ""}


def test_last_by_role_says_whose_call_it_was(tmp_path):
    """One slot per ROLE, two owners. owner-one's @iep call succeeds, then owner-two's fails:
    the slot now holds owner-two's failure, and without a name on it the gateway would show it
    to owner-one as theirs. Both outcome paths carry it."""
    b, _seen = _dispatchable(tmp_path)
    outcomes = iter([_CLI_OK, CliUnavailable("rate_limited", "usage limit")])

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    first = b.cli_state()["last_by_role"]["@iep"]
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-two"))
    second = b.cli_state()["last_by_role"]["@iep"]
    assert first["ok"] is True and first["requester"] == "owner-one"
    assert second["ok"] is False and second["requester"] == "owner-two"


def test_last_by_requester_keeps_each_owners_own_latest_per_role(tmp_path):
    """last_by_role is the role's latest WHOEVER called, so owner-two's call replaces
    owner-one's there, and a gateway showing each owner only their own then had nothing for
    owner-one: their own fallback badge cleared because somebody else used the role. The
    per-requester map keeps each owner's own latest, in the same entry shape."""
    b, _seen = _dispatchable(tmp_path)
    outcomes = iter([_CLI_OK, CliUnavailable("rate_limited", "usage limit")])

    async def _cli_chat(messages, *, format=None, options=None, think=None):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    b._cli_client = lambda **_kw: SimpleNamespace(chat=_cli_chat)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester=" owner-two "))
    state = b.cli_state()
    assert state["last_by_role"]["@iep"]["requester"] == "owner-two"
    by_req = state["last_by_requester"]
    assert set(by_req) == {"owner-one", "owner-two"}
    mine, theirs = by_req["owner-one"]["@iep"], by_req["owner-two"]["@iep"]
    assert mine["ok"] is True and mine["requester"] == "owner-one"
    assert theirs == {"ok": False, "at": theirs["at"], "reason": "rate_limited",
                      "requester": "owner-two", "model": ""}


def test_the_per_requester_map_is_bounded(tmp_path, monkeypatch):
    """Owners and eligible roles are small configured sets, but both are hot-read for as long
    as the broker runs. Capped either way, and the LEAST recently updated goes first: evicting
    by first-seen would drop the owner who uses the CLI every day."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_CLI_HISTORY_REQUESTERS", 2)
    monkeypatch.setattr(broker_mod, "_CLI_HISTORY_ROLES", 2)
    b = _broker(tmp_path)

    def rec(who, role):
        b._record_cli_outcome(role, {"ok": True, "at": time.time(), "requester": who})

    rec("owner-a", "@x")
    rec("owner-b", "@x")
    rec("owner-a", "@y")          # owner-a is now the most recently updated
    rec("owner-c", "@x")          # over the cap: owner-b, the least recent, goes
    assert list(b.cli_state()["last_by_requester"]) == ["owner-a", "owner-c"]
    rec("owner-a", "@z")          # owner-a holds @x, @y, @z: @x, the least recent, goes
    assert list(b.cli_state()["last_by_requester"]["owner-a"]) == ["@y", "@z"]


def test_last_by_role_names_the_requester_the_way_the_owner_check_matched_it(tmp_path):
    """The gateway filters on this with an exact comparison against the stored username. The
    padding goes, as it did for the owner check; the case stays, as it did there."""
    b, _seen = _dispatchable(tmp_path, cli_result=_CLI_OK, cli_owners="Owner-One")
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="  Owner-One "))
    assert b.cli_state()["last_by_role"]["@iep"]["requester"] == "Owner-One"
    assert list(b.cli_state()["last_by_requester"]) == ["Owner-One"]


def test_cli_state_hands_out_copies_not_the_live_records(tmp_path):
    """status() is serialised after it returns, by code the broker does not control. A caller
    that decorates what it was handed must not be editing the broker's own bookkeeping."""
    b, _seen = _dispatchable(tmp_path, cli_result=_CLI_OK)
    _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))

    async def scenario():
        [(started, release)] = _held_cli(b)
        call = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(started, call)
        handed = b.cli_state()
        # Entries first, containers second: clearing a LIVE container first would make the
        # entry edits raise instead of showing the leak.
        handed["active"][0]["requester"] = "someone-else"
        handed["last_by_role"]["@iep"]["ok"] = False
        handed["last_by_requester"]["owner-one"]["@iep"]["ok"] = False
        handed["active"].clear()
        handed["last_by_role"].clear()
        handed["last_by_requester"]["owner-one"].clear()
        handed["last_by_requester"].clear()
        # Frozen NOW. If cli_state leaked a live record, `fresh` would alias it too, and the
        # parked call finishing below would write a new outcome into it and repair the very
        # damage this test exists to see (found by the mutation harness).
        fresh = copy.deepcopy(b.cli_state())
        release.set()
        await call
        return fresh

    fresh = _run(scenario())
    assert [e["requester"] for e in fresh["active"]] == ["owner-one"]
    assert fresh["last_by_role"]["@iep"]["ok"] is True
    assert fresh["last_by_requester"]["owner-one"]["@iep"]["ok"] is True


# --- the per-role switch file (BROKER_CLI_ROLES_FILE) ----------------------------------------
#
# The admin Rails tab's switch. Which ROLES may use the CLI moves from an env var read at
# startup to a hot-read file the broker writes. Every guard below fails in the direction that
# spends money: a file the broker ignores leaves a role on that a super-admin switched off, a
# malformed file that falls back to the env re-enables it, and a write that loses a change
# loses an "off". The env set stays the answer until the file exists, so turning the feature on
# changes nothing by itself.

SWITCHES = "cli-roles.json"

# The roles these tests switch, and the BROKER_CLI_ROLES _filed starts them from: `chat` and
# `edu`, two roles the public snapshot's DEFAULT_ROLES keeps. The snapshot de-wires `iep` with its
# withheld rail, and a switch file that parses has every key judged against roles(), so a test
# that switched @iep through one was a test of an unknown role there, and failed in the public
# suite. BROKER_CLI_ROLES names roles without judging them, so the tests that reach @iep through
# it alone mean the same in both trees: every test above this section, the first one below (no
# switch file configured) and the two below whose switch file never parses.
FILE_ROLES = "chat,edu"


def _filed(tmp_path, content=None, **kw):
    """A broker whose switch file holds `content`: a dict (written as JSON), raw text, or None
    for a file that is configured but does not exist yet. Returns (broker, path). Its
    BROKER_CLI_ROLES is FILE_ROLES unless `cli_roles` is passed."""
    p = tmp_path / SWITCHES
    if isinstance(content, dict):
        p.write_text(json.dumps(content), encoding="utf-8")
    elif isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    kw.setdefault("cli_roles", FILE_ROLES)
    return _broker(tmp_path, cli_roles_file=str(p), **kw), p


def test_with_no_switch_file_configured_the_env_set_decides(tmp_path):
    b = _broker(tmp_path)
    assert b.settings.cli_effective()[:4] == ({"iep", "edu"}, "env", "", "")
    assert b.cli_decision("@iep", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert (state["roles_source"], state["roles_file_set"]) == ("env", False)


def test_a_configured_file_that_does_not_exist_yet_keeps_the_env_set(tmp_path):
    """Setting BROKER_CLI_ROLES_FILE is the one restart the feature costs, and it must change
    nothing by itself: until the first switch writes the file, BROKER_CLI_ROLES decides."""
    b, p = _filed(tmp_path, None)
    assert not p.exists()
    assert b.settings.cli_effective()[:4] == ({"chat", "edu"}, "env", "", "")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert state["ready"] is True
    assert (state["roles_source"], state["roles_file_set"]) == ("env", True)


def test_the_file_switches_off_a_role_the_env_has_on(tmp_path):
    """The file is AUTHORITATIVE. A ceiling model (the file can only remove what the env
    allows) would make "switch it on for job-aid" a restart again; this is the other half of
    authoritative: off in the file is off, whatever BROKER_CLI_ROLES says."""
    b, _p = _filed(tmp_path, {"chat": False, "edu": True})
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    assert b.cli_decision("@edu", "owner-one") == (True, "ok")
    assert b.cli_state()["roles"] == ["edu"]


def test_the_file_switches_on_a_role_the_env_never_named_and_drops_one_it_does_not_list(
        tmp_path):
    b, _p = _filed(tmp_path, {"code": True})
    assert b.cli_decision("@code", "owner-one") == (True, "ok")
    # @chat is in BROKER_CLI_ROLES but not in the file, so it is not eligible.
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    state = b.cli_state()
    assert (state["roles"], state["roles_source"]) == (["code"], "file")


def test_a_file_written_with_a_bom_is_read(tmp_path):
    """PowerShell 5.1's Set-Content and Out-File write a BOM. Read as plain UTF-8 the file
    would be invalid and every role would fail closed over an encoding nobody chose."""
    p = tmp_path / SWITCHES
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"chat": True}).encode("utf-8"))
    b = _broker(tmp_path, cli_roles_file=str(p))
    assert b.settings.cli_effective()[:4] == ({"chat"}, "file", "", "")


@pytest.mark.parametrize("content", [
    "{not json",
    '["chat", "edu"]',                   # a list, not an object
    '{"chat": "false"}',                 # a string, which a lenient read would call truthy
    '{"chat": 1}',                       # a number is not a boolean either
    # A comment with no leading `_` is not an annotation: its text fails the file like any
    # other value that is not true or false.
    '{"comment": "x", "chat": true}',
    # Parses the decoder cannot finish. The first is a RecursionError, the second a ValueError
    # that is not a JSONDecodeError (Python caps int conversion at 4300 digits); both used to
    # escape cli_decision and cli_state instead of failing closed.
    "[" * 200000 + "]" * 200000,
    '{"chat": ' + "1" * 5000 + "}",
], ids=["not-json", "a-list", "string-value", "number-value", "unmarked-comment", "too-deep",
        "int-too-long"])
def test_an_invalid_file_fails_closed_and_says_so(tmp_path, content):
    """NOT the env set. Falling back to BROKER_CLI_ROLES (which has chat) would silently switch
    back on whatever the broken file had switched off."""
    b, _p = _filed(tmp_path, content)
    roles, source, problem, note, _models = b.settings.cli_effective()
    assert (roles, source) == (set(), "file") and problem and note == ""
    assert b.cli_decision("@chat", "owner-one") == (False, "cli_roles_file_invalid")
    state = b.cli_state()
    assert state["ready"] is False
    assert "cli_roles_file_invalid" in state["blockers"]
    assert state["roles"] == [] and state["roles_problem"] == problem


def test_an_unreadable_file_fails_closed(tmp_path):
    """A directory where the file should be: present, but not something the broker can read."""
    (tmp_path / SWITCHES).mkdir()
    b = _broker(tmp_path, cli_roles_file=str(tmp_path / SWITCHES))
    assert b.cli_decision("@iep", "owner-one") == (False, "cli_roles_file_invalid")
    assert "cli_roles_file_invalid" in b.cli_state()["blockers"]


def test_a_file_too_deep_to_parse_falls_back_on_the_call_and_keeps_status_up(tmp_path):
    """The parse runs inside cli_decision, which runs before the CLI branch's try, and inside
    cli_state, which /v1/status calls outside its never-raise guard. A RecursionError there
    was an owner's 500 and every chip poller's 500."""
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK,
                            cli_roles_file=str(tmp_path / SWITCHES))
    (tmp_path / SWITCHES).write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert out["message"]["content"] == "from ollama"
    assert (seen["cli"], seen["ollama"]) == (0, 1)
    state = b.cli_state()
    assert "cli_roles_file_invalid" in state["blockers"] and state["roles"] == []


@pytest.mark.parametrize("raw", [b"\xff\xfe{\x00\"\x00", ("[" * 200000 + "]" * 200000).encode()],
                         ids=["not-utf8", "too-deep"])
def test_a_roles_json_the_broker_cannot_read_does_not_take_the_switch_file_down(tmp_path, raw):
    """Judging the switch file reads roles(), which reads roles.json. One that is not UTF-8
    raised UnicodeDecodeError (a ValueError the old except did not name) out of cli_state and
    cli_decision. It now degrades like a malformed one: the overlay is empty and the defaults
    stand, and @chat, a default role, is still switched on. Not a blocker, but not silent:
    roles_problem names roles.json and why."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_bytes(raw)
    b, _p = _filed(tmp_path, {"chat": True}, roles_file=str(roles_json))
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert state["ready"] is True and state["roles"] == ["chat"]
    assert state["roles_problem"].startswith("roles.json is not "), state["roles_problem"]


# roles.json that cannot be read. roles() falls back to DEFAULT_ROLES, so a role only roles.json
# defines looks unknown meanwhile, and a switch written then used to drop its ON switch for good
# (the next write keeps only roles roles() knows). The review's probes, ported. One case per
# problem branch of BrokerSettings._load_roles_json: saved as UTF-16 by Windows PowerShell 5.1's >
# or Out-File, nested too deep to parse, the usual hand-edit mistake (a trailing comma), a list
# instead of an object, and a path that is there but cannot be read as a file. Each value is what
# roles.json holds (None: a directory in its place) and a pattern for the problem's words after
# "roles.json ". The decoder's own message differs between Python versions, so for a parse error
# only its frame is pinned.
_OVERLAY_ONLY = "zz-overlay-only"
_UNREADABLE_ROLES_JSON = {
    "utf-16": (json.dumps({_OVERLAY_ONLY: "m:1b"}).encode("utf-16"), r"is not UTF-8 text \("),
    "too-deep": (("[" * 200000 + "]" * 200000).encode(), r"is not valid JSON \(RecursionError\)"),
    "not-json": (b'{"zz-overlay-only": "m:1b",}', r"is not valid JSON \(.+ at line 1\)"),
    "a-list": (json.dumps([_OVERLAY_ONLY]).encode(), r"must be a JSON object of role name"),
    "a-directory": (None, r"could not be read \(.+\)"),
}


def _break_roles_json(roles_json: Path, case: str) -> None:
    bad = _UNREADABLE_ROLES_JSON[case][0]
    if bad is None:
        # There, but not a file: an OSError that is not FileNotFoundError (an absent file is
        # fine, the defaults are then the whole map).
        roles_json.unlink(missing_ok=True)
        roles_json.mkdir()
    else:
        roles_json.write_bytes(bad)


def _mend_roles_json(roles_json: Path, text: str) -> None:
    if roles_json.is_dir():
        roles_json.rmdir()
    roles_json.write_text(text, encoding="utf-8")


def _said(case: str, text: str) -> bool:
    """`text` starts by naming roles.json and this case's problem."""
    return re.match(rf"roles\.json {_UNREADABLE_ROLES_JSON[case][1]}", text) is not None


@pytest.mark.parametrize("case", sorted(_UNREADABLE_ROLES_JSON))
def test_an_unreadable_roles_json_is_said_and_no_switch_is_lost(tmp_path, case):
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b"})
    roles_json.write_text(good, encoding="utf-8")
    b, p = _filed(tmp_path, None, roles_file=str(roles_json))
    b.settings.set_cli_role(_OVERLAY_ONLY, True)
    b.settings.set_cli_role("chat", True)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    switches = p.read_bytes()

    _break_roles_json(roles_json, case)
    state = b.cli_state()
    problem = state["roles_problem"]
    assert _said(case, problem), problem
    assert "no Claude switch can be turned on" in problem and "off still works" in problem
    # The overlay-only switch is named as kept, not as dropped by the next switch.
    assert repr(_OVERLAY_ONLY) in problem and "Kept" in problem and "drops" not in problem
    assert "cli_roles_file_invalid" not in state["blockers"] and state["ready"] is True
    assert b.cli_summary()["roles_problem"] == problem
    with pytest.raises(CliRolesFileError) as exc:
        b.settings.set_cli_role("edu", True)
    assert _said(case, str(exc.value)) and "no Claude switch" in str(exc.value)
    assert p.read_bytes() == switches, "a refused switch wrote the file"

    _mend_roles_json(roles_json, good)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    assert b.cli_state()["roles_problem"] == ""


@pytest.mark.parametrize("case", sorted(_UNREADABLE_ROLES_JSON))
def test_a_repoint_is_refused_while_roles_json_cannot_be_read(tmp_path, case):
    """The review's second probe. A repoint in the Rails tab (set_role) rewrote a roles.json that
    was not valid JSON, or not an object, from an empty dict: every mapping went, overlay-only
    roles included, roles_problem then read clean, and the next Claude switch was accepted and
    dropped the overlay-only role's ON for good. One that was not UTF-8 or too deep was a 500.
    Refused now, with the reason, and the file is left as it was."""
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b", "chat": "x:7b"})
    roles_json.write_text(good, encoding="utf-8")
    b, _p = _filed(tmp_path, None, roles_file=str(roles_json))
    b.settings.set_cli_role(_OVERLAY_ONLY, True)
    b.settings.set_cli_role("chat", True)

    _break_roles_json(roles_json, case)
    before = None if roles_json.is_dir() else roles_json.read_bytes()
    with pytest.raises(RolesFileError) as exc:
        b.settings.set_role("chat", "y:7b")
    assert _said(case, str(exc.value)) and "no role can be repointed" in str(exc.value)
    assert (None if roles_json.is_dir() else roles_json.read_bytes()) == before
    assert b.settings.roles_read()[1], "the refused repoint rewrote roles.json"

    _mend_roles_json(roles_json, good)
    b.settings.set_cli_role("edu", True)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    b.settings.set_role("chat", "y:7b")
    assert json.loads(roles_json.read_text(encoding="utf-8")) == {_OVERLAY_ONLY: "m:1b",
                                                                   "chat": "y:7b"}


@pytest.mark.parametrize("case", sorted(_UNREADABLE_ROLES_JSON))
def test_switching_a_role_off_still_works_while_roles_json_cannot_be_read(tmp_path, case):
    """The kill switch. Refused as well, an owner's calls on a role kept reaching Claude until
    roles.json was repaired, and the only ways off were an env change and a restart, or deleting
    the switch file, which reseeds from BROKER_CLI_ROLES and can switch other roles back on. The
    OFF changes its one key and carries every other key forward as it was, known or not: the
    overlay-only role's ON, a key no role answers to and an annotation all survive it."""
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b"})
    roles_json.write_text(good, encoding="utf-8")
    held = {"_comment": "pilot", _OVERLAY_ONLY: True, "chat": True, "edu": True,
            "no-such-role": False}
    b, p = _filed(tmp_path, held, roles_file=str(roles_json))
    _break_roles_json(roles_json, case)
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")

    b.settings.set_cli_role("chat", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {**held, "chat": False}
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    assert b.cli_decision("@edu", "owner-one") == (True, "ok")

    _mend_roles_json(roles_json, good)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")


def test_an_overlay_only_role_can_be_switched_off_while_roles_json_cannot_be_read(tmp_path):
    """It looks unknown meanwhile, but the switch file names it, so its OFF is a real one: once
    roles.json is fixed, the role must not come back on."""
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b"})
    roles_json.write_text(good, encoding="utf-8")
    b, p = _filed(tmp_path, {_OVERLAY_ONLY: True, "chat": True}, roles_file=str(roles_json))
    _break_roles_json(roles_json, "not-json")
    b.settings.set_cli_role(_OVERLAY_ONLY, False)
    assert json.loads(p.read_text(encoding="utf-8")) == {_OVERLAY_ONLY: False, "chat": True}
    _mend_roles_json(roles_json, good)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (False, "role_not_eligible")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")


def test_an_off_for_a_name_nobody_switched_on_is_refused_while_roles_json_cannot_be_read(
        tmp_path):
    """Neither a built-in role nor a key the switch file holds is on anywhere, so there is
    nothing to switch off, and a typo stays the caller's mistake rather than a key written."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, p = _filed(tmp_path, {"chat": True}, roles_file=str(roles_json))
    _break_roles_json(roles_json, "utf-16")
    before = p.read_bytes()
    with pytest.raises(ValueError, match="unknown role 'no-such-role'"):
        b.settings.set_cli_role("no-such-role", False)
    assert p.read_bytes() == before


def test_a_built_in_role_the_file_does_not_name_can_be_switched_off_while_roles_json_cannot_be_read(
        tmp_path):
    """The rule is a built-in role OR a key the file names. Every other OFF test switches a key
    the file or the env seed already holds, so narrowing the rule to the file's keys passed them
    all while this OFF answered 400 'unknown role'."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, p = _filed(tmp_path, {"chat": True}, roles_file=str(roles_json))
    _break_roles_json(roles_json, "utf-16")
    assert "code" in DEFAULT_ROLES
    b.settings.set_cli_role("code", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "code": False}


def test_a_repoint_judges_the_name_against_the_defaults_and_roles_json(tmp_path):
    """Only a known role may be repointed: a built-in one, or one roles.json defines (an
    overlay-only role is repointed like any other). A legal name neither knows is refused and
    nothing is written, so a typo cannot create a dead role."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, _p = _filed(tmp_path, None, roles_file=str(roles_json))
    b.settings.set_role(_OVERLAY_ONLY, "n:2b")
    assert json.loads(roles_json.read_text(encoding="utf-8")) == {_OVERLAY_ONLY: "n:2b"}
    before = roles_json.read_bytes()
    with pytest.raises(ValueError, match="unknown role 'zz-typo'"):
        b.settings.set_role("zz-typo", "x:1b")
    assert roles_json.read_bytes() == before


def test_a_repoint_of_an_overlay_only_role_names_roles_json_not_a_typo(tmp_path):
    """While roles.json cannot be read, an overlay-only role looks unknown. Judged in the other
    order, its repoint answered 400 "unknown role", sending the admin to look for a typo that
    is not there; the reason is the file, and the 409 says so."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, _p = _filed(tmp_path, None, roles_file=str(roles_json))
    _break_roles_json(roles_json, "not-json")
    with pytest.raises(RolesFileError) as exc:
        b.settings.set_role(_OVERLAY_ONLY, "n:2b")
    assert _said("not-json", str(exc.value))


def test_with_no_switch_file_an_off_while_roles_json_cannot_be_read_seeds_every_env_role(
        tmp_path):
    """The first write seeds from BROKER_CLI_ROLES. Judged against the defaults alone, the seed
    would leave out an overlay-only role the env switches on, and once roles.json was fixed that
    role would be off with nobody having switched it off."""
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b"})
    roles_json.write_text(good, encoding="utf-8")
    b, p = _filed(tmp_path, None, roles_file=str(roles_json),
                  cli_roles=f"chat,edu,{_OVERLAY_ONLY}")
    _break_roles_json(roles_json, "utf-16")
    b.settings.set_cli_role("chat", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": False, "edu": True,
                                                          _OVERLAY_ONLY: True}
    _mend_roles_json(roles_json, good)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")


def test_a_switch_judges_the_file_against_the_one_roles_json_read_it_checked(tmp_path,
                                                                           monkeypatch):
    """The check and the write use ONE read of roles.json. Every later read here comes back
    without the overlay, as if the file broke between the check and the write; judged against
    such a read, the write would drop the overlay-only role's ON switch."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, p = _filed(tmp_path, {_OVERLAY_ONLY: True, "chat": True}, roles_file=str(roles_json))
    real = BrokerSettings._read_overlay
    reads: list[int] = []

    def first_then_empty(self):
        reads.append(1)
        return real(self) if len(reads) == 1 else ({}, "")

    monkeypatch.setattr(BrokerSettings, "_read_overlay", first_then_empty)
    b.settings.set_cli_role("edu", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {_OVERLAY_ONLY: True, "chat": True,
                                                          "edu": True}


def test_the_route_refuses_a_switch_on_with_a_409_while_roles_json_cannot_be_read(switch_app):
    client, _b, p = switch_app()
    (p.parent / "roles.json").write_bytes(_UNREADABLE_ROLES_JSON["utf-16"][0])
    r = client.put("/v1/cli/roles/chat", json={"enabled": True})
    assert r.status_code == 409, r.text
    assert _said("utf-16", r.json()["detail"]), r.text
    assert not p.exists()


def test_the_route_switches_a_role_off_while_roles_json_cannot_be_read(switch_app):
    client, b, p = switch_app({"chat": True, "edu": True})
    (p.parent / "roles.json").write_bytes(_UNREADABLE_ROLES_JSON["utf-16"][0])
    r = client.put("/v1/cli/roles/chat", json={"enabled": False})
    assert r.status_code == 200, r.text
    cli = r.json()["cli"]
    assert cli["enabled_roles"] == ["edu"] and _said("utf-16", cli["roles_problem"])
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")


def test_the_route_refuses_a_repoint_with_a_409_while_roles_json_cannot_be_read(switch_app):
    client, _b, p = switch_app()
    roles_json = p.parent / "roles.json"
    bad = _UNREADABLE_ROLES_JSON["not-json"][0]
    roles_json.write_bytes(bad)
    r = client.put("/v1/roles/chat", json={"model": "y:7b"})
    assert r.status_code == 409, r.text
    assert _said("not-json", r.json()["detail"]), r.text
    assert roles_json.read_bytes() == bad


# A key no role answers to. It cannot route a call either way, so it is IGNORED and named in
# roles_problem, with no blocker. Failing the whole file closed over one turned Claude off for
# every role after an unrelated roles.json edit, and the tab could not repair it (the switch
# route refuses an invalid file).

@pytest.mark.parametrize("key", ["CHAT", "no-such-role", "../roles"],
                         ids=["uppercase", "unknown-role", "path"])
def test_a_key_no_role_answers_to_is_ignored_and_named_without_a_blocker(tmp_path, key):
    b, _p = _filed(tmp_path, {key: True, "chat": True})
    roles, source, problem, note, _models = b.settings.cli_effective()
    assert (roles, source, problem) == ({"chat"}, "file", "")
    assert repr(key) in note
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    # The file is authoritative, so the env's @edu stays off: ignoring a key is not a fallback.
    assert b.cli_decision("@edu", "owner-one") == (False, "role_not_eligible")
    state = b.cli_state()
    assert state["ready"] is True and "cli_roles_file_invalid" not in state["blockers"]
    assert state["roles"] == ["chat"] and state["roles_problem"] == note


def test_a_role_that_leaves_roles_json_no_longer_poisons_every_switch(tmp_path):
    """The reviewer's case: a switch written for a roles.json-only role, then roles.json loses
    that role (renamed by a pull, or the file degrading to {}). Every other switch stands."""
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({"chat": "x:1b", "edu": "y:1b", "gone": "z:1b"}),
                          encoding="utf-8")
    b, p = _filed(tmp_path, None, roles_file=str(roles_json))
    b.settings.set_cli_role("gone", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": True, "gone": False}
    roles_json.write_text(json.dumps({"chat": "x:1b", "edu": "y:1b"}), encoding="utf-8")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert state["ready"] is True and state["roles"] == ["chat", "edu"]
    assert "'gone'" in state["roles_problem"]


def test_the_next_switch_drops_a_key_no_role_answers_to(tmp_path):
    b, p = _filed(tmp_path, {"chat": True, "no-such-role": False})
    b.settings.set_cli_role("edu", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": True}
    assert b.cli_state()["roles_problem"] == ""


# An annotation: a key starting with `_`, the convention roles.json uses for a `_comment`. Skipped
# whatever its value, before the true-or-false rule, so a comment's text cannot fail the file
# closed (it used to turn every role off); not named in roles_problem; kept by the next switch.

@pytest.mark.parametrize("value", ["switched on for the pilot", True, 3, None, {"by": ["x"]}],
                         ids=["text", "boolean", "number", "null", "object"])
def test_an_annotation_is_ignored_whatever_its_value_and_kept_by_the_next_switch(tmp_path,
                                                                                value):
    b, p = _filed(tmp_path, {"chat": True, "edu": True, "_comment": value})
    assert b.settings.cli_effective()[:4] == ({"chat", "edu"}, "file", "", "")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert state["ready"] is True and state["roles_problem"] == ""
    b.settings.set_cli_role("edu", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"_comment": value, "chat": True,
                                                          "edu": False}
    assert b.cli_decision("@edu", "owner-one") == (False, "role_not_eligible")


def test_every_role_switched_off_is_its_own_blocker(tmp_path):
    """"Switched off in the Rails tab" is a decision; "never configured" is setup to do. One
    name for both sends an operator looking for an env var that was never the problem."""
    b, _p = _filed(tmp_path, {"chat": False, "edu": False})
    state = b.cli_state()
    assert state["ready"] is False
    assert "cli_all_roles_off" in state["blockers"]
    assert "no_roles_configured" not in state["blockers"]
    never = _broker(tmp_path, cli_roles="").cli_state()
    assert "no_roles_configured" in never["blockers"]
    assert "cli_all_roles_off" not in never["blockers"]


def test_a_non_owner_call_never_reads_the_switch_file(tmp_path, monkeypatch):
    """The file is on disk and cli_decision runs on every chat. Reading it only once the
    caller is known to be an owner keeps the disk off the path of every other call."""
    b, _p = _filed(tmp_path, {"chat": True})
    reads = []
    real = BrokerSettings.cli_effective
    monkeypatch.setattr(BrokerSettings, "cli_effective",
                        lambda self, *a, **k: reads.append(1) or real(self, *a, **k))
    assert b.cli_decision("@chat", None) == (False, "no_requester")
    assert b.cli_decision("@chat", "someone-else") == (False, "not_owner")
    assert reads == []
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert reads == [1]


# --- writing a switch -------------------------------------------------------------------------

def test_the_first_switch_seeds_from_the_env_set(tmp_path):
    """Switching ONE role on must not drop the roles BROKER_CLI_ROLES already had: with no file
    the effective set is the env set, so that is what the first write starts from."""
    b, p = _filed(tmp_path, None)
    b.settings.set_cli_role("code", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "chat": True, "code": True, "edu": True}
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert b.cli_decision("@code", "owner-one") == (True, "ok")


def test_the_seed_leaves_out_an_env_role_nobody_knows(tmp_path):
    """Written into the file, an unknown env entry would make the file invalid and fail every
    role closed on the very first switch. It could never be served locally anyway."""
    b, p = _filed(tmp_path, None, cli_roles="chat,no-such-role")
    b.settings.set_cli_role("edu", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": True}
    assert b.settings.cli_effective()[:4] == ({"chat", "edu"}, "file", "", "")


def test_a_switch_off_is_an_explicit_false_and_takes_effect_on_the_next_call(tmp_path):
    """Hot: the same Broker, no restart. The false is kept rather than the key deleted, so the
    file records the decision and not only its absence."""
    b, p = _filed(tmp_path, None)
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    b.settings.set_cli_role("chat", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": False, "edu": True}
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    assert b.cli_state()["roles"] == ["edu"]


@pytest.mark.parametrize("role", ["no-such-role", "CHAT", "../roles", ""])
def test_a_switch_for_a_role_the_broker_does_not_know_is_refused_and_writes_nothing(
        tmp_path, role):
    b, p = _filed(tmp_path, None)
    with pytest.raises(ValueError):
        b.settings.set_cli_role(role, True)
    assert not p.exists()


def test_a_switch_with_no_file_configured_is_refused(tmp_path):
    b = _broker(tmp_path)
    with pytest.raises(CliRolesFileError) as exc:
        b.settings.set_cli_role("chat", False)
    assert "BROKER_CLI_ROLES_FILE" in str(exc.value)


def test_a_switch_never_overwrites_an_invalid_file(tmp_path):
    """Re-seeding a broken file from the env would switch back on whatever it had switched off.
    The operator fixes or deletes it; the switch says so and leaves it alone."""
    b, p = _filed(tmp_path, '{"chat": "false"}')
    with pytest.raises(CliRolesFileError) as exc:
        b.settings.set_cli_role("edu", False)
    assert "invalid" in str(exc.value)
    assert p.read_text(encoding="utf-8") == '{"chat": "false"}'


@pytest.mark.parametrize("raw", [b'{"chat": "false"}', b"\xff\xfe{\x00"],
                         ids=["invalid", "not-utf8"])
def test_the_refusal_says_what_deleting_the_file_does(tmp_path, raw):
    """Deleting the file is the documented way back from a broken one, and it is not neutral:
    BROKER_CLI_ROLES decides again, so a role somebody switched off can come back on. The
    refusal is where the operator reads the advice, so it says so there."""
    b, p = _filed(tmp_path, None)
    p.write_bytes(raw)
    with pytest.raises(CliRolesFileError) as exc:
        b.settings.set_cli_role("edu", False)
    msg = str(exc.value)
    assert "delete it" in msg and "back to BROKER_CLI_ROLES (the service defaults)" in msg
    assert "every switch made here is lost" in msg


def test_a_deleted_file_resets_to_the_env_set_and_status_says_where_roles_come_from(tmp_path):
    """The reset is deliberate (it is the advice above), so it stays; what changed is that it
    is visible. `roles_file_set` true with `roles_source` "env" is the state the Rails tab
    reads as "service defaults, not the switch file"."""
    b, p = _filed(tmp_path, None)
    b.settings.set_cli_role("chat", False)
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    assert b.cli_state()["roles_source"] == "file"
    p.unlink()
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    state = b.cli_state()
    assert (state["roles_source"], state["roles_file_set"]) == ("env", True)


def test_a_write_that_dies_halfway_leaves_the_previous_switches_in_force(tmp_path, monkeypatch):
    """Through a temp file and an atomic replace. Written in place, a write that died halfway
    (a full disk, the service stopping) would leave half a JSON object, and the next read would
    fail every role closed, or worse, a hand-repaired file would come back with guesses in it."""
    b, p = _filed(tmp_path, {"chat": True, "edu": True})
    real = Path.write_text

    def half_then_fail(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", half_then_fail)
    with pytest.raises(CliRolesFileError):
        b.settings.set_cli_role("chat", False)
    monkeypatch.setattr(Path, "write_text", real)
    assert b.settings.cli_effective()[:4] == ({"chat", "edu"}, "file", "", "")


def test_two_switches_at_once_both_land(tmp_path, monkeypatch):
    """Read, change one key, write back: without the lock, B reads the file while A is between
    its read and its write, and A's write then drops B's change. The interleaving is forced
    here, not hoped for: A is held right after it reads the switch file, B runs until it is
    waiting for the lock or (with no lock) has written, then A goes.

    B's arrival is an event, not a 0.3 s sleep: a B the host started late reached the file only
    after A had written, read the fresh file, and passed with no lock at all."""
    import app.config as config_mod
    b, p = _filed(tmp_path, {"chat": True, "edu": True})
    a_read, go, b_parked = threading.Event(), threading.Event(), threading.Event()
    real = Path.read_text

    def read_text(self, *a, **k):
        out = real(self, *a, **k)
        if self.name == SWITCHES and threading.current_thread().name == "switch-a" \
                and not a_read.is_set():
            a_read.set()
            go.wait(10)
        return out

    class _Lock:
        """The switch lock, telling the test when B is about to wait on it."""

        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            if threading.current_thread().name == "switch-b":
                b_parked.set()
            self._lock.acquire()
            return self

        def __exit__(self, *exc):
            self._lock.release()

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(config_mod, "_CLI_ROLES_LOCK", _Lock(config_mod._CLI_ROLES_LOCK))
    errors: list[BaseException] = []

    def switch(role):
        try:
            b.settings.set_cli_role(role, False)
        except BaseException as exc:  # noqa: BLE001 - reported below, not swallowed
            errors.append(exc)
        finally:
            if threading.current_thread().name == "switch-b":
                b_parked.set()      # B wrote without waiting: no lock held it back

    ta = threading.Thread(target=switch, args=("chat",), name="switch-a")
    tb = threading.Thread(target=switch, args=("edu",), name="switch-b")
    ta.start()
    assert a_read.wait(10), "switch A never read the file"
    tb.start()
    assert b_parked.wait(10), "switch B neither reached the lock nor finished"
    go.set()
    ta.join(10)
    tb.join(10)
    monkeypatch.setattr(Path, "read_text", real)
    assert not errors, errors
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": False, "edu": False}


# --- the switch route and what the Rails tab reads --------------------------------------------

@pytest.fixture
def switch_app(tmp_path, monkeypatch):
    """The real broker app over a temp switch file; returns (client, broker, path)."""
    from app.main import app

    def build(content=None, **kw):
        b, p = _filed(tmp_path, content, roles_file=str(tmp_path / "roles.json"),
                      tokens_file=str(tmp_path / "tokens.json"), auth_token="", **kw)
        monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
        monkeypatch.setattr(app.state, "broker", b, raising=False)
        monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
        return TestClient(app), b, p

    return build


def test_the_route_switches_one_role_and_answers_with_the_fresh_state(switch_app):
    client, b, p = switch_app()
    r = client.put("/v1/cli/roles/chat", json={"enabled": False})
    assert r.status_code == 200, r.text
    cli = r.json()["cli"]
    assert cli["enabled_roles"] == ["edu"] and cli["roles_source"] == "file"
    assert set(cli) == {"ready", "blockers", "enabled_roles", "roles_source", "roles_file_set",
                        "roles_problem", "models", "default_model", "role_models", "vision",
                        "streams"}
    assert cli["vision"] is True and cli["streams"] is True
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")


@pytest.mark.parametrize("body", [{"enabled": "false"}, {"enabled": 0}, {}],
                         ids=["string", "number", "missing"])
def test_the_route_takes_a_json_boolean_only(switch_app, body):
    """Lax parsing reads "false" as a bool and 0 as false. A switch must not parse a string."""
    client, _b, p = switch_app()
    assert client.put("/v1/cli/roles/chat", json=body).status_code == 422
    assert not p.exists()


def test_the_route_refuses_an_unknown_role_with_a_400(switch_app):
    client, _b, _p = switch_app()
    r = client.put("/v1/cli/roles/no-such-role", json={"enabled": True})
    assert r.status_code == 400 and "unknown role" in r.json()["detail"]


def test_the_route_answers_409_in_words_when_the_file_cannot_take_a_switch(switch_app):
    client, _b, _p = switch_app("{not json")
    r = client.put("/v1/cli/roles/chat", json={"enabled": True})
    assert r.status_code == 409 and "BROKER_CLI_ROLES_FILE is invalid" in r.json()["detail"]


def test_the_rails_tab_read_carries_the_switch_state(switch_app):
    """roles?inventory=true is the Rails tab's ONE broker read (test_rails_tab_probe_count).
    The switches are drawn from the same response, read through the same function the routing
    uses, so the tab cannot show a role on that the broker refuses."""
    client, b, _p = switch_app({"chat": False, "edu": {"enabled": True, "model": HAIKU}},
                               cli_model=SONNET)
    b.ollama = SimpleNamespace(tags=lambda: _async([]), ps=lambda: _async([]))

    async def _sweep(also_probe=None, tags_read=None, registry=None):
        return [], {}

    b._roles_sweep = _sweep
    cli = client.get("/v1/roles", params={"inventory": "true"}).json()["cli"]
    state = b.cli_state()
    assert cli == {"ready": state["ready"], "blockers": state["blockers"],
                   "enabled_roles": ["edu"], "roles_source": "file", "roles_file_set": True,
                   "roles_problem": "", "models": [HAIKU, SONNET, OPUS],
                   "default_model": SONNET, "role_models": {"edu": HAIKU}, "vision": True,
                   "streams": True}


async def _async(value):
    return value


# --- the Claude model per role (BROKER_CLI_MODELS, and the switch file's object form) ---------
#
# The super-admin chooses which Claude model each role's CLI calls use, beside its switch. The
# model lives in the switch file as {"enabled": <bool>, "model": "<id>"}; a role without one stays
# a bare true or false. Only an id BROKER_CLI_MODELS allows reaches `--model`: refused on write,
# ignored with a note when hand-written. Who pays and whether the CLI runs stay env-only.

def _argv_for(b, ref: str) -> list[str]:
    """The argv the REAL client would run for a call on `ref`, built the way chat() builds it."""
    model = b._cli_model_for(ref, b.settings.cli_effective())
    return b._cli_client(model=model).build_argv("", want_json=False)


def _model_flag(argv: list[str]) -> str | None:
    """The value after `--model`, or None when the argv carries no `--model` at all."""
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def test_a_role_written_as_an_object_is_a_switch_with_a_model(tmp_path):
    b, _p = _filed(tmp_path, {"chat": {"enabled": True, "model": HAIKU}, "edu": True,
                              "code": {"enabled": False, "model": OPUS}})
    eff = b.settings.cli_effective()
    assert (eff.roles, eff.source, eff.problem, eff.note) == ({"chat", "edu"}, "file", "", "")
    assert eff.models == {"chat": HAIKU, "code": OPUS}
    assert b.settings.cli_effective()[:4] == ({"chat", "edu"}, "file", "", "")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert b.cli_decision("@code", "owner-one") == (False, "role_not_eligible")
    state = b.cli_state()
    assert state["ready"] is True and state["roles"] == ["chat", "edu"]
    assert state["models"] == [HAIKU, SONNET, OPUS] and state["default_model"] == ""
    # The choice of every role the file names (on or off), and what each eligible role's call
    # passes: its own model, else none (no BROKER_CLI_MODEL here).
    assert state["role_models"] == {"chat": HAIKU, "code": OPUS}
    assert state["call_models"] == {"chat": HAIKU, "edu": ""}


def test_the_call_for_a_role_passes_its_model_else_the_default_else_none(tmp_path):
    """The argv is the contract: the role's own model, else BROKER_CLI_MODEL, else no --model
    at all (the CLI's own default). Built by the real client from what chat() chooses."""
    switches = {"chat": {"enabled": True, "model": HAIKU}, "edu": True}
    b, _p = _filed(tmp_path, switches)
    assert _model_flag(_argv_for(b, "@chat")) == HAIKU
    assert _model_flag(_argv_for(b, "@edu")) is None
    (tmp_path / "pinned").mkdir()
    pinned, _p2 = _filed(tmp_path / "pinned", switches, cli_model=SONNET)
    assert _model_flag(_argv_for(pinned, "@chat")) == HAIKU
    assert _model_flag(_argv_for(pinned, "@edu")) == SONNET
    assert pinned.cli_state()["call_models"] == {"chat": HAIKU, "edu": SONNET}


def test_a_dispatched_call_runs_the_real_client_with_its_roles_model(tmp_path, monkeypatch):
    """End to end through chat(): the client it builds carries the role's model into the argv,
    and last_by_role records the model the call passed."""
    import app.broker as broker_mod
    b, _p = _filed(tmp_path, {"chat": {"enabled": True, "model": HAIKU}, "edu": True})
    ran: list[str | None] = []

    async def _chat(self, messages, *, format=None, options=None, think=None):
        ran.append(_model_flag(self.build_argv("", want_json=False)))
        return _CLI_OK

    monkeypatch.setattr(broker_mod.ClaudeCliClient, "chat", _chat)
    _run(b.chat("@chat", [{"role": "user", "content": "q"}], requester="owner-one"))
    _run(b.chat("@edu", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert ran == [HAIKU, None]
    last = b.cli_state()["last_by_role"]
    assert (last["@chat"]["model"], last["@edu"]["model"]) == (HAIKU, "")


def test_a_fallback_records_the_model_it_tried_and_a_refusal_records_none(tmp_path):
    b, _seen = _dispatchable(tmp_path, cli_raises=CliUnavailable("timeout", "no answer"),
                             cli_roles_file=str(tmp_path / SWITCHES))
    (tmp_path / SWITCHES).write_text(json.dumps({"chat": {"enabled": True, "model": OPUS}}),
                                     encoding="utf-8")
    _run(b.chat("@chat", [{"role": "user", "content": "q"}], requester="owner-one"))
    assert b.cli_state()["last_by_role"]["@chat"] == {
        "ok": False, "at": b.cli_state()["last_by_role"]["@chat"]["at"], "reason": "timeout",
        "requester": "owner-one", "model": OPUS}


@pytest.mark.parametrize("near", ["Claude-Haiku-5-5", "CLAUDE-HAIKU-5-5", " claude-haiku-5-5",
                                  "claude-haiku-5-5 ", "\tclaude-haiku-5-5"])
def test_a_hand_written_near_miss_of_an_allowed_id_is_not_that_id(tmp_path, near):
    """The read side matches the allowlist EXACTLY. Folded or trimmed, a hand-written
    'Claude-Haiku-5-5 ' would pass the check while its own unstripped spelling went to --model.
    It is refused like any other unknown id: no model recorded, and with no default there is no
    --model flag at all."""
    b, _p = _filed(tmp_path, {"chat": {"enabled": True, "model": near}, "edu": True})
    eff = b.settings.cli_effective()
    assert eff.models == {} and repr(near) in eff.note
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert _model_flag(_argv_for(b, "@chat")) is None
    assert b.cli_state()["role_models"] == {}


def test_a_model_the_allowlist_does_not_hold_is_ignored_with_a_note_and_never_passed(tmp_path):
    """Hand-written into the file, an id BROKER_CLI_MODELS does not allow must not reach
    --model. The switch stands (no blocker), the call uses the default, the note says so, and
    the next switch writes the role back as a bare switch."""
    b, p = _filed(tmp_path, {"chat": {"enabled": True, "model": "claude-x-9"}, "edu": True},
                  cli_model=SONNET)
    eff = b.settings.cli_effective()
    assert (eff.roles, eff.problem, eff.models) == ({"chat", "edu"}, "", {})
    assert "'chat' the model 'claude-x-9'" in eff.note and "BROKER_CLI_MODELS" in eff.note
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert _model_flag(_argv_for(b, "@chat")) == SONNET
    state = b.cli_state()
    assert state["ready"] is True and "cli_roles_file_invalid" not in state["blockers"]
    assert state["roles_problem"] == eff.note and state["role_models"] == {}
    b.settings.set_cli_role("edu", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": False}
    assert b.cli_state()["roles_problem"] == ""


def test_a_narrowed_allowlist_ignores_a_model_it_no_longer_holds(tmp_path):
    b, _p = _filed(tmp_path, {"chat": {"enabled": True, "model": OPUS}}, cli_models=HAIKU)
    assert b.settings.cli_effective().models == {}
    assert _model_flag(_argv_for(b, "@chat")) is None
    assert b.cli_state()["models"] == [HAIKU]


def test_a_null_model_in_the_file_is_no_model(tmp_path):
    b, _p = _filed(tmp_path, {"chat": {"enabled": True, "model": None}})
    eff = b.settings.cli_effective()
    assert (eff.roles, eff.problem, eff.note, eff.models) == ({"chat"}, "", "", {})


@pytest.mark.parametrize("value", [
    {"enabled": "true"},                          # "enabled" held to true-or-false
    {"model": HAIKU},                             # no "enabled"
    {},
    {"enabled": True, "model": 5},                # a model that is not a string
    {"enabled": True, "model": [HAIKU]},
    {"enabled": True, "extra": 1},                # any other key
    {"enabled": True, "Model": HAIKU},
    [True],                                       # a list is neither form
], ids=["enabled-string", "no-enabled", "empty", "model-number", "model-list", "extra-key",
        "model-case", "a-list"])
def test_a_switch_object_of_the_wrong_shape_fails_the_file_closed(tmp_path, value):
    """Strict about shape in both forms, because lenient fails open: an object read loosely
    would switch a role on the file never switched on. The same refusal for a key no role
    answers to, since the shape is judged before the name."""
    for key in ("chat", "no-such-role"):
        (tmp_path / key).mkdir()
        b, _p = _filed(tmp_path / key, {key: value, "edu": True})
        roles, source, problem, _note, _models = b.settings.cli_effective()
        assert (roles, source) == (set(), "file") and problem, (key, problem)
        assert b.cli_decision("@edu", "owner-one") == (False, "cli_roles_file_invalid")
        assert "cli_roles_file_invalid" in b.cli_state()["blockers"]


def test_choosing_a_model_writes_the_object_form_and_leaves_the_switch(tmp_path):
    """With no file yet the first write seeds from BROKER_CLI_ROLES, as a switch does. A model
    for a role the file does not name leaves it OFF: the file decides alone once it exists."""
    b, p = _filed(tmp_path, None)
    b.settings.set_cli_role("chat", None, model=HAIKU)
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "chat": {"enabled": True, "model": HAIKU}, "edu": True}
    b.settings.set_cli_role("code", None, model=OPUS)
    assert json.loads(p.read_text(encoding="utf-8"))["code"] == {"enabled": False, "model": OPUS}
    assert b.cli_decision("@code", "owner-one") == (False, "role_not_eligible")
    assert b.cli_decision("@chat", "owner-one") == (True, "ok")
    assert b.cli_state()["call_models"] == {"chat": HAIKU, "edu": ""}


def test_a_switch_keeps_the_model_and_clearing_it_writes_a_bare_switch_again(tmp_path):
    b, p = _filed(tmp_path, {"chat": {"enabled": True, "model": HAIKU}, "edu": True})
    b.settings.set_cli_role("chat", False)
    assert json.loads(p.read_text(encoding="utf-8"))["chat"] == {"enabled": False, "model": HAIKU}
    b.settings.set_cli_role("chat", True)
    assert json.loads(p.read_text(encoding="utf-8"))["chat"] == {"enabled": True, "model": HAIKU}
    b.settings.set_cli_role("chat", None, model=None)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": True}
    assert b.cli_state()["role_models"] == {}


@pytest.mark.parametrize("model,allowlist", [
    ("claude-x-9", None), ("", None), ("Claude-Haiku-5-5", None), (HAIKU, ""),
], ids=["unknown", "empty", "case-variant", "empty-allowlist"])
def test_a_model_outside_the_allowlist_is_refused_and_writes_nothing(tmp_path, model,
                                                                     allowlist):
    kw = {} if allowlist is None else {"cli_models": allowlist}
    b, p = _filed(tmp_path, None, **kw)
    with pytest.raises(ValueError, match="BROKER_CLI_MODELS"):
        b.settings.set_cli_role("chat", None, model=model)
    assert not p.exists()


def test_a_write_that_changes_nothing_is_refused(tmp_path):
    b, p = _filed(tmp_path, None)
    with pytest.raises(ValueError, match="nothing to change"):
        b.settings.set_cli_role("chat", None)
    assert not p.exists()


@pytest.mark.parametrize("case", sorted(_UNREADABLE_ROLES_JSON))
def test_a_model_cannot_be_chosen_or_cleared_while_roles_json_cannot_be_read(tmp_path, case):
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps({_OVERLAY_ONLY: "m:1b"}), encoding="utf-8")
    b, p = _filed(tmp_path, {"chat": {"enabled": True, "model": HAIKU}},
                  roles_file=str(roles_json))
    _break_roles_json(roles_json, case)
    before = p.read_bytes()
    for model in (OPUS, None):
        with pytest.raises(CliRolesFileError) as exc:
            b.settings.set_cli_role("chat", None, model=model)
        assert _said(case, str(exc.value)) and "no Claude model" in str(exc.value)
        assert p.read_bytes() == before, "a refused model write wrote the file"
    with pytest.raises(CliRolesFileError):
        b.settings.set_cli_role("chat", False, model=OPUS)
    assert p.read_bytes() == before


def test_an_off_while_roles_json_cannot_be_read_keeps_every_model(tmp_path):
    """The kill switch changes its one switch and nothing else: its own model, every other
    role's object, a key no role answers to and an annotation all survive it, and once
    roles.json is fixed the overlay-only role is on again with its model."""
    roles_json = tmp_path / "roles.json"
    good = json.dumps({_OVERLAY_ONLY: "m:1b"})
    roles_json.write_text(good, encoding="utf-8")
    held = {"_comment": "pilot", _OVERLAY_ONLY: {"enabled": True, "model": OPUS},
            "chat": {"enabled": True, "model": HAIKU}, "edu": True,
            "no-such-role": {"enabled": False, "model": "claude-x-9"}}
    b, p = _filed(tmp_path, held, roles_file=str(roles_json))
    _break_roles_json(roles_json, "utf-16")
    b.settings.set_cli_role("chat", False)
    assert json.loads(p.read_text(encoding="utf-8")) == {
        **held, "chat": {"enabled": False, "model": HAIKU}}
    assert b.cli_decision("@chat", "owner-one") == (False, "role_not_eligible")
    _mend_roles_json(roles_json, good)
    assert b.cli_decision(f"@{_OVERLAY_ONLY}", "owner-one") == (True, "ok")
    assert b.cli_state()["call_models"][_OVERLAY_ONLY] == OPUS


def test_the_next_switch_drops_an_object_for_a_key_no_role_answers_to(tmp_path):
    b, p = _filed(tmp_path, {"chat": True, "no-such-role": {"enabled": True, "model": HAIKU}})
    eff = b.settings.cli_effective()
    assert "'no-such-role'" in eff.note
    # A key no role answers to is ignored whole: its model is not recorded either, or status and
    # the Rails tab would list a model for a role that does not exist.
    assert eff.models == {} and b.cli_state()["role_models"] == {}
    b.settings.set_cli_role("edu", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {"chat": True, "edu": True}


def test_the_route_chooses_and_clears_a_model(switch_app):
    client, b, p = switch_app()
    r = client.put("/v1/cli/roles/chat", json={"model": HAIKU})
    assert r.status_code == 200, r.text
    cli = r.json()["cli"]
    assert cli["role_models"] == {"chat": HAIKU} and cli["enabled_roles"] == ["chat", "edu"]
    assert cli["models"] == [HAIKU, SONNET, OPUS] and cli["default_model"] == ""
    assert json.loads(p.read_text(encoding="utf-8"))["chat"] == {"enabled": True, "model": HAIKU}
    # A switch alone leaves the model as it is: only a body that sends `model` changes it.
    r = client.put("/v1/cli/roles/chat", json={"enabled": False})
    assert r.status_code == 200 and r.json()["cli"]["role_models"] == {"chat": HAIKU}, r.text
    assert json.loads(p.read_text(encoding="utf-8"))["chat"] == {"enabled": False, "model": HAIKU}
    r = client.put("/v1/cli/roles/chat", json={"model": None})
    assert r.status_code == 200 and r.json()["cli"]["role_models"] == {}
    assert json.loads(p.read_text(encoding="utf-8"))["chat"] is False
    r = client.put("/v1/cli/roles/edu", json={"enabled": False, "model": OPUS})
    assert r.status_code == 200, r.text
    assert json.loads(p.read_text(encoding="utf-8"))["edu"] == {"enabled": False, "model": OPUS}


def test_the_route_refuses_a_model_outside_the_allowlist_with_a_400(switch_app):
    client, _b, p = switch_app()
    r = client.put("/v1/cli/roles/chat", json={"model": "claude-x-9"})
    assert r.status_code == 400 and "BROKER_CLI_MODELS" in r.json()["detail"], r.text
    assert not p.exists()


@pytest.mark.parametrize("body", [{"model": 5}, {"model": [HAIKU]}, {"model": "x" * 129},
                                  {"enabled": None}],
                         ids=["number", "list", "too-long", "neither"])
def test_the_route_takes_a_model_string_or_null_only(switch_app, body):
    client, _b, p = switch_app()
    assert client.put("/v1/cli/roles/chat", json=body).status_code == 422
    assert not p.exists()


def test_the_route_refuses_a_model_with_a_409_while_roles_json_cannot_be_read(switch_app):
    client, _b, p = switch_app()
    (p.parent / "roles.json").write_bytes(_UNREADABLE_ROLES_JSON["utf-16"][0])
    r = client.put("/v1/cli/roles/chat", json={"model": HAIKU})
    assert r.status_code == 409 and _said("utf-16", r.json()["detail"]), r.text
    assert not p.exists()


# --- how many CLI calls run at once (BROKER_CLI_CONCURRENCY) -----------------------------------
#
# Every CLI call is its own claude.exe. Widening the roles must not mean one process per owner
# request in flight, so a call over the limit WAITS for a slot, and the wait comes out of the
# same cli_timeout budget as the call: a rail's own timeout has to cover cli_timeout plus one
# local answer (checked against the value status reports as cli.timeout_s), and a fresh budget
# after the wait would let the fallback start too late.

def test_no_more_than_the_limit_run_at_once_and_a_waiting_call_is_not_shown_running(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_concurrency=2)

    async def scenario():
        gates = _held_cli(b, calls=3)
        calls = [asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
            for _ in range(3)]
        await _parked(gates[0][0], calls[0])
        await _parked(gates[1][0], calls[1])
        await asyncio.sleep(0.2)
        third_started = gates[2][0].is_set()
        state = b.cli_state()
        gates[0][1].set()
        await _parked(gates[2][0], calls[2])
        gates[1][1].set()
        gates[2][1].set()
        outs = await asyncio.wait_for(asyncio.gather(*calls), 10)
        return third_started, state, outs

    third_started, state, outs = _run(scenario())
    assert third_started is False, "a third CLI call ran with the limit at two"
    assert len(state["active"]) == 2
    assert state["concurrency"] == {"limit": 2, "waiting": 1}
    assert [o["message"]["content"] for o in outs] == ["from cli"] * 3
    assert seen["ollama"] == 0
    assert b.cli_state()["concurrency"] == {"limit": 2, "waiting": 0}


def test_a_call_that_cannot_get_a_slot_in_time_falls_back_and_says_busy(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=0.3)

    async def scenario():
        [(started, release), _second] = _held_cli(b, calls=2)
        first = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(started, first)
        second = await asyncio.wait_for(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-two"), 10)
        release.set()
        await asyncio.wait_for(first, 10)
        return second

    second = _run(scenario())
    assert second["message"]["content"] == "from ollama"
    assert second["cli_fallback"] == {"backend": "ollama", "reason": "busy"}
    assert seen["ollama"] == 1


def test_the_wait_comes_out_of_the_call_budget(tmp_path):
    """A call that waited gets what is LEFT of cli_timeout. Handed a fresh cli_timeout after the
    wait, its fallback could start almost two budgets after it arrived, past the point every
    rail's own timeout was sized for."""
    b, _seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=30)
    budgets: list = []

    async def scenario():
        [(s1, r1), (s2, r2)] = _held_cli(b, calls=2)
        fake = b._cli_client

        def recording(timeout=None, model=None, **kw):
            budgets.append(timeout)
            return fake(timeout=timeout, model=model, **kw)

        b._cli_client = recording
        first = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s1, first)
        second = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-two"))
        await asyncio.sleep(0.5)
        r1.set()
        await _parked(s2, second)
        r2.set()
        await asyncio.wait_for(asyncio.gather(first, second), 10)

    _run(scenario())
    first_budget, second_budget = budgets
    assert 29 < first_budget <= 30
    assert second_budget <= 30 - 0.4, f"the second call got {second_budget:.2f} s after waiting"
    assert second_budget > 0


def test_a_fallback_and_a_cancel_each_give_their_slot_back(tmp_path):
    """Released in the `finally`, so neither path keeps a slot. A leaked slot is a limit that
    shrinks by one per failure until every owner call queues for cli_timeout and falls back."""
    b, seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=5)

    async def scenario():
        [(s1, r1), (s2, _r2), (s3, r3)] = _held_cli(
            b, calls=3, raises=CliUnavailable("rate_limited", "usage limit"))
        # 1: fails and falls back.
        one = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s1, one)
        r1.set()
        out1 = await asyncio.wait_for(one, 10)
        # 2: cancelled while it holds the slot.
        two = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s2, two)
        two.cancel()
        with pytest.raises(asyncio.CancelledError):
            await two
        # 3: must get the one slot at once, not wait out cli_timeout.
        t0 = time.monotonic()
        three = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s3, three)
        waited = time.monotonic() - t0
        r3.set()
        await asyncio.wait_for(three, 10)
        return out1, waited

    out1, waited = _run(scenario())
    assert out1["cli_fallback"]["reason"] == "rate_limited"
    assert waited < 2, f"the third call waited {waited:.1f} s for a slot nobody held"
    assert b.cli_state()["concurrency"]["waiting"] == 0


# --- a call that waited for a slot is decided again when it gets one ---------------------------
#
# cli_decision runs when a call arrives, and a call can then wait for a slot for most of
# cli_timeout. A super-admin who switches its role off meanwhile turns every chip local at once,
# so the call must not reach the CLI either when its slot comes free.

def _queued_behind_one(b, change, shape="text"):
    """Owner call A holds the only CLI slot and owner call B waits for it, both on @chat, which
    the caller has switched on in the file. `change()` runs while B waits, then A finishes.
    `shape` is B's kind of call (`_SHAPES`). Returns (B's answer, whether B entered the CLI
    fake)."""
    async def scenario():
        [(s1, r1), (s2, r2)] = _held_cli(b, calls=2)
        first = asyncio.create_task(
            b.chat("@chat", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s1, first)
        second = asyncio.create_task(
            b.chat("@chat", _SHAPES[shape](), requester="owner-one"))
        await asyncio.sleep(0.2)
        assert b.cli_state()["concurrency"]["waiting"] == 1
        change()
        r1.set()
        await asyncio.wait_for(first, 10)
        entered = asyncio.ensure_future(s2.wait())
        await asyncio.wait({entered, second}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        reached = s2.is_set()
        if not entered.done():
            entered.cancel()
        r2.set()
        return await asyncio.wait_for(second, 10), reached

    return _run(scenario())


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_a_call_queued_before_its_role_is_switched_off_does_not_reach_the_cli(tmp_path, shape):
    switch = tmp_path / SWITCHES
    b, seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=30,
                            cli_roles_file=str(switch))
    b.settings.set_cli_role("chat", True)
    out, reached = _queued_behind_one(b, lambda: b.settings.set_cli_role("chat", False), shape)
    assert reached is False, "a call queued before the switch-off was served by the CLI after it"
    assert out["message"]["content"] == "from ollama"
    assert out["cli_fallback"] == {"backend": "ollama", "reason": "role_not_eligible"}
    assert seen["ollama"] == 1


def test_a_call_queued_when_the_switch_file_turns_invalid_falls_back_closed(tmp_path):
    switch = tmp_path / SWITCHES
    b, _seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=30,
                             cli_roles_file=str(switch))
    b.settings.set_cli_role("chat", True)
    out, reached = _queued_behind_one(
        b, lambda: switch.write_text("{not json", encoding="utf-8"))
    assert reached is False
    assert out["cli_fallback"] == {"backend": "ollama", "reason": "cli_roles_file_invalid"}


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_a_queued_call_whose_decision_did_not_change_still_reaches_the_cli(tmp_path, shape):
    """The control for the two above: without a change while it waits, the queued call is
    served by the CLI, so "it never reached the CLI" there is the re-decision, not the queue."""
    b, seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=30,
                            cli_roles_file=str(tmp_path / SWITCHES))
    b.settings.set_cli_role("chat", True)
    out, reached = _queued_behind_one(b, lambda: None, shape)
    assert reached is True and out["message"]["content"] == "from cli"
    assert seen["ollama"] == 0


async def _wait_for_311(aw, timeout):
    """Python 3.11's asyncio.wait_for in the part that matters here: the awaitable is wrapped in
    a task, so it starts on a LATER turn of the loop (3.12 awaits it inline)."""
    task = asyncio.ensure_future(aw)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        raise asyncio.TimeoutError()
    return task.result()


def test_a_call_that_lost_a_free_slot_to_a_same_tick_arrival_is_decided_again_on_python_3_11(
        tmp_path, monkeypatch):
    """Two owner calls created in one tick both saw one free slot. Taken through wait_for, as on
    3.11 (supported: pyproject says >=3.11), the slot went to one of them a turn later and the
    other waited believing it had not, so it was decided again on its arrival's read: a role
    switched off while it waited still reached the CLI. A free slot is taken without a turn of
    the loop now, so the call that waits knows it does."""
    b, seen = _dispatchable(tmp_path, cli_concurrency=2, cli_timeout=30,
                            cli_roles_file=str(tmp_path / SWITCHES))
    b.settings.set_cli_role("chat", True)
    native = asyncio.wait_for
    monkeypatch.setattr(asyncio, "wait_for", _wait_for_311)
    q = [{"role": "user", "content": "q"}]

    async def scenario():
        [(s1, r1), (s2, r2), (s3, r3)] = _held_cli(b, calls=3)
        first = asyncio.create_task(b.chat("@chat", q, requester="owner-one"))
        await _parked(s1, first)
        second = asyncio.create_task(b.chat("@chat", q, requester="owner-one"))
        third = asyncio.create_task(b.chat("@chat", q, requester="owner-one"))  # same tick
        await _parked(s2, second)
        await asyncio.sleep(0.2)
        assert b.cli_state()["concurrency"]["waiting"] == 1
        b.settings.set_cli_role("chat", False)
        r1.set()
        await native(first, 10)
        entered = asyncio.ensure_future(s3.wait())
        await asyncio.wait({entered, third}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        reached = s3.is_set()
        if not entered.done():
            entered.cancel()
        r2.set()
        r3.set()
        return await native(second, 10), await native(third, 10), reached

    out2, out3, reached = _run(scenario())
    assert out2["message"]["content"] == "from cli", "the call that took the free slot"
    assert reached is False, "a call that waited through the switch-off reached the CLI"
    assert out3["cli_fallback"] == {"backend": "ollama", "reason": "role_not_eligible"}
    assert seen["ollama"] == 1


# --- a caller that hung up costs nothing more ----------------------------------------------------
#
# A rail that gives up (its own timeout, or the person leaving) closes the connection, and
# uvicorn does not cancel the handler. Asked through `client_gone` (the route passes Starlette's
# request.is_disconnected): a call that waited for a slot does not start on the CLI, and a
# failed CLI call does not start a local answer. Neither is a fallback.

@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.parametrize("hung_up", [True, False], ids=["hung-up", "still-there"])
def test_a_call_whose_caller_hung_up_while_it_waited_never_reaches_the_cli(tmp_path, hung_up,
                                                                            shape):
    b, seen = _dispatchable(tmp_path, cli_concurrency=1, cli_timeout=30)
    gone = {"now": False}

    async def client_gone():
        return gone["now"]

    async def scenario():
        [(s1, r1), (s2, r2)] = _held_cli(b, calls=2)
        first = asyncio.create_task(
            b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one"))
        await _parked(s1, first)
        second = asyncio.create_task(
            b.chat("@iep", _SHAPES[shape](), requester="owner-one",
                   client_gone=client_gone))
        await asyncio.sleep(0.2)
        gone["now"] = hung_up
        r1.set()
        await asyncio.wait_for(first, 10)
        if hung_up:
            with pytest.raises(ClientGone):
                await asyncio.wait_for(second, 10)
            return s2.is_set(), None
        await _parked(s2, second)
        r2.set()
        return s2.is_set(), await asyncio.wait_for(second, 10)

    reached, out = _run(scenario())
    state = b.cli_state()
    if hung_up:
        assert reached is False and seen["ollama"] == 0
        assert state["refusals"].get("client_gone") == 1
        assert state["fallbacks"]["count"] == 0
    else:
        assert reached is True and out["message"]["content"] == "from cli"
        assert "client_gone" not in state["refusals"]
    # The slot is back either way, and nothing is shown running.
    assert b._cli_slots.locked() is False and state["active"] == []


@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.parametrize("hung_up", [True, False], ids=["hung-up", "still-there"])
def test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback(tmp_path, hung_up,
                                                                         capsys, shape):
    """And is NOT counted as one. cli.fallbacks, fallbacks.last and a "CLI FALLBACK" log line
    all say a local answer ran, and for a caller who hung up none does: an operator reading
    them would count work that never happened. The CLI's own failure is still recorded per
    role, because it did fail. A caller still there gets the fallback, counted as before."""
    b, seen = _dispatchable(tmp_path, cli_raises=CliUnavailable("timeout", "no answer"))
    asked: list[int] = []

    async def client_gone():
        # There when the slot is taken, gone (or not) by the time the CLI has failed.
        asked.append(1)
        return hung_up and len(asked) > 1

    if hung_up:
        with pytest.raises(ClientGone):
            _run(b.chat("@iep", _SHAPES[shape](), requester="owner-one",
                        client_gone=client_gone))
        assert (seen["cli"], seen["ollama"]) == (1, 0)
    else:
        out = _run(b.chat("@iep", _SHAPES[shape](), requester="owner-one",
                          client_gone=client_gone))
        assert out["cli_fallback"]["reason"] == "timeout" and seen["ollama"] == 1
    assert len(asked) == 2
    state = b.cli_state()
    log = capsys.readouterr().err
    assert state["last_by_role"]["@iep"]["reason"] == "timeout"
    if hung_up:
        assert state["fallbacks"] == {"count": 0, "last": None}
        assert "CLI FALLBACK" not in log and "skipped" in log
    else:
        assert state["fallbacks"]["count"] == 1
        assert state["fallbacks"]["last"] == {"requested": "@iep", "reason": "timeout",
                                              "detail": "no answer"}
        assert "CLI FALLBACK: @iep -> local (timeout): no answer" in log


def test_a_question_about_the_caller_that_cannot_be_answered_counts_as_still_there(tmp_path):
    b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK)

    async def client_gone():
        raise RuntimeError("no transport")

    out = _run(b.chat("@iep", [{"role": "user", "content": "q"}], requester="owner-one",
                      client_gone=client_gone))
    assert out["message"]["content"] == "from cli" and seen["cli"] == 1


async def _asgi_post(app, path: str, payload: dict, *, hung_up: bool) -> list[dict]:
    """POST through the raw ASGI interface, with a connection that has (or has not) closed
    once the body was read: the one way to show the route asks the CONNECTION, which a
    TestClient cannot close."""
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        if hung_up:
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()        # still connected: nothing more ever arrives

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 50000),
             "server": ("127.0.0.1", 11500),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    await app(scope, receive, send)
    return sent


@pytest.mark.parametrize("hung_up", [True, False], ids=["hung-up", "still-there"])
def test_the_chat_route_hands_the_broker_the_connections_own_answer(tmp_path, monkeypatch,
                                                                     hung_up):
    from app.main import app
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="")
    heard: list = []

    async def chat(model, messages, **kw):
        heard.append(await kw["client_gone"]())
        return {"message": {"role": "assistant", "content": "ok"}}

    b.chat = chat
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    sent = _run(_asgi_post(app, "/v1/chat", {"model": "@iep", "messages": [
        {"role": "user", "content": "q"}], "requester": "owner-one"}, hung_up=hung_up))
    assert heard == [hung_up]
    assert sent[0]["status"] == 200


def test_the_chat_route_answers_a_caller_who_hung_up_with_a_499(tmp_path, monkeypatch):
    from app.main import app
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"), auth_token="")

    async def chat(model, messages, **kw):
        raise ClientGone("the caller hung up while this call waited for a CLI slot")

    b.chat = chat
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    r = TestClient(app).post("/v1/chat", json={"model": "@iep", "messages": [
        {"role": "user", "content": "q"}]})
    assert r.status_code == 499 and "hung up" in r.json()["detail"]


# --- who may name the person asking (`requester`) ----------------------------------------------
#
# The broker cannot check `requester`; it trusts whoever sends it. A full-scope credential (the
# shared token the gateway and most rails hold, or a named full token) could repoint every role
# anyway. An
# INFERENCE-scoped named token is limited trust (a LAN workstation), so its requester is dropped
# before cli_decision and the call runs locally, as background work.

@pytest.fixture
def chat_app(tmp_path, monkeypatch):
    """The real broker app, its CLI and Ollama faked; returns (client, broker, seen)."""
    from app.main import app

    def build(auth_token="shared-secret"):
        b, seen = _dispatchable(tmp_path, cli_result=_CLI_OK,
                                tokens_file=str(tmp_path / "tokens.json"),
                                roles_file=str(tmp_path / "roles.json"), auth_token=auth_token)
        monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
        monkeypatch.setattr(app.state, "broker", b, raising=False)
        monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
        return TestClient(app), b, seen

    return build


_OWNER_ASKS = {"model": "@iep", "messages": [{"role": "user", "content": "q"}],
               "requester": "owner-one"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("credential", ["shared", "named-full"])
def test_a_full_scope_credential_may_name_who_is_asking(chat_app, credential):
    client, b, seen = chat_app()
    token = "shared-secret" if credential == "shared" else b.settings.add_token("gw", "full")[0]
    r = client.post("/v1/chat", json=_OWNER_ASKS, headers=_bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == "from cli"
    assert (seen["cli"], seen["ollama"]) == (1, 0)


def test_an_inference_token_cannot_name_an_owner_onto_the_cli(chat_app):
    """The pre-existing hole: a workstation token holder who is not the owner sent
    requester=<owner> and reached the owner's subscription on any role switched on."""
    client, b, seen = chat_app()
    token, _row = b.settings.add_token("studio", "inference")
    r = client.post("/v1/chat", json=_OWNER_ASKS, headers=_bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == "from ollama"
    assert (seen["cli"], seen["ollama"]) == (0, 1)
    assert "cli_fallback" not in r.json()
    # Dropped BEFORE the decision, so it is counted exactly as background work is.
    assert b.cli_state()["refusals"] == {"no_requester": 1}


def test_an_open_broker_takes_requester_from_any_caller(chat_app):
    """No token configured at all: every route is open, the full-scope ones included, and so is
    this. Recorded so the posture is a decision, not an accident; a real deployment sets one."""
    client, _b, seen = chat_app(auth_token="")
    r = client.post("/v1/chat", json=_OWNER_ASKS)
    assert r.status_code == 200 and r.json()["message"]["content"] == "from cli"
    assert seen["cli"] == 1


def test_an_image_posted_to_the_chat_route_reaches_the_cli_as_an_image(tmp_path, monkeypatch):
    """The whole road a rail's photo takes, minus the real process: the HTTP body, the schema's
    `images` field, Broker.chat, the REAL client, and the stream-json line on stdin."""
    from app.main import app
    out = [{"type": "system", "subtype": "init"},
           {"type": "result", "is_error": False, "result": "a lemon tart"}]
    proc = _StreamProc(("\n".join(json.dumps(x) for x in out) + "\n").encode())

    async def fake_exec(*argv, **kw):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    b = _broker(tmp_path, tokens_file=str(tmp_path / "tokens.json"),
                roles_file=str(tmp_path / "roles.json"), auth_token="shared-secret")
    monkeypatch.setattr(app.state, "settings", b.settings, raising=False)
    monkeypatch.setattr(app.state, "broker", b, raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    r = TestClient(app).post("/v1/chat", headers=_bearer("shared-secret"), json={
        "model": "@iep", "requester": "owner-one", "messages": _img_q()})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == "a lemon tart"
    assert "cli_fallback" not in r.json()
    [image, text] = json.loads(proc.stdin_seen)["message"]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": _PNG_B64}
    assert text == {"type": "text", "text": "what is this"}


# --- the status block carries the CLI timeout ---------------------------------------------------

def test_status_reports_the_cli_timeout_every_rail_has_to_outlast(tmp_path):
    """Each CLI-wired rail's own client timeout is checked against the figure read here,
    because BROKER_CLI_TIMEOUT is usually set only in the broker's service environment."""
    assert _broker(tmp_path, cli_timeout=275).cli_state()["timeout_s"] == 275.0
