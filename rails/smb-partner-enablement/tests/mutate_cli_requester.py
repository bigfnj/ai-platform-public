"""Mutation harness for `requester`, the Claude CLI frames and the wait in
smb-partner-enablement. NOT a pytest file: it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\smb-partner-enablement\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`, either facade
call's wire, the three answer routes (the buffered scenario route stays retired),
`generate._pass`'s prefill rule, `/ws/scenario`'s heartbeat (`generate._with_heartbeat`), what
either heartbeat wrapper does when the client leaves (tests/test_stream_leave.py), the
capabilities chip or the broker wait.

The role guard fails OPEN and SILENTLY (another role gets the viewer's name), a dropped
requester makes the CLI silently inert (which looks healthy), a prefilled pass sent nameless
keeps most of the builder local while the chip says Claude, a missing heartbeat looks like
a flaky network, and a read left running after the client went keeps a CLI slot busy for
nobody: none of them shows on its own. Its siblings in other rails are
rails/ai-playground/tests/mutate_cli_requester.py and rails/gemini-cx/tests/mutate_cli_requester.py.

A verdict counts only with the expected test named on a FAILED line. The baseline must be green
with nothing skipped, the witness must show the suite imports THIS tree's smb_partner, and every
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

RAIL = Path(__file__).resolve().parents[1]               # rails/smb-partner-enablement
REPO = RAIL.parents[1]
PY = sys.executable
PYTHONPATH = [str(RAIL / "src")]
SUITE = [str(RAIL / "tests" / n) for n in ("test_cli_requester.py", "test_broker_stream.py",
                                           "test_stream_leave.py")]

PKG = RAIL / "src" / "smb_partner"
BROKER = PKG / "broker.py"
API = PKG / "api.py"
GEN = PKG / "generate.py"

CHIP = "test_an_owner_sees_reasoning_on_claude_and_nothing_else"
PACKAGE = "test_a_package_names_the_viewer_on_every_pass_prefilled_or_not"
CUSTOMER = "test_a_customer_package_is_all_prefilled_and_names_the_viewer_on_every_pass"

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
        '    return requester if requester and model == "@smb-partner-rag" else None',
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
        '                options={"num_predict": budget},\n                requester=who.user,\n',
        '                options={"num_predict": budget},\n',
        "test_the_buffered_ask_names_the_viewer",
    ),
    (
        "/ws/ask drops the viewer",
        API,
        "                        requester=user or None,\n",
        "",
        "test_the_streamed_ask_names_the_viewer",
    ),
    (
        # Retired 2026-10-14: its own case was "/api/scenario/generate drops the viewer".
        "the buffered scenario route is back, four passes behind the gateway's read timeout",
        API,
        '    @app.websocket("/ws/scenario")\n',
        '    @app.post("/api/scenario/generate")\n'
        "    async def scenario_generate(body: ScenarioBody,\n"
        "                                who: Identity = Depends(identity)) -> dict[str, Any]:\n"
        "        return await generate.generate_package(body.scenario_id, body.answers,\n"
        "                                               requester=who.user)\n"
        "\n"
        '    @app.websocket("/ws/scenario")\n',
        "test_the_buffered_scenario_route_is_retired",
    ),
    (
        "/ws/scenario drops the viewer",
        API,
        "body.scenario_id, body.answers, emit, requester=user or None)",
        "body.scenario_id, body.answers, emit)",
        "test_the_scenario_socket_names_the_viewer",
    ),
    # --- the package ---------------------------------------------------------------------------
    (
        # The converse was the case until 2026-10-14, when the broker began emulating a prefill
        # on the CLI path: a prefilled pass sent named then cost a CLI wait for a refusal.
        "a prefilled pass drops the name again, so most of the builder never reaches Claude",
        GEN,
        "        requester=requester)",
        "        requester=requester if not prefill else None)",
        CUSTOMER,
    ),
    (
        "a pass never names anyone",
        GEN,
        "        requester=requester)",
        "        requester=None)",
        PACKAGE,
    ),
    (
        "the package stops handing the name to its passes",
        GEN,
        'pass_set=scenario.get("pass_set", "customer"), requester=requester)',
        'pass_set=scenario.get("pass_set", "customer"))',
        PACKAGE,
    ),
    (
        "/ws/scenario goes silent while a pass waits (no heartbeat)",
        GEN,
        "    async with aclosing(_with_heartbeat(stream, emit) if emit else stream) as deltas:\n",
        "    async with aclosing(stream) as deltas:\n",
        "test_a_silent_pass_keeps_the_scenario_socket_alive_with_pings",
    ),
    (
        "a package built with no socket is pinged too",
        GEN,
        "    async with aclosing(_with_heartbeat(stream, emit) if emit else stream) as deltas:\n",
        "    async with aclosing(_with_heartbeat(stream, emit)) as deltas:\n",
        "test_a_package_built_with_no_socket_sends_no_ping",
    ),
    (
        "the heartbeat beats slower than the edge cuts an idle socket",
        GEN,
        "HEARTBEAT_S = 20.0\n",
        "HEARTBEAT_S = 120.0\n",
        "test_a_build_longer_than_the_gateways_read_timeout_still_delivers_its_package",
    ),
    (
        "a pass pings once and then goes quiet, so a long silence reaches the edge's idle cut",
        GEN,
        "                await emit(\"ping\", {})\n",
        "                await emit(\"ping\", {})\n"
        "                await asyncio.wait({nxt})\n",
        "test_a_build_longer_than_the_gateways_read_timeout_still_delivers_its_package",
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
        # The pending-task check itself (tools/tests/loop_tasks.py, 2026-10-19): with the broker
        # stream closed as before, a task the wrapper leaves running still fails the test.
        "the /ws/ask wrapper leaves a task of its own running once the client has gone",
        API,
        "        await _end_read(nxt, it)\n",
        "        await _end_read(nxt, it)\n        asyncio.ensure_future(asyncio.sleep(60))\n",
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
        "the /ws/ask wrapper's cleanup is not a finally, so only a finished stream is ended",
        API,
        "            yield tok\n    finally:\n        await _end_read(nxt, it)\n",
        "            yield tok\n    except BaseException:\n        raise\n",
        "test_a_ping_to_a_client_that_left_ends_the_broker_read",
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
    (
        "the scenario wrapper leaves the read in flight running when a ping raises",
        GEN,
        "            if nxt is not None:\n                nxt.cancel()\n",
        "            if False:\n                nxt.cancel()\n",
        "test_a_scenario_ping_to_a_client_that_left_ends_the_broker_read",
    ),
    (
        "a pass reads its stream without aclosing, so a failed token emit leaves it open",
        GEN,
        "from contextlib import aclosing\n",
        "from contextlib import nullcontext as aclosing\n",
        "test_ws_scenario_a_client_gone_mid_pass_ends_the_read_and_the_build",
    ),
    (
        "/ws/scenario logs a client leaving mid-build as a failure, with a traceback",
        API,
        "                except WebSocketDisconnect:\n"
        "                    # The client left mid-build",
        "                except ZeroDivisionError:\n"
        "                    # The client left mid-build",
        "test_ws_scenario_a_client_gone_mid_pass_ends_the_read_and_the_build",
    ),
    # --- the chip ------------------------------------------------------------------------------
    (
        "the chip stops opting reasoning in, so an owner is told it runs locally",
        API,
        "            out = modelstate.resolve(MODEL_SLOTS, requester=who.user, cli_slots=CLI_SLOTS)",
        "            out = modelstate.resolve(MODEL_SLOTS)",
        CHIP,
    ),
    (
        "the chip judges a different identity from the one the calls carry",
        API,
        "            out = modelstate.resolve(MODEL_SLOTS, requester=who.user, cli_slots=CLI_SLOTS)",
        "            out = modelstate.resolve(MODEL_SLOTS, requester=None, cli_slots=CLI_SLOTS)",
        CHIP,
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
        '"SMB_PARTNER_BROKER_TIMEOUT", "600"',
        '"SMB_PARTNER_BROKER_TIMEOUT", "240"',
        "test_the_code_default_outlasts_the_cli_timeout_plus_a_local_answer",
    ),
    # --- controls: no behaviour changes, so each must SURVIVE ----------------------------------
    (
        "CONTROL: the pass heartbeat's hand-off of the finished read spelled in two lines",
        GEN,
        "            read, nxt = nxt, None\n",
        "            read = nxt\n            nxt = None\n",
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
    for name in ("PLATFORM_STANDALONE", "SMB_PARTNER_BROKER_TIMEOUT", "SMB_PARTNER_RAG_MODEL"):
        env.pop(name, None)
    return env


def witness() -> str | None:
    """Prove the suite imports THIS tree's smb_partner, or say what it imports instead."""
    probe = ("import smb_partner.broker as b, smb_partner.api as a, smb_partner.generate as g\n"
             "for x in (b, a, g): print('WITNESS', x.__file__)")
    run = mr.run_bounded([PY, "-c", probe], cwd=RAIL, env=_env(), timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in run.out.splitlines()
             if ln.startswith("WITNESS ")]
    if run.code != 0 or len(files) != 3:
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
