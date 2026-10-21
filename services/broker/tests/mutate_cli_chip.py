"""Mutation harness for the header chip's CLI fields. NOT a pytest file -- it rewrites the tree.

    python services/broker/tests/mutate_cli_chip.py      (with the repo .venv's python)

Run this, not just pytest, before trusting a change to `cli_serves`, `_cli_busy` or the
`backend`/`busy` lines of `resolve()` in tools/rail_templates/modelstate.py.tmpl.

It mutates the TEMPLATE, never a rendered copy. Every rail's modelstate.py is generated from that
one file, and test_cli_chip_equivalence.py executes the template directly, so a mutation here is
seen by the suite without a `sync`. Running a rail's own tests would be the trap the opposite way:
they import the RENDERED copy, which this harness does not touch, so they would pass against every
mutation and report it as survived when they never ran the mutated code at all.

Why each guard needs a test that fails: every one of them errs in the direction that looks
fine. Drop the owner check and a non-owner's chip says "claude" while the broker quietly sends
their call to Ollama -- nothing breaks, the chip is just a lie about whose subscription pays.
Drop the per-slot opt-in and a slot whose call site passes no requester claims the CLI for work
the broker refuses to send there. A chip that is wrong in the reassuring direction is never
reported by anyone.

PROOF OF EXECUTION. A clean N/N means nothing unless the suite read the bytes this harness wrote.
Each run passes the SHA-256 of the file as written in CLI_CHIP_TEMPLATE_SHA256, and the suite's
witness test asserts it read exactly that file. A run in which the witness did not PASS is
reported NOT-EXECUTED, never FIRED or SURVIVED.

It also mutates the BROKER side (BROKER_MUTATIONS): the chip and the routing agree only while
`cli_state` and `cli_decision` read the eligible roles through the same function, and that is a
property of the broker, not of the template. Those runs carry CLI_CHIP_BROKER_SHA256, the digest
of broker.py + broker_cli.py (Broker's CLI methods, which broker.py runs) + config.py as written,
checked by a second witness against the files the suite actually imported and ran.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# This directory is sys.path[0] when the harness runs as a script. Every suite run is bounded:
# a mutation that hangs a test is reported as TIMEOUT instead of hanging the harness.
from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
REPO = BROKER.parents[1]
TEMPLATE = REPO / "tools" / "rail_templates" / "modelstate.py.tmpl"
PY = sys.executable
TEST_FILE = "tests/test_cli_chip_equivalence.py"
SUITE = [TEST_FILE]
WITNESS = f"PASSED {TEST_FILE}::test_the_template_under_test_is_the_one_on_disk"

GRID_OK = "test_template_agrees_with_the_broker[on-exe-owners-iep-ok]"

# (label, old, new, failing test that must be named, substrings its failure must show)
MUTATIONS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    (
        "the requester is not stripped, so ' owner-one ' reads ollama while the broker serves it",
        "    who = requester.strip()\n",
        "    who = requester\n",
        GRID_OK,
        ("requester=' owner-one '",),
    ),
    (
        "`ready` is ignored, so a broker with an unreadable token still paints the chip claude",
        '    if not isinstance(cli, dict) or cli.get("ready") is not True:\n',
        "    if not isinstance(cli, dict):\n",
        "test_template_agrees_with_the_broker[on-exe-owners-iep-token_unreadable]",
        ("broker says False (token_unreadable), template says True",),
    ),
    (
        "the chip folds case again, so a case-variant account's chip claims claude",
        "    if who not in {str(o).strip() for o in owners}:\n",
        "    if who.lower() not in {str(o).strip().lower() for o in owners}:\n",
        GRID_OK,
        ("requester='Owner-One' ref='@iep'",),
    ),
    (
        "any viewer's chip claims the owner's subscription",
        "    if who not in {str(o).strip() for o in owners}:\n"
        "        return False\n",
        "    if False:\n"
        "        return False\n",
        GRID_OK,
        ("requester='bob' ref='@iep'",),
    ),
    (
        "a concrete model name whose tail is a role is called claude",
        '    if not isinstance(ref, str) or not ref.startswith("@"):\n',
        "    if not isinstance(ref, str):\n",
        GRID_OK,
        ("ref='xiep'",),
    ),
    (
        "every eligible slot is claude, opted in or not",
        "        on_cli = slot in slots and cli_serves(cli, requester, ref)\n",
        "        on_cli = cli_serves(cli, requester, ref)\n",
        "test_a_slot_not_in_cli_slots_is_never_claude",
        (),
    ),
    (
        "busy lights for another owner's call",
        '    return any(isinstance(a, dict) and _role_key(a.get("role")) == role\n'
        '               and str(a.get("requester") or "").strip() == who\n'
        "               for a in active)\n",
        '    return any(isinstance(a, dict) and _role_key(a.get("role")) == role\n'
        "               for a in active)\n",
        "test_busy_is_only_for_the_viewers_own_call",
        (),
    ),
    (
        "busy lights for a call to a different role",
        '    return any(isinstance(a, dict) and _role_key(a.get("role")) == role\n'
        '               and str(a.get("requester") or "").strip() == who\n',
        "    return any(isinstance(a, dict)\n"
        '               and str(a.get("requester") or "").strip() == who\n',
        "test_busy_matches_the_role_case_insensitively_and_only_that_role",
        (),
    ),
    (
        "busy folds case, so another account's call lights this owner's dot",
        '               and str(a.get("requester") or "").strip() == who\n',
        '               and str(a.get("requester") or "").strip().lower() == who.lower()\n',
        "test_busy_does_not_light_for_a_case_variant_account",
        (),
    ),
    (
        "the chip folds the ref's case, so '@IEP' paints claude while the broker refuses it",
        '    return ref[1:] in {str(r).strip().lstrip("@").lower() for r in roles}\n',
        '    return ref[1:].lower() in {str(r).strip().lstrip("@").lower() for r in roles}\n',
        GRID_OK,
        ("ref='@IEP'",),
    ),
    (
        "the unreachable branch drops the CLI keys again, so its shape differs from ok",
        '                        "upstream": "local", "backend": OLLAMA, "busy": False}\n',
        '                        "upstream": "local"}\n',
        "test_every_entry_carries_every_key_in_both_branches[unreachable]",
        (),
    ),
]


# The BROKER half of the comparison. The template reads `cli_state()["roles"]`; the broker routes
# by `cli_decision`. Both must read the eligible roles through ONE function
# (`cli_effective`), or a switched-off role keeps its "claude" chip while the broker sends
# its calls to Ollama, or the reverse. A template-only harness cannot see that drift, because
# the template is not where it would happen. (label, broker file, old, new, test, details)
BROKER_MUTATIONS: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    (
        # Re-anchored 2026-10-09 when cli_state started reading each role's Claude model from
        # the same cli_effective read.
        "cli_state reports the env roles while the routing reads the switch file",
        "app/broker_cli.py",
        "        roles, roles_source, roles_problem, roles_note = eff.roles, eff.source, eff.problem, eff.note\n",
        '        roles, roles_source, roles_problem, roles_note = s.cli_role_set(), "env", "", ""\n',
        "test_template_agrees_with_the_broker[on-exe-owners-noroles-fileon]",
        ("broker says True (ok), template says False",),
    ),
    (
        # Re-anchored 2026-10-14 when cli_decision began reading cli_effective itself, so the
        # call takes its model from the same read. Same mutation.
        "the routing reads the env roles while cli_state reports the switch file",
        "app/broker_cli.py",
        "        roles, roles_problem = switches.roles, switches.problem\n",
        '        roles, roles_problem = s.cli_role_set(), ""\n',
        "test_template_agrees_with_the_broker[on-exe-owners-chat-fileoff]",
        ("broker says True (ok), template says False",),
    ),
]
#: In the order the witness hashes them: broker.py, the files of methods it ran, config.py.
BROKER_FILES = [BROKER / "app" / "broker.py", BROKER / "app" / "broker_cli.py",
                BROKER / "app" / "config.py"]
WITNESS_BROKER = f"PASSED {TEST_FILE}::test_the_broker_under_test_is_the_one_on_disk"


def _broker_digest() -> str:
    return hashlib.sha256(b"".join(p.read_bytes() for p in BROKER_FILES)).hexdigest()


def run_suite(written: bytes) -> SuiteRun:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BROKER)
    env["CLI_CHIP_TEMPLATE_SHA256"] = hashlib.sha256(written).hexdigest()
    # The broker files as they are on disk NOW (mutated or not), for the broker witness.
    env["CLI_CHIP_BROKER_SHA256"] = _broker_digest()
    # No .pyc written: a mutation and its restore can land in the same second with the same
    # size, and the import system would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # UTF-8 both ways. A failure traceback quotes template source, which carries characters
    # outside the Windows console code page, and a decode error here would read as a crash.
    env["PYTHONIOENCODING"] = "utf-8"
    # -rA lists PASSED lines too, which is what lets the witness be checked positively.
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "-rA", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=BROKER, env=env, encoding="utf-8", errors="replace")


def failed_lines(out: str) -> list[str]:
    # Only FAILED lines count as naming a test. -rA also prints a PASSED line for every test
    # that passed, so a bare substring search for a test id would match a pass.
    return [ln for ln in out.splitlines() if ln.startswith("FAILED ")]


def main() -> int:
    parse_harness_args(MUTATIONS + BROKER_MUTATIONS)
    original = TEMPLATE.read_bytes()
    code, out, hung = run_suite(original)
    if code != 0 or WITNESS not in out or WITNESS_BROKER not in out:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung else
              "BASELINE IS NOT GREEN (or its witness did not pass) -- fix that before trusting "
              "any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green, witness passed: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    try:
        for label, old, new, expected, details in MUTATIONS:
            # The shared helper replaces only the matched span, in the template's own
            # line endings; restore_exact below proves the original bytes are back.
            n, mutated = mutate_bytes(original, old, new)
            if n != 1:
                print(f"  ERROR  {label}: pattern found {n} times in the template, expected 1")
                results.append((label, "PATTERN"))
                continue
            TEMPLATE.write_bytes(mutated)
            code, out, hung = run_suite(mutated)
            failed = failed_lines(out)
            if hung:
                # Checked first: a killed run's witness line may or may not have been printed,
                # and either way nothing showed the named test failing.
                verdict = "TIMEOUT"
            elif WITNESS not in out:
                verdict = "NOT-EXECUTED"
            elif code == 0:
                verdict = "SURVIVED"
            elif named_failed(out, expected) and all(d in out for d in details):
                verdict = "FIRED"
            else:
                verdict = "FIRED-WRONG-TEST"
            results.append((label, verdict))
            print(f"  {verdict:17} {label}  ({len(failed)} failing)")
            if verdict != "FIRED":
                # Show the evidence. A verdict with nothing under it sends the next reader to
                # re-run the mutation by hand to find out which assertion it was.
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
            restore_exact(TEMPLATE, original)
    finally:
        TEMPLATE.write_bytes(original)

    broker_originals = {p: p.read_bytes() for p in BROKER_FILES}
    try:
        for label, relpath, old, new, expected, details in BROKER_MUTATIONS:
            path = BROKER / relpath
            n, mutated = mutate_bytes(broker_originals[path], old, new)
            if n != 1:
                print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
                results.append((label, "PATTERN"))
                continue
            path.write_bytes(mutated)
            code, out, hung = run_suite(original)
            failed = failed_lines(out)
            if hung:
                verdict = "TIMEOUT"
            elif WITNESS not in out or WITNESS_BROKER not in out:
                # The broker witness hashes the files the suite IMPORTED; a pass proves the
                # mutated bytes were the ones under test.
                verdict = "NOT-EXECUTED"
            elif code == 0:
                verdict = "SURVIVED"
            elif named_failed(out, expected) and all(d in out for d in details):
                verdict = "FIRED"
            else:
                verdict = "FIRED-WRONG-TEST"
            results.append((label, verdict))
            print(f"  {verdict:17} {label}  ({len(failed)} failing)")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
            restore_exact(path, broker_originals[path])
    finally:
        for p, raw in broker_originals.items():
            p.write_bytes(raw)

    restored = TEMPLATE.read_bytes() == original and all(
        p.read_bytes() == raw for p, raw in broker_originals.items())
    code, out, _hung = run_suite(original)
    clean = restored and code == 0 and WITNESS in out and WITNESS_BROKER in out
    print(f"\nrestored tree: {'green' if clean else 'STILL RED OR CHANGED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and clean else 1


if __name__ == "__main__":
    sys.exit(main())
