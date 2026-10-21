"""The Claude CLI backend: a streamed text call, chat_stream over --include-partial-messages.

Split out of test_cli_backend.py by concern on 2026-10-19 (its docstring says what the
backend's tests assert, and lists the files); the fakes and shared helpers are
cli_backend_fakes.py's.
"""
import asyncio
import copy
import json
import os
import subprocess
import sys
import time

import pytest

from app import cli_backend
from app.cli_backend import CliUnavailable

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    REQUIRED_FLAGS,
    _FakeProc,
    _PNG_B64,
    _Q,
    _SEEN,
    _SETTINGS,
    _after_first_delta,
    _alive,
    _cancel_again_and_again,
    _client,
    _ev,
    _frames,
    _img_q,
    _lines,
    _partial,
    _patch,
    _said,
    _streaming_proc,
    _system_prompt,
    _system_temp,
    _text_delta,
    group_kills,
    real_group_kill,
)


# --- a streamed text call: chat_stream over --include-partial-messages -------------------------

_STREAM_HEAD = ["C:/fake/claude.exe", "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--tools", "", "--strict-mcp-config",
                "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
                "--settings", _SETTINGS]


async def test_a_streamed_call_runs_the_argv_measured_and_sends_the_prompt_on_stdin(monkeypatch):
    """The text path's I/O on the way in (the prompt as plain text on stdin, closed), stream-json
    with partial messages on the way out (`-p` prints stream-json only with --verbose, and
    --include-partial-messages works only with both: claude 2.1.296's --help), and every
    isolation flag in the text path's order."""
    proc = _streaming_proc([_lines(_partial("Hi"))])
    seen = _patch(monkeypatch, proc)
    await _frames(_client(model="claude-haiku-5-5").chat_stream(
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "q"}]))
    assert seen["argv"] == _STREAM_HEAD + ["--model", "claude-haiku-5-5",
                                           "--system-prompt", "be terse"]
    assert proc.stdin_seen == b"q" and proc.stdin_closed


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_a_streamed_argv_carries_each_isolation_flag(flag):
    argv = _client().build_argv("sys", want_json=False, partial=True)
    assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert json.loads(argv[argv.index("--settings") + 1])["claudeMdExcludes"]
    assert argv[argv.index("--system-prompt") + 1] == "sys"
    assert "--json-schema" not in argv and "--input-format" not in argv and "json" not in argv


@pytest.mark.parametrize("kw", [{"want_json": True},
                                {"want_json": True, "schema": {"type": "object"}},
                                {"want_json": False, "schema": {"type": "object"}},
                                {"want_json": False, "stream_json": True}],
                         ids=["json", "json-schema", "schema", "stream-json-input"])
def test_a_streamed_argv_is_plain_text_only(kw):
    """JSON, a schema and images go buffered (Broker.chat_stream); this argv refuses them."""
    with pytest.raises(ValueError):
        _client().build_argv("", partial=True, **kw)


async def test_a_streamed_call_with_images_is_refused_before_anything_starts(monkeypatch):
    seen = _patch(monkeypatch, _streaming_proc([_lines(_partial("a pie"))]))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_img_q(_PNG_B64)))
    assert exc.value.reason == "vision_unsupported" and "argv" not in seen


@pytest.mark.parametrize("last,content", [("Assistant", "{"), ("tool", "{"), ("assistant", " \n")],
                         ids=["Assistant", "tool", "blank-prefill"])
async def test_a_streamed_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts(
        monkeypatch, last, content):
    seen = _patch(monkeypatch, _streaming_proc([_lines(_partial("ok"))]))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream([{"role": "user", "content": "q"},
                                             {"role": last, "content": content}]))
    assert exc.value.reason == "prefill_unsupported" and "argv" not in seen


_STREAM_PREFILLED = [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "Your next move: "}]


async def test_a_streamed_prefill_sends_only_its_continuation(monkeypatch):
    """The opening arrives in pieces ("Your", " next move", ":"), none of them a frame; the
    first frame is what follows it, and the rest streams as it comes. The buffered call's
    instruction, and the user's turn alone on stdin."""
    proc = _streaming_proc([_lines(_partial("Your", " next move", ":", " Lead", " with it."))])
    seen = _patch(monkeypatch, proc)
    frames = await _frames(_client().chat_stream(copy.deepcopy(_STREAM_PREFILLED)))
    assert _said(frames) == ["Lead", " with it.", ""]
    assert "\n<<<\nYour next move:\n>>>" in _system_prompt(seen["argv"])
    assert proc.stdin_seen == b"q"


async def test_nothing_is_yielded_before_the_prefill_is_confirmed(monkeypatch):
    """While the reply could still be the opening, nothing goes out, however long the CLI takes
    over the rest: here it has written "Your next move:" and stops until the test says.

    The stream reads stdout inline, so once it asks for the bytes after the opening it has
    handled every line of the opening, and any frame those lines made is already in `got`. The
    test waits for that read rather than sleeping 0.2 s, which a loaded host could outlast."""
    go_on = asyncio.Event()
    records = _partial("Your next", " move:", " Lead.")
    cut = _after_first_delta(records) + 1        # both pieces of the opening, none of the answer
    head = _lines(records[:cut])
    proc = _streaming_proc([head, go_on, _lines(records[cut:])])
    _patch(monkeypatch, proc)
    got: list = []

    async def consume():
        async for frame in _client().chat_stream(copy.deepcopy(_STREAM_PREFILLED)):
            got.append(frame)

    task = asyncio.ensure_future(consume())
    await asyncio.wait_for(proc.stdout.parked.wait(), 10)   # asked for what follows the opening
    assert proc.stdout.served == len(head) and got == []
    go_on.set()
    await asyncio.wait_for(task, 5)
    assert _said(got) == ["Lead.", ""]


async def test_a_streamed_reply_that_leaves_the_prefill_falls_back_before_any_frame(monkeypatch):
    """Decided the moment the reply can no longer be the opening, not once it is as long as the
    opening and not at its end: this CLI writes six characters and goes on running for ever,
    and the call is refused there with nothing yielded, the tree killed, neither text quoted."""
    proc = _streaming_proc([_lines(_partial("**Your", tail=False))], hold=True)
    seen = _patch(monkeypatch, proc)
    got: list = []

    async def consume():
        async for frame in _client().chat_stream(copy.deepcopy(_STREAM_PREFILLED)):
            got.append(frame)

    t0 = time.monotonic()
    with pytest.raises(CliUnavailable) as exc:
        await asyncio.wait_for(consume(), 5)
    assert exc.value.reason == "prefill_mismatch" and got == []
    assert time.monotonic() - t0 < 2
    assert proc.killed or "taskkill" in seen
    assert "Your" not in str(exc.value)


@pytest.mark.parametrize("texts,result,said", [
    ((), "Your next move: lead with it.", ["lead with it.", ""]),
    (("Your next",), "Your next move: lead with it.", ["lead with it.", ""]),
], ids=["whole-on-the-result-line", "result-extends-an-undecided-opening"])
async def test_a_prefill_undecided_when_the_deltas_end_is_judged_on_the_result_line(
        monkeypatch, texts, result, said):
    _patch(monkeypatch, _streaming_proc([_lines(_partial(*texts, result=result))]))
    frames = await _frames(_client().chat_stream(copy.deepcopy(_STREAM_PREFILLED)))
    assert _said(frames) == said


@pytest.mark.parametrize("texts,result,reason", [
    ((), "Here is the move: lead.", "prefill_mismatch"),
    (("Your next",), "Your next steps: lead.", "prefill_mismatch"),
    (("Your next move:",), "Your next move:", "empty"),
], ids=["whole-and-ignored", "result-leaves-the-opening", "the-prefill-alone"])
async def test_a_prefill_undecided_when_the_deltas_end_can_still_be_refused(
        monkeypatch, texts, result, reason):
    _patch(monkeypatch, _streaming_proc([_lines(_partial(*texts, result=result))]))
    got: list = []
    with pytest.raises(CliUnavailable) as exc:
        async for frame in _client().chat_stream(copy.deepcopy(_STREAM_PREFILLED)):
            got.append(frame)
    assert exc.value.reason == reason and got == []


async def test_text_deltas_stream_as_ollama_frames_and_the_done_frame_carries_the_usage(
        monkeypatch):
    """Every rail's stream reader parses Ollama's shape, so the frames are Ollama's. The done
    frame's `cli` is the buffered answer's: tokens summed as `_result_fields` sums them, and the
    instructions the CLI could not honour."""
    _patch(monkeypatch, _streaming_proc([_lines(_partial("One", "\nTwo", "\nThree"))]))
    frames = await _frames(_client(model="claude-haiku-5-5").chat_stream(
        _Q, options={"temperature": 0.2}, think=False))
    body, done = frames[:-1], frames[-1]
    assert [f["message"] for f in body] == [{"role": "assistant", "content": t}
                                           for t in ("One", "\nTwo", "\nThree")]
    assert all(f["model"] == "claude-haiku-5-5" and f["done"] is False
               and f["created_at"].endswith("Z") for f in body)
    assert done["done"] is True and done["done_reason"] == "stop"
    assert done["message"] == {"role": "assistant", "content": ""}
    assert done["cli"] == {"backend": "claude", "input_tokens": 946, "output_tokens": 44,
                           "duration_ms": 1567, "total_cost_usd": 0.0003, "num_turns": 1,
                           "dropped": ["temperature", "think"]}
    assert "prompt_eval_count" not in done


async def test_an_answer_cut_by_max_tokens_is_done_for_length(monkeypatch):
    _patch(monkeypatch, _streaming_proc([_lines(_partial("Half an ans", stop="max_tokens"))]))
    frames = await _frames(_client().chat_stream(_Q))
    assert frames[-1]["done_reason"] == "length"


async def test_thinking_and_other_deltas_are_never_content(monkeypatch):
    """A thinking model's reasoning, its signature, a tool call's arguments, an empty delta and
    the whole-message line the CLI repeats after the deltas: none of them is the answer."""
    records = _partial("Answer")
    at = _after_first_delta(records) - 1
    records[at:at] = [
        _ev({"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "Let me think"}}),
        _ev({"type": "content_block_delta", "index": 0,
             "delta": {"type": "signature_delta", "signature": "c2ln"}}),
        _ev({"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": "{\"q\":"}}),
        _text_delta(""),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "not a delta"}]}}]
    _patch(monkeypatch, _streaming_proc([_lines(records)]))
    frames = await _frames(_client().chat_stream(_Q))
    assert _said(frames) == ["Answer", ""]


async def test_a_frame_arrives_while_the_cli_is_still_writing(monkeypatch):
    """The point of streaming: the first words reach the caller while the CLI is still running,
    not once it has exited."""
    go_on = asyncio.Event()
    records = _partial("Hel", "lo")
    cut = _after_first_delta(records)
    proc = _streaming_proc([_lines(records[:cut]), go_on, _lines(records[cut:])])
    _patch(monkeypatch, proc)
    agen = _client().chat_stream(_Q)
    first = await asyncio.wait_for(agen.__anext__(), 2)
    assert first["message"]["content"] == "Hel" and proc.returncode is None
    go_on.set()
    assert _said(await _frames(agen)) == ["lo", ""]


async def test_no_text_within_the_budget_is_a_timeout_and_the_tree_is_killed(monkeypatch):
    """The first text has the call's remaining budget, however long the CLI sits in
    message_start (a thinking model is silent there). Past it: `timeout`, the tree killed."""
    records = _partial(tail=False)                          # no text, and it never ends
    proc = _streaming_proc([_lines(records)], hold=True)
    seen = _patch(monkeypatch, proc)
    t0 = time.monotonic()
    with pytest.raises(CliUnavailable) as exc:
        await asyncio.wait_for(_frames(_client(timeout=0.3).chat_stream(_Q)), 5)
    assert exc.value.reason == "timeout" and time.monotonic() - t0 < 2
    assert proc.killed or "taskkill" in seen


async def test_a_stream_that_goes_quiet_after_its_first_token_is_stalled(monkeypatch):
    monkeypatch.setattr(cli_backend, "_STREAM_IDLE_S", 0.2)
    records = _partial("Hel")
    proc = _streaming_proc([_lines(records[:_after_first_delta(records)])], hold=True)
    seen = _patch(monkeypatch, proc)
    got: list = []

    async def consume():
        async for frame in _client(timeout=30).chat_stream(_Q):
            got.append(frame)

    with pytest.raises(CliUnavailable) as exc:
        await asyncio.wait_for(consume(), 5)
    assert exc.value.reason == "stalled" and _said(got) == ["Hel"]
    assert proc.killed or "taskkill" in seen


async def test_a_stream_cancelled_again_and_again_still_takes_the_tree(monkeypatch):
    """A consumer cancelled the anyio way (again at every await) while the CLI is mid-answer.
    The generator's `finally` kills the tree BEFORE it waits for its stdin and stderr helpers:
    the other way round, that wait is cancelled first and the kill never starts."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)
    records = _partial("Hel")
    proc = _streaming_proc([_lines(records[:_after_first_delta(records)])], hold=True)
    seen = _patch(monkeypatch, proc, spawn_yields=True)
    got: list = []

    async def consume():
        async for frame in _client(timeout=30).chat_stream(_Q):
            got.append(frame)

    task = asyncio.ensure_future(consume())
    for _ in range(200):
        if got:
            break
        await asyncio.sleep(0.01)
    assert _said(got) == ["Hel"], "the first frame never arrived"
    await _cancel_again_and_again(task)
    await asyncio.wait_for(asyncio.gather(*cli_backend._REAPERS), 5)
    assert "taskkill" in seen, "the tree kill never ran: the cancellation reached it"


async def test_the_whole_answer_is_bounded_from_its_first_token(monkeypatch):
    """A CLI that never goes quiet and never finishes: a delta every 50 ms. The idle rule never
    fires, so the answer's own bound is what ends it."""
    steps: list = [_lines(_partial("a", tail=False))]
    for _ in range(60):
        steps += [0.05, _lines([_text_delta("b")])]
    proc = _streaming_proc(steps, hold=True)
    _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable) as exc:
        await asyncio.wait_for(_frames(_client(timeout=30, answer_timeout=0.4).chat_stream(_Q)), 5)
    assert exc.value.reason == "timeout" and "first token" in exc.value.detail


async def test_a_nonzero_exit_mid_stream_quotes_no_transcript_line(monkeypatch):
    """Killed after its text and before its result line, stderr empty. The detail reaches the
    service log and /v1/status, so it is the fixed sentence, never the text deltas (the model's
    answer: rail content), and the reason is `failed`, not one read out of the model's words."""
    records = _partial(_SEEN, result=False)
    _patch(monkeypatch, _streaming_proc([_lines(records)], code=1))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_Q))
    assert exc.value.reason == "failed"
    assert exc.value.detail == (f"the CLI exited with code 1 and printed no error sentence; "
                                f"its transcript ({len(records)} lines) is not recorded")
    for leaked in ("worksheet", "pupil", "quota"):
        assert leaked not in str(exc.value)


async def test_a_nonzero_exit_mid_stream_reports_the_clis_own_words(monkeypatch):
    _patch(monkeypatch, _streaming_proc([_lines(_partial("Hel", result=False))], code=1,
                                        err=b"Error: Claude usage limit reached\n"))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_Q))
    assert exc.value.reason == "rate_limited"
    assert exc.value.detail == "Error: Claude usage limit reached"


async def test_a_streamed_transcript_without_a_result_line_is_bad_stream(monkeypatch):
    _patch(monkeypatch, _streaming_proc([_lines(_partial("half an answer", result=False))]))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_Q))
    assert exc.value.reason == "bad_stream"


async def test_a_stream_with_no_output_at_all_is_empty(monkeypatch):
    _patch(monkeypatch, _streaming_proc([]))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_Q))
    assert exc.value.reason == "empty"


async def test_an_is_error_result_on_a_stream_is_classified(monkeypatch):
    _patch(monkeypatch, _streaming_proc([_lines(_partial(
        result="Claude usage limit reached", is_error=True))]))
    with pytest.raises(CliUnavailable) as exc:
        await _frames(_client().chat_stream(_Q))
    assert exc.value.reason == "rate_limited"


@pytest.mark.parametrize("texts,result,said", [
    (("Hello",), "Hello, world", ["Hello", ", world", ""]),
    ((), "All at once", ["All at once", ""]),
    (("Hello\n",), "Hello", ["Hello\n", ""]),
], ids=["result-extends-the-deltas", "no-delta-at-all", "result-trimmed"])
async def test_the_result_line_settles_what_was_streamed(monkeypatch, texts, result, said):
    """The result line is the verdict and the deltas a preview of it. A result that extends them
    sends the rest; with no delta it is sent whole; one that only drops the outer whitespace
    sends nothing more."""
    _patch(monkeypatch, _streaming_proc([_lines(_partial(*texts, result=result))]))
    assert _said(await _frames(_client().chat_stream(_Q))) == said


async def test_a_result_that_disagrees_with_the_streamed_text_is_refused(monkeypatch):
    """The caller has already shown text the CLI's own verdict does not contain. Refused, never
    patched, and neither text is quoted."""
    _patch(monkeypatch, _streaming_proc([_lines(_partial("Hello", result="Goodbye"))]))
    got: list = []
    with pytest.raises(CliUnavailable) as exc:
        async for frame in _client().chat_stream(_Q):
            got.append(frame)
    assert exc.value.reason == "stream_mismatch" and _said(got) == ["Hello"]
    assert "Hello" not in str(exc.value) and "Goodbye" not in str(exc.value)


async def test_a_second_message_after_the_answer_began_is_refused(monkeypatch):
    """With tools off a call is one turn. A second message_start after text went out is a turn
    the model took on its own, which a stream of frames has no way to show as one."""
    records = _partial("First")
    records.insert(_after_first_delta(records),
                   _ev({"type": "message_start", "message": {"role": "assistant"}}))
    _patch(monkeypatch, _streaming_proc([_lines(records)]))
    got: list = []
    with pytest.raises(CliUnavailable) as exc:
        async for frame in _client().chat_stream(_Q):
            got.append(frame)
    assert exc.value.reason == "bad_stream" and _said(got) == ["First"]


async def test_a_runaway_stream_is_cut_off_at_the_cap(monkeypatch):
    monkeypatch.setattr(cli_backend, "_STDOUT_CAP", 256 * 1024)
    proc = _FakeProc(b"", b"", 0, endless=True)
    seen = _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable) as exc:
        await asyncio.wait_for(_frames(_client(timeout=3).chat_stream(_Q)), 5)
    assert exc.value.reason == "too_large"
    assert proc.stdout.served <= 256 * 1024 + cli_backend._READ_CHUNK
    assert proc.killed or "taskkill" in seen


async def test_a_line_longer_than_a_stream_readers_limit_is_read_whole(monkeypatch):
    """A long answer's result line runs to hundreds of KB, past the 64 KiB at which a
    StreamReader's readline gives up. Read here through a REAL StreamReader, default limit."""
    long = "word " * 40_000
    proc = _FakeProc(b"", b"", 0)
    reader = asyncio.StreamReader()
    reader.feed_data(_lines(_partial(long)))
    reader.feed_eof()
    proc.stdout = reader
    _patch(monkeypatch, proc)
    assert _said(await _frames(_client().chat_stream(_Q))) == [long, ""]


async def test_a_streamed_call_runs_in_the_same_workdir_with_the_same_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "full-scope")
    seen = _patch(monkeypatch, _streaming_proc([_lines(_partial("ok"))]))
    client = _client(workdir=str(tmp_path), oauth_token="tok-1")
    await _frames(client.chat_stream(_Q))
    assert seen["cwd"] == str(tmp_path) and seen["env"] == client.build_env(seen["env"]["TEMP"])
    assert "BROKER_AUTH_TOKEN" not in seen["env"]
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1"


async def test_on_posix_a_streamed_cli_leads_a_session_of_its_own(monkeypatch):
    monkeypatch.setattr(cli_backend, "_POSIX", True)
    seen = _patch(monkeypatch, _streaming_proc([_lines(_partial("ok"))]))
    await _frames(_client().chat_stream(_Q))
    assert seen["kw"].get("start_new_session") is True


async def test_a_real_streaming_child_dies_with_its_tree_when_the_consumer_closes(
        tmp_path, monkeypatch, real_group_kill):
    """REAL processes. The stand-in CLI starts a grandchild (the node child the real CLI runs,
    which is what keeps generating), prints one text delta and hangs. The consumer reads that
    frame and closes the generator: the whole tree must die."""
    pidfile = tmp_path / "gc.pid"
    script = ("import subprocess, sys, time\n"
              "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
              f"print({json.dumps(_text_delta('Hello'))!r}, flush=True)\n"
              "time.sleep(60)\n")
    client = _client(timeout=20)
    monkeypatch.setattr(client, "build_argv", lambda *a, **k: [sys.executable, "-c", script])
    gc_pid = None
    try:
        agen = client.chat_stream(_Q)
        first = await asyncio.wait_for(agen.__anext__(), 20)
        assert first["message"]["content"] == "Hello"
        gc_pid = int(pidfile.read_text())
        assert _alive(gc_pid)
        await agen.aclose()
        deadline = time.monotonic() + 10
        while _alive(gc_pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        assert not _alive(gc_pid), "the CLI's child outlived the closed stream"
    finally:
        if gc_pid is None and pidfile.exists() and pidfile.read_text():
            gc_pid = int(pidfile.read_text())
        if gc_pid is not None and _alive(gc_pid):
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/PID", str(gc_pid)], capture_output=True)
            else:
                import signal
                os.kill(gc_pid, signal.SIGKILL)
