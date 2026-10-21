"""Mutation harness for `requester`, the Claude CLI frames and the wait in ai-playground.
NOT a pytest file: it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\ai-playground\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`, either facade
call's wire, `broker._served_by`, the RAG routes, `/ws/rag`'s heartbeat or backend frames (and
what the heartbeat wrapper does when the client leaves, tests/test_stream_leave.py), the
capabilities chip or the broker wait.

The role guard fails OPEN and SILENTLY (another rail's role gets the viewer's name), a dropped
requester makes the CLI silently inert (which looks healthy), a missing heartbeat looks like
a flaky network, and a read left running after the client went keeps a CLI slot busy for
nobody: none of them shows on its own. Its siblings in other rails are
rails/gemini-cx/tests/mutate_cli_requester.py and
rails/smb-partner-enablement/tests/mutate_cli_requester.py.

A verdict counts only with the expected test named on a FAILED line. The baseline must be green
with nothing skipped (node runs the frontend helper's test), the witness must show the suite
imports THIS tree's ai_playground, and every CONTROL (an edit that changes no behaviour) must
SURVIVE, or the harness is reporting the suite's noise rather than the mutations.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

RAIL = Path(__file__).resolve().parents[1]               # rails/ai-playground
REPO = RAIL.parents[1]
PY = sys.executable
PYTHONPATH = [str(RAIL / "src")]
SUITE = [str(RAIL / "tests" / n) for n in
         ("test_cli_requester.py", "test_broker_stream.py", "test_frontend_helpers.py",
          "test_stream_leave.py")]

PKG = RAIL / "src" / "ai_playground"
BROKER = PKG / "broker.py"
APP = PKG / "api" / "app.py"
SERVED = RAIL / "frontend" / "src" / "demos" / "rag" / "servedBy.ts"

CHIP = "test_an_owner_sees_generation_on_claude_and_nothing_else"

# (label, file, old, new, the test that must fail; None for a CONTROL, which must SURVIVE)
MUTATIONS: list[tuple[str, Path, str, str, str | None]] = [
    # --- the facade --------------------------------------------------------------------------
    (
        "any role rides the viewer's name to the CLI (the role guard is gone)",
        BROKER,
        "    return requester if requester and ref == DEFAULT_MODEL else None",
        "    return requester",
        "test_a_foreign_role_never_carries_the_name",
    ),
    (
        "the buffered call never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who\n    resp = _post(',
        '    if False:\n        payload["requester"] = who\n    resp = _post(',
        "test_the_generation_role_carries_the_requester",
    ),
    (
        "the buffered call sends an absent requester as null",
        BROKER,
        '    if who:\n        payload["requester"] = who\n    resp = _post(',
        '    payload["requester"] = who\n    resp = _post(',
        "test_no_requester_means_none_on_the_wire",
    ),
    (
        "the streamed call never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who\n    try:',
        '    if False:\n        payload["requester"] = who\n    try:',
        "test_the_stream_carries_the_requester_on_its_own_role_only",
    ),
    (
        "the streamed call keeps the name on every role",
        BROKER,
        "    who = _cli_requester(ref, requester)\n    if who:\n        payload[\"requester\"] = who\n"
        "    try:",
        "    who = requester\n    if who:\n        payload[\"requester\"] = who\n    try:",
        "test_the_stream_carries_the_requester_on_its_own_role_only",
    ),
    # --- the stream's CLI frames ---------------------------------------------------------------
    (
        "a CLI interruption is read as a finished answer",
        BROKER,
        '                    if frame.get("error"):\n',
        '                    if frame.get("error") and frame.get("done_reason") != '
        '"cli_interrupted":\n',
        "test_a_cli_interruption_raises_after_the_partial_tokens",
    ),
    (
        "the stream never says who answers",
        BROKER,
        "                        on_backend(served)\n",
        "                        pass\n",
        "test_the_cli_lead_frame_is_skipped_and_names_claude_once",
    ),
    (
        "Claude's done frame names Claude a second time",
        BROKER,
        '    if frame.get("done") or "message" in frame:\n        return None\n',
        "    if False:\n        return None\n",
        "test_the_cli_lead_frame_is_skipped_and_names_claude_once",
    ),
    (
        "a fallback is not told apart from Claude",
        BROKER,
        '        return {"backend": "ollama", "reason": str(fell.get("reason") or "")}',
        "        return None",
        "test_the_cli_fallback_frame_is_skipped_and_names_the_local_model",
    ),
    # --- the routes ----------------------------------------------------------------------------
    (
        "the buffered ask drops the viewer",
        APP,
        'options={"num_predict": config.MAX_TOKENS}, requester=ident.user)',
        'options={"num_predict": config.MAX_TOKENS})',
        "test_the_buffered_ask_names_the_viewer",
    ),
    (
        "the streamed ask drops the viewer",
        APP,
        "requester=user, on_backend=served.append)",
        "on_backend=served.append)",
        "test_the_streamed_ask_names_the_viewer",
    ),
    (
        "a NIM question goes to the broker instead",
        APP,
        '            if backend == "nim":\n                gen = nim.chat_stream(',
        '            if False:\n                gen = nim.chat_stream(',
        "test_a_nim_question_never_reaches_the_broker",
    ),
    (
        "the socket goes silent while the CLI thinks (no heartbeat)",
        APP,
        "            async with aclosing(_stream_with_heartbeat(ws, gen)) as toks:\n",
        "            async with aclosing(gen) as toks:\n",
        "test_a_silent_wait_keeps_the_socket_alive_with_pings",
    ),
    (
        "the socket never says who answered",
        APP,
        '                    while served:\n                        await ws.send_json('
        '{"type": "backend", **served.pop(0)})\n'
        '                    await ws.send_json({"type": "token"',
        '                    await ws.send_json({"type": "token"',
        "test_the_socket_says_who_answered_before_the_first_token",
    ),
    # --- a client that leaves (tests/test_stream_leave.py) -------------------------------------
    (
        "the /ws/rag wrapper leaves the read in flight running when a ping raises",
        APP,
        "        if pending is not None:\n            pending.cancel()\n",
        "        if False:\n            pending.cancel()\n",
        "test_a_ping_to_a_client_that_left_ends_the_read",
    ),
    (
        "the /ws/rag wrapper never closes the stream",
        APP,
        "        if aclose is not None:\n            await aclose()",
        "        if False:\n            await aclose()",
        "test_a_caller_that_stops_reading_closes_the_stream",
    ),
    (
        "/ws/rag reads the wrapper without aclosing, so a failed token send leaves it open",
        APP,
        # nullcontext hands the generator through unclosed: the loop as it was before.
        "from contextlib import aclosing\n",
        "from contextlib import nullcontext as aclosing\n",
        "test_ws_rag_a_client_gone_mid_answer_ends_the_read",
    ),
    # --- the chip ------------------------------------------------------------------------------
    (
        "the chip stops opting generation in, so an owner is told it runs locally",
        APP,
        "        ], requester=ident.user, cli_slots=CLI_SLOTS)",
        "        ])",
        CHIP,
    ),
    (
        "the chip judges a different identity from the one the calls carry",
        APP,
        "        ], requester=ident.user, cli_slots=CLI_SLOTS)",
        "        ], requester=None, cli_slots=CLI_SLOTS)",
        CHIP,
    ),
    (
        "the module's CLI_SLOTS opts nothing in",
        APP,
        'CLI_SLOTS = ("generation",)',
        "CLI_SLOTS = ()",
        CHIP,
    ),
    (
        "the Embedding Lab's draft is opted in although it names nobody",
        APP,
        'CLI_SLOTS = ("generation",)',
        'CLI_SLOTS = ("generation", "describe")',
        CHIP,
    ),
    # --- the wait ------------------------------------------------------------------------------
    (
        "the stream stops waiting the rail's broker timeout",
        BROKER,
        "        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:",
        "        async with httpx.AsyncClient(timeout=120.0) as client:",
        "test_a_named_call_waits_the_whole_broker_timeout",
    ),
    (
        "the buffered call stops waiting the rail's broker timeout",
        BROKER,
        "def _post(path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:",
        "def _post(path: str, payload: dict, timeout: float = 120.0) -> dict:",
        "test_a_named_call_waits_the_whole_broker_timeout",
    ),
    (
        "the code default falls under the CLI timeout plus a local answer",
        BROKER,
        '"AI_PLAYGROUND_BROKER_TIMEOUT", "600"',
        '"AI_PLAYGROUND_BROKER_TIMEOUT", "240"',
        "test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer",
    ),
    # --- the frontend's line -------------------------------------------------------------------
    (
        "the demo never says Claude answered",
        SERVED,
        "  if (frame.backend === 'claude') {",
        "  if (frame.backend === 'anthropic') {",
        "test_served_by_passes_under_node",
    ),
    # --- controls: no behaviour changes, so each must SURVIVE ----------------------------------
    (
        "CONTROL: the heartbeat's default read the other way round",
        APP,
        "    every = HEARTBEAT_S if heartbeat is None else heartbeat\n",
        "    every = heartbeat if heartbeat is not None else HEARTBEAT_S\n",
        None,
    ),
    (
        "CONTROL: the role guard parenthesised",
        BROKER,
        "    return requester if requester and ref == DEFAULT_MODEL else None",
        "    return requester if (requester and ref == DEFAULT_MODEL) else None",
        None,
    ),
]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env["PYTHONIOENCODING"] = "utf-8"
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("PLATFORM_STANDALONE", "AI_PLAYGROUND_BROKER_TIMEOUT",
                 "AI_PLAYGROUND_CHAT_MODEL"):
        env.pop(name, None)
    return env


def witness() -> str | None:
    """Prove the suite imports THIS tree's ai_playground, or say what it imports instead."""
    probe = ("import ai_playground.broker as b, ai_playground.api.app as a\n"
             "for x in (b, a): print('WITNESS', x.__file__)")
    run = mr.run_bounded([PY, "-c", probe], cwd=RAIL, env=_env(), timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in run.out.splitlines()
             if ln.startswith("WITNESS ")]
    if run.code != 0 or len(files) != 2:
        return f"import probe failed:\n{run.out}"
    stray = [str(f) for f in files if REPO.resolve() not in f.parents]
    return f"imports resolve outside this tree: {stray}" if stray else None


def run_suite() -> mr.SuiteRun:
    return mr.run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header", "-rfs",
                           "-p", "no:cacheprovider"], cwd=RAIL, env=_env())


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    if (why := witness()) is not None:
        print(f"WITNESS FAILED -- {why}")
        return 2
    base = run_suite()
    if base.code != 0 or "skipped" in base.out.strip().splitlines()[-1]:
        print("BASELINE IS NOT GREEN (or skipped something) -- fix that before trusting any "
              "mutation result.\n")
        print(base.out[-2500:])
        return 2
    print(f"baseline green: {base.out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str, bool]] = []
    for label, path, old, new, expected in MUTATIONS:
        control = expected is None
        original = path.read_bytes()
        n, mutated = mr.mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {path.name}, expected 1")
            results.append((label, "PATTERN", control))
            continue
        try:
            path.write_bytes(mutated)
            run = run_suite()
        finally:
            mr.restore_exact(path, original)
        if control:
            verdict = ("TIMEOUT" if run.timed_out else "SURVIVED" if run.code == 0
                       else "CONTROL-FAILED")
        else:
            named = mr.named_failed(run.out, expected)
            verdict = ("TIMEOUT" if run.timed_out else "FIRED" if run.code != 0 and named
                       else "FIRED-WRONG-TEST" if run.code != 0 else "SURVIVED")
        results.append((label, verdict, control))
        print(f"  {verdict:17} {label}")
        if verdict not in ("FIRED", "SURVIVED") or (verdict == "SURVIVED" and not control):
            tail = [ln for ln in run.out.splitlines() if ln.strip()][-12:]
            print("      " + "\n      ".join(tail))

    after = run_suite()
    print(f"\nrestored tree: {'green' if after.code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    muts = [r for r in results if not r[2]]
    ctrls = [r for r in results if r[2]]
    bad = [r for r in muts if r[1] != "FIRED"] + [r for r in ctrls if r[1] != "SURVIVED"]
    print(f"{sum(r[1] == 'FIRED' for r in muts)}/{len(muts)} mutations fired the right test; "
          f"{sum(r[1] == 'SURVIVED' for r in ctrls)}/{len(ctrls)} controls survived")
    for label, verdict, _ in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and after.code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
