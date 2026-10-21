"""Mutation harness for WHO reaches the Claude CLI. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_cli_routing.py [--whole-file]

Each case runs only the tests it names (the collected test ids whose name is the case's, or that
name with a parameter), so a full run takes minutes; it took 390 when every case re-ran the whole
file and every test built an HTTP client it never used. A case whose named test no longer exists
is NO-TEST, never SURVIVED. `--whole-file` runs the whole file for every case, as before
2026-10-14: slower, and it also shows a mutation that fails some test other than the one named
(FIRED-WRONG-TEST). The baseline and the restored-tree check run the whole file either way.

Run this, not just pytest, before trusting a change to `cli_decision`, `cli_state`, the CLI
branch of `Broker.chat` (its concurrency limit and the Claude model it passes included), the
switch file (`cli_effective`, `set_cli_role`, each role's model and BROKER_CLI_MODELS), the PUT
/v1/cli/roles route, or which credential's `requester` /v1/chat honours (`_trusted_requester`).

Every guard here fails OPEN and fails SILENTLY. Remove the requester check and the nightly
scheduler starts spending a personal subscription, successfully, with a 200 and better output
than before -- there is no symptom to notice. Remove the owner check and another user's
request is answered correctly, on the owner's account, which is the thing Anthropic's terms
forbid and the thing no status code will ever report. A guard whose removal makes the product
look BETTER is the one that most needs a test that fails.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# This directory is sys.path[0] when the harness runs as a script. Every suite run is bounded:
# a mutation that hangs a test is reported as TIMEOUT instead of hanging the harness.
from _suite_runner import (DEFAULT_TIMEOUT, SuiteRun, mutate_bytes, named_failed,
                           parse_harness_args, restore_exact, run_bounded)

BROKER = Path(__file__).resolve().parents[1]
PY = sys.executable
SUITE = ["tests/test_cli_routing.py"]

B = "app/broker.py"
BC = "app/broker_cli.py"   # Broker's CLI methods, run in broker.py's namespace (see there)
C = "app/config.py"
M = "app/main.py"
S = "app/schemas.py"
K = "app/cli_backend.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # Re-anchored 2026-10-09 when cli_decision started reading the eligible roles from the
    # switch file: `s.cli_role_set()` became the local `roles`. Same mutations, same named
    # tests.
    (
        "a case variant of an eligible role reaches the CLI, then UnknownRoleError on fallback",
        BC,
        "        if ref[1:] not in roles:",
        "        if ref[1:].lower() not in roles:",
        "test_a_case_variant_of_an_eligible_role_is_not_eligible",
    ),
    (
        "an unset workdir is not a blocker, so the CLI reads this repo's CLAUDE.md",
        BC,
        '            out.append("workdir_unset")',
        "            pass",
        "test_an_unset_workdir_refuses_the_call_and_says_so",
    ),
    (
        "background work with no requester reaches a personal subscription",
        BC,
        '        if not requester or not requester.strip():',
        "        if False:",
        "test_background_work_with_no_requester_is_refused",
    ),
    (
        "any authenticated user's request is billed to the owner's account",
        BC,
        "        if requester.strip() not in owners:\n"
        '            return False, "not_owner"',
        "        if False:\n"
        '            return False, "not_owner"',
        "test_another_user_is_refused",
    ),
    (
        "owner matching folds case again, so a case-variant account spends the subscription",
        BC,
        "        if requester.strip() not in owners:",
        "        if requester.strip().lower() not in {o.lower() for o in owners}:",
        "test_a_case_variant_of_an_owner_is_not_the_owner",
    ),
    (
        "the configured owner list is case-folded, so owner-one matches an owner Owner-One",
        C,
        '        return {o.strip() for o in self.cli_owners.split(",") if o.strip()}',
        '        return {o.strip().lower() for o in self.cli_owners.split(",") if o.strip()}',
        "test_an_owner_list_entry_is_matched_as_written_not_folded",
    ),
    (
        "every role becomes eligible, so the finance scan leaves the box too",
        BC,
        "        if ref[1:] not in roles:\n"
        '            return False, "role_not_eligible"',
        "        if False:\n"
        '            return False, "role_not_eligible"',
        "test_an_ineligible_role_is_refused",
    ),
    (
        "a concrete model name is accepted, so any token holder can name the CLI",
        BC,
        '        if not ref.startswith("@"):\n'
        '            return False, "not_a_role"',
        "        if False:\n"
        '            return False, "not_a_role"',
        "test_a_concrete_model_name_is_refused",
    ),
    (
        "an empty owner list means everyone instead of nobody",
        BC,
        "        if not owners:\n"
        '            return False, "no_owners_configured"',
        "        if False:\n"
        '            return False, "no_owners_configured"',
        "test_no_owners_configured_means_nobody",
    ),
    (
        # Re-anchored 2026-10-14 when cli_state began handing in the token read it already holds.
        # Same mutation.
        "an unreadable token file degrades to whatever ambient login exists",
        BC,
        "        _token, problem = token_read or s.cli_token()\n"
        "        if problem:\n"
        '            out.append("token_unreadable")',
        "        _token, problem = token_read or s.cli_token()\n"
        "        if False:\n"
        '            out.append("token_unreadable")',
        "test_an_unreadable_token_file_refuses_rather_than_calling",
    ),
    (
        # Re-anchored 2026-10-14 when the decision began handing its token read in. Same mutation.
        "a misconfigured workdir is reported but the call is routed anyway",
        BC,
        "        for blocker in self._cli_env_blockers(token_read):\n"
        "            return False, blocker",
        "        if False:\n"
        "            return False, 'unreachable'",
        "test_a_missing_workdir_refuses_the_call_not_just_the_status",
    ),
    (
        "invalid settings JSON is accepted, so the CLAUDE.md exclusion silently stops",
        BC,
        "            except (ValueError, RecursionError):\n"
        "                # ValueError covers JSONDecodeError",
        "            except KeyboardInterrupt:\n"
        "                # ValueError covers JSONDecodeError",
        "test_invalid_settings_json_refuses_the_call",
    ),
    (
        "only the anticipated failure falls back; everything else is a 502",
        B,
        "            except Exception as exc:  # noqa: BLE001\n"
        "                # EVERY exception, not just CliUnavailable.",
        # RuntimeError, not CliUnavailable. broker.py imports CliUnavailable again (it raises
        # one for a call that waited too long for a slot), but CliUnavailable subclasses
        # RuntimeError, so this narrowing still catches every anticipated failure and misses
        # only the unanticipated one -- which is exactly the property under test.
        "            except RuntimeError as exc:  # noqa: BLE001\n"
        "                # EVERY exception, not just CliUnavailable.",
        "test_any_exception_falls_back_not_just_the_expected_one",
    ),
    (
        "a refusal is not counted, so 'ready, and nothing changed' has no answer",
        BC,
        "            self._cli_refusals[why] = self._cli_refusals.get(why, 0) + 1",
        "            pass",
        "test_a_refusal_is_counted_by_reason",
    ),
    # Re-anchored 2026-10-13, every case below that names `att.`, `_cli_slot`, `_cli_served`,
    # `_cli_failed` or `_cli_fallback_starts`: the CLI lifecycle moved out of Broker.chat into
    # those helpers so the streamed path shares it. Same mutations, same named tests, unless a
    # case says otherwise.
    # Re-anchored 2026-10-14, the three cases that name chat()'s call to `att.client.chat`: it is
    # now awaited inside `self._while_there(...)`, which asks client_gone while it runs. Same
    # mutations, same named tests.
    (
        "options and think are not handed over, so nothing can report them dropped",
        B,
        "                        att.client.chat(messages, format=format, options=options,\n"
        "                                        think=think),\n"
        "                        client_gone,",
        "                        att.client.chat(messages, format=format),\n"
        "                        client_gone,",
        "test_options_and_think_are_handed_to_the_cli",
    ),
    (
        "the fallback is served but never announced in the response",
        B,
        "        if cli_fallback is not None and isinstance(out, dict):\n"
        '            out = {**out, "cli_fallback": cli_fallback}\n'
        "        return out",
        "        return out",
        "test_a_cli_failure_falls_back_to_ollama_and_says_so",
    ),
    (
        "the CLI path evicts the resident model it does not need",
        BC,
        # Re-anchored 2026-10-09: the client is now built inside the try, once a slot is held.
        # And again the same day, when it started carrying the role's Claude model, and on
        # 2026-10-14, when it began taking the decision's token read on a second line.
        "                                          token_read=att.token_read)\n",
        "                                          token_read=att.token_read)\n"
        "            await self._evict_other_heavy(keep=None)\n",
        "test_the_cli_path_takes_no_gpu_gate_and_evicts_nothing",
    ),
    (
        "status hides a blocker, so fixing one of three shows no change",
        BC,
        "        if not owners:\n"
        '            blockers.append("no_owners_configured")',
        "        if False:\n"
        '            blockers.append("no_owners_configured")',
        "test_status_names_every_blocker_not_just_the_first",
    ),
    (
        "a served CLI call is not counted, so the operator cannot see spend happening",
        BC,
        "        self._cli_calls += 1",
        "        pass",
        "test_a_served_call_is_counted",
    ),
    (
        "the format is dropped, so a rail asking for JSON is handed prose",
        B,
        "                        att.client.chat(messages, format=format, options=options,\n"
        "                                        think=think),\n"
        "                        client_gone,",
        "                        att.client.chat(messages, format=None, options=options,\n"
        "                                        think=think),\n"
        "                        client_gone,",
        "test_the_format_is_handed_to_the_cli",
    ),
    # --- in-flight visibility (`active`, `last_by_role`) ------------------------------
    # These fail SILENTLY in the other direction: nothing is billed wrongly, the UI simply
    # shows a call that is not running, or misses one that is, and every status code is 200.
    (
        "a CLI call is never registered, so the UI reads idle while the owner's call runs",
        BC,
        "            self._cli_active[att.token] = {",
        "            _unregistered = {",
        "test_an_in_flight_cli_call_is_visible_and_gone_once_served",
    ),
    (
        "the entry is never removed, so a finished call shows as running for ever",
        BC,
        "            self._cli_active.pop(att.token, None)",
        "            pass",
        "test_an_in_flight_cli_call_is_visible_and_gone_once_served",
    ),
    (
        "the entry is removed on success and fallback but not on cancellation",
        BC,
        "            self._cli_active.pop(att.token, None)",
        # One line on purpose: behaves exactly like removal written into the success and
        # except paths, which is the shape a `finally` replaces, without rewriting the block.
        "            self._cli_active.pop(att.token, None)"
        " if not asyncio.current_task().cancelling() else None",
        "test_an_active_entry_is_gone_after_the_call_is_cancelled",
    ),
    # The outcome-write patterns below were re-anchored twice on 2026-10-07: when each entry
    # gained `requester`, and when the write moved into _record_cli_outcome so it also lands in
    # the per-requester map. Same mutations and named tests each time. The note below is the
    # first re-anchoring's.
    # The next three patterns were re-anchored 2026-10-07 when each last_by_role entry gained
    # `requester` and the normalised name moved into one `who` shared with the active entry.
    # Same mutations, same named tests: the write removed, the write removed, lowercasing lost.
    # The outcome writes were re-anchored again 2026-10-09 when each entry gained `model`.
    (
        "a served call does not update last_by_role, so a recovered role still shows failed",
        BC,
        '        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(), '
        '"requester": att.who,\n'
        '                                             "model": att.cli_model})',
        "        pass",
        "test_last_by_role_records_each_outcome_for_that_role",
    ),
    (
        "a fallback does not update last_by_role, so a failing role still shows ok",
        BC,
        '        self._record_cli_outcome(att.model, {"ok": False, "at": time.time(),\n'
        '                                             "reason": reason, "requester": att.who,\n'
        "                                             **({} if att.cli_model is None\n"
        '                                                else {"model": att.cli_model})})',
        "        pass",
        "test_last_by_role_records_each_outcome_for_that_role",
    ),
    (
        "the in-flight entry folds the requester's case, so the owner's call is filed elsewhere",
        BC,
        '        who = (requester or "").strip()',
        '        who = (requester or "").strip().lower()',
        "test_the_active_entry_names_the_requester_as_it_was_matched",
    ),
    (
        "a served outcome does not say whose call it was, so another owner is shown it",
        BC,
        '        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(), '
        '"requester": att.who,\n',
        '        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(),\n',
        "test_last_by_role_says_whose_call_it_was",
    ),
    (
        "a fallback outcome does not say whose call it was",
        BC,
        '                                             "reason": reason, "requester": att.who,\n',
        '                                             "reason": reason,\n',
        "test_last_by_role_says_whose_call_it_was",
    ),
    (
        # Re-expressed 2026-10-13: the outcome and the in-flight entry now read one `att.who`,
        # so the raw requester is no longer in reach of the outcome write. The fault is the
        # same: the outcome names the requester other than as the owner check matched it.
        "the outcome names the requester padded, so the gateway's filter hides an owner's own",
        BC,
        '        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(), '
        '"requester": att.who,\n',
        '        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(), '
        '"requester": f" {att.who} ",\n',
        "test_last_by_role_names_the_requester_the_way_the_owner_check_matched_it",
    ),
    (
        "the rail is not filled in, so the UI cannot say which rail is waiting",
        BC,
        '                "role": att.model, "rail": att.rail, "since": time.time(),',
        '                "role": att.model, "rail": None, "since": time.time(),',
        "test_the_active_entry_names_the_rail_from_the_role",
    ),
    (
        "cli_state hands out the live active entries",
        BC,
        '            "active": [dict(e) for e in sorted(self._cli_active.values(),',
        '            "active": [e for e in sorted(self._cli_active.values(),',
        "test_cli_state_hands_out_copies_not_the_live_records",
    ),
    (
        "cli_state hands out the live last_by_role entries",
        BC,
        '            "last_by_role": {role: dict(e) for role, e in self._cli_last_by_role.items()},',
        '            "last_by_role": dict(self._cli_last_by_role),',
        "test_cli_state_hands_out_copies_not_the_live_records",
    ),
    # --- each requester's own latest outcome per role (`last_by_requester`) -----------------
    (
        "outcomes are keyed by role alone again, so owner B's call erases owner A's own record",
        BC,
        '        who = str(entry.get("requester") or "")',
        '        who = ""',
        "test_last_by_requester_keeps_each_owners_own_latest_per_role",
    ),
    (
        "the per-requester map grows without bound",
        BC,
        "        while len(self._cli_last_by_requester) > _CLI_HISTORY_REQUESTERS:",
        "        while False:",
        "test_the_per_requester_map_is_bounded",
    ),
    (
        "one requester's role map grows without bound",
        BC,
        "        while len(mine) > _CLI_HISTORY_ROLES:",
        "        while False:",
        "test_the_per_requester_map_is_bounded",
    ),
    (
        "an update does not refresh recency, so the busiest owner is the one evicted",
        BC,
        "        mine = self._cli_last_by_requester.pop(who, {})",
        "        mine = self._cli_last_by_requester.get(who, {})",
        "test_the_per_requester_map_is_bounded",
    ),
    (
        "cli_state hands out the live per-requester entries",
        BC,
        '            "last_by_requester": {who: {role: dict(e) for role, e in roles.items()}',
        '            "last_by_requester": {who: roles',
        "test_cli_state_hands_out_copies_not_the_live_records",
    ),
    # --- the per-role switch file (BROKER_CLI_ROLES_FILE) -----------------------------------
    # Every one of these fails in the direction that spends money or loses an "off", with a 200.
    # The cli_effective returns, the parse's returns and its call sites in set_cli_role were
    # re-anchored 2026-10-09 when the switch file gained each role's Claude model (CliRoles and
    # _SwitchFile). Same mutations, same named tests.
    (
        "the switch file is ignored once it exists, so a role switched off stays on",
        C,
        '        return CliRoles({r for r, on in switches.items() if on}, "file", "", note,',
        '        return CliRoles(self.cli_role_set(), "env", "", note,',
        "test_the_file_switches_off_a_role_the_env_has_on",
    ),
    (
        "an invalid file hands back the env set, re-enabling what it switched off",
        C,
        '            return CliRoles(set(), "file", f"BROKER_CLI_ROLES_FILE is invalid: {problem}",',
        '            return CliRoles(self.cli_role_set(), "file", f"BROKER_CLI_ROLES_FILE is invalid: {problem}",',
        "test_an_invalid_file_fails_closed_and_says_so",
    ),
    (
        "a configured file that does not exist yet fails every role closed",
        C,
        "        except FileNotFoundError:\n"
        '            return CliRoles(self.cli_role_set(), "env", "", "", {})',
        "        except FileNotFoundError:\n"
        '            return CliRoles(set(), "file", "", "", {})',
        "test_a_configured_file_that_does_not_exist_yet_keeps_the_env_set",
    ),
    (
        "a string value is read as a switch, so \"false\" is not refused",
        C,
        "            if not isinstance(value, bool):",
        "            if value is None:",
        "test_an_invalid_file_fails_closed_and_says_so",
    ),
    # A key no role answers to is IGNORED and noted, never a blocker (and never a role).
    (
        "a key no role answers to fails the whole file closed again",
        C,
        "                ignored.append(str(key))",
        '                return _bad_switch_file(f"{key!r} is not a known role")',
        "test_a_key_no_role_answers_to_is_ignored_and_named_without_a_blocker",
    ),
    (
        "a key no role answers to is taken as a role",
        C,
        "            if _ROLE_NAME.fullmatch(str(key)) and key in known:",
        "            if True:",
        "test_a_key_no_role_answers_to_is_ignored_and_named_without_a_blocker",
    ),
    (
        "status says nothing about the keys it ignored",
        BC,
        '            "roles_problem": " ".join(p for p in (overlay_problem, roles_problem or roles_note)',
        '            "roles_problem": " ".join(p for p in (overlay_problem, roles_problem)',
        "test_a_key_no_role_answers_to_is_ignored_and_named_without_a_blocker",
    ),
    (
        "a switch writes the ignored keys back, so the note never clears",
        C,
        "                current, problem, models = parsed.switches, parsed.problem, parsed.models\n",
        "                current, problem, models = parsed.switches, parsed.problem, parsed.models\n"
        "                current.update({k: False for k in parsed.ignored})\n",
        "test_the_next_switch_drops_a_key_no_role_answers_to",
    ),
    # A parse the decoder cannot finish fails closed instead of raising.
    (
        "a switch file nested too deep to parse raises out of the call and /v1/status",
        C,
        "        except (ValueError, RecursionError) as exc:\n"
        '            return _bad_switch_file(f"not valid JSON ({type(exc).__name__})")',
        "        except ArithmeticError as exc:\n"
        '            return _bad_switch_file(f"not valid JSON ({type(exc).__name__})")',
        "test_a_file_too_deep_to_parse_falls_back_on_the_call_and_keeps_status_up",
    ),
    (
        # Re-anchored 2026-10-09 when the overlay read moved into _read_overlay and started
        # naming its problem: one case per exception that used to escape.
        "a roles.json that is not UTF-8 raises out of cli_state again",
        C,
        "        except UnicodeDecodeError:\n            return {}, (f\"{label} is not UTF-8 text",
        "        except ZeroDivisionError:\n            return {}, (f\"{label} is not UTF-8 text",
        "test_a_roles_json_the_broker_cannot_read_does_not_take_the_switch_file_down",
    ),
    (
        "a roles.json nested too deep raises out of cli_state again",
        C,
        "        except (ValueError, RecursionError) as exc:\n"
        "            return {}, f\"{label} is not valid JSON",
        "        except ZeroDivisionError as exc:\n"
        "            return {}, f\"{label} is not valid JSON",
        "test_a_roles_json_the_broker_cannot_read_does_not_take_the_switch_file_down",
    ),
    (
        "the refusal for an invalid file no longer says what deleting it does",
        C,
        '                        f"eligible. {_CLI_ROLES_RESET}")',
        '                        f"eligible. Fix or delete it, then switch again.")',
        "test_the_refusal_says_what_deleting_the_file_does",
    ),
    (
        "the file is read without its BOM, so a PowerShell-written file fails closed",
        C,
        '            raw = Path(path).read_text(encoding="utf-8-sig")',
        '            raw = Path(path).read_text(encoding="utf-8")',
        "test_a_file_written_with_a_bom_is_read",
    ),
    (
        "the routing ignores an invalid file's problem and calls it role_not_eligible",
        BC,
        "        if roles_problem:\n"
        "            # Fail closed, and say why",
        "        if False:\n"
        "            # Fail closed, and say why",
        "test_an_invalid_file_fails_closed_and_says_so",
    ),
    (
        "status does not report an invalid file as a blocker",
        BC,
        "        if roles_problem:\n"
        '            blockers.append("cli_roles_file_invalid")',
        "        if False:\n"
        '            blockers.append("cli_roles_file_invalid")',
        "test_an_invalid_file_fails_closed_and_says_so",
    ),
    (
        "every role switched off reads as never configured",
        BC,
        '            blockers.append("cli_all_roles_off")',
        '            blockers.append("no_roles_configured")',
        "test_every_role_switched_off_is_its_own_blocker",
    ),
    (
        # Re-anchored 2026-10-09 when cli_state started reading the models from the same read.
        "status reports the env set while the routing reads the file",
        BC,
        "        roles, roles_source, roles_problem, roles_note = eff.roles, eff.source, eff.problem, eff.note",
        '        roles, roles_source, roles_problem, roles_note = s.cli_role_set(), "env", "", ""',
        "test_the_file_switches_off_a_role_the_env_has_on",
    ),
    (
        "the switch file is read for every caller, owner or not",
        BC,
        "        owners = s.cli_owner_set()\n"
        "        if not owners:\n"
        '            return False, "no_owners_configured"',
        # Re-anchored 2026-10-15: the settings helper this called is gone, and a call to it
        # would "fire" on the AttributeError. The read itself is the mutation.
        "        s.cli_effective()\n"
        "        owners = s.cli_owner_set()\n"
        "        if not owners:\n"
        '            return False, "no_owners_configured"',
        "test_a_non_owner_call_never_reads_the_switch_file",
    ),
    # Re-anchored 2026-10-09 when an OFF started being written while roles.json cannot be read
    # (the seed and the unknown-role check gained an `overlay_problem` arm). Same mutations, same
    # named tests.
    (
        "the first switch does not seed, so switching one role on drops chat and edu",
        C,
        "                current = {r: True for r in sorted(self.cli_role_set())\n"
        "                           if (_ROLE_NAME.fullmatch(r) if overlay_problem else r in known)}",
        "                current = {}",
        "test_the_first_switch_seeds_from_the_env_set",
    ),
    (
        "the seed copies an unknown env role, so the first switch fails every role closed",
        C,
        "if (_ROLE_NAME.fullmatch(r) if overlay_problem else r in known)}",
        "if _ROLE_NAME.fullmatch(r)}",
        "test_the_seed_leaves_out_an_env_role_nobody_knows",
    ),
    (
        "a switch for a role the broker does not know is written",
        C,
        "        if role not in known and not overlay_problem:\n"
        '            raise ValueError(f"unknown role {role!r}")',
        "        if False:\n"
        '            raise ValueError(f"unknown role {role!r}")',
        "test_a_switch_for_a_role_the_broker_does_not_know_is_refused_and_writes_nothing",
    ),
    (
        "an invalid file is overwritten by the next switch",
        C,
        "                if problem:\n"
        "                    raise CliRolesFileError(\n"
        '                        f"BROKER_CLI_ROLES_FILE is invalid ({problem}), so no role is "',
        "                if False:\n"
        "                    raise CliRolesFileError(\n"
        '                        f"BROKER_CLI_ROLES_FILE is invalid ({problem}), so no role is "',
        "test_a_switch_never_overwrites_an_invalid_file",
    ),
    (
        "the switch is written in place, so a write that dies halfway corrupts the file",
        C,
        '                tmp.write_text(body, encoding="utf-8")\n'
        "                replace_with_retry(tmp, p)",
        '                p.write_text(body, encoding="utf-8")',
        "test_a_write_that_dies_halfway_leaves_the_previous_switches_in_force",
    ),
    (
        "two switches at once are not serialised, so one drops the other's change",
        C,
        "        with _CLI_ROLES_LOCK:",
        "        with threading.Lock():",
        "test_two_switches_at_once_both_land",
    ),
    (
        "the route parses a string as a switch",
        S,
        "    enabled: StrictBool",
        "    enabled: bool",
        "test_the_route_takes_a_json_boolean_only",
    ),
    (
        "a file that cannot take a switch reads as the caller's mistake",
        M,
        "    except CliRolesFileError as exc:\n"
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc",
        "    except CliRolesFileError as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc",
        "test_the_route_answers_409_in_words_when_the_file_cannot_take_a_switch",
    ),
    (
        "the Rails tab's one read loses the switch state",
        B,
        '                "cli": self.cli_summary()}',
        "}",
        "test_the_rails_tab_read_carries_the_switch_state",
    ),
    (
        "the Rails tab is told the env set while the routing reads the file",
        BC,
        '                "enabled_roles": list(st["roles"]), "roles_source": st["roles_source"],',
        '                "enabled_roles": sorted(self.settings.cli_role_set()),'
        ' "roles_source": st["roles_source"],',
        "test_the_rails_tab_read_carries_the_switch_state",
    ),
    # --- the concurrency limit (BROKER_CLI_CONCURRENCY) -------------------------------------
    (
        "no limit: every owner call spawns its own claude.exe at once",
        BC,
        "                await asyncio.wait_for(self._cli_slots.acquire(), timeout=budget)",
        "                pass",
        "test_no_more_than_the_limit_run_at_once_and_a_waiting_call_is_not_shown_running",
    ),
    (
        "the wait for a slot is unbounded, so the rail times out before any fallback",
        BC,
        "                await asyncio.wait_for(self._cli_slots.acquire(), timeout=budget)",
        "                await self._cli_slots.acquire()",
        "test_a_call_that_cannot_get_a_slot_in_time_falls_back_and_says_busy",
    ),
    (
        # Re-anchored 2026-10-14 when the call grew a second line (the token read). Same mutation.
        "a call that waited gets a fresh budget, so its fallback starts too late",
        BC,
        "            att.client = self._cli_client(timeout=left, model=att.cli_model,\n",
        "            att.client = self._cli_client(timeout=budget, model=att.cli_model,\n",
        "test_the_wait_comes_out_of_the_call_budget",
    ),
    (
        "a slot is never given back, so the limit shrinks with every call",
        BC,
        "            if holding:\n"
        "                self._cli_slots.release()",
        "            if False:\n"
        "                self._cli_slots.release()",
        "test_a_fallback_and_a_cancel_each_give_their_slot_back",
    ),
    (
        "a call still waiting for a slot is shown as running on the subscription",
        BC,
        "            self._cli_waiting += 1\n",
        "            self._cli_waiting += 1\n"
        '            self._cli_active[att.token] = {"role": att.model, "rail": att.rail,'
        ' "since": time.time(), "requester": att.who}\n',
        "test_no_more_than_the_limit_run_at_once_and_a_waiting_call_is_not_shown_running",
    ),
    (
        "the waiting count is never decremented",
        BC,
        "                self._cli_waiting -= 1",
        "                pass",
        "test_no_more_than_the_limit_run_at_once_and_a_waiting_call_is_not_shown_running",
    ),
    # --- a call that waited for a slot is decided again when it gets one -------------------
    (
        # Re-anchored 2026-10-14 when the re-decision began taking the arrival's switch-file
        # read for a call that never waited, and again that day for its token read. Same
        # mutation.
        "a call that waited is not decided again, so a role switched off meanwhile reaches it",
        BC,
        "            still, why_now, att.switches, att.token_read = self._cli_decide(\n"
        "                att.model, requester, None if waits else att.switches,\n"
        "                None if waits else att.token_read)\n",
        '            still, why_now = True, "ok"\n',
        "test_a_call_queued_before_its_role_is_switched_off_does_not_reach_the_cli",
    ),
    (
        # Re-anchored 2026-10-14 when the token read took the next line. Same mutation.
        "a call that waited is decided again on its arrival's read, so the switch is not seen",
        BC,
        "                att.model, requester, None if waits else att.switches,\n",
        "                att.model, requester, att.switches,\n",
        "test_a_call_queued_before_its_role_is_switched_off_does_not_reach_the_cli",
    ),
    (
        "every call counts as one that took a free slot, so a switch made while it waited is "
        "not seen",
        BC,
        "        waits = self._cli_slots.locked()\n",
        "        waits = False\n",
        "test_a_call_queued_before_its_role_is_switched_off_does_not_reach_the_cli",
    ),
    (
        # Added 2026-10-15 (the delta audit). On Python 3.11 wait_for starts acquire() a turn
        # later, so a same-tick arrival waited believing it had not; the test puts 3.11's
        # wait_for in place, since this box runs a later Python.
        "a free slot is taken through wait_for again, so on 3.11 a same-tick arrival waits "
        "believing it did not",
        BC,
        "                if waits:\n"
        "                    await asyncio.wait_for(self._cli_slots.acquire(), timeout=budget)\n"
        "                else:\n"
        "                    await self._cli_slots.acquire()\n",
        "                await asyncio.wait_for(self._cli_slots.acquire(), timeout=budget)\n",
        "test_a_call_that_lost_a_free_slot_to_a_same_tick_arrival_is_decided_again_on_python_3_11",
    ),
    (
        "the re-decision falls back under a generic reason instead of its own",
        BC,
        '                raise CliUnavailable(why_now, "refused once a CLI slot came free: the "',
        '                raise CliUnavailable("failed", "refused once a CLI slot came free: the "',
        "test_a_call_queued_when_the_switch_file_turns_invalid_falls_back_closed",
    ),
    # --- a caller that hung up costs nothing more ---------------------------------------------
    (
        "a call whose caller hung up while it waited still starts on the CLI",
        BC,
        "            if await self._caller_gone(client_gone):\n"
        '                raise ClientGone("the caller hung up while this call waited',
        "            if False:\n"
        '                raise ClientGone("the caller hung up while this call waited',
        "test_a_call_whose_caller_hung_up_while_it_waited_never_reaches_the_cli",
    ),
    (
        "a hung-up call is treated as a CLI failure and answered locally for nobody",
        B,
        "            except ClientGone:\n",
        "            except ZeroDivisionError:\n",
        "test_a_call_whose_caller_hung_up_while_it_waited_never_reaches_the_cli",
    ),
    (
        "a failed CLI call whose caller hung up still loads a local model",
        BC,
        "        if await self._caller_gone(client_gone):\n"
        "            # The CLI failed, often by timing out",
        "        if False:\n"
        "            # The CLI failed, often by timing out",
        "test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback",
    ),
    (
        "a question about the caller that fails takes the call down with it",
        BC,
        "        try:\n"
        "            return bool(await client_gone())\n"
        "        except Exception:  # noqa: BLE001",
        "        try:\n"
        "            return bool(await client_gone())\n"
        "        except ZeroDivisionError:  # noqa: BLE001",
        "test_a_question_about_the_caller_that_cannot_be_answered_counts_as_still_there",
    ),
    (
        # Re-anchored 2026-10-15 on the chat route's own comment, when /v1/tts_light took the
        # same line (its read-aloud hands the broker the connection too). Same mutation.
        "the chat route does not hand the broker the connection",
        M,
        "            # before a local fallback: a rail that already gave up must not cost either.\n"
        "            client_gone=request.is_disconnected,",
        "            # before a local fallback: a rail that already gave up must not cost either.\n"
        "            client_gone=None,",
        "test_the_chat_route_hands_the_broker_the_connections_own_answer",
    ),
    (
        # Re-anchored 2026-10-15, as above: /v1/tts_light answers a hang-up with a 499 too.
        "a caller who hung up is answered as a broker failure",
        M,
        "    except ClientGone as exc:\n        # Nobody is listening; 499 is nginx's",
        "    except ZeroDivisionError as exc:\n        # Nobody is listening; 499 is nginx's",
        "test_the_chat_route_answers_a_caller_who_hung_up_with_a_499",
    ),
    # --- who may name the person asking ---------------------------------------------------
    (
        "an inference-scoped token's requester is honoured, so it can name an owner",
        M,
        '    if getattr(request.state, "broker_scope", None) == "full":\n'
        "        return requester",
        "    if True:\n"
        "        return requester",
        "test_an_inference_token_cannot_name_an_owner_onto_the_cli",
    ),
    (
        "the chat route forwards the body's requester whatever the credential",
        M,
        "            requester=_trusted_requester(request, req.requester),",
        "            requester=req.requester,",
        "test_an_inference_token_cannot_name_an_owner_onto_the_cli",
    ),
    (
        # Re-anchored 2026-10-14: require_token's decision moved into `_authorise`, which the
        # gate in front of routing shares; require_token records the scope it returns.
        "require_token records every named token as full scope",
        M,
        '    scope = "full" if match.get("scope") == "full" else "inference"',
        '    scope = "full"',
        "test_an_inference_token_cannot_name_an_owner_onto_the_cli",
    ),
    (
        "a full-scope credential's requester is dropped too, so no rail reaches the CLI",
        M,
        '    if getattr(request.state, "broker_scope", None) == "full":',
        '    if getattr(request.state, "broker_scope", None) == "admin":',
        "test_a_full_scope_credential_may_name_who_is_asking",
    ),
    # --- the status block carries the CLI timeout --------------------------------------------
    (
        "status reports a fixed timeout instead of this box's",
        BC,
        '            "timeout_s": float(s.cli_timeout),',
        '            "timeout_s": 300.0,',
        "test_status_reports_the_cli_timeout_every_rail_has_to_outlast",
    ),
    # --- roles.json that cannot be read (the review's UTF-16 probe) ---------------------------
    (
        # Re-anchored 2026-10-09: only an ON is refused now (an OFF is the kill switch). And
        # again that day, when choosing a model joined the ON in being refused.
        "a switch ON is written while roles.json cannot be read",
        C,
        "        if overlay_problem and (enabled or choose):\n            raise CliRolesFileError(",
        "        if False:\n            raise CliRolesFileError(",
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    (
        "the switch re-reads roles.json to judge names, so one that broke after the check drops "
        "overlay-only roles",
        C,
        "                parsed = self._parse_cli_roles(raw, known)",
        "                parsed = self._parse_cli_roles(raw)",
        "test_a_switch_judges_the_file_against_the_one_roles_json_read_it_checked",
    ),
    (
        # Re-anchored 2026-10-14 when cli_state began reading roles.json once (roles_read).
        "an unreadable roles.json is silent on the status block again",
        BC,
        "        overlay_problem = roles_read[1]\n",
        '        overlay_problem = ""\n',
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    (
        "roles.json saved as UTF-16 reads as no problem at all",
        C,
        "        except UnicodeDecodeError:\n            return {}, (f\"{label} is not UTF-8 text",
        "        except UnicodeDecodeError:\n            return {}, \"\"\n            return {}, (f\"{label} is not UTF-8 text",
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    (
        # Re-anchored 2026-10-14 when cli_effective began taking cli_state's read, and again
        # when it took its word on roles.json from its own one read. Same mutation.
        "the note still says the next switch drops an overlay-only role it now keeps",
        C,
        "            # From the read the names were judged against, never a second one.\n"
        "            if overlay_problem:\n",
        "            # From the read the names were judged against, never a second one.\n"
        "            if False:\n",
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    # The other problem branches of _load_roles_json (the review's plants: each survived the
    # whole broker suite when silenced, until the cases below were added to the test).
    (
        "roles.json that is not valid JSON (a trailing comma) reads as no problem at all",
        C,
        '            return {}, f"{label} is not valid JSON ({exc.msg} at line {exc.lineno})"',
        '            return {}, ""',
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    (
        "a roles.json that is not an object reads as no problem at all",
        C,
        '            return {}, f"{label} must be a JSON object of role name to model pattern"',
        '            return {}, ""',
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    (
        "a roles.json that cannot be read as a file reads as no problem at all",
        C,
        '            return {}, f"{label} could not be read ({exc.strerror})"',
        '            return {}, ""',
        "test_an_unreadable_roles_json_is_said_and_no_switch_is_lost",
    ),
    # --- a repoint (set_role) while roles.json cannot be read ----------------------------------
    (
        "a repoint while roles.json cannot be read rewrites it from nothing",
        C,
        "        if problem:\n            raise RolesFileError(",
        "        if False:\n            raise RolesFileError(",
        "test_a_repoint_is_refused_while_roles_json_cannot_be_read",
    ),
    (
        "the route answers a refused repoint as a server error",
        M,
        "    except RolesFileError as exc:\n"
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc\n",
        "",
        "test_the_route_refuses_a_repoint_with_a_409_while_roles_json_cannot_be_read",
    ),
    (
        "a repoint accepts any legal name, so a typo creates a dead role",
        C,
        "        if role not in DEFAULT_ROLES and role not in current:\n            raise ValueError(f\"unknown role {role!r}\")",
        "        if False:\n            raise ValueError(f\"unknown role {role!r}\")",
        "test_a_repoint_judges_the_name_against_the_defaults_and_roles_json",
    ),
    (
        "a repoint knows only the built-in roles, so an overlay-only one is 'unknown'",
        C,
        "        if role not in DEFAULT_ROLES and role not in current:\n            raise ValueError(f\"unknown role {role!r}\")",
        "        if role not in DEFAULT_ROLES:\n            raise ValueError(f\"unknown role {role!r}\")",
        "test_a_repoint_judges_the_name_against_the_defaults_and_roles_json",
    ),
    (
        "an unreadable roles.json is judged after the name, so the admin is sent after a typo",
        C,
        "        current, problem = self._load_roles_json()\n        if problem:\n",
        "        current, problem = self._load_roles_json()\n"
        "        if problem and role not in DEFAULT_ROLES:\n"
        "            raise ValueError(f\"unknown role {role!r}\")\n"
        "        if problem:\n",
        "test_a_repoint_of_an_overlay_only_role_names_roles_json_not_a_typo",
    ),
    (
        "while roles.json cannot be read, only a key the file names may be switched off",
        C,
        "            if overlay_problem and role not in known and role not in current:\n",
        "            if overlay_problem and role not in current:\n",
        "test_a_built_in_role_the_file_does_not_name_can_be_switched_off_while_roles_json_cannot_be_read",
    ),
    # --- switching OFF while roles.json cannot be read (the kill switch) -----------------------
    (
        "an OFF is refused again while roles.json cannot be read",
        C,
        "        if overlay_problem and (enabled or choose):\n",
        "        if overlay_problem:\n",
        "test_switching_a_role_off_still_works_while_roles_json_cannot_be_read",
    ),
    (
        "an OFF while roles.json cannot be read keeps only the default roles' switches",
        C,
        "                if overlay_problem:\n"
        "                    # Every key carried forward",
        "                if False:\n"
        "                    # Every key carried forward",
        "test_switching_a_role_off_still_works_while_roles_json_cannot_be_read",
    ),
    (
        "the seed for an OFF while roles.json cannot be read drops the env's overlay-only roles",
        C,
        "if (_ROLE_NAME.fullmatch(r) if overlay_problem else r in known)}",
        "if r in known}",
        "test_with_no_switch_file_an_off_while_roles_json_cannot_be_read_seeds_every_env_role",
    ),
    (
        "an OFF for an overlay-only role is refused while roles.json cannot be read",
        C,
        "            if overlay_problem and role not in known and role not in current:",
        "            if overlay_problem and role not in known:",
        "test_an_overlay_only_role_can_be_switched_off_while_roles_json_cannot_be_read",
    ),
    (
        "an OFF for a name nobody switched on is written while roles.json cannot be read",
        C,
        "            if overlay_problem and role not in known and role not in current:",
        "            if False:",
        "test_an_off_for_a_name_nobody_switched_on_is_refused_while_roles_json_cannot_be_read",
    ),
    # --- a CLI failure whose caller hung up is not a fallback ----------------------------------
    (
        "a CLI failure whose caller hung up is counted as a fallback",
        BC,
        '            raise ClientGone("the caller hung up before the local fallback started")',
        "            self._cli_fallbacks += 1\n"
        '            raise ClientGone("the caller hung up before the local fallback started")',
        "test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback",
    ),
    (
        "a CLI failure whose caller hung up sets fallbacks.last",
        BC,
        '            raise ClientGone("the caller hung up before the local fallback started")',
        '            self._last_cli_fallback = {"requested": model}\n'
        '            raise ClientGone("the caller hung up before the local fallback started")',
        "test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback",
    ),
    (
        "the skipped case is logged as a CLI FALLBACK",
        BC,
        "            print(f\"[broker] CLI: {model} failed ({cli_fallback['reason']}) and was "
        "skipped, \"",
        "            print(f\"[broker] CLI FALLBACK: {model} failed ({cli_fallback['reason']}) "
        "and was skipped, \"",
        "test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback",
    ),
    (
        "a fallback that does run is no longer counted",
        BC,
        "        self._cli_fallbacks += 1\n        self._last_cli_fallback = {",
        "        self._last_cli_fallback = {",
        "test_a_failed_cli_call_whose_caller_hung_up_skips_the_local_fallback",
    ),
    # --- annotations in the switch file -------------------------------------------------------
    (
        "an annotation is held to the true-or-false rule, so a text _comment turns every role off",
        C,
        '            if str(key).startswith("_"):\n                continue\n',
        "",
        "test_an_annotation_is_ignored_whatever_its_value_and_kept_by_the_next_switch",
    ),
    (
        "a value that is not true or false is skipped instead of failing the file closed",
        C,
        '            if str(key).startswith("_"):\n                continue\n',
        "            if not isinstance(value, bool):\n                continue\n",
        "test_an_invalid_file_fails_closed_and_says_so",
    ),
    (
        "the next switch drops an annotation",
        C,
        '                notes = {str(k): v for k, v in json.loads(raw).items() if str(k).startswith("_")}',
        "                notes = {}",
        "test_an_annotation_is_ignored_whatever_its_value_and_kept_by_the_next_switch",
    ),
    # --- the Claude model per role (BROKER_CLI_MODELS, the switch file's object form) ---------
    # Added 2026-10-09. These fail quietly too: the call is still answered, by a model nobody
    # chose, or a model nobody allowed reaches `--model`, with a 200 either way.
    (
        # Re-anchored 2026-10-14 when the model began coming from the decision's read.
        "every call passes BROKER_CLI_MODEL, whatever the role's own",
        BC,
        "            att.cli_model = self._cli_model_for(att.model, att.switches)\n",
        '            att.cli_model = (self.settings.cli_model or "").strip()\n',
        "test_a_dispatched_call_runs_the_real_client_with_its_roles_model",
    ),
    (
        # Re-anchored 2026-10-14 when the call grew a second line (the token read). Same mutation.
        "the role's model is chosen but never handed to the client",
        BC,
        "            att.client = self._cli_client(timeout=left, model=att.cli_model,\n",
        "            att.client = self._cli_client(timeout=left,\n",
        "test_a_dispatched_call_runs_the_real_client_with_its_roles_model",
    ),
    (
        # Re-anchored 2026-10-14 when the switch-file read became one handed in. Same mutation.
        "the model a call passes ignores the switch file",
        BC,
        "        return self.settings.cli_role_model(ref[1:], switches.models)",
        "        return self.settings.cli_role_model(ref[1:], {})",
        "test_the_call_for_a_role_passes_its_model_else_the_default_else_none",
    ),
    (
        "BROKER_CLI_MODEL wins over the role's own model",
        C,
        '        return models.get(role) or (self.cli_model or "").strip()',
        '        return (self.cli_model or "").strip() or models.get(role, "")',
        "test_the_call_for_a_role_passes_its_model_else_the_default_else_none",
    ),
    (
        "a hand-written model the allowlist does not hold reaches --model",
        C,
        "                if model is not None and model in allowed:",
        "                if model is not None:",
        "test_a_model_the_allowlist_does_not_hold_is_ignored_with_a_note_and_never_passed",
    ),
    (
        "the read side folds case and trims before the allowlist, so a near miss reaches --model",
        C,
        "                if model is not None and model in allowed:",
        "                if model is not None and model.strip().lower() in {a.lower() for a in allowed}:",
        "test_a_hand_written_near_miss_of_an_allowed_id_is_not_that_id",
    ),
    (
        "a key no role answers to has its model recorded",
        C,
        "            if _ROLE_NAME.fullmatch(str(key)) and key in known:\n                out[str(key)] = value\n",
        "            if model is not None and model in allowed:\n                models[str(key)] = model\n"
        "            if _ROLE_NAME.fullmatch(str(key)) and key in known:\n                out[str(key)] = value\n",
        "test_the_next_switch_drops_an_object_for_a_key_no_role_answers_to",
    ),
    (
        "a refused model is not named, so the role silently runs on the default",
        C,
        "        if parsed.refused:\n",
        "        if False:\n",
        "test_a_model_the_allowlist_does_not_hold_is_ignored_with_a_note_and_never_passed",
    ),
    (
        "the next switch keeps a model the allowlist does not hold",
        C,
        "                current, problem, models = parsed.switches, parsed.problem, parsed.models\n",
        "                current, problem, models = parsed.switches, parsed.problem, parsed.models\n"
        "                models.update(dict(parsed.refused))\n",
        "test_a_model_the_allowlist_does_not_hold_is_ignored_with_a_note_and_never_passed",
    ),
    (
        "a write accepts a model outside the allowlist",
        C,
        "            if model not in allowed:\n                raise ValueError(",
        "            if False:\n                raise ValueError(",
        "test_a_model_outside_the_allowlist_is_refused_and_writes_nothing",
    ),
    (
        "a switch object with another key is read as a switch",
        C,
        "    if extra:\n",
        "    if False:\n",
        "test_a_switch_object_of_the_wrong_shape_fails_the_file_closed",
    ),
    (
        "a switch object without enabled is read as a switch",
        C,
        '    if "enabled" not in value:\n',
        "    if False:\n",
        "test_a_switch_object_of_the_wrong_shape_fails_the_file_closed",
    ),
    (
        "a switch object whose model is not a string is read as a switch",
        C,
        "    if model is not None and not isinstance(model, str):\n",
        "    if False:\n",
        "test_a_switch_object_of_the_wrong_shape_fails_the_file_closed",
    ),
    (
        "choosing a model switches the role on",
        C,
        "                    current.setdefault(role, False)\n",
        "                    current[role] = True\n",
        "test_choosing_a_model_writes_the_object_form_and_leaves_the_switch",
    ),
    (
        "a switch writes the role back without its model",
        C,
        '                written = {r: ({"enabled": on, "model": models[r]} if r in models else on)',
        "                written = {r: (on if r in models else on)",
        "test_a_switch_keeps_the_model_and_clearing_it_writes_a_bare_switch_again",
    ),
    (
        "clearing a model leaves it in the file",
        C,
        "                if choose and model is None:\n                    models.pop(role, None)\n",
        "                if choose and model is None:\n                    pass\n",
        "test_a_switch_keeps_the_model_and_clearing_it_writes_a_bare_switch_again",
    ),
    (
        "a write that changes nothing rewrites the file",
        C,
        "        if enabled is None and not choose:\n            raise ValueError(",
        "        if False:\n            raise ValueError(",
        "test_a_write_that_changes_nothing_is_refused",
    ),
    (
        "a model is chosen while roles.json cannot be read",
        C,
        "        if overlay_problem and (enabled or choose):\n",
        "        if overlay_problem and enabled:\n",
        "test_a_model_cannot_be_chosen_or_cleared_while_roles_json_cannot_be_read",
    ),
    (
        "an OFF while roles.json cannot be read drops the role's model",
        C,
        '                current[role] = {**held, "enabled": False} if isinstance(held, dict) else False',
        "                current[role] = False",
        "test_an_off_while_roles_json_cannot_be_read_keeps_every_model",
    ),
    (
        "status reports BROKER_CLI_MODEL for every eligible role",
        BC,
        '            "call_models": {r: s.cli_role_model(r, eff.models) for r in sorted(roles)},',
        '            "call_models": {r: (s.cli_model or "").strip() for r in sorted(roles)},',
        "test_a_role_written_as_an_object_is_a_switch_with_a_model",
    ),
    (
        "status hides each role's model",
        BC,
        '            "role_models": dict(sorted(eff.models.items())),',
        '            "role_models": {},',
        "test_a_role_written_as_an_object_is_a_switch_with_a_model",
    ),
    (
        # Re-anchored 2026-10-13: it had not matched since `vision` joined the line (a PATTERN),
        # and again the same day when `streams` followed it.
        "the Rails tab's read loses each role's model",
        BC,
        '                "role_models": dict(st["role_models"]), "vision": st["vision"],',
        '                "role_models": {}, "vision": st["vision"],',
        "test_the_rails_tab_read_carries_the_switch_state",
    ),
    (
        "the Rails tab's read loses the allowlist",
        BC,
        '                "models": list(st["models"]), "default_model": st["default_model"],',
        '                "models": [], "default_model": st["default_model"],',
        "test_the_rails_tab_read_carries_the_switch_state",
    ),
    (
        "a served outcome does not record the model it passed",
        BC,
        '"requester": att.who,\n                                             "model": att.cli_model})',
        '"requester": att.who})',
        "test_a_dispatched_call_runs_the_real_client_with_its_roles_model",
    ),
    (
        "a fallback does not record the model it tried",
        BC,
        "                                             **({} if att.cli_model is None\n"
        '                                                else {"model": att.cli_model})})',
        "                                             })",
        "test_a_fallback_records_the_model_it_tried_and_a_refusal_records_none",
    ),
    (
        "the route drops the model",
        M,
        '    change = {"model": req.model} if "model" in req.model_fields_set else {}',
        "    change = {}",
        "test_the_route_chooses_and_clears_a_model",
    ),
    (
        "a switch sent alone clears the role's model",
        M,
        '    change = {"model": req.model} if "model" in req.model_fields_set else {}',
        '    change = {"model": req.model}',
        "test_the_route_chooses_and_clears_a_model",
    ),
    (
        "the route takes a body that changes nothing",
        S,
        '        if self.enabled is None and "model" not in self.model_fields_set:',
        "        if False:",
        "test_the_route_takes_a_model_string_or_null_only",
    ),
    (
        "the route takes a model that is not a string",
        S,
        "    model: StrictStr | None = Field(default=None, max_length=128)",
        "    model: Any = None",
        "test_the_route_takes_a_model_string_or_null_only",
    ),
    # --- a call carrying images (added 2026-10-09, when images started reaching the CLI) ------
    (
        "the broker strips the images before handing a call to the CLI",
        B,
        "                        att.client.chat(messages, format=format, options=options,\n"
        "                                        think=think),\n"
        "                        client_gone,",
        "                        att.client.chat([{k: v for k, v in m.items()\n"
        "                                          if k != \"images\"} for m in messages],\n"
        "                                        format=format, options=options,\n"
        "                                        think=think),\n"
        "                        client_gone,",
        "test_an_owner_image_call_reaches_the_cli_with_its_images",
    ),
    (
        "the local fallback is handed the messages without their images",
        B,
        "            out = await self.ollama.chat(\n"
        "                model, messages, options=options, keep_alive=keep_alive,\n"
        "                format=format, think=think,\n"
        "            )",
        "            out = await self.ollama.chat(\n"
        "                model, [{k: v for k, v in m.items() if k != \"images\"}\n"
        "                        for m in messages], options=options, keep_alive=keep_alive,\n"
        "                format=format, think=think,\n"
        "            )",
        "test_an_image_the_cli_will_not_send_falls_back_with_the_images_intact",
    ),
    (
        "the chat route's schema drops a message's images, so a photo arrives as text alone",
        S,
        "    images: list[str] | None = None\n",
        "",
        "test_an_image_posted_to_the_chat_route_reaches_the_cli_as_an_image",
    ),
    (
        "the client refuses every image call again, so none reaches the CLI",
        K,
        "        stream = any(m.get(\"images\") for m in messages)",
        "        stream = False",
        "test_a_dispatched_image_call_runs_the_real_client_over_stream_json",
    ),
    (
        "a transcript with no result line is served as an empty answer instead of falling back",
        K,
        "        if data is None:\n"
        "            raise CliUnavailable(\"bad_stream\", \"the stream-json transcript has no "
        "result line\")",
        "        if data is None:\n"
        "            data = {\"result\": \"(no answer)\"}",
        "test_a_dispatched_image_call_runs_the_real_client_over_stream_json[no-result-line]",
    ),
    # --- review fixes, 2026-10-09: the capability field, log hygiene, assistant-turn images ---
    (
        "the broker stops saying that its CLI path sends images",
        K,
        "SENDS_IMAGES = True",
        "SENDS_IMAGES = False",
        "test_the_cli_block_says_the_cli_path_sends_images",
    ),
    (
        # Re-anchored 2026-10-13 when `streams` joined the summary after `vision`.
        "the Rails tab's read drops the image capability, so a gateway reads an older broker",
        BC,
        "                \"role_models\": dict(st[\"role_models\"]), \"vision\": st[\"vision\"],\n",
        "                \"role_models\": dict(st[\"role_models\"]),\n",
        "test_the_cli_block_says_the_cli_path_sends_images",
    ),
    (
        "the client refuses every image while the status block still says it sends them",
        K,
        "    line = {\"type\": \"user\", \"message\": {\"role\": \"user\", \"content\": blocks}}",
        "    raise CliUnavailable(\"vision_unsupported\", \"every image\")",
        "test_the_cli_block_says_the_cli_path_sends_images",
    ),
    (
        # Re-anchored 2026-10-13 when the rule moved into _exit_failure_from (see the same case
        # in mutate_cli_backend.py). Same fault: the last line stands in for stderr.
        "a killed image call's last transcript line, the model's account of the picture, "
        "reaches the log and cli.fallbacks.last",
        K,
        "    return _exit_failure_from(_last_result(stdout), stderr, code, lines)",
        "    return _exit_failure_from(_last_result(stdout),\n"
        "                              stderr or (stdout.strip().splitlines() or [\"\"])[-1],\n"
        "                              code, lines)",
        "test_a_failed_image_call_quotes_no_transcript_line_anywhere[killed-before-the-result-line]",
    ),
    (
        "an image call that exited nonzero is served from its success-looking result line",
        K,
        "        if proc.returncode != 0:",
        "        if proc.returncode != 0 and not (\n"
        "                stream and (_last_result(stdout) or {}).get(\"is_error\") is False):",
        "test_a_failed_image_call_quotes_no_transcript_line_anywhere[success-result-line]",
    ),
    (
        # Re-anchored 2026-10-13: it had not matched since the rule became "exactly user", so
        # it reported PATTERN. Same mutation: the refusal is gone.
        "an assistant turn's image goes to the CLI as the asker's own",
        K,
        "            if images and role != \"user\":",
        "            if False:",
        "test_an_image_on_a_user_turn_reaches_the_cli_and_on_an_assistant_turn_falls_back[assistant]",
    ),
    (
        "only the last message's images choose the image path, so an earlier turn's photo "
        "falls back",
        K,
        "        stream = any(m.get(\"images\") for m in messages)",
        "        stream = bool(messages[-1].get(\"images\"))",
        "test_an_image_on_a_user_turn_reaches_the_cli_and_on_an_assistant_turn_falls_back[user]",
    ),
]


#: Seconds one suite run may take here. Longer than the shared default (300) because one mutation
#: is slow without hanging: with slots never given back, every CLI call after the second on one
#: broker waits out its whole budget (180 s by default) before falling back `busy`. Measured
#: 2026-10-09: that mutation was a TIMEOUT at 300 s with failures already printed, and FIRED with
#: 1500. A real hang still ends here as TIMEOUT, never as FIRED.
SUITE_TIMEOUT = max(DEFAULT_TIMEOUT, 1500.0)


def run_suite(tests: list[str] | None = None, *extra: str) -> SuiteRun:
    """The suite, or only `tests` (pytest node ids) when given."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BROKER)
    return run_bounded([PY, "-m", "pytest", *(tests or SUITE), "-q", "--no-header",
                        "-p", "no:cacheprovider", *extra],
                       cwd=BROKER, env=env, timeout=SUITE_TIMEOUT)


def collected() -> list[str]:
    """Every test id in the suite, as pytest names them (`tests/x.py::name[param]`)."""
    code, out, _hung = run_suite(None, "--collect-only")
    ids = [ln.strip() for ln in out.splitlines() if "::" in ln and not ln.startswith(" ")]
    if code != 0 or not ids:
        raise SystemExit(f"could not collect the suite:\n{out[-2500:]}")
    return ids


def targets(expected: str, ids: list[str]) -> list[str]:
    """The ids a case names: its test, every parameter of it when it names none, exactly that
    parameter when it does. Matched by name, never by substring, so one case cannot quietly
    run a second test whose name merely starts the same way."""
    return [i for i in ids
            if (name := i.split("::", 1)[1]) == expected or name.startswith(expected + "[")]


def main() -> int:
    whole = "--whole-file" in parse_harness_args(MUTATIONS, flags=("--whole-file",)).flags
    code, out, hung = run_suite()
    if code != 0:
        # A baseline that ran out of time is its own verdict, and nothing below can mean
        # anything after it.
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")
    ids = collected()

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        chosen = None if whole else targets(expected, ids)
        if chosen == []:
            # Run against nothing, it would "survive": a renamed test must not read as a hole.
            print(f"  NO-TEST            {label}: no collected test is named {expected!r}")
            results.append((label, "NO-TEST"))
            continue
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
            code, out, hung = run_suite(chosen)
            fired = code != 0
            named = named_failed(out, expected)
            # A hang broke SOMETHING, but nothing showed the named test failing, so it is not
            # FIRED. Its own verdict, so it is never mistaken for one.
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                # Show the evidence; see the same block in mutate_cli_backend.py for why.
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
