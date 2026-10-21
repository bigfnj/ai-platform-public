"""Mutation harness for `requester`, the Claude CLI frames and the wait in gemini-cx.
NOT a pytest file: it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\gemini-cx\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`, either facade
call's wire, `/api/ask` or `/ws/ask` (and what its heartbeat wrapper does when the client leaves,
tests/test_stream_leave.py), the chips on `/api/capabilities` and `/api/health`, or the broker
wait.

The role guard fails OPEN and SILENTLY (another role gets the viewer's name), a dropped
requester makes the CLI silently inert (which looks healthy), and a read left running after the
client went keeps a CLI slot busy for nobody: none of them shows on its own. Its
siblings in other rails are rails/ai-playground/tests/mutate_cli_requester.py and
rails/smb-partner-enablement/tests/mutate_cli_requester.py.

A verdict counts only with the expected test named on a FAILED line. The baseline must be green
with nothing skipped, the witness must show the suite imports THIS tree's gemini_cx, and every
CONTROL (an edit that changes no behaviour) must SURVIVE, or the harness is reporting the
suite's noise rather than the mutations.
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

RAIL = Path(__file__).resolve().parents[1]               # rails/gemini-cx
REPO = RAIL.parents[1]
PY = sys.executable
PYTHONPATH = [str(RAIL / "src")]
SUITE = [str(RAIL / "tests" / n) for n in ("test_cli_requester.py", "test_broker_stream.py",
                                           "test_stream_leave.py")]

PKG = RAIL / "src" / "gemini_cx"
BROKER = PKG / "broker.py"
API = PKG / "api.py"

CHIP = "test_an_owner_sees_reasoning_on_claude_and_nothing_else"
OPTS = '"num_predict": config.MAX_TOKENS, "temperature": 0.2}'

# (label, file, old, new, the test that must fail; None for a CONTROL, which must SURVIVE)
MUTATIONS: list[tuple[str, Path, str, str, str | None]] = [
    # --- the facade --------------------------------------------------------------------------
    (
        "any role rides the viewer's name to the CLI (the role guard is gone)",
        BROKER,
        "    return requester if requester and model == config.RAG_MODEL else None",
        "    return requester",
        "test_a_foreign_role_never_carries_the_name",
    ),
    (
        "the guard pins the default role, so a repointed answer slot loses the CLI",
        BROKER,
        "    return requester if requester and model == config.RAG_MODEL else None",
        '    return requester if requester and model == "@gemini-cx-rag" else None',
        "test_the_guard_follows_the_configured_answer_role",
    ),
    (
        "the buffered call never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who\n    resp = _post(',
        '    if False:\n        payload["requester"] = who\n    resp = _post(',
        "test_the_answer_role_carries_the_requester",
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
        "test_the_answer_role_carries_the_requester",
    ),
    (
        "a CLI interruption is read as a finished answer",
        BROKER,
        '                    if frame.get("error"):\n',
        '                    if frame.get("error") and frame.get("done_reason") != '
        '"cli_interrupted":\n',
        "test_a_cli_interruption_raises_after_the_partial_tokens",
    ),
    # --- the routes ----------------------------------------------------------------------------
    (
        "/api/ask drops the viewer",
        API,
        f"                options={{{OPTS},\n                requester=who.user)",
        f"                options={{{OPTS})",
        "test_the_buffered_ask_names_the_viewer",
    ),
    (
        "/ws/ask drops the viewer",
        API,
        f"                            options={{{OPTS},\n"
        "                            requester=user or None))) as toks:",
        f"                            options={{{OPTS}))) as toks:",
        "test_the_streamed_ask_names_the_viewer",
    ),
    # --- a client that leaves (tests/test_stream_leave.py) -------------------------------------
    (
        "the /ws/ask wrapper leaves the read in flight running when a ping raises",
        API,
        "        if pending is not None:\n            pending.cancel()\n",
        "        if False:\n            pending.cancel()\n",
        "test_a_ping_to_a_client_that_left_ends_the_broker_read",
    ),
    (
        "the /ws/ask wrapper never closes the broker stream",
        API,
        "        if aclose is not None:\n            await aclose()",
        "        if False:\n            await aclose()",
        "test_a_caller_that_stops_reading_closes_the_stream",
    ),
    (
        "/ws/ask reads the wrapper without aclosing, so a failed token send leaves it open",
        API,
        # nullcontext hands the generator through unclosed: the loop as it was before.
        "from contextlib import aclosing, asynccontextmanager\n",
        "from contextlib import asynccontextmanager, nullcontext as aclosing\n",
        "test_ws_ask_a_client_gone_mid_answer_ends_the_read_and_logs_no_traceback",
    ),
    (
        "/ws/ask logs a client leaving mid-answer as a failure, with a traceback",
        API,
        "                except WebSocketDisconnect:\n"
        "                    # The client left mid-answer",
        "                except ZeroDivisionError:\n"
        "                    # The client left mid-answer",
        "test_ws_ask_a_client_gone_mid_answer_ends_the_read_and_logs_no_traceback",
    ),
    # --- the chips -----------------------------------------------------------------------------
    (
        "the chips stop opting reasoning in, so an owner is told it runs locally",
        API,
        "    return modelstate.resolve(MODEL_SLOTS, requester=viewer, cli_slots=CLI_SLOTS)",
        "    return modelstate.resolve(MODEL_SLOTS)",
        CHIP,
    ),
    (
        "/api/capabilities judges nobody, so an owner is told it runs locally",
        API,
        "        models = _models(who.user)",
        "        models = _models()",
        CHIP,
    ),
    (
        "/api/health judges nobody, so its chips disagree with /api/capabilities",
        API,
        '            "models": _models(who.user),',
        '            "models": _models(),',
        "test_the_health_chips_judge_the_same_viewer",
    ),
    (
        "the module's CLI_SLOTS opts nothing in",
        API,
        'CLI_SLOTS = ("reasoning",)',
        "CLI_SLOTS = ()",
        CHIP,
    ),
    (
        "the retrieval chip is opted in although embeddings never reach the CLI",
        API,
        'CLI_SLOTS = ("reasoning",)',
        'CLI_SLOTS = ("reasoning", "retrieval")',
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
        '"GEMINI_CX_BROKER_TIMEOUT", "600"',
        '"GEMINI_CX_BROKER_TIMEOUT", "240"',
        "test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer",
    ),
    # --- controls: no behaviour changes, so each must SURVIVE ----------------------------------
    (
        "CONTROL: the chip helper's annotation quoted",
        API,
        "def _models(viewer: str | None = None) -> dict[str, Any]:",
        'def _models(viewer: "str | None" = None) -> dict[str, Any]:',
        None,
    ),
    (
        "CONTROL: the role guard parenthesised",
        BROKER,
        "    return requester if requester and model == config.RAG_MODEL else None",
        "    return requester if (requester and model == config.RAG_MODEL) else None",
        None,
    ),
]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env["PYTHONIOENCODING"] = "utf-8"
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("PLATFORM_STANDALONE", "GEMINI_CX_BROKER_TIMEOUT", "GEMINI_CX_RAG_MODEL"):
        env.pop(name, None)
    return env


def witness() -> str | None:
    """Prove the suite imports THIS tree's gemini_cx, or say what it imports instead."""
    probe = ("import gemini_cx.broker as b, gemini_cx.api as a\n"
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
