"""Mutation harness for named broker tokens. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_broker_tokens.py

This is an authentication control, so the only thing that matters is whether it can FAIL OPEN.
Every mutation below removes one guard and requires a named test to notice. Four of them are
the shapes that would be invisible in normal use:

  * a revoked token still working (the whole point of the feature)
  * an `inference` token reaching a mutating route (the scope being decorative)
  * revoking the LAST token reopening the broker to everyone (the footgun the store-exists
    check closes, found by writing the test rather than by reading the code)
  * the plaintext being persisted (which would make the hash pointless)

The last one is mutated against the STORE rather than the auth path, because a token list you
can read off disk is not protected by any amount of correct comparison.

Since 2026-10-14 it also covers the gate in front of routing (app/main.py `_TokenAndBodyGate`):
the token checked before a body is read, the per-route body ceilings, and the non-ASCII bearer
that was a 500.
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
REPO = BROKER.parents[1]
# The interpreter running THIS harness, not a hardcoded REPO/.venv. A git worktree has no .venv,
# so the harness died there with FileNotFoundError before mutating anything; and the venv's
# editable installs point at ONE checkout, so the child run is also handed this tree's own
# source roots (_TREE_ENV) -- otherwise it mutates one tree and tests another.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
SUITE = ["tests/test_auth_token.py", "tests/test_auth_token_file.py", "tests/test_tokens_store.py",
         "tests/test_token_mint_lockout.py", "tests/test_auth_gate.py"]

MAIN = "app/main.py"
CFG = "app/config.py"
GATE_401 = "test_an_unauthenticated_200_mb_chat_is_a_401_before_a_byte_is_read"

# Re-anchored 2026-10-14: the decision moved out of require_token into `_authorise`, which the
# gate in front of routing (_TokenAndBodyGate) shares, so the auth-path cases mutate it there.
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a revoked token is still accepted",
        MAIN,
        "    match = next((r for r in named if _same(str(r.get(\"hash\", \"\")), digest)), None)\n"
        "    if match is None:\n"
        "        raise _Refused(401, _NO_TOKEN)",
        "    match = next((r for r in named if _same(str(r.get(\"hash\", \"\")), digest)), None)\n"
        "    if match is None:\n"
        '        return "full", "revoked", None',
        "test_a_revoked_token_is_refused",
    ),
    (
        "revoking the last token reopens the broker to everyone",
        MAIN,
        "    if not env_token and not settings.tokens_configured():",
        "    if not env_token and not settings.tokens():",
        "test_an_EMPTY_store_still_closes_the_broker",
    ),
    (
        "the scope check is removed, so an inference token can repoint a role",
        MAIN,
        '    if _needs_full(method, path) and match.get("scope") != "full":',
        "    if False:",
        "test_inference_scope_cannot_repoint_a_role",
    ),
    (
        "the mutating-route list loses /v1/tokens, so a token can mint tokens",
        MAIN,
        '    ("GET", "/v1/tokens"),\n    ("POST", "/v1/tokens"),\n    ("DELETE", "/v1/tokens"),',
        '    ("GET", "/v1/zzz"),',
        "test_inference_scope_cannot_read_or_mint_tokens",
    ),
    (
        "the mutating-route list loses /v1/cli, so a workstation token can switch on the CLI",
        MAIN,
        '    ("PUT", "/v1/cli"),\n',
        "",
        "test_inference_scope_cannot_switch_a_role_onto_the_cli",
    ),
    (
        "the hash compare is replaced by a prefix match",
        MAIN,
        '    match = next((r for r in named if _same(str(r.get("hash", "")), digest)), None)',
        '    match = next((r for r in named if str(r.get("prefix", "")).startswith(supplied)),'
        " None)",
        "test_a_token_that_is_a_prefix_of_a_live_one_is_refused",
    ),
    (
        "last-seen is never stamped, so the revoke workflow cannot tell what is in use",
        MAIN,
        '    request.app.state.token_seen[match["id"]] = time.time()',
        "    pass",
        "test_a_named_token_stamps_last_seen",
    ),
    (
        "the plaintext is persisted alongside the hash",
        CFG,
        '            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(),',
        '            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(),\n'
        '            "token": plain,',
        "test_the_plaintext_is_never_written_to_disk",
    ),
    (
        "a duplicate label is allowed, so two rows are indistinguishable when revoking",
        CFG,
        '        if any(r.get("label") == label for r in rows):',
        "        if False:",
        "test_a_duplicate_label_is_refused",
    ),
    (
        "the first mint on an open broker is allowed again, locking every client out",
        MAIN,
        "    if not settings.auth_token and not settings.tokens_configured():\n"
        "        raise HTTPException(status_code=409, detail=(",
        "    if False:\n"
        "        raise HTTPException(status_code=409, detail=(",
        "test_the_first_mint_on_an_open_broker_is_refused_and_writes_nothing",
    ),
    (
        "the mint guard ignores the shared secret, so no box can ever mint a token",
        MAIN,
        "    if not settings.auth_token and not settings.tokens_configured():\n",
        "    if not settings.tokens_configured():\n",
        "test_with_the_shared_secret_set_minting_works_and_the_secret_still_does",
    ),
    (
        "the mint guard ignores an existing store, so a named-token box cannot mint more",
        MAIN,
        "    if not settings.auth_token and not settings.tokens_configured():\n",
        "    if not settings.auth_token:\n",
        "test_once_the_store_exists_a_full_token_may_mint_more",
    ),
    (
        "an unknown scope is accepted and stored",
        CFG,
        "        if scope not in TOKEN_SCOPES:",
        "        if False:",
        "test_an_unknown_scope_is_refused",
    ),
    (
        "a token file that is not UTF-8 stops the broker with the raw codec error again",
        CFG,
        "            except ValueError as exc:\n                # A UnicodeDecodeError",
        "            except ZeroDivisionError as exc:\n                # A UnicodeDecodeError",
        "test_a_utf16_file_refuses_to_start_saying_why",
    ),
    (
        # Added 2026-10-14.
        "a no-BOM UTF-16 token file starts the broker on a NUL-laced token again",
        CFG,
        "            if any(ch < \" \" for ch in self.auth_token):\n"
        "                # UTF-16LE with no BOM IS valid UTF-8",
        "            if False:\n"
        "                # UTF-16LE with no BOM IS valid UTF-8",
        "test_a_utf16_file_with_no_bom_refuses_to_start_saying_why",
    ),
    (
        "only a NUL is refused, so any other control character passes into the token",
        CFG,
        "            if any(ch < \" \" for ch in self.auth_token):\n"
        "                # UTF-16LE with no BOM IS valid UTF-8",
        "            if \"\\x00\" in self.auth_token:\n"
        "                # UTF-16LE with no BOM IS valid UTF-8",
        "test_a_token_holding_a_control_character_refuses_to_start",
    ),
    # --- the gate in front of routing: the token, then the body (added 2026-10-14) -----------
    (
        "the gate is not installed, so a body is read whole before its 401 again",
        MAIN,
        "app.add_middleware(_TokenAndBodyGate)\n",
        "",
        f"{GATE_401}[declared-none]",
    ),
    (
        "the gate leaves the token to require_token, so a multipart upload is spooled first",
        MAIN,
        '        if path != "/healthz":\n            try:\n                await run_in_threadpool(',
        "        if False:\n            try:\n                await run_in_threadpool(",
        "test_an_unauthenticated_upload_to_transcriptions_is_a_401_before_it_is_spooled",
    ),
    (
        "the gate judges every request as a GET, so a full-scope route's 403 waits for the body",
        MAIN,
        "                                        request.method, path, request.headers)",
        '                                        "GET", path, request.headers)',
        "test_an_inference_token_on_a_full_scope_route_is_a_403_before_a_byte_is_read",
    ),
    (
        "the gate loses the /healthz exemption",
        MAIN,
        '        if path != "/healthz":\n            try:\n                await run_in_threadpool(',
        "        if True:\n            try:\n                await run_in_threadpool(",
        "test_healthz_needs_no_credential",
    ),
    (
        "a bearer outside ASCII is compared as str again, a TypeError and a 500",
        MAIN,
        '    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))',
        "    return secrets.compare_digest(a, b)",
        "test_a_bearer_outside_ascii_is_a_401_from_require_token_too",
    ),
    (
        "a declared length over the ceiling is read anyway",
        MAIN,
        "        if length is not None and length > ceiling:",
        "        if False:",
        "test_a_declared_body_over_the_ceiling_is_a_413_before_a_byte_is_read",
    ),
    (
        "the counted ceiling refuses a body exactly at it",
        MAIN,
        "                if taken > ceiling:",
        "                if taken >= ceiling:",
        "test_a_body_exactly_at_the_ceiling_is_served_and_one_byte_more_is_not[chunked]",
    ),
    (
        "a body that declares no length is never counted",
        MAIN,
        "                if taken > ceiling:",
        "                if False:",
        "test_a_chunked_body_over_the_ceiling_is_cut_off_one_chunk_later",
    ),
    (
        "the route's answer to a body cut off is sent instead of the 413",
        MAIN,
        "            if over:\n                return          # the route's answer to a body it never got",
        "            if False:\n                return",
        "test_a_chunked_body_over_the_ceiling_is_cut_off_one_chunk_later",
    ),
    (
        "a route that raises on a cut-off body is a 500 instead of the 413",
        MAIN,
        "        except Exception:\n            if not over:\n                raise",
        "        except ZeroDivisionError:\n            if not over:\n                raise",
        "test_a_cut_off_body_to_a_route_that_raises_on_it_is_a_413_not_a_500",
    ),
    (
        "chat gets the 1 MiB default, so a chat carrying photos is refused",
        MAIN,
        '    **dict.fromkeys(("/v1/chat", "/v1/chat/stream",',
        '    **dict.fromkeys(("/v1/chat/stream",',
        "test_an_authenticated_chat_with_a_large_body_reaches_the_route[declared-shared]",
    ),
    (
        "transcribe's room loses the allowance for the rest of its JSON",
        MAIN,
        '    "/v1/transcribe": TRANSCRIBE_MAX_B64 + _BODY_MAX,',
        '    "/v1/transcribe": TRANSCRIBE_MAX_B64,',
        "test_transcribe_takes_its_largest_audio_and_is_cut_off_past_its_room",
    ),
    (
        "the OpenAI surface's 413 loses the envelope its SDKs read",
        MAIN,
        '    if path.startswith("/openai/"):',
        "    if False:",
        "test_a_413_on_the_openai_surface_is_openai_shaped",
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=_TREE_ENV)


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
            fired = code != 0 and named_failed(out, expected)
            # A hang broke SOMETHING, but nothing showed the named test failing, so it is not
            # FIRED. Its own verdict, so it is never mistaken for one.
            verdict = "TIMEOUT" if hung else "FIRED" if fired else (
                "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
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
