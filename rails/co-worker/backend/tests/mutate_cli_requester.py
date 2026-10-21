"""Mutation harness for `requester` in co-worker. NOT a pytest file: it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\co-worker\\backend\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `main.cli_requester`, to the route or
chip that reads it, or to any synthesis function that hands it down to the broker call.

Every guard here fails OPEN and fails SILENTLY. Drop the inbox-owner check and another owner
account's click gets a better brief from Claude, with no warning, in THAT account's name and
with the inbox owner's work mail in the prompt. A guard whose removal makes the product
look better is the one that most needs a test that fails. Its twin in another rail is
rails/recipe-book/tests/mutate_cli_requester.py.

A verdict counts only with the expected test named on a FAILED line. The baseline must be
green and the witness must show the suite imports THIS tree's co_worker_app.
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

BACKEND = Path(__file__).resolve().parents[1]           # rails/co-worker/backend
RAIL = BACKEND.parent
REPO = RAIL.parents[1]
PY = sys.executable
PYTHONPATH = [str(BACKEND), str(REPO / "packages" / "platform_core")]
SUITE = [str(BACKEND / "tests" / "test_cli_requester.py")]

MAIN = BACKEND / "co_worker_app" / "main.py"
SYNTH = BACKEND / "co_worker_app" / "synthesize.py"
AB = RAIL / "tools" / "ab_synthesis.py"

MUTATIONS: list[tuple[str, Path, str, str, str]] = [
    (
        "any viewer's refresh carries their own name (the inbox-owner check is gone)",
        MAIN,
        "    if not owner or not isinstance(user, str) or user != owner:\n        return None",
        "    if not isinstance(user, str):\n        return None",
        "test_another_subscriber_refreshing_the_same_inbox_stays_local",
    ),
    (
        "a blank inbox-owner setting stops meaning nobody",
        MAIN,
        "    if not owner or not isinstance(user, str) or user != owner:",
        "    if not isinstance(user, str) or user != owner:",
        "test_a_blank_setting_names_nobody",
    ),
    (
        "the owner match is loosened to a case-insensitive one",
        MAIN,
        "    if not owner or not isinstance(user, str) or user != owner:",
        "    if not owner or not isinstance(user, str) or user.lower() != owner.lower():",
        "test_names_are_compared_byte_exact",
    ),
    (
        "a foreign @role carries the owner's name past this rail's switch",
        MAIN,
        '    if settings.synthesis_model.strip() != f"@{BROKER_ROLE}":\n        return None',
        "    if False:\n        return None",
        "test_a_foreign_role_never_carries_the_owner",
    ),
    (
        "the route hands the worker the raw viewer instead of the guarded value",
        MAIN,
        "                                    requester=cli_requester(ident.user))",
        "                                    requester=ident.user)",
        "test_another_subscriber_refreshing_the_same_inbox_stays_local",
    ),
    (
        "the route never hands the worker a requester",
        MAIN,
        "                                    requester=cli_requester(ident.user))",
        "                                    requester=None)",
        "test_the_inbox_owner_refreshing_names_themselves",
    ),
    (
        "the chip stops opting the slot in, so the owner is told their brief runs locally",
        MAIN,
        "                             cli_slots=CLI_SLOTS)",
        "                             cli_slots=())",
        "test_the_owner_sees_synthesis_on_claude",
    ),
    (
        "the chip judges the raw viewer, so another owner account is told Claude answers them",
        MAIN,
        "    out = modelstate.resolve(_model_slots(), requester=cli_requester(ident.user),",
        "    out = modelstate.resolve(_model_slots(), requester=ident.user,",
        "test_another_subscriber_sees_the_local_model",
    ),
    # --- wiring: each one makes the CLI silently inert for that call, which looks healthy ---
    (
        "the broker call never puts requester on the wire",
        SYNTH,
        '    if requester:\n        body["requester"] = requester',
        '    if False:\n        body["requester"] = requester',
        "test_the_inbox_owner_refreshing_names_themselves",
    ),
    (
        "the broker call sends an absent requester as null",
        SYNTH,
        '    if requester:\n        body["requester"] = requester',
        '    body["requester"] = requester',
        "test_another_subscriber_refreshing_the_same_inbox_stays_local",
    ),
    (
        "a lane pass drops it on the way to the broker call",
        SYNTH,
        "                           settings.synthesis_model, requester=requester)",
        "                           settings.synthesis_model)",
        "test_the_inbox_owner_refreshing_names_themselves",
    ),
    (
        "the per-lane run stops handing it to each lane",
        SYNTH,
        "            brief, n_eligible, n_read = _run_pass(items, state, now, period, src, dry_run,\n"
        "                                                  requester=requester)",
        "            brief, n_eligible, n_read = _run_pass(items, state, now, period, src, dry_run)",
        "test_the_inbox_owner_refreshing_names_themselves",
    ),
    (
        "a single-lane run stops handing it to its pass",
        SYNTH,
        "    brief, _, _ = _run_pass(items, state, now, period, source, dry_run, requester=requester)",
        "    brief, _, _ = _run_pass(items, state, now, period, source, dry_run)",
        "test_a_single_lane_refresh_carries_it_too",
    ),
    (
        "the worker drops it for a full refresh",
        SYNTH,
        "                synthesize_all(inbox, requester=requester)",
        "                synthesize_all(inbox)",
        "test_the_inbox_owner_refreshing_names_themselves",
    ),
    (
        "the worker drops it for a single-lane refresh",
        SYNTH,
        "                synthesize(inbox, source=source, requester=requester)",
        "                synthesize(inbox, source=source)",
        "test_a_single_lane_refresh_carries_it_too",
    ),
    # --- the background checks themselves: planted defects they must catch ---
    (
        "the module entry point starts naming an owner",
        SYNTH,
        "        result = synthesize_all(args.inbox, dry_run=args.dry_run)",
        '        result = synthesize_all(args.inbox, dry_run=args.dry_run, requester="owner-one")',
        "test_the_module_entry_point_sends_no_requester",
    ),
    (
        "the A/B harness starts naming an owner",
        AB,
        '        "format": "json",\n        "options": {"temperature": 0.15, "num_predict": 2048',
        '        "format": "json", "requester": "owner-one",\n'
        '        "options": {"temperature": 0.15, "num_predict": 2048',
        "test_the_ab_harness_sends_no_requester",
    ),
]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env["PYTHONIOENCODING"] = "utf-8"
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness() -> str | None:
    """Prove the suite imports THIS tree's co_worker_app, or say what it imports instead."""
    probe = "import co_worker_app.main as m, co_worker_app.synthesize as s\n" \
            "for x in (m, s): print('WITNESS', x.__file__)"
    run = mr.run_bounded([PY, "-c", probe], cwd=BACKEND, env=_env(), timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in run.out.splitlines()
             if ln.startswith("WITNESS ")]
    if run.code != 0 or len(files) != 2:
        return f"import probe failed:\n{run.out}"
    stray = [str(f) for f in files if REPO.resolve() not in f.parents]
    return f"imports resolve outside this tree: {stray}" if stray else None


def run_suite() -> mr.SuiteRun:
    return mr.run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header", "-rf",
                           "-p", "no:cacheprovider"], cwd=BACKEND, env=_env())


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    if (why := witness()) is not None:
        print(f"WITNESS FAILED -- {why}")
        return 2
    base = run_suite()
    if base.code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(base.out[-2500:])
        return 2
    print(f"baseline green: {base.out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, path, old, new, expected in MUTATIONS:
        original = path.read_bytes()
        n, mutated = mr.mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {path.name}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            run = run_suite()
        finally:
            mr.restore_exact(path, original)
        named = mr.named_failed(run.out, expected)
        verdict = ("TIMEOUT" if run.timed_out else "FIRED" if run.code != 0 and named
                   else "FIRED-WRONG-TEST" if run.code != 0 else "SURVIVED")
        results.append((label, verdict))
        print(f"  {verdict:17} {label}")
        if verdict != "FIRED":
            tail = [ln for ln in run.out.splitlines() if ln.strip()][-12:]
            print("      " + "\n      ".join(tail))

    after = run_suite()
    print(f"\nrestored tree: {'green' if after.code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and after.code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
