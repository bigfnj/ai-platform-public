"""Mutation harness for the Claude CLI backend. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_cli_backend.py

Run this, not just pytest, before trusting a change to `cli_backend.py`.

Everything this backend gets wrong, it gets wrong QUIETLY, while returning a perfectly good
answer. A dropped `--setting-sources ""` still answers, and runs the owner's hooks under the
broker's service account on the way. A dropped `--no-session-persistence` still answers, and
leaves a session file AgentFlow reports as an agent waiting for a human. A brace scanner that
takes the FIRST match still returns JSON -- `{}` from the preamble, which a rail that asked for
nine sections writes out as nine blank ones and calls success. None of that shows in an exit code, so each
mutation here removes one guard and names the test that must notice.

Uses `sys.executable`, so it runs from a worktree (no venv of its own) as well as the main
checkout.
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
# The backend's tests: test_cli_backend.py and its siblings by concern (split 2026-10-19), every
# one, since a case may name a test in any of them.
SUITE = sorted(f"tests/{p.name}" for p in (BROKER / "tests").glob("test_cli_backend*.py"))

C = "app/cli_backend.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "stderr is awaited to EOF again, so a held pipe turns a finished answer into a fallback",
        C,
        "            err = await asyncio.wait_for(asyncio.shield(err_reader), timeout=_STDERR_GRACE)",
        "            err = await err_reader",
        "test_a_held_stderr_does_not_hold_a_finished_answer",
    ),
    (
        "what stderr said before the grace ran out is thrown away",
        C,
        "            err = bytes(err_head)",
        '            err = b""',
        "test_a_held_stderr_does_not_hold_a_finished_answer",
    ),
    (
        "the CLI child inherits the broker's whole environment, credentials included",
        C,
        "        env = {k: v for k, v in os.environ.items() if _child_may_inherit(k)}",
        "        env = dict(os.environ)",
        "test_env_keeps_the_broker_credentials_out_of_the_child",
    ),
    (
        "the allowlist loses the account's home, so the CLI cannot find its own login",
        C,
        '    "HOME", "USERPROFILE", "HOMEDRIVE",',
        '    "HOME", "HOMEDRIVE",',
        "test_env_still_carries_what_the_cli_needs",
    ),
    (
        "the allowlist loses CLAUDE_*, so a configured CLAUDE_CONFIG_DIR is dropped",
        C,
        '_CHILD_ENV_PREFIXES = ("CLAUDE_", "LC_", "XDG_")',
        '_CHILD_ENV_PREFIXES = ("LC_", "XDG_")',
        "test_env_still_carries_what_the_cli_needs",
    ),
    (
        "the owner's hooks, env and model pin load into every rail call",
        C,
        '            "--setting-sources", "",\n',
        "",
        "test_argv_loads_no_user_project_or_local_settings",
    ),
    (
        "sessions are persisted, so AgentFlow announces the platform's own calls",
        C,
        '            "--no-session-persistence",\n',
        "",
        "test_argv_carries_each_isolation_flag[--no-session-persistence]",
    ),
    (
        "the owner's MCP servers load into a rail's prompt",
        C,
        '            "--strict-mcp-config",\n',
        "",
        "test_argv_carries_each_isolation_flag[--strict-mcp-config]",
    ),
    (
        "skills load into a rail's prompt",
        C,
        '            "--disable-slash-commands",\n',
        "",
        "test_argv_carries_each_isolation_flag[--disable-slash-commands]",
    ),
    (
        "`--tools` loses its empty argument and swallows the next flag as a tool name",
        C,
        '            "--tools", "",\n',
        '            "--tools",\n',
        "test_argv_disables_tools_with_an_empty_string",
    ),
    (
        "the owner's personal CLAUDE.md and rules are read into every rail prompt",
        C,
        'DEFAULT_SETTINGS_JSON = json.dumps(\n'
        '    {"claudeMdExcludes": ["**/.claude/CLAUDE.md", "**/.claude/rules/**"]})',
        'DEFAULT_SETTINGS_JSON = ""',
        "test_argv_excludes_the_owners_personal_claude_md_by_default",
    ),
    (
        "a broker call can self-update the owner's CLI install as LocalSystem",
        C,
        '        env["DISABLE_AUTOUPDATER"] = "1"\n',
        "",
        "test_env_disables_the_auto_updater",
    ),
    (
        "an unset OAuth token is written as empty, outranking the ambient login",
        C,
        "        if self.oauth_token:\n"
        '            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token',
        '        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token',
        "test_env_omits_the_token_key_when_unset",
    ),
    (
        "the TEXT flattener drops an image, so a caller that routed one there gets an answer "
        "about a picture the model never saw",
        C,
        '        if m.get("images"):\n'
        '            raise CliUnavailable("vision_unsupported",\n'
        '                                 "a message carries images; the CLI path is text-only")',
        "        if False:\n"
        '            raise CliUnavailable("vision_unsupported", "unreachable")',
        "test_images_refuse_rather_than_being_dropped",
    ),
    # extract_json: re-anchored 2026-10-14 when the per-opener walk became one pass
    # (_json_spans). Same two mutations: strings ignored, the first value taken.
    (
        "the brace scanner ignores strings, so a `}` in a value truncates the document",
        C,
        "        if c == '\"':\n"
        "            out, inside, esc = inside, _join_scans(out, esc), None\n",
        "        if c == '\"':\n"
        "            pass\n",
        "test_extract_json_ignores_braces_inside_strings[in-prose]",
    ),
    (
        "extract_json takes the FIRST value, so `{}` in a preamble becomes the answer",
        C,
        "    spans.sort(key=lambda span: (span[0] - span[1], span[0]))",
        "    spans.sort(key=lambda span: span[0])",
        "test_extract_json_prefers_the_real_answer_over_an_empty_object_in_the_preamble",
    ),
    # --- extract_json in one pass, and a cut-off answer (added 2026-10-14) -----------------------
    (
        "an escaped quote's walk is dropped, so a value with `\\\"` in it never closes",
        C,
        "            out, inside, esc = inside, _join_scans(out, esc), None",
        "            out, inside, esc = inside, out, None",
        "test_extract_json_handles_an_escaped_quote[in-prose]",
    ),
    (
        "the character after a backslash is never seen in a gap, so `\\t` swallows the quote",
        C,
        "        if esc is not None and at != last + 1:",
        "        if False:",
        "test_extract_json_reads_each_opener_from_where_it_stands[escape-then-letter]",
    ),
    (
        "an opener inside another value's string starts no walk of its own",
        C,
        "            if c in \"{[\":\n"
        "                if out is None:\n"
        "                    out = _JsonScan()\n"
        "                out.open(kind, at)",
        "            if c in \"{[\":\n"
        "                if out is None and inside is None and esc is None:\n"
        "                    out = _JsonScan()\n"
        "                if out is not None:\n"
        "                    out.open(kind, at)",
        "test_extract_json_reads_each_opener_from_where_it_stands[inside-a-cut-string]",
    ),
    (
        "two walks that join keep only the larger one's openers",
        C,
        "        for level, openers in b.pending[kind].items():\n"
        "            into.setdefault(level + shift, []).extend(openers)",
        "        pass",
        "test_extract_json_reads_each_opener_from_where_it_stands[two-walks-join]",
    ),
    (
        "two walks that join keep their own counts, so one's openers close at the wrong bracket",
        C,
        "        shift = a.level[kind] - b.level[kind]",
        "        shift = 0",
        "test_extract_json_agrees_with_the_per_opener_walk",
    ),
    (
        "the parse budget is never spent, so nested failures cost a parse each",
        C,
        "            budget -= exc.pos - start + 1\n"
        "            if budget < 0:\n"
        "                return None\n",
        "            budget -= exc.pos - start + 1\n",
        "test_extract_json_stops_when_failing_candidates_cost_more_than_the_budget",
    ),
    (
        "a candidate nested past the parser raises out of extract_json",
        C,
        "        except RecursionError:\n"
        "            return None\n"
        "        except json.JSONDecodeError as exc:",
        "        except json.JSONDecodeError as exc:",
        "test_extract_json_refuses_a_value_nested_past_the_parser",
    ),
    (
        "an unclosed run too deep to read whole refuses the reply, hiding the value after it",
        C,
        "        except (ValueError, RecursionError):\n"
        "            # Not one value alone,",
        "        except RecursionError:\n"
        "            return None\n"
        "        except ValueError:\n"
        "            # Not one value alone,",
        "test_extract_json_refuses_a_value_nested_past_the_parser",
    ),
    (
        "a value too deep to write back raises out of extract_json",
        C,
        "        return json.dumps(value)\n"
        "    except RecursionError:\n"
        "        return None",
        "        return json.dumps(value)\n"
        "    finally:\n"
        "        pass",
        "test_extract_json_refuses_a_value_too_deep_to_write_back[whole]",
    ),
    (
        "a JSON answer cut off at max_tokens is served as the fragment left in it",
        C,
        '            if stop == "max_tokens":\n'
        "                # A JSON answer cut off",
        "            if False:\n"
        "                # A JSON answer cut off",
        "test_a_json_answer_cut_off_at_max_tokens_falls_back[json-text]",
    ),
    (
        "the result object's stop_reason is dropped again",
        C,
        '        return result, usage, stop if isinstance(stop, str) else ""',
        '        return result, usage, ""',
        "test_a_json_answer_cut_off_at_max_tokens_falls_back[schema-image]",
    ),
    (
        "a buffered answer cut off at max_tokens is reported as stopped",
        C,
        '            "done_reason": "length" if stop == "max_tokens" else "stop",',
        '            "done_reason": "stop",',
        "test_a_text_answer_cut_off_at_max_tokens_is_done_for_length[text]",
    ),
    (
        "prose is returned for a JSON request instead of falling back",
        C,
        "            if extracted is None:\n"
        "                raise CliUnavailable(\n"
        '                    "bad_json", "asked for JSON and the answer did not contain a JSON value")\n'
        "            content = extracted",
        "            if extracted is not None:\n"
        "                content = extracted",
        "test_json_mode_falls_back_when_the_answer_is_prose",
    ),
    (
        "a JSON schema is dropped, so the CLI's own structured output is never used",
        C,
        "        if schema is not None:",
        "        if False:",
        "test_a_schema_format_uses_the_cli_s_own_structured_output",
    ),
    (
        "empty output is handed back as a successful empty answer",
        C,
        "        if not text:\n"
        '            raise CliUnavailable("empty", (stderr or "the CLI produced no output").strip()[:300])',
        "        if False:\n"
        '            raise CliUnavailable("empty", (stderr or "the CLI produced no output").strip()[:300])',
        "test_empty_output_is_a_fallback_not_an_empty_answer",
    ),
    (
        # Re-anchored 2026-10-13 when the reap went through _reap, and 2026-10-14 when every
        # exit reaps (the call's temp directory goes with the tree). Same mutation: no kill.
        "the child is never reaped, so an abandoned generation keeps billing the owner",
        C,
        "            await _reap(proc, temp)\n"
        "\n"
        "        stdout = out.decode",
        "            await _remove_call_temp(temp)\n"
        "\n"
        "        stdout = out.decode",
        "test_timeout_kills_the_tree",
    ),
    # --- the reap survives a cancellation delivered again and again (added 2026-10-13) -------
    (
        "the reap is awaited in the cancelled task itself, so anyio's re-delivered cancellation "
        "stops taskkill and only the launcher dies",
        C,
        # Re-anchored 2026-10-14 when the wait went through settle, and again when the task
        # became _end_call (the kill, then the call's temp directory). Same mutation.
        "    task = asyncio.ensure_future(_end_call(proc, temp))\n"
        "    _REAPERS.add(task)\n"
        "    task.add_done_callback(_REAPERS.discard)\n"
        "    await settle(task)",
        "    await _end_call(proc, temp)",
        "test_the_reap_completes_under_a_cancellation_delivered_again_and_again",
    ),
    (
        # Re-anchored 2026-10-14 with the reap above. Same mutation.
        "the buffered call's finally kills the tree in its own task again",
        C,
        "            await _reap(proc, temp)\n"
        "\n"
        "        stdout = out.decode",
        "            await _end_call(proc, temp)\n"
        "\n"
        "        stdout = out.decode",
        "test_a_buffered_call_cancelled_again_and_again_still_takes_the_tree",
    ),
    # --- each call's own TEMP, removed once its tree is dead, and the startup sweep (added
    # --- 2026-10-14) ----------------------------------------------------------------------------
    (
        "the spawn hands the child the broker's TEMP again, so every call leaves its session "
        "directory in the service account's temp",
        C,
        "                env=self.build_env(temp),",
        "                env=self.build_env(),",
        "test_a_buffered_call_leaves_no_temp_behind[served]",
    ),
    (
        "TEMP and TMP stay the broker's, the call's directory unused",
        C,
        '            env["TEMP"] = env["TMP"] = temp\n',
        "            pass\n",
        "test_the_child_gets_the_calls_own_temp_in_place_of_the_brokers[windows]",
    ),
    (
        "TMPDIR stays the broker's on POSIX, where Node reads it first",
        C,
        '            if _POSIX or "TMPDIR" in env:',
        '            if "TMPDIR" in env:',
        "test_the_child_gets_the_calls_own_temp_in_place_of_the_brokers[posix]",
    ),
    (
        "the call's temp directory is never removed",
        C,
        "    if temp:\n"
        "        await _remove_call_temp(temp)",
        "    pass",
        "test_a_buffered_call_leaves_no_temp_behind[served]",
    ),
    (
        "the removal runs in the caller after the reap, so a cancellation delivered again "
        "skips it and the call's directory stays",
        C,
        "            await _reap(proc, temp)\n"
        "\n"
        "        stdout = out.decode",
        "            await _reap(proc)\n"
        "            await _remove_call_temp(temp)\n"
        "\n"
        "        stdout = out.decode",
        "test_a_buffered_call_leaves_no_temp_behind[cancelled-again-and-again]",
    ),
    (
        "the directory is removed before the tree is killed, so a child still running writes "
        "it back (and on Windows holds its files open)",
        C,
        "    await _kill_tree(proc)\n"
        "    if temp:\n"
        "        await _remove_call_temp(temp)",
        "    if temp:\n"
        "        await _remove_call_temp(temp)\n"
        "    await _kill_tree(proc)",
        "test_a_real_cli_that_writes_into_its_temp_leaves_nothing_behind[cancelled]",
    ),
    (
        "a removal a file filter refuses for a moment is not tried again",
        C,
        "_TEMP_REMOVE_TRIES = 5",
        "_TEMP_REMOVE_TRIES = 1",
        "test_a_removal_a_file_filter_refuses_for_a_moment_is_retried",
    ),
    (
        "a removal that outlasts its tries is never logged, so nobody learns a directory stayed",
        C,
        '    print(f"[broker] CLI temp directory {path} could not be removed after "\n'
        '          f"{_TEMP_REMOVE_TRIES} tries ({failure.__class__.__name__}: "\n'
        '          f"{failure.strerror or failure}); the next startup\'s sweep removes it",\n'
        "          file=sys.stderr, flush=True)",
        "    pass",
        "test_a_removal_that_never_succeeds_is_logged_once_and_left_for_the_sweep",
    ),
    (
        "a spawn that fails leaves the directory made for it",
        C,
        "            if not started:",
        "            if False:",
        "test_a_spawn_that_fails_leaves_no_temp_behind",
    ),
    (
        "a temp root that cannot be made escapes as an error instead of a fallback",
        C,
        '            raise CliUnavailable("failed", f"no temp directory for the call under {root} "\n'
        '                                           f"({exc.strerror or exc})") from exc',
        "            raise",
        "test_a_temp_root_that_cannot_be_made_is_a_fallback_and_nothing_starts",
    ),
    (
        "the default temp root goes inside the workdir, which is no longer empty",
        C,
        "        return str(Path(os.path.abspath(work)).parent / _TEMP_ROOT_NAME)",
        "        return os.path.join(work, _TEMP_ROOT_NAME)",
        "test_with_no_temp_root_a_calls_temp_goes_beside_the_workdir",
    ),
    (
        "a call's directory takes the root's permissions (a Public root: every logged-on user)",
        C,
        "            os.mkdir(path, 0o700)",
        "            os.mkdir(path)",
        "test_a_calls_temp_directory_is_the_brokers_account_alone",
    ),
    (
        "the startup sweep is never run, so what a removal could not clear stays for ever",
        "app/main.py",
        "    await asyncio.to_thread(broker.sweep_cli_temp)\n",
        "",
        "test_the_broker_sweeps_stale_call_temps_at_startup",
    ),
    (
        "the sweep removes a call's directory however recent, another broker's call in flight "
        "included",
        C,
        "            if now - _last_touched(entry.path) < older_than:\n"
        "                continue\n",
        "",
        "test_the_sweep_removes_stale_call_temps_and_leaves_fresh_ones_and_anything_else",
    ),
    (
        "the sweep removes anything under the root, an operator's own folder included",
        C,
        "        if not name.fullmatch(entry.name):\n"
        "            continue\n",
        "",
        "test_the_sweep_removes_stale_call_temps_and_leaves_fresh_ones_and_anything_else",
    ),
    (
        "the sweep reads a directory's own time only, so a call still writing deep inside it "
        "looks stale",
        C,
        "    for dirpath, dirnames, filenames in os.walk(path):",
        "    for dirpath, dirnames, filenames in ():",
        "test_the_sweep_removes_stale_call_temps_and_leaves_fresh_ones_and_anything_else",
    ),
    (
        "the startup sweep waits an hour whatever BROKER_CLI_TIMEOUT is, so it can clear a long "
        "call still running",
        "app/broker_cli.py",
        "                root, older_than=max(cli_backend._TEMP_STALE_S, 3 * float(s.cli_timeout)))",
        "                root, older_than=cli_backend._TEMP_STALE_S)",
        "test_the_startup_sweep_waits_an_hour_or_three_call_timeouts_if_longer[three-timeouts]",
    ),
    (
        "a non-executable shim escapes as a 502 instead of degrading to Ollama",
        C,
        "        except (OSError, ValueError) as exc:",
        "        except KeyboardInterrupt as exc:",
        "test_a_shim_that_cannot_exec_is_a_fallback",
    ),
    (
        "a CONFIGURED path skips the shim rule, so claude.ps1 is accepted",
        C,
        "        return str(p) if _is_executable_path(p) else None",
        "        return str(p) if p.is_file() else None",
        "test_resolve_rejects_a_configured_cmd_shim",
    ),
    (
        "a shim found on PATH is accepted",
        C,
        "        if found and _is_executable_path(Path(found)):",
        "        if found and Path(found).is_file():",
        "test_resolve_ignores_a_cmd_shim_found_on_path",
    ),
    (
        "unsupported sampling options are discarded with no trace",
        C,
        "    out = [k for k in _SAMPLING_KEYS if options and k in options]",
        "    out = []",
        "test_unsupported_sampling_options_are_reported_not_silently_eaten",
    ),
    (
        "a malformed usage block raises out of a call that already succeeded",
        C,
        "                try:\n"
        "                    return int(u.get(key) or 0)\n"
        "                except (TypeError, ValueError):\n"
        "                    return 0",
        "                return int(u.get(key) or 0)",
        "test_a_malformed_usage_block_does_not_break_a_successful_call",
    ),
    (
        "a failed call is classified from the JSON envelope, burying the real reason",
        C,
        '                if isinstance(body, dict) and body.get("result"):\n'
        '                    msg = str(body["result"]).strip()',
        "                if False:\n"
        "                    pass",
        "test_a_nonzero_exit_reports_the_result_sentence_not_the_json_envelope",
    ),
    (
        "a 401 from a bad token is filed as `failed` instead of `not_signed_in`",
        C,
        '                       "authenticate", "unauthorized", "oauth token", "credentials",\n'
        '                       "invalid bearer", "invalid token", "invalid api key", "401")),',
        '                       "authentication", "unauthorized", "oauth token", "credentials")),',
        "test_classify_failure[Failed to authenticate. API Error: 401 Invalid bearer token-not_signed_in]",
    ),
    (
        "CLI token counts are folded into Ollama's own counters",
        C,
        '            "cli": {"backend": "claude", **usage,',
        '            "prompt_eval_count": usage.get("input_tokens", 0),\n'
        '            "cli": {"backend": "claude", **usage,',
        "test_usage_is_namespaced_not_folded_into_ollama_counters",
    ),
    # --- the stdout cap as a memory bound, and the pipes around it ------------------------
    (
        "the stdout cap is checked only once the stream ends, so a runaway child is buffered "
        "until the timeout (the original post-hoc check)",
        C,
        "        if not chunk:\n"
        "            return bytes(buf)\n"
        "        buf += chunk\n"
        "        if len(buf) > _STDOUT_CAP:\n"
        '            raise CliUnavailable("too_large", f"more than {_STDOUT_CAP} bytes of stdout")',
        "        if not chunk:\n"
        "            if len(buf) > _STDOUT_CAP:\n"
        '                raise CliUnavailable("too_large", f"more than {_STDOUT_CAP} bytes of stdout")\n'
        "            return bytes(buf)\n"
        "        buf += chunk",
        "test_a_runaway_stdout_is_cut_off_at_the_cap_not_read_to_the_end",
    ),
    (
        "stderr is kept whole, so a chatty child grows the broker without bound",
        C,
        "        room = _STDERR_CAP - len(head)\n"
        "        if room > 0:\n"
        "            head += chunk[:room]",
        "        head += chunk",
        "test_stderr_is_drained_to_the_end_but_only_its_head_is_kept",
    ),
    (
        "stderr stops being read at the cap, so a chatty child blocks on a full pipe",
        C,
        "        room = _STDERR_CAP - len(head)\n"
        "        if room > 0:\n"
        "            head += chunk[:room]",
        "        head += chunk[:_STDERR_CAP - len(head)]\n"
        "        if len(head) >= _STDERR_CAP:\n"
        "            return bytes(head)",
        "test_stderr_is_drained_to_the_end_but_only_its_head_is_kept",
    ),
    (
        "the pipe is abandoned after the kill, so every refused call leaks a transport",
        C,
        # Re-anchored when the reap fix replaced the drain loop with a gather of the reap and
        # both drains. Same mutation: nothing is drained.
        "                         *(_bounded(_discard(s)) for s in (proc.stdout, proc.stderr)\n",
        "                         *(_bounded(_discard(s)) for s in ()\n",
        "test_a_real_runaway_child_is_killed_at_the_cap",
    ),
    (
        "the reap waits before draining, so on POSIX a capped call sits out the reap timeout",
        C,
        "    await asyncio.gather(_bounded(proc.wait()),\n"
        "                         *(_bounded(_discard(s))",
        "    await _bounded(proc.wait())\n"
        "    await asyncio.gather(*(_bounded(_discard(s))",
        "test_the_reap_does_not_wait_for_a_pipe_only_the_drain_can_close",
    ),
    # --- the POSIX tree kill: a session of its own, and the group killed ------------------
    (
        "on POSIX only the launcher is killed, so the CLI's node child outlives a timeout",
        C,
        "            _killpg(proc.pid)",
        "            pass",
        "test_on_posix_a_timeout_kills_the_clis_whole_process_group",
    ),
    (
        "on POSIX the CLI shares the broker's process group, so a group kill would miss it",
        C,
        '                **({"start_new_session": True} if _POSIX else {}),\n',
        "",
        "test_on_posix_a_timeout_kills_the_clis_whole_process_group",
    ),
    (
        "Windows is handed a POSIX-only session flag",
        C,
        '                **({"start_new_session": True} if _POSIX else {}),',
        '                **({"start_new_session": True}),',
        "test_on_windows_taskkill_takes_the_tree_and_no_session_is_asked_for",
    ),
    (
        "stdin is written but never closed, so the CLI waits for EOF until the timeout",
        C,
        "        with contextlib.suppress(Exception):\n"
        "            stdin.close()",
        "        pass",
        "test_chat_sends_the_prompt_on_stdin",
    ),
    # --- images over stream-json (added 2026-10-09) ----------------------------------------
    (
        "an image call takes the text path again and is refused there",
        C,
        "        stream = any(m.get(\"images\") for m in messages)",
        "        stream = False",
        "test_an_image_call_sends_one_stream_json_user_message",
    ),
    (
        "every call takes the stream-json path, so a text call's argv and stdin change",
        C,
        "        stream = any(m.get(\"images\") for m in messages)",
        "        stream = True",
        "test_a_call_without_images_runs_the_argv_and_stdin_it_always_did",
    ),
    (
        "the text path's argv drifts (one flag more)",
        C,
        "                      \"--verbose\"] if stream_json else [\"--output-format\", \"json\"])",
        "                      \"--verbose\"] if stream_json else [\"--output-format\", \"json\",\n"
        "                                                          \"--verbose\"])",
        "test_a_call_without_images_builds_the_argv_it_always_did",
    ),
    (
        "the image argv loses --verbose, which -p needs to print stream-json at all",
        C,
        "                      \"--verbose\"] if stream_json else [\"--output-format\", \"json\"])",
        "                      ] if stream_json else [\"--output-format\", \"json\"])",
        "test_an_image_call_sends_one_stream_json_user_message",
    ),
    (
        "the image argv is rebuilt without the isolation flags",
        C,
        "        if self.settings_json:\n"
        "            argv += [\"--settings\", self.settings_json]",
        "        if stream_json:\n"
        "            argv = [self.exe, \"-p\", *io_format]\n"
        "        if self.settings_json:\n"
        "            argv += [\"--settings\", self.settings_json]",
        "test_an_image_argv_carries_each_isolation_flag",
    ),
    (
        "an image call's schema is dropped, so the CLI's own structured output is not used",
        C,
        "schema=schema, stream_json=stream)",
        "schema=None if stream else schema, stream_json=stream)",
        "test_an_image_call_with_a_schema_uses_the_cli_s_json_schema",
    ),
    (
        "every image is announced as PNG whatever its bytes are",
        C,
        "    media_type = sniff_image_type(raw)",
        "    media_type = \"image/png\"",
        "test_the_media_type_is_sniffed_from_the_bytes",
    ),
    (
        "bytes that are no image Claude reads are sent as PNG",
        C,
        "        return \"image/webp\"\n"
        "    return None",
        "        return \"image/webp\"\n"
        "    return \"image/png\"",
        "test_a_format_claude_does_not_read_is_refused",
    ),
    (
        "WebP is not recognised, so a WebP photo always falls back",
        C,
        "raw[8:12] == b\"WEBP\"",
        "raw[8:12] == b\"WAVE\"",
        "test_the_media_type_is_sniffed_from_the_bytes[webp]",
    ),
    (
        "a data URL is accepted by dropping its header",
        C,
        "    text = b64.replace(\"\\r\", \"\").replace(\"\\n\", \"\")",
        "    text = b64.split(\",\")[-1].replace(\"\\r\", \"\").replace(\"\\n\", \"\")",
        "test_a_data_url_is_refused_and_its_declared_type_never_sent",
    ),
    (
        "a corrupt base64 string is quietly repaired by a lenient decoder",
        C,
        "validate=True",
        "validate=False",
        "test_invalid_base64_is_refused_not_repaired",
    ),
    (
        "the caller's base64 is passed on as it came, line breaks and all",
        C,
        "            \"data\": base64.b64encode(raw).decode(\"ascii\")}",
        "            \"data\": b64}",
        "test_line_breaks_in_the_base64_are_dropped_as_ollama_drops_them",
    ),
    (
        "an image that is not a string is not refused by name",
        C,
        "    if not isinstance(b64, str):",
        "    if False:",
        "test_an_image_that_is_not_a_string_is_refused",
    ),
    (
        "the per-image cap is gone",
        C,
        "    if len(text) > _IMAGE_MAX_B64:",
        "    if False:",
        "test_an_image_over_the_per_image_cap_is_refused",
    ),
    (
        "the image-count cap is gone, so a 21st image brings the 2000 px rule",
        C,
        "    if count > _IMAGE_MAX_COUNT:",
        "    if False:",
        "test_more_images_than_the_cap_are_refused_counted_across_the_call",
    ),
    (
        "the total cap is gone, so a call can pass the 32 MB request limit",
        C,
        "            if total > _IMAGES_MAX_TOTAL_B64:",
        "            if False:",
        "test_images_over_the_total_cap_are_refused",
    ),
    (
        "the per-image cap is raised to the direct API's 10 MB, past Bedrock's and Vertex's",
        C,
        "_IMAGE_MAX_B64 = 5_000_000",
        "_IMAGE_MAX_B64 = 10_000_000",
        "test_the_caps_are_claudes_own_or_below",
    ),
    (
        "the count cap is raised past 20",
        C,
        "_IMAGE_MAX_COUNT = 20",
        "_IMAGE_MAX_COUNT = 100",
        "test_the_caps_are_claudes_own_or_below",
    ),
    (
        "an image on a system turn is silently dropped",
        C,
        "            if images:\n"
        "                raise CliUnavailable(\"vision_unsupported\",\n"
        "                                     \"an image on a system message; --system-prompt is "
        "text only\")",
        "            if False:\n"
        "                pass",
        "test_an_image_on_a_system_turn_is_refused",
    ),
    (
        "a turn's text goes before its images",
        C,
        "            blocks.append({\"type\": \"text\", \"text\": text})",
        "            blocks.insert(len(blocks) - len(images), {\"type\": \"text\", \"text\": text})",
        "test_images_come_before_their_turns_text_and_the_text_is_the_text_paths",
    ),
    (
        "an image is written to a temp file on its way to the CLI",
        C,
        "    media_type = sniff_image_type(raw)",
        "    __import__(\"tempfile\").TemporaryFile().write(raw)\n"
        "    media_type = sniff_image_type(raw)",
        "test_an_image_is_never_written_to_disk",
    ),
    (
        "a transcript that never reached its result line is answered with its last line",
        C,
        "        if data is None:\n"
        "            raise CliUnavailable(\"bad_stream\", \"the stream-json transcript has no "
        "result line\")",
        "        if data is None:\n"
        "            data = {\"result\": stdout.strip().splitlines()[-1]}",
        "test_a_transcript_without_a_result_line_falls_back",
    ),
    (
        "a line that is not JSON ends the scan, hiding the result line after it",
        C,
        "        except json.JSONDecodeError:\n"
        "            continue\n"
        "        if isinstance(obj, dict) and obj.get(\"type\") == \"result\":",
        "        except json.JSONDecodeError:\n"
        "            break\n"
        "        if isinstance(obj, dict) and obj.get(\"type\") == \"result\":",
        "test_a_line_that_is_not_json_does_not_hide_the_result",
    ),
    (
        "an in-band is_error is delivered as the answer",
        C,
        "        if data.get(\"is_error\"):",
        "        if False:",
        "test_an_is_error_result_line_falls_back_classified",
    ),
    # Re-anchored 2026-10-09 when the stream path's nonzero exit moved into _stream_exit_failure
    # (log hygiene: no transcript line as the detail). Same mutation: the result sentence is
    # ignored. Its old fallback, the transcript's last line, is gone, so the label changed.
    (
        "a failed image call ignores its result line's error sentence, so the reason is lost",
        C,
        "    if last is not None and last.get(\"is_error\"):\n"
        "        msg = str(last.get(\"result\") or last.get(\"error\") or \"\").strip()",
        "    if False:\n"
        "        pass",
        "test_a_failed_image_call_reports_the_result_sentence",
    ),
    # --- log hygiene, the exit code, assistant-turn images -----------------------------------
    (
        # Re-anchored 2026-10-13 when the rule moved into _exit_failure_from, which the streamed
        # call shares and which never sees the transcript. Same fault: with no error sentence,
        # the transcript's last line stands in for stderr, so it is quoted and classified.
        "a failed image call with no error sentence quotes the transcript's last line again, "
        "the model's description of the picture, into the log and /v1/status",
        C,
        "    return _exit_failure_from(_last_result(stdout), stderr, code, lines)",
        "    return _exit_failure_from(_last_result(stdout),\n"
        "                              stderr or (stdout.strip().splitlines() or [\"\"])[-1],\n"
        "                              code, lines)",
        "test_a_failed_image_call_without_an_error_sentence_records_no_transcript_line",
    ),
    (
        "a result line that is NOT an error lends its answer to the failure's detail",
        C,
        "    if last is not None and last.get(\"is_error\"):",
        "    if last is not None:",
        "test_a_nonzero_exit_after_a_success_result_line_is_refused_and_not_quoted",
    ),
    (
        "an image call that exited nonzero is answered from a success-looking result line",
        C,
        "        if proc.returncode != 0:",
        "        if proc.returncode != 0 and not (\n"
        "                stream and (_last_result(stdout) or {}).get(\"is_error\") is False):",
        "test_a_nonzero_exit_after_a_success_result_line_is_refused_and_not_quoted",
    ),
    (
        "a text call that exited nonzero is answered from a success-looking result object",
        C,
        "        if proc.returncode != 0:",
        "        if proc.returncode != 0 and not (not stream and '\"is_error\": false' in stdout):",
        "test_a_nonzero_exit_of_a_text_call_is_refused_whatever_its_result_says",
    ),
    (
        "a failed image call's stderr is thrown away",
        C,
        "    msg = msg or stderr.strip()",
        "    msg = msg",
        "test_a_failed_image_call_reports_its_stderr",
    ),
    (
        "an image on an assistant turn is moved into the user message as the asker's own",
        C,
        "            if images and role != \"user\":",
        "            if False:",
        "test_chat_refuses_an_image_on_an_assistant_turn",
    ),
    (
        "only the exact lowercase 'assistant' role is refused, so 'Assistant' or 'tool' slips through",
        C,
        "            if images and role != \"user\":",
        "            if images and role == \"assistant\":",
        "test_chat_refuses_an_image_on_an_assistant_turn",
    ),
    (
        "an image on an assistant turn is silently dropped",
        C,
        "            if images and role != \"user\":\n"
        "                raise CliUnavailable(\"vision_unsupported\",\n"
        "                                     f\"an image on a {role!r} message; this call is one "
        "user \"\n"
        "                                     \"message, so the picture would arrive as the "
        "asker's own\")\n"
        "            turns.append((role, content, images))",
        "            turns.append((role, content, images if role == \"user\" else []))",
        "test_chat_refuses_an_image_on_an_assistant_turn",
    ),
    (
        "the path is chosen by the LAST message's images only",
        C,
        "        stream = any(m.get(\"images\") for m in messages)",
        "        stream = bool(messages[-1].get(\"images\"))",
        "test_chat_sends_the_images_of_every_user_turn",
    ),
    (
        "a bare string in `images` is taken as a one-image list",
        C,
        "        images = m.get(\"images\") or []\n"
        "        if not isinstance(images, list):",
        "        images = m.get(\"images\") or []\n"
        "        if isinstance(images, str):\n"
        "            images = [images]\n"
        "        if not isinstance(images, list):",
        "test_images_that_are_not_a_list_are_refused[a-string]",
    ),
    # --- a prefill (a trailing assistant turn) is emulated and verified (2026-10-14; refused
    # --- outright from 2026-10-13) -----------------------------------------------------------
    (
        "a reply that ignores the prefill is returned as if it had continued it",
        C,
        "            cont = continuation(content, prefill)\n"
        "            if cont is None:\n"
        "                raise _prefill_mismatch()",
        "            cont = continuation(content, prefill)\n"
        "            if cont is None:\n"
        "                cont = content",
        "test_a_reply_that_ignores_the_prefill_falls_back_without_quoting_either",
    ),
    (
        "the reply is not cut to its continuation, so the rail shows the opening twice",
        C,
        "            content = cont\n"
        "        dropped = dropped_options(options, think)",
        "            pass\n"
        "        dropped = dropped_options(options, think)",
        "test_a_trailing_assistant_turn_is_an_instruction_and_only_its_continuation_returns",
    ),
    (
        "the mismatch's detail quotes the reply, rail content in the log and on status",
        C,
        "            if cont is None:\n"
        "                raise _prefill_mismatch()\n"
        "            if not cont.strip():",
        "            if cont is None:\n"
        "                raise CliUnavailable(\"prefill_mismatch\", content[:80])\n"
        "            if not cont.strip():",
        "test_a_reply_that_ignores_the_prefill_falls_back_without_quoting_either",
    ),
    (
        "a reply that is the prefill alone is returned as a blank answer",
        C,
        "                raise _prefill_mismatch()\n"
        "            if not cont.strip():",
        "                raise _prefill_mismatch()\n"
        "            if False:",
        "test_a_reply_that_is_the_prefill_alone_is_empty",
    ),
    (
        "the instruction never reaches the system prompt, so the CLI answers afresh",
        C,
        "            system = prefill_system(system, prefill)\n"
        "\n"
        "        argv = self.build_argv(system, want_json=want_json",
        "            pass\n"
        "\n"
        "        argv = self.build_argv(system, want_json=want_json",
        "test_a_trailing_assistant_turn_is_an_instruction_and_only_its_continuation_returns",
    ),
    (
        "the prefill stays in the prompt as an Assistant line as well",
        C,
        "    return rest, content",
        "    return messages, content",
        "test_the_prefilled_prompt_carries_no_assistant_turn",
    ),
    (
        "an 'Assistant', 'model' or 'tool' turn is emulated as a prefill too",
        C,
        "    if role != \"assistant\":\n"
        "        raise CliUnavailable(\"prefill_unsupported\",",
        "    if role.lower() not in (\"assistant\", \"model\", \"tool\"):\n"
        "        raise CliUnavailable(\"prefill_unsupported\",",
        "test_a_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts",
    ),
    (
        "a prefill's images are dropped and its text continued",
        C,
        "    if turn.get(\"images\"):",
        "    if False:",
        "test_a_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts",
    ),
    (
        "a blank prefill is emulated",
        C,
        "    if not isinstance(content, str) or not content.strip():",
        "    if not isinstance(content, str):",
        "test_a_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts",
    ),
    (
        "a prefill with no user turn before it, or after another assistant turn, is emulated",
        C,
        "    if not before or before[-1] != \"user\":",
        "    if False:",
        "test_a_prefill_the_cli_path_cannot_emulate_is_refused_before_anything_starts",
    ),
    (
        "a prefill with a format is emulated, an opening cut from a rebuilt JSON value",
        C,
        "        if prefill and want_json:",
        "        if False:",
        "test_a_prefill_with_a_format_is_refused_before_anything_starts",
    ),
    (
        "a system turn after the user's is read as the last turn, so the call is refused",
        C,
        "        if str(m.get(\"role\") or \"user\") != \"system\":\n"
        "            last = i",
        "        if True:\n"
        "            last = i",
        "test_a_call_whose_last_turn_is_the_users_is_sent[system-after-user]",
    ),
    (
        "a last turn with no role is not the user's, so the call is refused",
        C,
        "    role = str(turn.get(\"role\") or \"user\")",
        "    role = str(turn.get(\"role\"))",
        "test_a_call_whose_last_turn_is_the_users_is_sent[no-role]",
    ),
    (
        "the reply's leading whitespace is read as text, so a reply on a fresh line mismatches",
        C,
        "    text = reply.lstrip()",
        "    text = reply",
        "test_the_continuation_is_what_follows_the_prefill[leading-blank-lines]",
    ),
    (
        "a prefill's trailing space is no word boundary, so 'Dearest' begins 'Dear '",
        C,
        "        return None     # the reply runs on through the word break the prefill ends with",
        "        return rest",
        "test_a_reply_that_does_not_begin_with_the_prefill_has_no_continuation"
        "[runs-through-the-space]",
    ),
    (
        "a heading prefill is begun by the same heading extended along its line",
        C,
        "    if \"\\n\" in trail and \"\\n\" not in lead:",
        "    if False:",
        "test_a_reply_that_does_not_begin_with_the_prefill_has_no_continuation[heading-extended]",
    ),
    (
        "the reply's own whitespace is always dropped, so a heading loses its blank line",
        C,
        "    return rest[len(trail):] if lead.startswith(trail) else rest[len(lead):]",
        "    return rest[len(lead):]",
        "test_the_continuation_is_what_follows_the_prefill[heading-blank-line]",
    ),
    (
        "the prefill's trailing whitespace is kept beside the reply's, doubling the space",
        C,
        "    return rest[len(trail):] if lead.startswith(trail) else rest[len(lead):]",
        "    return rest",
        "test_the_continuation_is_what_follows_the_prefill[space]",
    ),
    (
        "more than the header is checked, so the README's 'magic bytes only' is no longer true",
        C,
        "    if raw.startswith(_PNG_MAGIC):",
        "    if raw.startswith(_PNG_MAGIC) and len(raw) > len(_PNG_MAGIC):",
        "test_only_the_header_is_checked[png-signature-alone]",
    ),
    # --- a large JSON answer is read off the event loop (added 2026-10-14) -------------------
    (
        "a large JSON answer is read in the event loop again",
        C,
        "    return await asyncio.to_thread(extract_json, text)",
        "    return extract_json(text)",
        "test_a_large_json_answer_is_read_off_the_event_loop[at-the-threshold]",
    ),
    (
        "the call reads its JSON answer in the loop, whatever its size",
        C,
        "            extracted = await _extract_json_off_loop(content)",
        "            extracted = extract_json(content)",
        "test_a_ticker_keeps_running_while_a_large_json_answer_is_read",
    ),
    (
        "every JSON answer goes to a thread, however short",
        C,
        "    if len(text) < _JSON_THREAD_FROM:\n        return extract_json(text)",
        "    if False:\n        return extract_json(text)",
        "test_a_large_json_answer_is_read_off_the_event_loop[just-under-the-threshold]",
    ),
    (
        "an answer exactly at the threshold is read in the loop",
        C,
        "    if len(text) < _JSON_THREAD_FROM:",
        "    if len(text) <= _JSON_THREAD_FROM:",
        "test_a_large_json_answer_is_read_off_the_event_loop[at-the-threshold]",
    ),
    (
        "the threshold is a mebibyte",
        C,
        "_JSON_THREAD_FROM = 64 * 1024",
        "_JSON_THREAD_FROM = 1024 * 1024",
        "test_the_json_thread_threshold_is_64_kib",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    # The repo's editable installs point at the MAIN checkout, so without this a run from a
    # worktree would import `app` from the wrong tree and report on code it never changed.
    env["PYTHONPATH"] = str(BROKER)
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider", "-k", "not LiveTests"],
                       cwd=BROKER, env=env)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        # A baseline that ran out of time is its own verdict, and nothing below can mean
        # anything after it.
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = BROKER / relpath
        # Bytes, not text: read_text/write_text translate line endings on Windows, so a
        # "restore" could rewrite an LF file as CRLF. The pattern is matched in the file's own
        # line endings and only its span replaced (mutate_bytes); restore_exact proves the
        # original bytes are back.
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected)
            # A hang broke SOMETHING, but nothing showed the named test failing, so it is not
            # FIRED. Its own verdict, so it is never mistaken for one.
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                # The evidence, not just the verdict. One run reported FIRED-WRONG-TEST for a
                # mutation that, run alone, fails exactly the named test; the output that would
                # have said why was discarded, so the cause could only be guessed at.
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
