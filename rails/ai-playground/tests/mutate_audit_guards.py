"""Mutation harness for the guards the 2026-10-08 audit added to this rail. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\ai-playground\\tests\\mutate_audit_guards.py

Run this, not just pytest, before trusting a change to the NVIDIA budgets (app._charge_nim and
the /ws/rag NIM branch), the Embedding Lab's cancel path (app.ws_bench, engine.run's `cancel`),
the weekly re-pull's verdict, the upload and chunk caps, or the Embedding Lab frontend helpers
under frontend/src/demos/embed-bench/ (exercised by node through test_frontend_helpers.py).

Every one of these fails QUIETLY. Drop the answer budget and NVIDIA bills 800 tokens a question
for as long as somebody asks; key the ceiling on the caller's name and a sibling container
rotates past it; let the cancel go unchecked and a closed tab re-embeds every model on the
shared GPU for nobody. Each guard is broken on purpose below to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
RAIL = REPO / "rails" / "ai-playground"
RAIL_SRC = RAIL / "src"
PY = sys.executable
# The whole rail suite (~10 s): a mutation that breaks something other than its named test
# should show up as FIRED-WRONG-TEST rather than pass unseen in a file this run skipped.
SUITE = ["tests"]
RUN_TIMEOUT = 300

A = "rails/ai-playground/src/ai_playground/api/app.py"
E = "rails/ai-playground/src/ai_playground/bench/engine.py"
SS = "rails/ai-playground/frontend/src/streamSocket.ts"
ROWS = "rails/ai-playground/frontend/src/demos/embed-bench/rows.ts"
CSS = "rails/ai-playground/frontend/src/theme.css"
REF = "rails/ai-playground/src/ai_playground/bench/refresh.py"
RAG = "rails/ai-playground/src/ai_playground/rag.py"
COR = "rails/ai-playground/src/ai_playground/corpora.py"
NODE = "test_lab_helpers_pass_under_node"
VP = "rails/ai-playground/frontend/src/visiblePoll.ts"
VP_NODE = "test_visible_poll_passes_under_node"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    # --- the NVIDIA answer budget ---------------------------------------------------------
    (
        "the NVIDIA answer path is not charged at all (the audit's finding, restored)",
        [(A, "                    _charge_nim(_NIM_CHAT_LIMIT, user)\n",
             "                    pass\n")],
        ["test_one_caller_is_stopped_at_their_own_budget",
         "test_a_refused_question_does_no_work_at_all",
         "test_rotating_the_name_does_not_get_past_the_rail_wide_ceiling"],
    ),
    (
        "the rail-wide ceiling is never charged, so rotating the name is unlimited",
        [(A, "    _NIM_GLOBAL_LIMIT.check(_RAIL_WIDE)\n", "")],
        ["test_rotating_the_name_does_not_get_past_the_rail_wide_ceiling",
         "test_the_probe_draws_on_the_same_ceiling",
         "test_the_weekly_check_draws_on_the_same_ceiling"],
    ),
    (
        "the ceiling is keyed on the caller's name: one more bucket per invented name",
        [(A, "    _NIM_GLOBAL_LIMIT.check(_RAIL_WIDE)\n", "    _NIM_GLOBAL_LIMIT.check(user)\n")],
        ["test_rotating_the_name_does_not_get_past_the_rail_wide_ceiling",
         "test_the_probe_draws_on_the_same_ceiling"],
    ),
    (
        "the per-caller budget is dropped, so one caller can spend the whole ceiling",
        [(A, "    per_caller.check(user)\n", "")],
        ["test_one_caller_is_stopped_at_their_own_budget",
         "test_another_caller_keeps_their_own_allowance"],
    ),
    (
        "every caller shares one per-caller bucket, so one person locks everyone out",
        [(A, "    per_caller.check(user)\n", "    per_caller.check(None)\n")],
        ["test_another_caller_keeps_their_own_allowance"],
    ),
    (
        "the question is retrieved BEFORE the charge, so a refusal still embeds on the GPU",
        [(A, "                try:\n                    _charge_nim(_NIM_CHAT_LIMIT, user)\n",
             "                await asyncio.to_thread(corpora.retrieve, corpus_id, question, "
             "config.TOP_K, user)\n"
             "                try:\n                    _charge_nim(_NIM_CHAT_LIMIT, user)\n")],
        ["test_a_refused_question_does_no_work_at_all"],
    ),
    (
        "a non-numeric corpus is parsed outside the guard again",
        [(A, "            try:\n                corpus_id = int(req.get(\"corpus\") or 0)\n"
             "            except (TypeError, ValueError):\n"
             "                await ws.send_json({\"type\": \"error\", \"message\": "
             "\"corpus must be a number\"})\n"
             "                return\n",
             "            corpus_id = int(req.get(\"corpus\") or 0)\n")],
        ["test_a_malformed_request_gets_an_error_frame_not_a_dead_socket"],
    ),
    # --- the benchmark thread stops when its client goes -----------------------------------
    (
        "the engine never checks the event between configs (the audit's finding)",
        [(E, "    for rc in run_configs:\n        _check(cancel)\n",
             "    for rc in run_configs:\n")],
        ["test_a_closed_socket_stops_the_benchmark_thread_between_configs",
         "test_a_cancelled_engine_stops_before_the_next_config"],
    ),
    (
        "a reranked row is still started after the cancel",
        [(E, "        if reranker is not None and shared is not None:\n            _check(cancel)\n",
             "        if reranker is not None and shared is not None:\n")],
        ["test_a_cancelled_engine_starts_no_rerank_row"],
    ),
    (
        "the handler never tells the thread (only the asyncio task is cancelled)",
        [(A, "            # signal engine.run polls between configs; see the reader's finally above.\n"
             "            cancel.set()\n",
             "            # signal engine.run polls between configs; see the reader's finally above.\n")],
        ["test_a_closed_socket_stops_the_benchmark_thread_between_configs"],
    ),
    (
        "the event is never handed to the engine",
        [(A, "                                         reranker_id=reranker, rerank_depth=rerank_depth,\n"
             "                                         cancel=cancel)\n",
             "                                         reranker_id=reranker, rerank_depth=rerank_depth)\n")],
        ["test_a_closed_socket_stops_the_benchmark_thread_between_configs"],
    ),
    (
        "over-correction: the event is checked inverted, so a normal run stops at once",
        [(E, "    if cancel is not None and cancel.is_set():\n",
             "    if cancel is not None and not cancel.is_set():\n")],
        ["test_an_uncancelled_run_is_unchanged"],
    ),
    # --- stream end, socket close, the dropped reranker row: the frontend helpers, under node -
    (
        "a stream that closes with no terminal frame is silence again",
        [(SS, "    h.onLost(failed ? LOST_FAILED : LOST_CLOSED)\n", "")],
        [NODE],
    ),
    (
        "close() is not remembered, so an unmount's own close is reported as an error",
        [(SS, "      if (stopped) return\n      stopped = true\n      ws.close()\n",
             "      ws.close()\n")],
        [NODE],
    ),
    (
        "a terminal frame does not close the socket",
        [(SS, "      finished = true\n      ws.close()\n    }\n", "      finished = true\n    }\n")],
        [NODE],
    ),
    (
        "unmatched result rows are dropped again (the reranker failure row disappears)",
        [(ROWS, "  return [...merged, ...results.filter((r) => !planned.has(r.id))]\n",
                "  return merged\n")],
        [NODE],
    ),
    # --- the stylesheet stays under the wrapper ----------------------------------------------
    (
        "one rule loses its wrapper prefix, so .chip restyles the whole platform again",
        [(CSS, ".ai-playground .chip {\n", ".chip {\n")],
        ["test_every_rule_is_under_the_wrapper"],
    ),
    (
        "a rule inside @media loses its prefix",
        [(CSS, "{ .ai-playground .lab-body { flex-direction: column; }",
               "{ .lab-body { flex-direction: column; }")],
        ["test_every_rule_is_under_the_wrapper"],
    ),
    (
        "the keyframes name goes back to a global one",
        [(CSS, "@keyframes ap-blink {", "@keyframes blink {")],
        ["test_keyframe_names_carry_the_rail_prefix"],
    ),
    # --- the weekly re-pull's verdict ---------------------------------------------------------
    (
        "a run whose pulls failed answers 200 again (the audit's finding)",
        [(A, '        if result["failed"]:\n', "        if False:\n")],
        ["test_every_pull_failing_is_a_failed_run", "test_one_failed_pull_fails_the_run_too"],
    ),
    (
        "only a TOTAL failure fails the run",
        [(A, '        if result["failed"]:\n', '        if result["failed"] == result["count"]:\n')],
        ["test_one_failed_pull_fails_the_run_too"],
    ),
    (
        "failures are not counted",
        [(REF, '    failed = [r for r in results if not r["ok"]]\n', "    failed = []\n")],
        ["test_every_pull_failing_is_a_failed_run", "test_one_failed_pull_fails_the_run_too"],
    ),
    # --- recorded 2026-09-10/11: chunk, upload, corpus and cache bounds -----------------------
    (
        "a paragraph has no maximum length again (one 207k-char chunk)",
        [(RAG, "    if len(para) <= MAX_CHARS:\n        return [para]\n",
               "    if True:\n        return [para]\n")],
        ["test_a_hard_wrapped_file_is_no_longer_one_chunk",
         "test_a_single_huge_line_is_cut_at_spaces_and_loses_nothing",
         "test_a_line_with_no_spaces_is_still_cut"],
    ),
    (
        "the 40-character floor is applied to the pieces, dropping a long paragraph's tail",
        [(RAG, "            for piece in _pieces(para)]\n",
               "            for piece in _pieces(para) if len(piece) > MIN_CHARS]\n")],
        ["test_the_short_tail_of_a_long_paragraph_is_kept"],
    ),
    (
        "uploads are read whole again, with no ceiling",
        [(A, "            if total > cap:\n", "            if False:\n")],
        ["test_an_oversized_document_upload_is_413_and_nothing_is_ingested",
         "test_the_cap_is_on_the_total_not_per_file",
         "test_an_oversized_query_set_is_413_before_it_is_parsed"],
    ),
    (
        "the ceiling is per file, so many files pass it together",
        [(A, "        parts: list[bytes] = []\n",
             "        parts: list[bytes] = []\n        total = 0\n")],
        ["test_the_cap_is_on_the_total_not_per_file"],
    ),
    (
        "no per-owner corpus ceiling",
        [(COR, "        if db.count_user_corpora(con, owner) >= config.MAX_CORPORA_PER_USER:\n",
               "        if False:\n")],
        ["test_the_corpus_ceiling_refuses_before_embedding"],
    ),
    (
        "the retrieval cache is unbounded again",
        [(COR, "    while len(_CACHE) > max(1, config.CORPUS_CACHE_SIZE):\n"
               "        _CACHE.popitem(last=False)\n", "")],
        ["test_the_retrieval_cache_is_bounded_and_least_recently_used"],
    ),
    (
        "a cache hit does not count as a use, so the busiest corpus is evicted first",
        [(COR, "        if cached is not None:\n            _CACHE.move_to_end(corpus_id)\n", "")],
        ["test_the_retrieval_cache_is_bounded_and_least_recently_used"],
    ),
    # --- the weekly re-pull leaves the platform embedder alone unless opted in ---------------
    (
        "the platform embedder is re-pulled every week again",
        [(REF, "        if embedder is not None and registry._broker_available(s, {embedder}):\n",
               "        if False:\n")],
        ["test_the_platform_embedder_is_not_repulled_by_default"],
    ),
    (
        "the opt-in is ignored, so the embedder can never be updated on purpose",
        [(REF, "    embedder = None if config.REPULL_EMBEDDER else platform_embedder()\n",
               "    embedder = platform_embedder()\n")],
        ["test_opting_in_repulls_the_embedder_too"],
    ),
    (
        "an unresolvable @embed is treated as no embedder, so everything is pulled",
        [(REF, '    if not resolved or resolved.startswith("@"):\n', "    if False:\n")],
        ["test_an_unresolvable_embedder_refuses_the_run_and_pulls_nothing"],
    ),
    (
        "the skip is silent: the run record never says what was left out",
        [(REF, '    if skipped:\n        out["skipped"]', '    if False:\n        out["skipped"]')],
        ["test_the_platform_embedder_is_not_repulled_by_default",
         "test_the_skip_survives_into_a_failed_runs_record"],
    ),
    # --- the header poll skips a hidden page (frontend, under node) --------------------------
    (
        "a hidden page's ticks still poll (the audit's finding)",
        [(VP, "  const stopTimer = every(() => { if (!env.hidden()) load() })\n",
              "  const stopTimer = every(() => { load() })\n")],
        [VP_NODE],
    ),
    (
        "no catch-up on return, so the chips stay stale until the next tick",
        [(VP, "  const stopListening = env.onVisibilityChange(() => { if (!env.hidden()) load() })\n",
              "  const stopListening = env.onVisibilityChange(() => {})\n")],
        [VP_NODE],
    ),
    (
        "going hidden counts as a reason to poll",
        [(VP, "  const stopListening = env.onVisibilityChange(() => { if (!env.hidden()) load() })\n",
              "  const stopListening = env.onVisibilityChange(() => { load() })\n")],
        [VP_NODE],
    ),
    (
        "the visibility listener outlives the component",
        [(VP, "    stopTimer()\n    stopListening()\n", "    stopTimer()\n")],
        [VP_NODE],
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = str(RAIL_SRC)
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "AI_PLAYGROUND_NVIDIA_API_KEY",
                "AI_PLAYGROUND_NIM_CHAT_LIMIT", "AI_PLAYGROUND_NIM_GLOBAL_LIMIT"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import ai_playground.api.app as a, ai_playground.bench.engine as e;"
         "print(a.__file__); print(e.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider", *extra],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from a FAILED line can mean anything: a
    # renamed test would otherwise turn every mutation naming it into a silent SURVIVED.
    _, collected = run_suite(("--collect-only",))
    named = {t for _, _, tests in MUTATIONS for t in tests}
    missing = sorted(t for t in named if t not in collected)
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    # The witness label: a node-backed test only means something if node actually ran it.
    if "skipped" in out.splitlines()[-1]:
        print("BASELINE SKIPPED TESTS -- " + out.splitlines()[-1])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original_bytes: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                current = path.read_text(encoding="utf-8")
                n = current.count(old)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                mr.write_like(path, current.replace(old, new), original_bytes[path])
            if not pattern_ok:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
