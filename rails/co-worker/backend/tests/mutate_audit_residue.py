"""Mutation harness for co-worker's audit-residue fixes. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\co-worker\\backend\\tests\\mutate_audit_residue.py

Each mutation puts one recorded defect back (or breaks the compose pass-through) and names the
test that must fail for it. A red run that fails some OTHER test is FIRED-WRONG-TEST, not FIRED:
it proves the tree broke, not that the guard is held. CONTROLS are rewrites the suite cannot
see, and each must SURVIVE: a case that fires on any rewrite of its line proves nothing.

Also the NUL guards (2026-10-20): a doc path or an item id holding a NUL is a 404 before any path
is built (test_request_paths.py), not Python 3.11's 500.
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

REPO = Path(__file__).resolve().parents[4]
RAIL_SRC = REPO / "rails" / "co-worker" / "backend"
PY = sys.executable
SUITE = ["rails/co-worker/backend/tests/test_audit_residue.py",
         "rails/co-worker/backend/tests/test_compose_env.py",
         "rails/co-worker/backend/tests/test_request_paths.py"]
RUN_TIMEOUT = 300

M = "rails/co-worker/backend/co_worker_app/main.py"
S = "rails/co-worker/backend/co_worker_app/synthesize.py"
DC = "deploy/docker-compose.yml"
IC = "deploy/installer/docker-compose.installer.yml"

MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "a listing reads the directory a second time for its periods",
        [(M, '"periods": sorted({str(i.get("period")) for i in every if i.get("period")},',
             '"periods": sorted({str(i.get("period")) for i in _collect(d)[0] if i.get("period")},')],
        ["test_a_listing_reads_each_item_once_and_still_lists_every_period"],
    ),
    (
        "the chip poll's parse memo is never consulted",
        [(M, "    if cached is not None and cached[0] == key:\n",
             "    if False:\n")],
        ["test_the_chip_poll_does_not_reread_an_unchanged_inbox"],
    ),
    (
        "the memo is keyed on names only, so a rewritten item is not re-read",
        [(M, "tuple((p.name, st.st_mtime_ns, st.st_size) for p, st in stamped))",
             "tuple(p.name for p, st in stamped))")],
        ["test_the_memo_follows_an_added_a_rewritten_and_a_removed_item"],
    ),
    (
        "an auto trigger stamps the cooldown before it knows the run started",
        [(M, "    started = synthesize_background(_inbox(), source=source,\n",
             "    if auto:\n        _last_auto_attempt = now\n"
             "    started = synthesize_background(_inbox(), source=source,\n")],
        ["test_an_auto_refresh_that_409s_does_not_spend_the_cooldown"],
    ),
    (
        "any lane name is accepted again",
        [(M, "    if source is not None and source not in _lane_names():\n",
             "    if False:\n")],
        ["test_a_lane_no_item_carries_is_refused_and_creates_no_file"],
    ),
    (
        "only lanes some item carries are accepted, so a built-in lane is refused",
        [(M, "    return set(SOURCES) | {str(i.get(\"source\") or \"\").strip() for i in items} - {\"\"}",
             "    return {str(i.get(\"source\") or \"\").strip() for i in items} - {\"\"}")],
        ["test_a_lane_the_harvest_uses_or_a_built_in_lane_is_still_accepted"],
    ),
    (
        "the merged brief's signature is taken after the run",
        [(S, "    combined[\"_source_signature\"] = signature\n",
             "    combined[\"_source_signature\"] = _source_signature(inbox)\n")],
        ["test_an_item_harvested_during_the_run_leaves_the_brief_stale"],
    ),
    (
        "loaded items carry no _mtime, so the recency tiebreak is a constant",
        [(S, "                data.setdefault(\"_mtime\", mtime)\n", "")],
        ["test_the_cut_order_keeps_the_newer_of_two_equal_items"],
    ),
    (
        "the triage sidecar's read-modify-write is unlocked",
        [(M, "    with _state_lock:\n        state = _load_state()\n",
             "    if True:\n        state = _load_state()\n")],
        ["test_two_triage_writes_at_once_both_land"],
    ),
    (
        "a refresh with no source runs one lane instead of every lane",
        [(S, "            if source is None:\n                synthesize_all(inbox, requester=requester)\n",
             "            if source is None and False:\n"
             "                synthesize_all(inbox, requester=requester)\n")],
        ["test_no_source_runs_every_lane_and_a_source_runs_one"],
    ),
    (
        "the lean installer stops passing the inbox owner's name",
        [(IC, "      CO_WORKER_USER_NAME: ${CO_WORKER_USER_NAME:-}\n", "")],
        ["test_every_operator_knob_is_passed_through_from_the_env_file"],
    ),
    (
        "the full stack stops passing whose inbox it is, so no refresh can ever reach Claude",
        [(DC, "      CO_WORKER_INBOX_OWNER: ${CO_WORKER_INBOX_OWNER:-}\n", "")],
        ["test_every_operator_knob_is_passed_through_from_the_env_file"],
    ),
    (
        "the lean installer stops passing whose inbox it is",
        [(IC, "      CO_WORKER_INBOX_OWNER: ${CO_WORKER_INBOX_OWNER:-}\n", "")],
        ["test_every_operator_knob_is_passed_through_from_the_env_file"],
    ),
    (
        "the full stack passes the address under a name Settings ignores",
        [(DC, "      CO_WORKER_USER_EMAIL: ${CO_WORKER_USER_EMAIL:-}\n",
              "      CO_WORKER_USER_MAIL: ${CO_WORKER_USER_EMAIL:-}\n")],
        ["test_every_prefixed_key_compose_passes_is_a_real_setting"],
    ),
    (
        "the full stack pins the auto-trigger to a literal deploy/.env cannot change",
        [(DC, "      CO_WORKER_AUTO_SYNTHESIZE: ${CO_WORKER_AUTO_SYNTHESIZE:-true}\n",
              "      CO_WORKER_AUTO_SYNTHESIZE: \"true\"\n")],
        ["test_every_operator_knob_is_passed_through_from_the_env_file"],
    ),
    # --- a NUL in a path the request names (2026-10-20): a 404, not 3.11's 500 ----------------
    (
        "a NUL in a doc path reaches resolve() again (a 500 on the image's Python)",
        [(M, '    if "\\x00" in doc_path:\n', "    if False:\n")],
        ["test_a_nul_in_a_doc_path_names_no_document"],
    ),
    (
        "a NUL in an item id reaches resolve() again (a 500 on the image's Python)",
        [(M, '    if "\\x00" in item_id:\n', "    if False:\n")],
        ["test_a_nul_in_an_item_id_names_no_item"],
    ),
]

#: Rewrites the suite cannot see; each must SURVIVE. (label, edits)
CONTROLS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("the doc path's NUL spelled chr(0)",
     [(M, '    if "\\x00" in doc_path:\n', "    if chr(0) in doc_path:\n")]),
    ("the item id's NUL spelled chr(0)",
     [(M, '    if "\\x00" in item_id:\n', "    if chr(0) in item_id:\n")]),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: an editable install elsewhere would be tested instead.
    env["PYTHONPATH"] = str(RAIL_SRC)
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import co_worker_app.main as m, co_worker_app.synthesize as s;"
         "print(m.__file__); print(s.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def _apply(label: str, edits: list[tuple[str, str, str]],
           original_bytes: dict[Path, bytes]) -> bool:
    """Write a case's edits, each file's original bytes kept in `original_bytes` for the
    restore; False (and nothing more written) at the first anchor not found exactly once."""
    for rel, old, new in edits:
        path = REPO / rel
        original_bytes.setdefault(path, path.read_bytes())
        n, mutated = mr.mutate_bytes(path.read_bytes(), old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
            return False
        path.write_bytes(mutated)
    return True


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original_bytes: dict[Path, bytes] = {}
        try:
            if not _apply(label, edits, original_bytes):
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

    controls: list[tuple[str, str]] = []
    for label, edits in CONTROLS:
        original_bytes = {}
        try:
            if not _apply(label, edits, original_bytes):
                controls.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            verdict = "TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED"
            controls.append((label, verdict))
            print(f"  {verdict:17} (control) {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls SURVIVED")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
