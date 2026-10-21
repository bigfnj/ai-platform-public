"""Mutation harness for the Claude CLI on a STREAMED chat. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_cli_stream.py

Run this, not just pytest, before trusting a change to `ClaudeCliClient.chat_stream` (its argv,
its deadlines, what counts as content, the result line's verdict, the prefill it holds back
until confirmed, the reap and `settle`, the call's own temp directory), to the
streamed branch of `Broker.chat_stream` (fallback before the first token, interruption after it,
the leading frames, the idle tick, the wait for the reap before the slot comes free, counting a
hang-up delivered as a cancellation or a close), to `Broker._while_there` (the tick on a buffered
CLI call, /v1/chat's included), to the /v1/chat/stream route's `requester` and disconnect
handling, or to `ClosingStreamingResponse` (app/streaming.py), which closes a stream's body
when the stream ends in Starlette's send().

The faults here fail in the two directions that matter. Some make the stream look BETTER: a
failure after the first token quietly answered by the local model reads as one complete answer;
a dropped deadline just waits; thinking passed through as content reads as a more thoughtful
reply. Others cost money nobody sees: a reap that a re-delivered cancellation stops leaves the
CLI's node child generating for a caller who left. None of them shows in a status code.

Each case names the test that must fail. CONTROLS are rewrites that change nothing the suite
can see, and must SURVIVE: a harness whose controls fire is measuring its own noise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# This directory is sys.path[0] when the harness runs as a script. Every suite run is bounded:
# a mutation that hangs a test is reported as TIMEOUT instead of hanging the harness.
from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
PY = sys.executable
# With the backend's tests: test_cli_backend.py and its siblings by concern (split 2026-10-19).
SUITE = ["tests/test_cli_stream.py",
         *sorted(f"tests/{p.name}" for p in (BROKER / "tests").glob("test_cli_backend*.py"))]

B = "app/broker.py"
BC = "app/broker_cli.py"   # Broker's CLI methods, run in broker.py's namespace (see there)
M = "app/main.py"
K = "app/cli_backend.py"
S = "app/streaming.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- the streaming client (cli_backend.ClaudeCliClient.chat_stream) ----------------------
    (
        # Re-anchored 2026-10-14 when the wait went through settle, and again when the task
        # became _end_call (the kill, then the call's temp directory). Same mutation.
        "the reap is awaited in the cancelled task itself, so anyio's re-delivered cancellation "
        "stops taskkill and the CLI's node child keeps generating",
        K,
        "    task = asyncio.ensure_future(_end_call(proc, temp))\n"
        "    _REAPERS.add(task)\n"
        "    task.add_done_callback(_REAPERS.discard)\n"
        "    await settle(task)",
        "    await _end_call(proc, temp)",
        "test_the_reap_completes_under_a_cancellation_delivered_again_and_again",
    ),
    # --- a CLI slot comes free only once its tree is dead (added 2026-10-14) -----------------
    (
        "the reap's caller waits behind a bare shield again, so the first re-delivered "
        "cancellation ends the wait and the caller frees its slot while taskkill runs",
        K,
        "    task.add_done_callback(_REAPERS.discard)\n"
        "    await settle(task)",
        "    task.add_done_callback(_REAPERS.discard)\n"
        "    await asyncio.shield(task)",
        "test_the_reap_completes_under_a_cancellation_delivered_again_and_again",
    ),
    (
        "settle gives up at the second cancellation, so a caller cancelled again and again "
        "still leaves before the kill",
        K,
        "            except asyncio.CancelledError:\n"
        "                continue",
        "            except asyncio.CancelledError:\n"
        "                break",
        "test_the_reap_completes_under_a_cancellation_delivered_again_and_again",
    ),
    (
        "--include-partial-messages is dropped, so the answer arrives in one piece at the end",
        K,
        '            io_format = ["--output-format", "stream-json", "--verbose", '
        '"--include-partial-messages"]',
        '            io_format = ["--output-format", "stream-json", "--verbose"]',
        "test_a_streamed_call_runs_the_argv_measured_and_sends_the_prompt_on_stdin",
    ),
    (
        "a thinking model's reasoning is sent as the answer",
        K,
        '                        text = delta.get("text") if delta.get("type") == "text_delta" '
        "else None",
        '                        text = delta.get("text") or delta.get("thinking")',
        "test_thinking_and_other_deltas_are_never_content",
    ),
    (
        "the first token has no deadline, so a CLI stuck before it holds the call for ever",
        K,
        "                    wait = began + self.timeout - now\n",
        "                    wait = 3600.0\n",
        "test_no_text_within_the_budget_is_a_timeout_and_the_tree_is_killed",
    ),
    (
        "a stream that goes quiet after its first token is waited on for ever",
        K,
        "                    wait = _STREAM_IDLE_S\n",
        "                    wait = 3600.0\n",
        "test_a_stream_that_goes_quiet_after_its_first_token_is_stalled",
    ),
    (
        "the whole answer has no bound once it began, so a CLI that never finishes never ends",
        K,
        "                elif first + self.answer_timeout - now <= _STREAM_IDLE_S:",
        "                elif False:",
        "test_the_whole_answer_is_bounded_from_its_first_token",
    ),
    (
        "a second message after the answer began is streamed on as if it were the same answer",
        K,
        "                        if streamed:\n"
        '                            raise CliUnavailable("bad_stream", "a second message began',
        "                        if False:\n"
        '                            raise CliUnavailable("bad_stream", "a second message began',
        "test_a_second_message_after_the_answer_began_is_refused",
    ),
    (
        "the result line is not reconciled with the deltas, so a lost delta is never sent",
        K,
        '            tail = _stream_tail("".join(streamed), content)',
        '            tail = ""',
        "test_the_result_line_settles_what_was_streamed",
    ),
    (
        "a result line that disagrees with the text already shown is accepted",
        K,
        "            if tail is None:\n"
        '                raise CliUnavailable("stream_mismatch",',
        "            if False:\n"
        '                raise CliUnavailable("stream_mismatch",',
        "test_a_result_that_disagrees_with_the_streamed_text_is_refused",
    ),
    # --- a streamed prefill is held back until confirmed (2026-10-14; refused outright from
    # --- 2026-10-13) --------------------------------------------------------------------------
    (
        "a streamed prefill is no longer held back, so its opening reaches the rail before it "
        "is confirmed",
        K,
        "                            if held:\n",
        "                            if False:\n",
        "test_nothing_is_yielded_before_the_prefill_is_confirmed",
    ),
    (
        "the stream sends the opening it held back, so the rail shows it twice",
        K,
        "                                text = cont\n",
        "                                text = \"\".join(streamed)\n",
        "test_a_streamed_prefill_sends_only_its_continuation",
    ),
    (
        "a stream that leaves the opening is not refused until it ends",
        K,
        "                                if cont is None:\n"
        "                                    raise _prefill_mismatch()",
        "                                if cont is None:\n"
        "                                    cont = _UNDECIDED",
        "test_a_streamed_reply_that_leaves_the_prefill_falls_back_before_any_frame",
    ),
    (
        "a reply is judged only once it is as long as the opening, so a short one that already "
        "left it holds the stream",
        K,
        "        return _UNDECIDED if not final and core.startswith(text) else None",
        "        return _UNDECIDED if not final else None",
        "test_a_streamed_reply_that_leaves_the_prefill_falls_back_before_any_frame",
    ),
    (
        "an opening still undecided when the deltas end is never judged, so a whole answer "
        "arrives opening and all",
        K,
        "            if held:\n"
        "                # Still undecided",
        "            if False:\n"
        "                # Still undecided",
        "test_a_prefill_undecided_when_the_deltas_end_is_judged_on_the_result_line",
    ),
    (
        "the end-of-stream judgement reads the deltas without the result line's rest",
        K,
        "                cont = continuation(\"\".join(streamed) + tail, prefill)",
        "                cont = continuation(\"\".join(streamed), prefill)",
        "test_a_prefill_undecided_when_the_deltas_end_is_judged_on_the_result_line",
    ),
    (
        "the streamed call never asks for the opening",
        K,
        "            system = prefill_system(system, prefill)\n"
        "        held = bool(prefill)",
        "            pass\n"
        "        held = bool(prefill)",
        "test_a_streamed_prefill_sends_only_its_continuation",
    ),
    (
        "the streamed prompt keeps the prefill as an Assistant line",
        K,
        "        messages, prefill = split_prefill(messages)\n"
        "        system, prompt = flatten_messages(messages)",
        "        _rest, prefill = split_prefill(messages)\n"
        "        system, prompt = flatten_messages(messages)",
        "test_a_streamed_prefill_sends_only_its_continuation",
    ),
    (
        "a stream whose CLI exited nonzero is judged by its transcript alone",
        K,
        "            if proc.returncode:\n"
        "                raise _exit_failure_from(last, stderr, proc.returncode, lines)",
        "            if False:\n"
        "                raise _exit_failure_from(last, stderr, proc.returncode, lines)",
        "test_a_nonzero_exit_mid_stream_reports_the_clis_own_words",
    ),
    (
        "the stdout cap is gone from the stream, so a runaway child is buffered without bound",
        K,
        "                    if total > _STDOUT_CAP:",
        "                    if False:",
        "test_a_runaway_stream_is_cut_off_at_the_cap",
    ),
    (
        # Re-anchored 2026-10-14 when every exit reaps (the call's temp directory goes with the
        # tree). Same mutation: no kill.
        "a consumer that closes the stream leaves the CLI's tree running",
        K,
        "            await _reap(proc, temp)  # the consumer left, a refusal above, or the answer "
        "ended",
        "            await _remove_call_temp(temp)",
        "test_a_real_streaming_child_dies_with_its_tree_when_the_consumer_closes",
    ),
    (
        # Re-anchored 2026-10-14 with the reap above. Same mutation.
        "the stream waits for its helpers before the kill, so a re-delivered cancellation skips it",
        K,
        "            await _reap(proc, temp)  # the consumer left, a refusal above, or the answer "
        "ended\n"
        "            await asyncio.gather(feeder, err_reader, return_exceptions=True)",
        "            await asyncio.gather(feeder, err_reader, return_exceptions=True)\n"
        "            await _reap(proc, temp)  # the consumer left, a refusal above, or the answer "
        "ended",
        "test_a_stream_cancelled_again_and_again_still_takes_the_tree",
    ),
    (
        # Added 2026-10-14 with each call's own TEMP.
        "a stream's temp directory is removed in the stream after the reap, so a cancellation "
        "delivered again skips it",
        K,
        "            await _reap(proc, temp)  # the consumer left, a refusal above, or the answer "
        "ended",
        "            await _reap(proc)\n"
        "            await _remove_call_temp(temp)",
        "test_a_streamed_call_leaves_no_temp_behind[cancelled-again-and-again]",
    ),
    (
        # Added 2026-10-14 with each call's own TEMP.
        "a hung-up stream's temp directory outlives it: the reap kills the tree and stops",
        K,
        "    if temp:\n"
        "        await _remove_call_temp(temp)",
        "    pass",
        "test_a_hung_up_streams_slot_comes_free_only_once_its_tree_is_dead[text]",
    ),
    (
        "max_tokens is reported as a finished answer",
        K,
        '            yield _done_frame(name, "length" if stop == "max_tokens" else "stop",',
        '            yield _done_frame(name, "stop",',
        "test_an_answer_cut_by_max_tokens_is_done_for_length",
    ),
    # --- the broker's streamed branch (Broker.chat_stream, _watch, _cli_interrupted) ----------
    (
        "a failure before the first token ends the stream with an error instead of falling back",
        B,
        "                if begun:\n"
        "                    yield self._cli_interrupted(att, exc, chars)",
        "                if True:\n"
        "                    yield self._cli_interrupted(att, exc, chars)",
        "test_a_failure_before_the_first_token_falls_back_transparently",
    ),
    (
        "a failure after the first token is answered locally, appended to half the CLI's answer",
        B,
        "                if begun:\n"
        "                    yield self._cli_interrupted(att, exc, chars)",
        "                if False:\n"
        "                    yield self._cli_interrupted(att, exc, chars)",
        "test_a_failure_after_the_first_token_ends_the_stream_with_an_error",
    ),
    (
        "the fallback's leading frame is keyed \"error\", so every rail's reader raises on it",
        B,
        '            yield {"cli_fallback": cli_fallback}',
        '            yield {"error": cli_fallback}',
        "test_a_failure_before_the_first_token_falls_back_transparently",
    ),
    (
        "the CLI stream takes the GPU gate, so local work queues behind a call on another box",
        B,
        "                async with self._cli_slot(att, requester, client_gone, stream=True):",
        "                async with self.gate.hold(model=model, source=rail), "
        "self._cli_slot(att, requester, client_gone, stream=True):",
        "test_an_owner_stream_is_served_token_by_token_by_the_cli",
    ),
    (
        "an interruption is not counted, so a stream cut mid-answer leaves no trace on status",
        BC,
        "        self._cli_interruptions += 1",
        "        pass",
        "test_a_failure_after_the_first_token_ends_the_stream_with_an_error",
    ),
    (
        "the idle tick never asks, so a caller who left is noticed only at the next frame",
        BC,
        "                    if not nxt.done() and await self._caller_gone(client_gone):",
        "                    if False:",
        "test_a_caller_who_hangs_up_mid_stream_stops_it_on_the_next_idle_tick",
    ),
    # --- a hung-up stream frees its slot only once its tree is dead; every hang-up is counted;
    # --- a buffered call is watched while it runs (added 2026-10-14) ------------------------
    (
        "a cancelled text stream stops waiting for its next-frame task, so the slot comes free "
        "while that task's finally is still running taskkill",
        BC,
        "                nxt.add_done_callback(lambda t: t.cancelled() or t.exception())\n"
        "                await settle(nxt)",
        "                nxt.add_done_callback(lambda t: t.cancelled() or t.exception())\n"
        "                await asyncio.wait({nxt})",
        "test_a_hung_up_streams_slot_comes_free_only_once_its_tree_is_dead[text]",
    ),
    (
        "a cancelled buffered call stops waiting for its own task, so the slot comes free while "
        "the call's finally is still running taskkill",
        BC,
        "                task.add_done_callback(lambda t: t.cancelled() or t.exception())\n"
        "                await settle(task)",
        "                task.add_done_callback(lambda t: t.cancelled() or t.exception())\n"
        "                await asyncio.wait({task})",
        "test_a_hung_up_streams_slot_comes_free_only_once_its_tree_is_dead[json]",
    ),
    (
        "a hang-up Starlette delivers by cancelling the stream is not counted as client_gone",
        B,
        "                if await self._hung_up(client_gone):",
        "                if False:",
        "test_a_caller_who_disconnects_mid_stream_takes_the_cli_tree_with_it",
    ),
    (
        "every cancellation is counted as a hang-up, a server shutting down included",
        B,
        "                if await self._hung_up(client_gone):",
        "                if True:",
        "test_a_cancelled_stream_counts_as_a_hang_up_only_when_the_connection_says_so"
        "[shutting-down]",
    ),
    (
        "a streamed call with a format is not watched while the CLI answers, so under ASGI 2.4 a "
        "caller who left is noticed only when the CLI ends",
        B,
        "                                            think=think),\n"
        "                            client_gone, every=_GONE_TICK_S)",
        "                                            think=think),\n"
        "                            None, every=_GONE_TICK_S)",
        "test_a_caller_who_disconnects_mid_stream_takes_the_cli_tree_with_it",
    ),
    (
        "a buffered /v1/chat call is not watched while the CLI answers, so it runs on for a "
        "caller who left",
        B,
        "                                        think=think),\n"
        "                        client_gone, every=_GONE_TICK_S)",
        "                                        think=think),\n"
        "                        None, every=_GONE_TICK_S)",
        "test_a_buffered_call_whose_caller_disconnects_takes_the_cli_tree_with_it",
    ),
    (
        "the buffered call's tick never asks, so a caller who left is noticed only at its end",
        BC,
        "                if not task.done() and await self._caller_gone(client_gone):",
        "                if False:",
        "test_a_buffered_call_whose_caller_disconnects_takes_the_cli_tree_with_it",
    ),
    (
        "a format goes to the streaming client, so a JSON answer is streamed unvalidated",
        B,
        "                    if format is not None or any(m.get(\"images\") for m in messages):",
        "                    if any(m.get(\"images\") for m in messages):",
        "test_a_format_goes_to_the_buffered_client_inside_the_stream",
    ),
    (
        "a served stream is not counted or recorded",
        B,
        '                                    self._cli_served(att, frame.get("cli") or {})',
        "                                    pass",
        "test_an_owner_stream_is_served_token_by_token_by_the_cli",
    ),
    (
        "the in-flight entry does not say it is a stream",
        BC,
        '                "requester": att.who, **({"stream": True} if stream else {}),',
        '                "requester": att.who,',
        "test_an_in_flight_stream_is_active_until_its_last_frame",
    ),
    (
        "status stops saying the CLI path streams",
        BC,
        '            "streams": STREAMS_TEXT,',
        '            "streams": False,',
        "test_status_reports_streaming_and_interruptions_as_copies",
    ),
    (
        "status hands out the live interruption record",
        BC,
        '                              "last": (dict(self._last_cli_interruption)',
        '                              "last": ((self._last_cli_interruption)',
        "test_status_reports_streaming_and_interruptions_as_copies",
    ),
    # --- the /v1/chat/stream route --------------------------------------------------------------
    (
        "the stream route forwards the body's requester whatever the credential",
        M,
        '    who = _trusted_requester(request, req.requester, route="/v1/chat/stream")',
        "    who = req.requester",
        "test_an_inference_token_cannot_name_an_owner_onto_a_streamed_cli",
    ),
    (
        "the stream route does not hand the broker the connection",
        M,
        "    gone = request.is_disconnected",
        "    gone = None",
        "test_the_stream_route_hands_the_broker_the_connections_own_answer",
    ),
    (
        "a caller who hung up is written an error line, as if the broker had failed",
        M,
        "        except ClientGone:\n"
        "            # Nobody is reading.",
        "        except ZeroDivisionError:\n"
        "            # Nobody is reading.",
        "test_the_stream_route_ends_without_an_error_line_for_a_caller_who_hung_up",
    ),
    (
        "the stream route drops the requester, so no streamed call reaches the CLI",
        M,
        "                format=req.format, think=req.think, requester=who, client_gone=gone,",
        "                format=req.format, think=req.think, client_gone=gone,",
        "test_the_stream_route_honours_a_full_scope_requester",
    ),
    # --- a stream that ends in send() is closed by the route, and the close is counted -------
    # --- (added 2026-10-14) ------------------------------------------------------------------
    (
        "the stream route answers with a plain StreamingResponse again, so a stream that ends in "
        "send() leaves its body, the broker's stream and the CLI's tree to the collector",
        M,
        '    return ClosingStreamingResponse(_gen(), media_type="application/x-ndjson")',
        '    return StreamingResponse(_gen(), media_type="application/x-ndjson")',
        "test_a_stream_cancelled_while_its_write_is_stuck_takes_the_cli_tree_with_it",
    ),
    (
        "the response never closes its body, so a write that fails leaves the tree to the "
        "collector and the hang-up uncounted",
        S,
        "                await self._body.aclose()",
        "                pass",
        "test_a_hang_up_only_a_failed_write_noticed_takes_the_cli_tree_and_is_counted",
    ),
    (
        "the body is closed unshielded, so the cancellation that ended the stream cuts its "
        "cleanup off at the first await",
        S,
        "            with anyio.CancelScope(shield=True):",
        "            with anyio.CancelScope():",
        "test_the_response_closes_its_body_through_the_cancellation_that_ended_it",
    ),
    (
        "closing the route's generator no longer closes the broker's, so the CLI call ends "
        "after the request, whenever the finalizer gets to it",
        M,
        "            async with contextlib.aclosing(broker.chat_stream(",
        "            async with contextlib.nullcontext(broker.chat_stream(",
        "test_a_stream_cancelled_while_its_write_is_stuck_takes_the_cli_tree_with_it",
    ),
    (
        "a close the connection confirms is not counted as a hang-up",
        B,
        "            except (asyncio.CancelledError, GeneratorExit) as stopped:",
        "            except asyncio.CancelledError as stopped:",
        "test_a_closed_stream_counts_as_a_hang_up_only_when_the_connection_says_so[hung-up]",
    ),
    # --- a reader that stops reading: every send is bounded (added 2026-10-14) ---------------
    (
        "a send is never bounded, so a reader that stopped holds its CLI slot",
        S,
        "            with anyio.move_on_after(limit) as waited:",
        "            with anyio.CancelScope() as waited:",
        "test_a_stream_whose_reader_stopped_takes_the_cli_tree_with_it[asgi-2.3]",
    ),
    (
        "a send past the bound is skipped and the stream writes on",
        S,
        "            if waited.cancelled_caught:",
        "            if False:",
        "test_a_send_past_the_bound_ends_the_stream_and_closes_its_body[asgi-2.4]",
    ),
    (
        "the stall is an OSError, which Starlette turns into a hang-up under ASGI 2.4",
        S,
        "class SendStalled(Exception):",
        "class SendStalled(TimeoutError):",
        "test_a_stream_whose_reader_stopped_takes_the_cli_tree_with_it[asgi-2.4]",
    ),
    (
        "the bound is armed once for the whole stream, so a slow reader is cut off",
        S,
        "        limit = SEND_TIMEOUT_S\n"
        "\n"
        "        async def bounded(message: Any) -> None:\n"
        "            with anyio.move_on_after(limit) as waited:",
        "        limit = SEND_TIMEOUT_S\n"
        "        armed = anyio.current_time() + limit\n"
        "\n"
        "        async def bounded(message: Any) -> None:\n"
        "            with anyio.CancelScope(deadline=armed) as waited:",
        "test_a_slow_reader_is_not_cut_off[asgi-2.3]",
    ),
    (
        "the stall is raised to the server instead of ending the response",
        S,
        "        except SendStalled as stalled:",
        "        except ZeroDivisionError as stalled:",
        "test_a_stream_whose_reader_stopped_takes_the_cli_tree_with_it[asgi-2.3]",
    ),
    (
        # Re-anchored 2026-10-14 when the line became a WARNING. Same mutation.
        "a stream closed behind a stalled send leaves no log line",
        S,
        "            print(f\"[broker] WARNING: {scope.get('path', 'stream')}: {stalled}; the stream was \"\n"
        "                  f\"closed\", file=sys.stderr, flush=True)",
        "            pass",
        "test_a_send_past_the_bound_ends_the_stream_and_closes_its_body[asgi-2.3]",
    ),
    (
        "the shipped bound is ten minutes",
        S,
        "SEND_TIMEOUT_S = 60.0",
        "SEND_TIMEOUT_S = 600.0",
        "test_the_shipped_send_bound_is_the_cli_s_own_idle_minute",
    ),
    # --- the bound's own WARNING, and nothing from uvicorn at ERROR (added 2026-10-14) ---
    (
        "the stream the bound closed is never marked, so uvicorn logs its ERROR",
        S,
        "            _CLOSED_BY_BOUND.set(True)\n",
        "            pass\n",
        "test_a_stream_the_bound_closed_leaves_no_error_in_uvicorn_s_log[h11]",
    ),
    (
        "the filter is never installed, so uvicorn logs its ERROR",
        S,
        'logging.getLogger("uvicorn.error").addFilter(_BoundClosedFilter())',
        "pass",
        "test_a_stream_the_bound_closed_leaves_no_error_in_uvicorn_s_log[httptools]",
    ),
    (
        "the filter drops every unfinished-response ERROR, whoever left the response unfinished",
        S,
        "        return not (_CLOSED_BY_BOUND.get() and record.msg == _UNFINISHED)",
        "        return not (record.msg == _UNFINISHED)",
        "test_a_stream_the_bound_closed_leaves_no_error_in_uvicorn_s_log[h11]",
    ),
    (
        "the bound's line is not marked a WARNING",
        S,
        "            print(f\"[broker] WARNING: {scope.get('path', 'stream')}: {stalled}; the stream was \"\n",
        "            print(f\"[broker] {scope.get('path', 'stream')}: {stalled}; the stream was \"\n",
        "test_a_stream_the_bound_closed_leaves_no_error_in_uvicorn_s_log[h11]",
    ),
]

#: Rewrites the suite cannot see. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "the capability flag is spelled differently but still True",
        K,
        "STREAMS_TEXT = True",
        "STREAMS_TEXT = bool(1)",
    ),
    (
        "the undecided sentinel is compared by equality: an object() equals only itself",
        K,
        "                                if cont is _UNDECIDED:",
        "                                if cont == _UNDECIDED:",
    ),
    (
        "the ask after a cancellation is bounded at twice the tick: Starlette's answer is "
        "immediate either way",
        BC,
        "                return await asyncio.wait_for(self._caller_gone(client_gone), _GONE_TICK_S)",
        "                return await asyncio.wait_for(self._caller_gone(client_gone),\n"
        "                                              2 * _GONE_TICK_S)",
    ),
    (
        # Added 2026-10-14 with each call's own TEMP.
        "a failed removal's first pause is halved: the retries still outlast a filter's moment",
        K,
        "_TEMP_REMOVE_PAUSE = 0.1",
        "_TEMP_REMOVE_PAUSE = 0.05",
    ),
    (
        # Added 2026-10-14 with the send bound.
        "the send bound is written as an int: the same minute, logged the same way",
        S,
        "SEND_TIMEOUT_S = 60.0",
        "SEND_TIMEOUT_S = 60",
    ),
    (
        # Added 2026-10-14 with the filter: uvicorn passes that message with no arguments.
        "the filter compares the formatted message: the same words",
        S,
        "        return not (_CLOSED_BY_BOUND.get() and record.msg == _UNFINISHED)",
        "        return not (_CLOSED_BY_BOUND.get() and record.getMessage() == _UNFINISHED)",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    # The repo's editable installs point at the MAIN checkout, so without this a run from a
    # worktree would import `app` from the wrong tree and report on code it never changed.
    env["PYTHONPATH"] = str(BROKER)
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider", "-k", "not LiveTests"], cwd=BROKER, env=env)


def _apply(relpath: str, old: str, new: str, label: str):
    """(path, original bytes, mutated bytes) or None, after reporting a PATTERN."""
    path = BROKER / relpath
    # Bytes, not text: see mutate_cli_backend.py. mutate_bytes matches in the file's own line
    # endings and refuses an anchor found more than once; restore_exact proves the restore.
    original = path.read_bytes()
    n, mutated = mutate_bytes(original, old, new)
    if n != 1:
        print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
        return None
    return path, original, mutated


def main() -> int:
    parse_harness_args(MUTATIONS, CONTROLS)
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        applied = _apply(relpath, old, new, label)
        if applied is None:
            results.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected)
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    controls: list[tuple[str, str]] = []
    for label, relpath, old, new in CONTROLS:
        applied = _apply(relpath, old, new, label)
        if applied is None:
            controls.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            verdict = "TIMEOUT" if hung else "SURVIVED" if code == 0 else "FIRED"
            controls.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
