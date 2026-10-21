"""The Claude CLI backend: the call itself, from the spawn to the answer or the fallback, and
a finished answer that a pipe something else keeps open does not hold.

Split out of test_cli_backend.py by concern on 2026-10-19 (its docstring says what the
backend's tests assert, and lists the files); the fakes and shared helpers are
cli_backend_fakes.py's.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app import cli_backend
from app.cli_backend import CliUnavailable

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    _FakeProc,
    _FakeStream,
    _PNG_B64,
    _alive,
    _cancel_again_and_again,
    _client,
    _img_q,
    _patch,
    _result,
    _system_temp,
    _transcript,
    group_kills,
    real_group_kill,
)


# --- the call --------------------------------------------------------------

async def test_chat_returns_an_ollama_shaped_response(monkeypatch):
    """Rails read `data["message"]["content"]`. The new backend fits the old shape so the
    translation lives here rather than in every rail."""
    seen = _patch(monkeypatch, _FakeProc(_result("hi there"), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}])
    assert out["message"]["content"] == "hi there"
    assert out["message"]["role"] == "assistant"
    assert out["done"] is True
    assert seen["argv"][0] == "C:/fake/claude.exe"


async def test_chat_sends_the_prompt_on_stdin(monkeypatch):
    proc = _FakeProc(_result("ok"), b"", 0)
    _patch(monkeypatch, proc)
    await _client().chat([{"role": "user", "content": "the question"}])
    assert proc.stdin_seen == b"the question"
    # Closed, not just written: `claude -p` reads stdin to EOF, so an open pipe is a call that
    # waits out its whole timeout and then falls back.
    assert proc.stdin_closed


async def test_usage_is_namespaced_not_folded_into_ollama_counters(monkeypatch):
    """A token count from a different billing system landing in `prompt_eval_count` would be
    aggregated as GPU work by anything reading Ollama's fields."""
    _patch(monkeypatch, _FakeProc(
        _result("ok", usage={"input_tokens": 10, "cache_read_input_tokens": 5,
                             "output_tokens": 7}, duration_ms=1234), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}])
    assert out["cli"]["input_tokens"] == 15
    assert out["cli"]["output_tokens"] == 7
    assert out["cli"]["duration_ms"] == 1234
    assert "prompt_eval_count" not in out


async def test_json_mode_extracts_from_a_fenced_answer(monkeypatch):
    _patch(monkeypatch, _FakeProc(_result('```json\n{"es": "hola"}\n```'), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}], format="json")
    assert json.loads(out["message"]["content"]) == {"es": "hola"}


async def test_json_mode_falls_back_when_the_answer_is_prose(monkeypatch):
    """Returning the prose would surface two layers away as the RAIL's "malformed JSON"
    error, blaming the model for a translation this layer declined to finish."""
    _patch(monkeypatch, _FakeProc(_result("I could not do that."), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}], format="json")
    assert exc.value.reason == "bad_json"


async def test_a_schema_format_uses_the_cli_s_own_structured_output(monkeypatch):
    """`--json-schema` exists on claude 2.1.292 and enforces the shape itself, which beats
    asking for JSON in prose and checking afterwards. This path used to REFUSE a schema on
    the grounds that the CLI had no structured-output mode; that was simply untrue of the
    version the rest of this file measures against."""
    seen = _patch(monkeypatch, _FakeProc(_result('{"a": 1}'), b"", 0))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    out = await _client().chat([{"role": "user", "content": "q"}], format=schema)
    assert json.loads(out["message"]["content"]) == {"a": 1}
    assert json.loads(seen["argv"][seen["argv"].index("--json-schema") + 1]) == schema


_CUT_ARRAY = json.dumps([{"i": i, "v": "x" * 20} for i in range(3000)])[:-40]


@pytest.mark.parametrize("image", [False, True], ids=["text", "image"])
@pytest.mark.parametrize("fmt", ["json", {"type": "array"}], ids=["json", "schema"])
async def test_a_json_answer_cut_off_at_max_tokens_falls_back(monkeypatch, fmt, image):
    """The audit's case: a 3,000-item array cut off at the token limit. extract_json finds the
    largest value LEFT in it, one of the items, and that went back as the answer, an object
    where the rail asked for an array, with done_reason "stop". The result object says why it
    ended (`stop_reason`, on every result object in claude 2.1.299's schema), so it is a
    fallback, on the text path and on an image call's transcript alike."""
    body = (_transcript(_CUT_ARRAY, stop_reason="max_tokens") if image
            else _result(_CUT_ARRAY, stop_reason="max_tokens"))
    _patch(monkeypatch, _FakeProc(body, b"", 0))
    messages = _img_q(_PNG_B64) if image else [{"role": "user", "content": "q"}]
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(messages, format=fmt)
    assert exc.value.reason == "bad_json"
    assert "max_tokens" in exc.value.detail


@pytest.mark.parametrize("stop", ["end_turn", None, 7])
async def test_a_json_answer_that_ended_on_its_own_is_served(monkeypatch, stop):
    """Only `max_tokens` is a cut-off: an answer that ended its turn, or a result with no stop
    reason or a malformed one, is served as before."""
    _patch(monkeypatch, _FakeProc(_result('{"a": 1}', stop_reason=stop), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}], format="json")
    assert json.loads(out["message"]["content"]) == {"a": 1}
    assert out["done_reason"] == "stop"


@pytest.mark.parametrize("image", [False, True], ids=["text", "image"])
async def test_a_text_answer_cut_off_at_max_tokens_is_done_for_length(monkeypatch, image):
    """A text answer cut off is still the answer, and Ollama says so with done_reason "length",
    as the streamed path already did (test_an_answer_cut_by_max_tokens_is_done_for_length). The
    buffered path said "stop" whatever the result object said."""
    body = (_transcript("Half an ans", stop_reason="max_tokens") if image
            else _result("Half an ans", stop_reason="max_tokens"))
    _patch(monkeypatch, _FakeProc(body, b"", 0))
    messages = _img_q(_PNG_B64) if image else [{"role": "user", "content": "q"}]
    out = await _client().chat(messages)
    assert out["message"]["content"] == "Half an ans"
    assert out["done_reason"] == "length"


async def test_a_json_answer_nested_past_the_parser_is_bad_json(monkeypatch):
    """extract_json let the parser's RecursionError out, and the broker counted the call as a CLI
    failure named "RecursionError". It is a reply with no JSON value this path can read."""
    _patch(monkeypatch, _FakeProc(_result("[" * 20000 + "]" * 20000), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}], format="json")
    assert exc.value.reason == "bad_json"


def _wrapped_reply(chars: int) -> str:
    """A JSON array with prose around it, exactly `chars` characters long. Its strings hold
    quotes and brackets, which are what the scan acts on."""
    head, tail = "Here is the result: ", " -- hope that helps."
    items, size = [], len(head) + len(tail) + 2
    while size < chars - 96:
        items.append({"i": len(items), "v": 'a "quoted" {brace} [bracket]'})
        size += len(json.dumps(items[-1])) + 2
    text = head + json.dumps(items) + tail
    return text + " " * (chars - len(text))


@pytest.mark.parametrize("delta,off_loop", [(-1, False), (0, True)],
                         ids=["just-under-the-threshold", "at-the-threshold"])
async def test_a_large_json_answer_is_read_off_the_event_loop(monkeypatch, delta, off_loop):
    """extract_json is linear now, but Python: a reply with prose around a 3 MiB value held the
    event loop, and every other request with it, for about 210 ms. From _JSON_THREAD_FROM
    characters it runs in a thread; below, where a thread could not give the loop a turn before
    the scan ends, inline."""
    import threading
    text = _wrapped_reply(cli_backend._JSON_THREAD_FROM + delta)
    threads: list[int] = []
    real = cli_backend.extract_json

    def recording(t):
        threads.append(threading.get_ident())
        return real(t)

    monkeypatch.setattr(cli_backend, "extract_json", recording)
    _patch(monkeypatch, _FakeProc(_result(text), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}], format="json")
    assert json.loads(out["message"]["content"])[0] == {"i": 0, "v": 'a "quoted" {brace} [bracket]'}
    assert len(threads) == 1
    assert (threads[0] != threading.get_ident()) is off_loop, (
        f"a {len(text)}-character answer was read {'on' if off_loop else 'off'} the loop")


def test_the_json_thread_threshold_is_64_kib():
    """Where the inline scan reaches the GIL's switch interval on this box (about 3.7 ms for
    64 KiB with prose around the value); the reasoning is on the constant."""
    assert cli_backend._JSON_THREAD_FROM == 64 * 1024


async def test_a_ticker_keeps_running_while_a_large_json_answer_is_read(monkeypatch):
    """The outcome: beside a buffered JSON call whose reply wraps a 4 MiB value in prose, a
    ticker that sleeps 1 ms never waits half as long as extract_json takes on that reply (timed
    here first, so the bound follows the box). Inline the loop waited for all of it and more; in
    a thread what remains is the decoder's and the writer's single C calls, and the parse of the
    CLI's result line, which runs in the loop either way."""
    text = _wrapped_reply(4 * 1024 * 1024)
    began = time.perf_counter()
    assert cli_backend.extract_json(text) is not None
    scan = time.perf_counter() - began
    _patch(monkeypatch, _FakeProc(_result(text), b"", 0))
    worst = 0.0
    done = False

    async def tick():
        nonlocal worst
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now

    ticker = asyncio.ensure_future(tick())
    await asyncio.sleep(0.05)
    worst = 0.0
    try:
        out = await _client().chat([{"role": "user", "content": "q"}], format="json")
    finally:
        done = True
        await ticker
    assert out["message"]["content"].startswith('[{"i": 0')
    assert worst < scan / 2, (f"the event loop waited {worst * 1000:.0f} ms; extract_json takes "
                              f"{scan * 1000:.0f} ms on this reply")


def test_a_schema_replaces_the_prose_instruction_rather_than_joining_it():
    """Both would be belt and braces; it is also two different statements of the contract,
    and the prose one is the weaker. With a schema the instruction is redundant."""
    argv = _client().build_argv("sys", want_json=True, schema={"type": "object"})
    assert "raw JSON" not in argv[argv.index("--system-prompt") + 1]


def test_plain_json_mode_sends_no_schema():
    argv = _client().build_argv("sys", want_json=True)
    assert "--json-schema" not in argv


async def test_nonzero_exit_classifies_a_usage_limit(monkeypatch):
    _patch(monkeypatch, _FakeProc(b"", b"Claude usage limit reached", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "rate_limited"


async def test_a_nonzero_exit_reports_the_result_sentence_not_the_json_envelope(monkeypatch):
    """The real shape of a bad token: exit 1, EMPTY stderr, and the reason inside the result
    object on stdout. The first version classified the raw envelope and recorded its first
    300 characters -- usage counters and a session id, cut off before the sentence that said
    what was wrong. Found by a real CLI call with a bad token, not by any mock. The session id
    below is synthetic."""
    body = json.dumps({
        "type": "result", "subtype": "success", "is_error": True,
        "duration_api_ms": 0, "session_id": "00000000-0000-4000-8000-000000000000",
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0},
        "result": "Failed to authenticate. API Error: 401 Invalid bearer token",
        "api_error_status": 401}).encode()
    _patch(monkeypatch, _FakeProc(body, b"", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "not_signed_in"
    assert exc.value.detail.startswith("Failed to authenticate")


async def test_is_error_result_is_classified(monkeypatch):
    """Exit code 0 with `is_error: true` is a real shape: the CLI reports a refusal in-band."""
    body = json.dumps({"type": "result", "is_error": True,
                       "result": "Please run /login"}).encode()
    _patch(monkeypatch, _FakeProc(body, b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "not_signed_in"


async def test_empty_output_is_a_fallback_not_an_empty_answer(monkeypatch):
    """An empty string handed back as a successful answer is how a rail writes a blank
    document and reports success."""
    _patch(monkeypatch, _FakeProc(b"", b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "empty"


async def test_timeout_kills_the_tree(monkeypatch):
    """A timed-out generation that keeps running still bills the owner and still holds a
    node child. The direct kill reaches the launcher only, so the tree kill is the point."""
    proc = _FakeProc(_result("too late"), b"", 0, hang=True)
    seen = _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable) as exc:
        await _client(timeout=0.05).chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "timeout"
    assert proc.killed or "taskkill" in seen


async def test_on_posix_a_timeout_kills_the_clis_whole_process_group(monkeypatch, group_kills):
    """The POSIX half of the tree kill. `proc.kill()` alone reached the launcher only and
    orphaned the node child that does the work, and keeps billing. The CLI now starts as a
    session leader, so its pid IS its process group, and the group is what gets killed."""
    monkeypatch.setattr(cli_backend, "_POSIX", True)
    proc = _FakeProc(_result("too late"), b"", 0, hang=True)
    seen = _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable):
        await _client(timeout=0.05).chat([{"role": "user", "content": "q"}])
    assert seen["kw"].get("start_new_session") is True, "the CLI was not given its own session"
    assert group_kills == [proc.pid]
    assert "taskkill" not in seen


async def test_on_windows_taskkill_takes_the_tree_and_no_session_is_asked_for(monkeypatch,
                                                                              group_kills):
    """The Windows path is unchanged: taskkill /T, and nothing POSIX-only passed to the spawn."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)
    proc = _FakeProc(_result("too late"), b"", 0, hang=True)
    seen = _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable):
        await _client(timeout=0.05).chat([{"role": "user", "content": "q"}])
    assert seen["taskkill"][:5] == ["taskkill", "/F", "/T", "/PID", str(proc.pid)]
    assert "start_new_session" not in seen["kw"]
    assert group_kills == []


async def test_a_timed_out_cli_takes_its_children_with_it(tmp_path, monkeypatch,
                                                         real_group_kill):
    """REAL processes, on whichever platform runs this. The "CLI" starts a child of its own
    that inherits the stdout pipe and outlives a plain kill of its parent (on POSIX any orphan
    does; on Windows Python's children are in no kill-on-close job), then hangs. After the
    timeout that child must be gone as well: taskkill /T on Windows, the group kill on POSIX."""
    pidfile = tmp_path / "gc.pid"
    script = ("import subprocess, sys, time\n"
              "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
              "time.sleep(60)\n")
    client = _client(timeout=3)
    monkeypatch.setattr(client, "build_argv", lambda *a, **k: [sys.executable, "-c", script])
    gc_pid = None
    try:
        t0 = time.monotonic()
        with pytest.raises(CliUnavailable) as exc:
            await client.chat([{"role": "user", "content": "q"}])
        assert exc.value.reason == "timeout"
        gc_pid = int(pidfile.read_text())
        deadline = time.monotonic() + 10
        while _alive(gc_pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        assert not _alive(gc_pid), "the CLI's child outlived the kill"
        # The child held the pipe; only a tree kill lets the drain finish inside the reap bound.
        assert time.monotonic() - t0 < 3 + 5, "the reap waited on a pipe the child still held"
    finally:
        if gc_pid is None and pidfile.exists():
            gc_pid = int(pidfile.read_text())
        if gc_pid is not None and _alive(gc_pid):
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/PID", str(gc_pid)], capture_output=True)
            else:
                import signal
                os.kill(gc_pid, signal.SIGKILL)


async def test_missing_executable_is_a_fallback(monkeypatch):
    async def boom(*a, **kw):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "not_installed"


async def test_a_shim_that_cannot_exec_is_a_fallback(monkeypatch):
    """WinError 193 is what a .cmd/.ps1 shim produces under create_subprocess_exec. It must
    degrade to the local model, not surface as a 502 on a rail that worked yesterday."""
    async def boom(*a, **kw):
        raise OSError(193, "%1 is not a valid Win32 application")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "not_installed"


async def test_unsupported_sampling_options_are_reported_not_silently_eaten(monkeypatch):
    """A rail may ask for temperature 0.3 and num_ctx 16384; the CLI has neither. Refusing the call
    over it would be worse than running at the default, but discarding the request with no
    trace is the silent approximation this module says it does not do."""
    _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}],
                               options={"temperature": 0.3, "num_ctx": 16384}, think=False)
    assert set(out["cli"]["dropped"]) == {"temperature", "num_ctx", "think"}


async def test_no_dropped_key_when_nothing_was_dropped(monkeypatch):
    _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}])
    assert "dropped" not in out["cli"]


async def test_a_malformed_usage_block_does_not_break_a_successful_call(monkeypatch):
    """`usage` comes from the CLI. A float string or a nested object there used to raise
    straight out of a call that had already succeeded and been paid for."""
    _patch(monkeypatch, _FakeProc(
        _result("ok", usage={"input_tokens": "12.5", "output_tokens": {"n": 1}}), b"", 0))
    out = await _client().chat([{"role": "user", "content": "q"}])
    assert out["message"]["content"] == "ok"
    assert out["cli"]["input_tokens"] == 0


async def test_the_child_is_reaped_when_the_caller_is_cancelled(monkeypatch):
    """Reaping lives in a `finally`, not in the two except clauses it replaced. media.py
    already learned this: an except-based reap misses the paths nobody enumerated, and each
    one leaves a generation running against the owner's subscription with nobody reading it."""
    proc = _FakeProc(_result("too late"), b"", 0, hang=True)
    seen = _patch(monkeypatch, proc)
    task = asyncio.ensure_future(_client(timeout=30).chat([{"role": "user", "content": "q"}]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed or "taskkill" in seen


async def test_the_reap_completes_under_a_cancellation_delivered_again_and_again(monkeypatch):
    """`_kill_tree` awaits its spawn of taskkill, and swallows whatever stops it, so awaited in a
    task that is cancelled again at every await, taskkill never runs and only the launcher dies.
    Its node child keeps generating, and billing, for nobody. `_reap` runs the kill as its own
    task, so the cancellations reach the caller's wait and never the kill; and the caller ends
    only once the kill has (cli_backend.settle), because a CLI call gives its slot back when its
    reap returns, and one that returned at the first cancellation freed the slot while taskkill
    was still running."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)        # the taskkill path, on any box
    proc = _FakeProc(b"", b"", 0, hang=True)
    seen = _patch(monkeypatch, proc, spawn_yields=True)
    reap = asyncio.ensure_future(cli_backend._reap(proc))
    await asyncio.sleep(0)                                    # started, waiting on the kill
    await _cancel_again_and_again(reap)
    assert reap.cancelled(), "the cancellation was swallowed instead of re-raised"
    assert all(t.done() for t in cli_backend._REAPERS), (
        "the reap's caller ended before the kill did")
    await asyncio.sleep(0)                                    # the set's own done callback
    assert seen.get("taskkill", [])[:5] == ["taskkill", "/F", "/T", "/PID", str(proc.pid)], (
        "the tree kill never ran: the cancellation reached it")
    assert proc.killed and not cli_backend._REAPERS


async def test_a_buffered_call_cancelled_again_and_again_still_takes_the_tree(monkeypatch):
    """chat()'s own reap, under the same cancellation: a stream that asked for a format runs the
    buffered call inside the cancelled task itself, so its `finally` is where the kill has to
    survive being cancelled again."""
    monkeypatch.setattr(cli_backend, "_POSIX", False)
    proc = _FakeProc(_result("too late"), b"", 0, hang=True)
    seen = _patch(monkeypatch, proc, spawn_yields=True)
    call = asyncio.ensure_future(_client(timeout=30).chat([{"role": "user", "content": "q"}]))
    await asyncio.sleep(0.05)                                 # spawned, waiting on the answer
    await _cancel_again_and_again(call)
    await asyncio.wait_for(asyncio.gather(*cli_backend._REAPERS), 5)
    assert "taskkill" in seen, "the tree kill never ran: the cancellation reached it"


async def test_oversized_stdout_is_refused(monkeypatch):
    proc = _FakeProc(b"x" * (8 * 1024 * 1024 + 1), b"", 0)
    seen = _patch(monkeypatch, proc)
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "too_large"
    assert proc.killed or "taskkill" in seen


async def test_a_runaway_stdout_is_cut_off_at_the_cap_not_read_to_the_end(monkeypatch):
    """The cap is a MEMORY bound, so it has to act while the child is still writing. Checked
    after `communicate()`, as it first was, it bounded nothing: a child that never stops
    writing was read into RAM until the timeout, and only then refused. Here the stream never
    ends, so the only way to get `too_large` (rather than `timeout`) is to stop reading at the
    cap, and the amount read proves how much the broker held."""
    monkeypatch.setattr(cli_backend, "_STDOUT_CAP", 256 * 1024)
    proc = _FakeProc(b"", b"", 0, endless=True)
    seen = _patch(monkeypatch, proc)
    t0 = time.monotonic()
    with pytest.raises(CliUnavailable) as exc:
        await _client(timeout=3).chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "too_large"
    assert time.monotonic() - t0 < 2
    assert proc.stdout.served <= 256 * 1024 + cli_backend._READ_CHUNK
    # Killed, not abandoned: a child left writing into a pipe nobody reads keeps billing.
    assert proc.killed or "taskkill" in seen


async def test_a_real_runaway_child_is_killed_at_the_cap(monkeypatch, real_group_kill):
    """The same property through a REAL pipe and a real process tree, because a fake stream
    cannot show what a StreamReader buffers on its own or whether the kill lands. The child
    writes four times the cap and then hangs without closing stdout, so the call can only end
    inside its timeout by stopping at the cap. Deliberately NOT a child that writes for ever:
    if the cap regressed to post-hoc, that child would be read into this process's RAM at
    pipe speed until the timeout, and the test would cost the box gigabytes to fail."""
    monkeypatch.setattr(cli_backend, "_STDOUT_CAP", 1024 * 1024)
    real = asyncio.create_subprocess_exec
    children = []

    async def spy(*argv, **kw):
        p = await real(*argv, **kw)
        if argv and argv[0] == sys.executable:
            children.append(p)
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    writer = ("import sys, time\n"
              "sys.stdout.buffer.write(b'x' * (4 * 1024 * 1024))\n"
              "sys.stdout.buffer.flush()\n"
              "time.sleep(600)\n")
    client = _client(timeout=10)
    monkeypatch.setattr(client, "build_argv", lambda *a, **k: [sys.executable, "-c", writer])
    t0 = time.monotonic()
    with pytest.raises(CliUnavailable) as exc:
        await client.chat([{"role": "user", "content": "q"}])
    assert exc.value.reason == "too_large"
    assert time.monotonic() - t0 < 8, "ended at the timeout, not at the cap"
    [child] = children
    assert child.returncode is not None, "the runaway child was left running"
    # Drained to EOF after the kill. A pipe left paused at the cap never sees EOF, its
    # transport is never closed, and every refused call leaks one.
    assert child.stdout.at_eof(), "stdout was abandoned mid-stream, leaking its transport"


async def test_the_reap_does_not_wait_for_a_pipe_only_the_drain_can_close(monkeypatch):
    """asyncio reports a child's exit only once its pipe transports close, and a stdout pipe
    paused at the cap closes only when it is drained. Reaping BEFORE draining therefore sat out
    the whole _REAP_TIMEOUT: on Linux the real-child test above took 10 s in 5 of 8 runs
    (python:3.11-slim, 2026-10-07), and never on this Windows box. This fake has exactly that
    ordering, so the old order fails here on every platform, every run.

    The verdict is the exit being seen: in the old order the reap's wait is cut off at its bound
    before the drain can close the pipe, so it never sees one. It was also `< 1.0 s` from the
    call, which a drain slowed by a loaded host could exceed with the order right; the bound is
    5 s now, far from a drain's microseconds, so only the old order reaches it."""
    monkeypatch.setattr(cli_backend, "_REAP_TIMEOUT", 5.0)
    # The POSIX path, so no taskkill is spawned. Only the one attribute _kill_tree branches on
    # (it used to replace the module's whole `sys`); the group kill itself is recorded, not sent,
    # by the autouse `group_kills` fixture.
    monkeypatch.setattr(cli_backend, "_POSIX", True)

    class _PausedPipe:
        def __init__(self):
            self.drained = asyncio.Event()

        async def read(self, n=-1):
            self.drained.set()
            return b""

    class _Proc:
        pid = 4242
        returncode = None

        def __init__(self):
            self.stdout, self.stderr = _PausedPipe(), _PausedPipe()

        def kill(self):
            pass

        async def wait(self):
            await self.stdout.drained.wait()      # the exit is reported once the pipe closes
            self.returncode = -9
            return self.returncode

    proc = _Proc()
    await asyncio.wait_for(cli_backend._kill_tree(proc), 30)
    assert proc.returncode == -9, (
        "the reap waited out its timeout before draining, so it gave up before it saw the exit")


async def test_stderr_is_drained_to_the_end_but_only_its_head_is_kept():
    """Draining matters as much as the cap: a child blocked writing into a full stderr pipe
    never exits, and the call would sit there until the timeout. Keeping only the head is what
    makes draining it safe."""
    stream = _FakeStream(b"e" * 200_000)
    head = await cli_backend._read_head(stream)
    assert len(head) == cli_backend._STDERR_CAP
    assert stream.served == 200_000


async def test_runs_in_the_configured_workdir(monkeypatch):
    """A fixed, empty directory. Run anywhere else and the CLI reads whatever CLAUDE.md or
    AGENTS.md happens to sit above the broker's working directory."""
    seen = _patch(monkeypatch, _FakeProc(_result("ok"), b"", 0))
    await _client(workdir="C:/fake/work").chat([{"role": "user", "content": "q"}])
    assert seen["cwd"] == "C:/fake/work"


# --- a finished answer is not held by a pipe something else keeps open -------------------

class _HeldStderrStdin:
    def write(self, _d):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def test_a_held_stderr_does_not_hold_a_finished_answer(monkeypatch):
    """stdout closed and the CLI exited, but stderr never reaches EOF (a grandchild still
    holds it). It used to be awaited to EOF first, so the complete answer waited out the
    call's whole timeout and came back as a fallback. Now stderr gets a short grace."""
    monkeypatch.setattr(cli_backend, "_STDERR_GRACE", 0.05)

    async def go():
        out = asyncio.StreamReader()
        out.feed_data(b'{"result": "ok"}')
        out.feed_eof()
        err = asyncio.StreamReader()
        err.feed_data(b"a warning")            # and never an EOF

        async def wait():
            return 0

        proc = SimpleNamespace(stdin=_HeldStderrStdin(), stdout=out, stderr=err, wait=wait,
                               returncode=0)
        return await asyncio.wait_for(cli_backend._exchange(proc, b"prompt"), 2.0)

    out, err = asyncio.run(go())
    assert out == b'{"result": "ok"}'
    assert err == b"a warning", "what stderr said before the grace ran out was lost"


def test_stderr_that_closes_normally_is_read_whole(monkeypatch):
    async def go():
        out = asyncio.StreamReader()
        out.feed_data(b"{}")
        out.feed_eof()
        err = asyncio.StreamReader()
        err.feed_data(b"line one\nline two\n")
        err.feed_eof()

        async def wait():
            return 0

        proc = SimpleNamespace(stdin=_HeldStderrStdin(), stdout=out, stderr=err, wait=wait,
                               returncode=0)
        return await cli_backend._exchange(proc, b"prompt")

    assert asyncio.run(go()) == (b"{}", b"line one\nline two\n")
