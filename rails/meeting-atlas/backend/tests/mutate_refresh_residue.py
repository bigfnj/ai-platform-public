"""Mutation harness for meeting-atlas's refresh gate and indexer residue. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\meeting-atlas\\backend\\tests\\mutate_refresh_residue.py

Each mutation puts one recorded defect back and names the test that must fail for it. A red
run that fails some OTHER test is FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not
that the guard is held.
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
RAIL_SRC = REPO / "rails" / "meeting-atlas" / "backend"
PY = sys.executable
SUITE = ["rails/meeting-atlas/backend/tests/test_refresh.py",
         "rails/meeting-atlas/backend/tests/test_indexer.py"]
RUN_TIMEOUT = 300

M = "rails/meeting-atlas/backend/meeting_atlas_app/main.py"
X = "rails/meeting-atlas/backend/meeting_atlas_app/indexer.py"

MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "a fingerprint check that finds nothing new no longer restarts the interval",
        [(M, "        if _mount_fingerprint() == _fingerprint:\n"
             "            _checked_at = time.time()\n"
             "            return\n",
             "        if _mount_fingerprint() == _fingerprint:\n"
             "            return\n")],
        ["test_a_quiet_mount_is_walked_once_per_interval_not_once_per_read"],
    ),
    (
        "the rebuild runs after the build lock is released again",
        [(M, "            return\n        reindex(\"mount changed\")\n",
             "            return\n    reindex(\"mount changed\")\n")],
        ["test_the_rebuild_runs_under_the_build_lock"],
    ),
    (
        "bullet action items are taken on trust again",
        [(X, "+ [_action_bullet(b, segments) for b in _bullets(buf)]",
             "+ [{\"task\": b, \"owner\": None, \"due\": None} for b in _bullets(buf)]")],
        ["test_a_bullet_citing_a_line_nobody_said_is_flagged",
         "test_a_cited_bullet_is_verified_like_a_table_row",
         "test_the_meeting_row_counts_a_flagged_bullet"],
    ),
    (
        "a bare time of day in a bullet is read as a transcript position",
        [(X, "    claimed = _claimed_seconds(bracket.group(0)) if bracket else None\n",
             "    claimed = _claimed_seconds(text)\n")],
        ["test_a_time_of_day_in_a_bullet_is_not_a_citation"],
    ),
    (
        "quote reuse is no longer looked for across the table and the bullets",
        [(X, "            out[\"actions\"].extend(_mark_shared(\n",
             "            out[\"actions\"].extend((\n")],
        ["test_one_quote_behind_a_table_row_and_a_bullet_is_reuse"],
    ),
    (
        "summary rows are taken in table order whatever their status",
        [(X, "            if held is None or _summary_rank(row) >= _summary_rank(held):\n",
             "            if True:\n")],
        ["test_a_failed_retry_never_replaces_the_completed_summary"],
    ),
    (
        "of two equal summaries the earlier wins",
        [(X, "_summary_rank(row) >= _summary_rank(held)",
             "_summary_rank(row) > _summary_rank(held)")],
        ["test_of_two_completed_summaries_the_later_row_wins"],
    ),
    (
        "the unseen WAL is never reported",
        [(X, "    return head[18] == 2 and not os.path.exists(db_path + \"-wal\")\n",
             "    return False\n")],
        ["test_a_wal_database_mounted_without_its_log_says_so",
         "test_healthz_reports_a_wal_database_whose_log_is_not_mounted"],
    ),
    (
        "every WAL database is reported, log beside it or not",
        [(X, "    return head[18] == 2 and not os.path.exists(db_path + \"-wal\")\n",
             "    return head[18] == 2\n")],
        ["test_a_wal_database_mounted_without_its_log_says_so"],
    ),
    (
        "healthz drops the unseen-WAL flag",
        [(M, "        \"meetily_wal_unseen\": meetily_wal_unseen(settings.meetily_db),\n",
             "        \"meetily_wal_unseen\": False,\n")],
        ["test_healthz_reports_a_wal_database_whose_log_is_not_mounted"],
    ),
    (
        "_due_implausible carries its own copy of the placeholder pattern again",
        [(X, "    if _PLACEHOLDER_DUE.search(text):\n        return False\n    base =",
             "    if re.search(r\"\\b(tbd|asap|ongoing|n/?a|none|immediate)\\b\", text):\n"
             "        return False\n    base =")],
        ["test_due_implausible_reads_the_one_shared_placeholder_pattern"],
    ),
    (
        "the fingerprint ignores the Meetily database again",
        [(M, "    return (len(dirs), round(newest, 3), _db_stamp())\n",
             "    return (len(dirs), round(newest, 3))\n")],
        ["test_a_summary_written_only_into_the_database_is_indexed_at_the_next_check"],
    ),
    (
        "the database stamp differs from itself, so every check rebuilds",
        [(M, "            stamp.append((round(st.st_mtime, 3), st.st_size))\n",
             "            stamp.append((time.time(), st.st_size))\n")],
        ["test_an_unchanged_database_triggers_no_rebuild"],
    ),
    (
        "a missing summary_processes table costs the titles too again",
        [(X, "            if log:\n"
             "                log.warning(\"meetily summaries unreadable (%s); titles only\", exc)\n",
             "            raise\n")],
        ["test_a_database_without_summary_processes_keeps_its_titles"],
    ),
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
         "import meeting_atlas_app.main as m, meeting_atlas_app.indexer as x;"
         "print(m.__file__); print(x.__file__)"],
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


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
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
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                n, mutated = mr.mutate_bytes(path.read_bytes(), old, new)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                path.write_bytes(mutated)
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
