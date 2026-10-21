"""Mutation harness for `mutation_runner.py`, the runner every mutation harness shares.
NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_mutation_runner.py

A time limit that does not hold fails in the worst direction: the harness simply never answers,
and that is indistinguishable from a slow run. Each mutation removes one part of the limit and
names the test that must notice. Runs take ~2 minutes, because two of the mutations make the
suite wait out the children's own 60 s sleeps.

The harness process imported `mutation_runner` BEFORE any mutation was written, so its own suite
runs keep the real limit; only the pytest child reads the mutated file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mutation_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args,
                             restore_exact, run_bounded)

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
SUITE = ["tools/tests/test_mutation_runner.py", "tools/tests/test_mutation_runner_js.py"]

R = "tools/tests/mutation_runner.py"
J = "tools/tests/mutation_runner.mjs"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "run_tree decodes text in the console code page again, so a UTF-8 child kills the harness",
        R,
        '        popen_kw["encoding"] = "utf-8"\n        popen_kw.setdefault("errors", "replace")\n',
        "        pass\n",
        "test_run_tree_decodes_text_as_utf8_with_replacement",
    ),
    (
        "no time limit, so a hung suite hangs the harness",
        R,
        "        out, err = proc.communicate(timeout=limit)",
        "        out, err = proc.communicate()",
        "test_a_hung_suite_is_killed_at_the_timeout",
    ),
    (
        "only the child is killed, so a grandchild holding the pipe keeps the harness waiting",
        R,
        '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n'
        "                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                           timeout=REAP_TIMEOUT, check=False)\n"
        "        else:\n"
        "            os.killpg(proc.pid, signal.SIGKILL)",
        "            pass\n"
        "        else:\n"
        "            pass",
        "test_a_grandchild_holding_the_pipe_cannot_keep_the_runner_waiting",
    ),
    (
        "a killed run is reported as an ordinary failure, so a hang could read as FIRED",
        R,
        "        return SuiteRun(-1, (out or \"\") + (err or \"\") + note, True)",
        "        return SuiteRun(-1, (out or \"\") + (err or \"\") + note, False)",
        "test_a_hung_suite_is_killed_at_the_timeout",
    ),
    (
        "mutate_bytes looks for the LF spelling only, so a CRLF file never matches",
        R,
        '    if "\\n" in old:',
        '    if False:',
        "test_a_crlf_file_is_mutated_in_its_own_line_endings",
    ),
    (
        "an LF match inside a CRLF break counts again, so one occurrence reads as two",
        R,
        '            if not inside_crlf:\n',
        "            if True:\n",
        "test_a_pattern_starting_at_a_line_break_is_found_once_in_a_crlf_file",
    ),
    (
        "the whole file is renormalised, so every other line ending changes too",
        R,
        '    return 1, (text[:at] + n + text[at + len(o):]).encode("utf-8")',
        '    return 1, text.replace("\\r\\n", "\\n").replace(old, new).encode("utf-8")',
        "test_a_mixed_file_changes_only_the_matched_span",
    ),
    (
        "a restore is assumed rather than checked",
        R,
        '    if path.read_bytes() != original:',
        '    if False:',
        "test_a_restore_is_proven_byte_for_byte",
    ),
    # --- run_tree, the subprocess.run drop-in ---------------------------------------------
    (
        "run_tree waits for ever, so a harness built on it hangs",
        R,
        '        out, err = proc.communicate(input=input, timeout=limit)',
        '        out, err = proc.communicate(input=input)',
        "test_run_tree_raises_timeout_expired_promptly",
    ),
    (
        "run_tree kills only the child, so a grandchild holding the pipe keeps it waiting",
        R,
        '        _kill_tree(proc)\n'
        '        try:\n'
        '            out, err = proc.communicate(timeout=REAP_TIMEOUT)\n'
        '        except subprocess.TimeoutExpired:\n'
        '            out = err = None',
        '        proc.kill()\n'
        '        try:\n'
        '            out, err = proc.communicate(timeout=REAP_TIMEOUT)\n'
        '        except subprocess.TimeoutExpired:\n'
        '            out = err = None',
        "test_run_tree_kills_a_grandchild_holding_the_pipe",
    ),
    (
        "run_tree with no timeout given waits for ever",
        R,
        '    limit = DEFAULT_TIMEOUT if timeout is None else timeout\n'
        '    if capture_output:',
        '    limit = timeout\n'
        '    if capture_output:',
        "test_run_tree_without_a_timeout_is_still_bounded",
    ),
    (
        "write_like writes LF into a CRLF file, so the mutated file differs on every line",
        R,
        '    if b"\\r\\n" in original:',
        '    if False:',
        "test_write_like_keeps_each_files_own_line_endings",
    ),
    (
        "a copy-based harness's real-tree check compares nothing",
        R,
        "    changed = [str(p) for p, b in snap.items() if p.read_bytes() != b]",
        "    changed = []",
        "test_a_changed_real_file_is_named_as_a_dirty_tree",
    ),
    # --- no child ever inherits a live opt-in -------------------------------------------------
    (
        "run_bounded hands the suite the caller's CLI_LIVE, so every mutation spends real quota",
        R,
        "    proc = subprocess.Popen(cmd, cwd=cwd, env=_without_live(env), stdout=subprocess.PIPE,",
        "    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,",
        "test_no_child_ever_inherits_a_live_opt_in",
    ),
    (
        "run_tree hands the suite an explicitly passed CLI_LIVE",
        R,
        '    popen_kw["env"] = _without_live(popen_kw.get("env"))\n',
        "",
        "test_no_child_ever_inherits_a_live_opt_in",
    ),
    (
        "the live opt-ins are listed but never removed",
        R,
        "        out.pop(name, None)",
        "        pass",
        "test_no_child_ever_inherits_a_live_opt_in",
    ),
    # --- parse_harness_args: the command line, read before anything is touched --------------
    (
        "--list prints the cases and then returns to the harness, which runs them",
        R,
        '              + "; listed, nothing run")\n        raise SystemExit(0)\n',
        '              + "; listed, nothing run")\n',
        "test_list_names_every_case_and_control_and_exits_before_returning",
    ),
    (
        "an unknown option is taken as a label filter again, so it selects nothing and runs",
        R,
        '        elif a.startswith("-") and a != "-":\n            refuse(f"unknown option {a!r}")\n',
        "",
        "test_an_unknown_option_is_refused_with_a_usage_line",
    ),
    (
        "a positional argument to a harness with no filter is ignored, and every case runs",
        R,
        '            refuse(f"takes no label filter, so {a!r} would have been ignored and every case run")',
        "            pass",
        "test_a_positional_argument_to_a_harness_with_no_filter_is_refused",
    ),
    (
        "--list leaves the controls out",
        R,
        '        for label in picked_controls:\n            print(f"control\\t{label}")\n',
        "",
        "test_list_names_every_case_and_control_and_exits_before_returning",
    ),
    (
        "--list ignores the label filter it was given",
        R,
        "        if only:\n            keep = ",
        "        if False:\n            keep = ",
        "test_list_with_a_filter_names_only_what_the_filter_selects",
    ),
    (
        "--list matches case-sensitively where the harness's own filter does not",
        R,
        "        fold = (lambda s: s.lower()) if ignore_case else (lambda s: s)",
        "        fold = (lambda s: s)",
        "test_list_with_a_filter_names_only_what_the_filter_selects",
    ),
    (
        "a harness that lists itself never gets --list back",
        R,
        "    return HarnessArgs(listing, tuple(only), frozenset(given))",
        "    return HarnessArgs(False, tuple(only), frozenset(given))",
        "test_a_harness_that_lists_itself_gets_list_back_and_unknown_options_still_refused",
    ),
    # --- the verdict, named_failed ------------------------------------------------------------
    (
        "a summary line is read anywhere in a line, so an indented copy of one counts",
        R,
        '_SUMMARY = re.compile(r"^(?:FAILED|',
        '_SUMMARY = re.compile(r"(?:FAILED|',
        "test_each_shape_a_harness_names_a_test_in",
    ),
    (
        "the id is split on :: with its parametrize id and message, so an id that quotes another "
        "test reads as that test",
        R,
        '    got = test.replace("\\\\", "/").split("::")',
        '    got = (test + rest).replace("\\\\", "/").split("::")',
        "test_an_id_that_quotes_another_test_id_names_only_its_own_test",
    ),
    (
        "an expected parametrize id is matched as a prefix, so test_a[k] is test_a[k]2]",
        R,
        '    return not bracket or rest == bracket + param or rest.startswith(bracket + param + " - ")',
        "    return not bracket or rest.startswith(bracket + param)",
        "test_each_shape_a_harness_names_a_test_in",
    ),
    (
        "a name is matched as a prefix, so test_x is found in test_x_more",
        R,
        "    if got[-1] != want[-1]:\n        return False",
        "    if not got[-1].startswith(want[-1]):\n        return False",
        "test_a_failed_line_for_another_test_with_the_same_prefix_or_suffix_does_not",
    ),
    (
        "a bare name no longer matches a parametrized run of its test",
        R,
        "    return not bracket or ",
        "    return ",
        "test_each_shape_a_harness_names_a_test_in",
    ),
    (
        "a file part is matched as a suffix, so old_test_x.py passes for test_x.py",
        R,
        '    if not all(g == w or g.endswith("/" + w) for g, w in zip(got[:-1], want[:-1])):',
        "    if not all(g.endswith(w) for g, w in zip(got[:-1], want[:-1])):",
        "test_each_shape_a_harness_names_a_test_in",
    ),
    # --- the node twin, mutation_runner.mjs -------------------------------------------------
    (
        "the node watchdog kills only the child, so node --test's file process survives",
        J,
        '    killTree(child.pid)\n',
        '    child.kill()\n',
        "test_a_grandchild_holding_the_pipe_is_killed_with_the_tree",
    ),
    (
        "the node watchdog's limit never fires, so only the backstop ends a hung suite",
        J,
        '  }, limit)',
        '  }, limit * 1000)',
        "test_a_hung_run_ends_at_its_limit_as_timeout",
    ),
    (
        "a node timeout is returned as an exit code, so a hang can read as a red suite",
        J,
        "  return { status: timedOut ? 'TIMEOUT' : p.status, out, timedOut }",
        "  return { status: p.status, out, timedOut }",
        "test_a_hung_run_ends_at_its_limit_as_timeout",
    ),
    (
        "a node restore is assumed rather than checked",
        J,
        '  if (!Buffer.from(fs.readFileSync(file)).equals(original)) {',
        '  if (false) {',
        "test_a_restore_is_proven_byte_for_byte",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "tools" / "tests")
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=REPO, env=env)


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
        path = REPO / relpath
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
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
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
