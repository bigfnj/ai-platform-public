"""Mutation harness for OPENMAIC_CLI_REQUESTER and the shim's stream. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\openmaic\\tests\\mutate_cli_requester.py [label-substring ...]

Run this, not just pytest, before trusting a change to who the LLM shim names
(api/llm.py `_shim_requester` and its published-token guard, `_requester_for` and the `Caller`
`llm_caller` hands it, `chip_requester`), the proxy's ledger behind it (api/proxy.py
`note_user`, `sole_user`), broker.py's own-role guard, the shim's heartbeat and its handling of
the broker's CLI frames, or the broker wait. Who may feed that ledger at all (the gateway's
address only) is mutate_peer_guard.py's.

Every one of these fails SILENTLY. A name read from the request lets anything holding the shim
token spend the owner's Claude subscription; a ledger that ignores a second user attributes
their generation to the owner, and so does a shim that ignores who called it directly; a guard
that is gone sends an app-chosen model's calls past the reasoning slot's switch; a reader that
lets an interruption through finishes half a slide with `finish_reason: "stop"`. None of them
shows in a status code.

Each case names the test that must fail. CONTROLS are rewrites that change nothing the suite
can see, and must SURVIVE: a harness whose controls fire is measuring its own noise. Its sibling
for the rails that name a viewer is rails/terminal-fun/backend/tests/mutate_cli_requester.py.
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
RAIL = REPO / "rails" / "openmaic"
RAIL_SRC = RAIL / "backend"
PY = sys.executable
SUITE = ["tests/test_cli_requester.py", "tests/test_no_requester.py"]
RUN_TIMEOUT = 300

L = "rails/openmaic/backend/openmaic_app/api/llm.py"
A = "rails/openmaic/backend/openmaic_app/api/app.py"
P = "rails/openmaic/backend/openmaic_app/api/proxy.py"
B = "rails/openmaic/backend/openmaic_app/broker.py"
C = "rails/openmaic/backend/openmaic_app/config.py"
DC = "deploy/docker-compose.yml"

# (label, file, old, new, expected test)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- who the shim names --------------------------------------------------------------
    (
        "the shim names the configured account without asking whether it is alone",
        L,
        "    return name if sole_user(name, settings.cli_lease_s) else None",
        "    return name",
        "test_another_user_in_the_lease_names_nobody",
    ),
    # --- the published shim token ----------------------------------------------------------
    (
        "the published token is not checked, so any peer's call rides the owner's name",
        L,
        "    if _SHIM_TOKEN == PUBLISHED_SHIM_TOKEN:\n        _note_published_token()\n"
        "        return None\n",
        "",
        "test_with_the_published_token_the_shim_names_nobody",
    ),
    (
        "the guard watches for a token compose does not ship",
        L,
        'PUBLISHED_SHIM_TOKEN = "openmaic-local-dev"',
        'PUBLISHED_SHIM_TOKEN = "openmaic-dev"',
        "test_the_published_token_is_the_one_compose_falls_back_to",
    ),
    (
        "the reason is logged on every call and every chip poll",
        L,
        "    if _published_token_noted:\n        return\n",
        "",
        "test_the_published_token_is_named_in_the_log_once",
    ),
    (
        "a box that never set OPENMAIC_CLI_REQUESTER is told about the token anyway",
        L,
        "    if not name:\n        return None\n    if _SHIM_TOKEN == PUBLISHED_SHIM_TOKEN:",
        "    if not name and _SHIM_TOKEN != PUBLISHED_SHIM_TOKEN:\n        return None\n"
        "    if _SHIM_TOKEN == PUBLISHED_SHIM_TOKEN:",
        "test_unset_the_published_token_is_not_mentioned",
    ),
    (
        "the shim names whoever the X-Platform-User header says",
        L,
        "    requester = _requester_for(caller)",
        '    requester = request.headers.get("x-platform-user") or _requester_for(caller)',
        "test_another_gateway_user_calling_the_shim_directly_names_nobody",
    ),
    (
        "the shim names whoever the OpenAI `user` field says",
        L,
        "    requester = _requester_for(caller)",
        '    requester = body.get("user") or _requester_for(caller)',
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    # --- whose call it is (_requester_for) -------------------------------------------------
    (
        "the shim ignores who called, so another user's direct call rides the owner's name",
        L,
        "    requester = _requester_for(caller)",
        "    requester = _shim_requester()",
        "test_another_gateway_user_calling_the_shim_directly_names_nobody",
    ),
    (
        "any platform user's direct call carries the owner's name",
        L,
        '    if caller.kind == "user" and caller.user == name:',
        '    if caller.kind == "user":',
        "test_another_gateway_user_calling_the_shim_directly_names_nobody",
    ),
    (
        "the owner's own direct call never carries the name",
        L,
        '    if caller.kind == "user" and caller.user == name:\n        return name\n',
        "",
        "test_the_configured_account_calling_the_shim_directly_is_named",
    ),
    (
        "the app's call never carries the name",
        L,
        '    if caller.kind == "app":\n        return name\n',
        "",
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    (
        "the token wins over a platform identity, so a gateway user with it reads as the app",
        L,
        "    if x_platform_user and x_platform_user.strip():\n"
        '        return Caller("user", x_platform_user.strip())\n'
        "    supplied = _bearer(authorization)\n"
        "    if _SHIM_TOKEN and supplied and secrets.compare_digest(supplied, _SHIM_TOKEN):\n"
        "        return APP_CALLER\n",
        "    supplied = _bearer(authorization)\n"
        "    if _SHIM_TOKEN and supplied and secrets.compare_digest(supplied, _SHIM_TOKEN):\n"
        "        return APP_CALLER\n"
        "    if x_platform_user and x_platform_user.strip():\n"
        '        return Caller("user", x_platform_user.strip())\n',
        "test_another_gateway_user_calling_the_shim_directly_names_nobody",
    ),
    (
        "the buffered completion drops the name",
        L,
        "\n                                     keep_alive=keep_alive, requester=requester)",
        "\n                                     keep_alive=keep_alive)",
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    (
        "the streamed completion drops the name",
        L,
        "             want_usage=want_usage, requester=requester),",
        "             want_usage=want_usage),",
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    (
        "the stream's broker call drops the name",
        L,
        "think=think,\n                                keep_alive=keep_alive, requester=requester)",
        "think=think,\n                                keep_alive=keep_alive)",
        "test_the_stream_passes_the_shims_name_to_the_broker",
    ),
    # --- the ledger ----------------------------------------------------------------------
    (
        "a visit through the proxy is not recorded",
        P,
        "    note_user(ident.user)\n    headers = upstream_headers(request, ident.user)",
        "    headers = upstream_headers(request, ident.user)",
        "test_a_visit_through_the_proxy_is_what_lets_the_name_on",
    ),
    (
        "sole_user asks only whether the account was seen, not whether anyone else was",
        P,
        "    return recent == {who}",
        "    return who in recent",
        "test_another_user_in_the_lease_names_nobody",
    ),
    (
        "the lease is ignored: a visit counts for ever",
        P,
        "        recent = {user for user, at in _seen.items() if now - at <= within}",
        "        recent = set(_seen)",
        "test_the_lease_runs_out_both_ways",
    ),
    # --- the broker client ---------------------------------------------------------------
    (
        "the own-role guard is removed: an app-chosen model rides the name",
        B,
        "    return requester if requester and requester.strip() and model == settings.llm_model"
        " else None",
        "    return requester",
        "test_a_model_off_the_reasoning_role_names_nobody",
    ),
    (
        "the buffered call never puts requester on the wire",
        B,
        '    if who:\n        payload["requester"] = who\n    return await _apost("/v1/chat", payload)',
        '    return await _apost("/v1/chat", payload)',
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    (
        "the streamed call never puts requester on the wire",
        B,
        '    if who:\n        payload["requester"] = who\n    try:\n',
        "    try:\n",
        "test_set_and_alone_every_app_call_names_exactly_the_configured_account",
    ),
    (
        "the buffered call sends an absent requester as null",
        B,
        '    if who:\n        payload["requester"] = who\n    return await _apost("/v1/chat", payload)',
        '    payload["requester"] = who\n    return await _apost("/v1/chat", payload)',
        "test_a_buffered_completion_names_no_requester",
    ),
    # --- the stream ----------------------------------------------------------------------
    (
        "no heartbeat goes out while the broker is silent",
        L,
        "                if frame is None:\n                    yield PING\n                    continue",
        "                if frame is None:\n                    continue",
        "test_a_silent_broker_gets_a_heartbeat_and_the_text_is_unchanged",
    ),
    (
        "the broker stream is read without the pacer",
        L,
        "    paced = _paced(stream, HEARTBEAT_S)",
        "    paced = stream",
        "test_a_silent_broker_gets_a_heartbeat_and_the_text_is_unchanged",
    ),
    (
        "a read in flight is left running when the client leaves",
        L,
        "        if pending is not None:",
        "        if False:",
        "test_a_client_that_leaves_mid_wait_ends_the_broker_read",
    ),
    (
        "the wait for the cancelled read is not shielded from anyio's re-delivered cancellation",
        L,
        "            with anyio.CancelScope(shield=True):",
        "            with anyio.CancelScope():",
        "test_an_anyio_cancellation_mid_wait_still_ends_the_broker_read",
    ),
    (
        "a cli_interrupted ending is let through as a finished answer",
        L,
        '                if frame.get("error"):',
        '                if frame.get("error") and frame.get("done_reason") != "cli_interrupted":',
        "test_a_cli_interruption_is_an_sse_error_not_a_finished_answer",
    ),
    (
        "a content-less frame (the cli lead) is treated as an error",
        L,
        '                if frame.get("error"):',
        '                if frame.get("error") or "message" not in frame:',
        "test_the_brokers_cli_frames_are_skipped",
    ),
    # --- the chip ------------------------------------------------------------------------
    (
        "the module's CLI_SLOTS opts nothing in",
        A,
        'CLI_SLOTS = ("reasoning",)',
        "CLI_SLOTS = ()",
        "test_the_configured_account_alone_sees_the_reasoning_slot_on_claude",
    ),
    (
        "the chip is judged for the viewer, whom the shim never names",
        A,
        "                                    requester=llm.chip_requester(_ident.user),",
        "                                    requester=_ident.user,",
        "test_another_viewer_sees_ollama_even_as_a_broker_owner",
    ),
    (
        "chip_requester hands back any viewer",
        L,
        '    return name if name and (viewer or "").strip() == name else None',
        "    return viewer",
        "test_another_viewer_sees_ollama_even_as_a_broker_owner",
    ),
    # --- the wait ------------------------------------------------------------------------
    (
        "the broker wait goes back under a CLI call plus its local fallback",
        C,
        "    broker_timeout: float = 600.0",
        "    broker_timeout: float = 300.0",
        "test_the_broker_wait_outlasts_a_cli_call_and_its_local_fallback",
    ),
    (
        "the proxy's wait on the app goes back to the CLI timeout",
        C,
        "    app_timeout: float = 1320.0",
        "    app_timeout: float = 300.0",
        "test_the_app_wait_outlasts_a_cli_call_and_its_local_fallback",
    ),
    (
        "the proxy's client ignores the app timeout",
        P,
        "            timeout=httpx.Timeout(settings.app_timeout, connect=10.0),",
        "            timeout=httpx.Timeout(300.0, connect=10.0),",
        "test_the_app_wait_outlasts_a_cli_call_and_its_local_fallback",
    ),
    (
        "compose passes the old app wait",
        DC,
        "      OPENMAIC_APP_TIMEOUT: ${OPENMAIC_APP_TIMEOUT:-1320}\n",
        "      OPENMAIC_APP_TIMEOUT: ${OPENMAIC_APP_TIMEOUT:-300}\n",
        "test_the_app_wait_outlasts_a_cli_call_and_its_local_fallback",
    ),
    (
        "compose stops passing the setting, so deploy/.env never reaches the rail",
        DC,
        "      OPENMAIC_CLI_REQUESTER: ${OPENMAIC_CLI_REQUESTER:-}\n",
        "",
        "test_compose_hands_the_setting_to_the_rail",
    ),
]

#: Rewrites the suite cannot see. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "the heartbeat line spelled another way, same bytes",
        L,
        'PING = ": ping\\n\\n"',
        'PING = ":" + " ping\\n\\n"',
    ),
    (
        "sole_user's set test spelled another way, same truth table",
        P,
        "    return recent == {who}",
        "    return len(recent) == 1 and who in recent",
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = str(RAIL_SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "OPENMAIC_CLI_REQUESTER", "OPENMAIC_CLI_LEASE_S",
                "OPENMAIC_BROKER_TIMEOUT", "OPENMAIC_APP_TIMEOUT", "OPENMAIC_LLM_MODEL"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import openmaic_app.api.llm as l, openmaic_app.broker as b;"
         "print(l.__file__); print(b.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header", "-rfE",
                         "-p", "no:cacheprovider", *extra],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _apply(rel: str, old: str, new: str, label: str):
    """(path, original bytes, mutated bytes), or None after reporting a PATTERN."""
    path = REPO / rel
    original = path.read_bytes()
    n, mutated = mr.mutate_bytes(original, old, new)
    if n != 1:
        print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
        return None
    return path, original, mutated


def main() -> int:
    only = mr.parse_harness_args(MUTATIONS, CONTROLS, filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    controls = [c for c in CONTROLS if not only or any(o in c[0] for o in only)]
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from pytest's summary of failures and
    # errors can mean anything.
    _, collected = run_suite(("--collect-only",))
    missing = sorted({m[4] for m in chosen if m[4] not in collected})
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, rel, old, new, expected in chosen:
        applied = _apply(rel, old, new, label)
        if applied is None:
            results.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            fired = code not in (0, -1)
            named = mr.named_failed(out, expected)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and named else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    survived: list[tuple[str, str]] = []
    for label, rel, old, new in controls:
        applied = _apply(rel, old, new, label)
        if applied is None:
            survived.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            verdict = "TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED"
            survived.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in survived if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(survived) - len(bad_controls)}/{len(survived)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
