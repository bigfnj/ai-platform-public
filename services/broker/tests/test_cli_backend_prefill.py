"""The Claude CLI backend: a prefill (a trailing assistant turn), emulated as an instruction and
then verified.

Split out of test_cli_backend.py by concern on 2026-10-19 (its docstring says what the
backend's tests assert, and lists the files); the fakes and shared helpers are
cli_backend_fakes.py's.
"""
import copy

import pytest

from app import cli_backend
from app.cli_backend import CliUnavailable

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    _FakeProc,
    _PNG_B64,
    _blocks,
    _client,
    _patch,
    _result,
    _system_prompt,
    _system_temp,
    _transcript,
    group_kills,
)


# --- a prefill (a trailing assistant turn) is emulated: an instruction, then verified ----------

#: smb-partner-enablement's Scenario Builder opens its "next move" pass this way.
_OPENING = "Your next move: "
_PREFILLED = [{"role": "system", "content": "be terse"}, {"role": "user", "content": "q"},
              {"role": "assistant", "content": _OPENING}]


def _without_system_prompt(argv: list[str]) -> list[str]:
    at = argv.index("--system-prompt")
    return argv[:at] + argv[at + 2:]


async def test_a_trailing_assistant_turn_is_an_instruction_and_only_its_continuation_returns(
        monkeypatch):
    """Ollama continues a trailing assistant turn (a PREFILL) and answers with the continuation
    alone; the Scenario Builder joins `prefill + answer`. The CLI has no such turn, so the turn
    leaves the prompt, the system prompt asks for a reply that begins with its text, and the
    reply is cut to what follows it. The prompt is the user's turn verbatim (no `Assistant:`
    line), and the argv is the plain call's in every flag but the system prompt's text."""
    seen = _patch(monkeypatch, _FakeProc(_result("Your next move: lead with Business Premium."),
                                         b"", 0))
    out = await _client().chat(copy.deepcopy(_PREFILLED))
    assert out["message"]["content"] == "lead with Business Premium."
    system = _system_prompt(seen["argv"])
    assert system.startswith("be terse\n\n") and "\n<<<\nYour next move:\n>>>" in system
    assert "Begin your reply with exactly the text between the two marker lines" in system
    plain = _client().build_argv("be terse", want_json=False)
    assert _without_system_prompt(seen["argv"]) == _without_system_prompt(plain)


async def test_the_prefilled_prompt_carries_no_assistant_turn(monkeypatch):
    proc = _FakeProc(_result("Your next move: call them."), b"", 0)
    _patch(monkeypatch, proc)
    await _client().chat(copy.deepcopy(_PREFILLED))
    assert proc.stdin_seen == b"q"


@pytest.mark.parametrize("prefill,reply,said", [
    ("Your next move: ", "Your next move: lead with it.", "lead with it."),
    ("Your next move: ", "\n\n  Your next move: lead with it.", "lead with it."),
    ("Your next move: ", "Your next move:\nlead with it.", "lead with it."),
    ("Your next move: ", "Your next move:  lead with it.", " lead with it."),
    ("## Where the value comes from\n", "## Where the value comes from\n\nTime saved.",
     "\nTime saved."),
    ("## Where the value comes from\n", "## Where the value comes from\nTime saved.",
     "Time saved."),
    ("**", "**What breaks first?** Seats.", "What breaks first?** Seats."),
    ('**"', '**"Why change?"** Because.', 'Why change?"** Because.'),
    ("It is", "It is red.", " red."),
], ids=["space", "leading-blank-lines", "newline-for-space", "two-spaces", "heading-blank-line",
        "heading", "bold", "bold-quote", "mid-sentence"])
def test_the_continuation_is_what_follows_the_prefill(prefill, reply, said):
    """What the caller appends to its prefill, as Ollama would hand it back: the reply's leading
    whitespace skipped, the prefill's trailing whitespace read as a boundary (the reply's own
    kept when it starts with the prefill's, so a heading keeps its blank line; replaced by the
    prefill's when it does not, so a space is never doubled)."""
    assert cli_backend.continuation(reply, prefill) == said


@pytest.mark.parametrize("prefill,reply", [
    ("Your next move: ", "**Your next move:** lead with it."),
    ("Your next move: ", "Sure! Your next move: lead with it."),
    ("Your next move: ", "your next move: lead with it."),
    ("Dear ", "Dearest friend,"),
    ("## Where the value comes from\n", "## Where the value comes from and why it matters\n"),
    ("**", "1. **What breaks first?**"),
    ("Your next move: ", "Your next"),
], ids=["bolded", "preamble", "case", "runs-through-the-space", "heading-extended",
        "different-opening", "shorter-than-the-prefill"])
def test_a_reply_that_does_not_begin_with_the_prefill_has_no_continuation(prefill, reply):
    assert cli_backend.continuation(reply, prefill) is None


async def test_a_reply_that_ignores_the_prefill_falls_back_without_quoting_either(monkeypatch):
    """Verified, never trusted: a reply that leads with anything else would reach the caller
    joined to its prefill, the opening twice or a preamble in the middle. Refused as
    `prefill_mismatch` (a fallback), and the detail, which is logged, quotes neither text."""
    _patch(monkeypatch, _FakeProc(_result("Sure! Here is the move: call them."), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(copy.deepcopy(_PREFILLED))
    assert exc.value.reason == "prefill_mismatch"
    assert "Sure" not in str(exc.value) and "next move" not in str(exc.value)


async def test_a_reply_that_is_the_prefill_alone_is_empty(monkeypatch):
    _patch(monkeypatch, _FakeProc(_result("Your next move:"), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(copy.deepcopy(_PREFILLED))
    assert exc.value.reason == "empty"


async def test_an_image_call_with_a_text_prefill_is_emulated_the_same_way(monkeypatch):
    """The stream-json path takes the same instruction: the turn leaves the user message (one
    text block, the question's) and the transcript's answer is cut to the continuation."""
    proc = _FakeProc(_transcript("It is red."), b"", 0)
    seen = _patch(monkeypatch, proc)
    out = await _client().chat([{"role": "user", "content": "what colour?",
                                 "images": [_PNG_B64]},
                                {"role": "assistant", "content": "It is"}])
    assert out["message"]["content"] == " red."
    assert [b["type"] for b in _blocks(proc.stdin_seen)] == ["image", "text"]
    assert _blocks(proc.stdin_seen)[1]["text"] == "what colour?"
    assert "\n<<<\nIt is\n>>>" in _system_prompt(seen["argv"])


@pytest.mark.parametrize("msgs,why", [
    ([{"role": "user", "content": "q"}, {"role": "Assistant", "content": "Dear family, {"}],
     "'Assistant'"),
    ([{"role": "user", "content": "q"}, {"role": "model", "content": "Dear family, {"}],
     "'model'"),
    ([{"role": "user", "content": "q"}, {"role": "tool", "content": "Dear family, {"}],
     "'tool'"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "content": "Dear family, {", "images": [_PNG_B64]}], "images"),
    ([{"role": "user", "content": "q"}, {"role": "assistant", "content": "  \n"}], "no text"),
    ([{"role": "user", "content": "q"}, {"role": "assistant", "content": None}], "no text"),
    ([{"role": "system", "content": "Dear family, {"},
      {"role": "assistant", "content": "Dear family, {"}], "does not follow a user turn"),
    ([{"role": "user", "content": "q"}, {"role": "assistant", "content": "Dear family, {"},
      {"role": "assistant", "content": "Dear family, {"}], "does not follow a user turn"),
], ids=["Assistant", "model", "tool", "images-on-the-prefill", "blank", "not-a-string",
        "nothing-before-it", "two-assistant-turns"])
@pytest.mark.parametrize("shape", ["text", "image"])
async def test_a_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts(
        monkeypatch, msgs, why, shape):
    """Only Ollama's own shape is emulated: exactly "assistant", plain text, after a user turn.
    Anything else is refused before any process starts, text call or image call, so the local
    model answers it as it was put. The detail says why and never quotes the turn's text."""
    msgs = copy.deepcopy(msgs)
    if shape == "image":
        msgs[0] = {**msgs[0], "images": [_PNG_B64]} if msgs[0]["role"] == "user" else msgs[0]
    seen = _patch(monkeypatch, _FakeProc(_result("Dear family, {}"), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(msgs)
    assert exc.value.reason == "prefill_unsupported" and why in exc.value.detail
    assert "argv" not in seen
    assert "Dear family" not in exc.value.detail


@pytest.mark.parametrize("fmt", ["json", {"type": "object"}], ids=["json", "schema"])
async def test_a_prefill_with_a_format_is_refused_before_anything_starts(monkeypatch, fmt):
    """A schema is enforced on the whole reply, and a bare json answer is rebuilt from the
    largest JSON value in it: neither leaves an opening to verify and cut off."""
    seen = _patch(monkeypatch, _FakeProc(_result('{"a": 1}'), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"},
                              {"role": "assistant", "content": "{"}], format=fmt)
    assert exc.value.reason == "prefill_unsupported" and "argv" not in seen


@pytest.mark.parametrize("msgs", [
    [{"role": "user", "content": "q"}, {"role": "system", "content": "be terse"}],
    [{"role": "assistant", "content": "earlier"}, {"role": "user", "content": "q"}],
    [{"content": "q"}],
], ids=["system-after-user", "assistant-then-user", "no-role"])
async def test_a_call_whose_last_turn_is_the_users_is_sent(monkeypatch, msgs):
    """The control: a system turn after the user's is not a turn, an assistant turn EARLIER is
    history, and a turn with no role is the user's, as the text path reads it."""
    seen = _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0))
    out = await _client().chat(msgs)
    assert out["message"]["content"] == "ok" and "argv" in seen
