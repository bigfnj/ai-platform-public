"""Mutation harness for the unknown-field warning on /v1/chat. NOT a pytest file -- it rewrites
the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_chat_unknown_fields.py

The guard sits between two failures that both look fine from outside. Drop the warning and a
rail's misplaced setting is discarded with a 200, which is exactly how watch-builder's token cap
went nowhere. Tighten it into a refusal and every rail sending a harmless extra breaks. Each
mutation pushes it one way or the other, or makes it noisy or leaky, and names the test in
test_chat_unknown_fields.py that must notice.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
PY = sys.executable
SUITE = ["tests/test_chat_unknown_fields.py"]

M = "app/main.py"
S = "app/schemas.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the buffered route stops warning, so a dropped setting is silent again",
        M,
        '    _warn_unknown_fields("/v1/chat", req)\n',
        "",
        "test_each_dropped_field_is_named_in_a_warning",
    ),
    (
        "the streaming route stops warning",
        M,
        '    _warn_unknown_fields("/v1/chat/stream", req)\n',
        "",
        "test_the_streaming_route_warns_too",
    ),
    (
        "ChatRequest reverts to pydantic's default and drops extras unseen",
        S,
        '    model_config = ConfigDict(extra="allow")',
        '    model_config = ConfigDict(extra="ignore")',
        "test_each_dropped_field_is_named_in_a_warning",
    ),
    (
        "ChatRequest refuses extras, so a rail that works today gets a 422",
        S,
        '    model_config = ConfigDict(extra="allow")',
        '    model_config = ConfigDict(extra="forbid")',
        "test_unknown_top_level_fields_are_still_accepted_not_refused",
    ),
    (
        "the warning logs the values, which can be prompt text or a credential",
        M,
        '    names = ", ".join(repr(k[:40]) for k in extra[:20]) + (" ..." if len(extra) > 20 else "")',
        "    names = repr(req.model_extra)",
        "test_the_warning_names_fields_not_values",
    ),
    (
        "the warning fires on every call, so it is noise nobody reads",
        M,
        "    if not extra:\n        return\n",
        "    if False:\n        return\n",
        "test_a_body_of_known_fields_logs_nothing",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BROKER)
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=env)


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
