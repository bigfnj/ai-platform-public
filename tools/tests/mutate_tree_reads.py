"""Mutation harness for the checks that must hold in whichever tree they run in: this private
checkout, or the public snapshot publish.py builds from it, which CI's dry run tests as a plain
directory. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_tree_reads.py

Covers tree_floors.py (a whole-tree scan is held to its own tree's floor, and in the private tree
publish's share of it to a snapshot's), the scans that use it (test_mutation_harness_cli.py,
test_node_tests_reached.py), and the two checks that read the tree without asking git where it is
no checkout: the deploy\\local ignore rule (test_lean_broker_files.py, through own_ignore_rules.py)
and the installer's .env keys (test_ops_small_defects.py). Each mutation breaks one of those and names the tests that must
fail; a red run that fails something else is FIRED-WRONG-TEST. Each control means the same and
must SURVIVE.

Runs in the private repo: several of its tests ask publish.py, which a snapshot withholds, and
skip there, so a baseline with a skip is refused rather than read as SURVIVED.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
RUN_TIMEOUT = 300

FLOORS = "tools/tests/tree_floors.py"
HCLI = "tools/tests/test_mutation_harness_cli.py"
NODE = "tools/tests/test_node_tests_reached.py"
LBF = "tools/tests/test_lean_broker_files.py"
# Its ignore check, shared with rails/ai-voice's test_db_connections.py since 2026-10-20.
OWN_IGNORE = "tools/tests/own_ignore_rules.py"
SMALL = "tools/tests/test_ops_small_defects.py"

# test_tree_floors.py
OWN_TREE = "test_publish_py_tells_the_private_tree_from_a_snapshot"
PRIVATE_FLOOR = "test_the_private_tree_is_held_to_its_own_floor"
SHARE_FLOOR = "test_the_private_tree_holds_publishs_share_to_a_snapshots_floor"
SNAPSHOT_FLOOR = "test_a_snapshot_is_held_to_its_own_floor_and_asks_publish_nothing"
REAL_SHARE = "test_the_share_is_publishs_own_selection"
# test_mutation_harness_cli.py
HARNESS_CUT = "test_each_scan_fails_when_it_finds_less_than_its_trees_floor"
HARNESS_SHARE_CUT = "test_each_scan_fails_when_publish_ships_less_than_a_snapshots_floor"
# test_node_tests_reached.py
NODE_TREE = "test_every_node_test_file_is_run_by_a_target_or_a_wrapper"
NODE_CUT = "test_the_tree_check_fails_on_a_scan_cut_below_its_trees_floor"
NODE_SHARE_CUT = "test_the_tree_check_fails_when_publish_ships_less_than_a_snapshots_floor"
NODE_SHARE_GAP = "test_the_tree_check_fails_when_a_published_file_is_reached_only_by_what_publish_withholds"
# test_lean_broker_files.py
IGNORED = "test_deploy_local_is_ignored_where_the_snapshot_ships_it"
OWN_RULES = "test_only_a_trees_own_rules_are_credited_to_it"
# test_ops_small_defects.py
KEYS_READ = "test_every_key_the_installer_writes_into_env_is_read_by_something"
WALKED = "test_a_tree_that_is_no_checkout_is_read_by_a_walk"
WALK_WHOLE = "test_the_walk_misses_no_file_git_tracks"

#: Named tests, not whole files: test_mutation_harness_cli.py also launches every harness, and the
#: other two files run PowerShell under both engines for tests nothing here touches.
SUITE = [
    "tools/tests/test_tree_floors.py",
    f"{HCLI}::test_every_harness_reads_its_command_line_before_anything_runs",
    f"{HCLI}::test_every_python_harness_starts_its_children_through_the_shared_runner",
    f"{HCLI}::test_no_python_harness_judges_fired_by_a_substring_of_the_output",
    f"{HCLI}::test_no_python_harness_ends_a_node_id_at_the_first_dash_of_a_line",
    f"{HCLI}::{HARNESS_CUT}", f"{HCLI}::{HARNESS_SHARE_CUT}",
    f"{NODE}::{NODE_TREE}", f"{NODE}::test_the_tree_check_fails_on_a_new_file_nothing_runs",
    f"{NODE}::{NODE_CUT}", f"{NODE}::{NODE_SHARE_CUT}", f"{NODE}::{NODE_SHARE_GAP}",
    f"{LBF}::{IGNORED}", f"{LBF}::{OWN_RULES}",
    f"{SMALL}::{KEYS_READ}", f"{SMALL}::{WALKED}", f"{SMALL}::{WALK_WHOLE}",
]

PRIVATE_CHECK = "    if len(found) < floor.private:\n"
WALK_PRUNE = "            sub[:] = [s for s in sub if s not in PRUNE and not s.startswith(\".venv\")]\n"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    # --- tree_floors.py -------------------------------------------------------------------------
    (
        "the private tree reads as a snapshot, so a snapshot's floor and ways in apply to it",
        [(FLOORS, '    return not (repo / "tools" / "publish.py").is_file()\n', "    return True\n")],
        [OWN_TREE, NODE_TREE],
    ),
    (
        "the private tree's own floor is not checked",
        [(FLOORS, PRIVATE_CHECK, "    if False:\n")],
        [PRIVATE_FLOOR, HARNESS_CUT, NODE_CUT],
    ),
    (
        "a scan exactly at the private tree's floor fails",
        [(FLOORS, PRIVATE_CHECK, "    if len(found) <= floor.private:\n")],
        [PRIVATE_FLOOR],
    ),
    (
        "publish's share of the private scan is not held to a snapshot's floor",
        [(FLOORS, "    if len(share) < floor.published:\n", "    if False:\n")],
        [SHARE_FLOOR, HARNESS_SHARE_CUT, NODE_SHARE_CUT],
    ),
    (
        "a snapshot goes on to the private tree's floor and asks publish for its share",
        [(FLOORS, "{floor.published}: a scan that found so few saw nothing. {found}\")\n        return\n",
          "{floor.published}: a scan that found so few saw nothing. {found}\")\n")],
        [SNAPSHOT_FLOOR],
    ),
    (
        "the share is every path found, not publish's selection",
        [(FLOORS, "    return lambda paths: pub.select(cfg, list(paths))[0]\n",
          "    return lambda paths: list(paths)\n")],
        [REAL_SHARE, NODE_TREE],
    ),
    # --- the scans that use it ------------------------------------------------------------------
    (
        "the harness scan drops its floor",
        [(HCLI, "    found = harnesses()\n    tree_floors.reach(found, HARNESS_FLOOR)\n",
          "    found = harnesses()\n")],
        [HARNESS_CUT],
    ),
    (
        "the node test scan drops its floor",
        [(NODE, "    files = node_test_files()\n    tree_floors.reach(files, NODE_FLOOR)\n",
          "    files = node_test_files()\n")],
        [NODE_CUT],
    ),
    (
        "the private tree no longer reads publish's node test files the way a snapshot will",
        [(NODE, "    assert unrun == [], (\n        \"published node test file(s) a snapshot",
          "    assert True, (\n        \"published node test file(s) a snapshot")],
        [NODE_SHARE_GAP],
    ),
    # --- deploy\local's ignore rule, asked without the tree's own checkout ----------------------
    (
        "deploy\\local is re-included right after its rule",
        [("deploy/.gitignore", "\nlocal/\n", "\nlocal/\n!local/\n")],
        [IGNORED],
    ),
    (
        "only the root's .gitignore is copied to the scratch repository",
        [(OWN_IGNORE, "    for depth in range(len(parts)):", "    for depth in range(1):")],
        [IGNORED, OWN_RULES],
    ),
    (
        "the box's own git config is read, so its init template's exclude is credited",
        [(OWN_IGNORE, "    env = {k: v for k, v in os.environ.items() if not k.startswith(\"GIT_\")}\n"
               "    env.update(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM=\"1\")\n",
          "    env = dict(os.environ)\n")],
        [OWN_RULES],
    ),
    (
        "core.excludesFile is left unset, so the box's default excludes file is credited",
        [(OWN_IGNORE, "[\"git\", \"-C\", str(repo), \"-c\", f\"core.excludesFile={empty.as_posix()}\",\n"
                      "                        \"check-ignore\"",
          "[\"git\", \"-C\", str(repo),\n                        \"check-ignore\"")],
        [OWN_RULES],
    ),
    # --- the installer's .env keys, read without git where the tree is no checkout --------------
    (
        "git is asked for the file list whatever the tree",
        [(SMALL, "    if _own_checkout():\n        out = subprocess.run(",
          "    if True:\n        out = subprocess.run(")],
        [WALKED],
    ),
    (
        "the walk goes into node_modules and virtualenvs",
        [(SMALL, WALK_PRUNE, "")],
        [WALKED],
    ),
]

#: Rewrites that mean the same. Each must SURVIVE.
CONTROLS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "publish.py's absence asked the other way round",
        [(FLOORS, '    return not (repo / "tools" / "publish.py").is_file()\n',
          '    return (repo / "tools" / "publish.py").exists() is False\n')],
    ),
    (
        "the private floor's comparison written the other way round",
        [(FLOORS, PRIVATE_CHECK, "    if not len(found) >= floor.private:\n")],
    ),
    (
        "a file under deploy\\local is re-included, which git cannot do inside an ignored folder",
        [("deploy/.gitignore", "\nlocal/\n", "\nlocal/\n!local/README.md\n")],
    ),
    (
        "the walk extends its list from a generator",
        [(SMALL, "            found += [Path(root) / f for f in files]\n",
          "            found.extend(Path(root, f) for f in files)\n")],
    ),
]


def run_suite(extra: tuple[str, ...] = ()) -> mr.SuiteRun:
    return mr.run_bounded([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                           "-p", "no:cacheprovider", "-rfEs", *extra],
                          cwd=REPO, timeout=RUN_TIMEOUT)


def apply(label: str, edits: list[tuple[str, str, str]], original: dict[Path, bytes]) -> bool:
    """Write every edit (each `old` exactly once), recording each file's bytes first."""
    for rel, old, new in edits:
        path = REPO / rel
        original.setdefault(path, path.read_bytes())
        n, mutated = mr.mutate_bytes(path.read_bytes(), old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
            return False
        path.write_bytes(mutated)
    return True


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
    # Every named test must EXIST before its absence from a FAILED line can mean anything: a
    # renamed test would otherwise turn every mutation naming it into a silent SURVIVED.
    listed = run_suite(("--collect-only",)).out
    named = sorted({test for case in MUTATIONS for test in case[2]})
    missing = [t for t in named if f"::{t}" not in listed]
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    base = run_suite()
    if base.code != 0:
        print("BASELINE TIMEOUT\n" if base.timed_out else
              "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(base.out[-2500:])
        return 2
    summary = base.out.strip().splitlines()[-1]
    if "skipped" in summary:
        # A test that skips (publish.py withheld, no git checkout) cannot fail, and a mutation
        # naming it would read as SURVIVED for a test that never ran.
        print(f"BASELINE SKIPPED TESTS -- {summary}: this harness runs in the private repo")
        return 2
    print(f"baseline green: {summary}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original: dict[Path, bytes] = {}
        try:
            if not apply(label, edits, original):
                results.append((label, "PATTERN"))
                continue
            run = run_suite()
            missed = [t for t in expected if not mr.named_failed(run.out, t)]
            verdict = ("TIMEOUT" if run.timed_out else
                       "FIRED" if run.code != 0 and not missed else
                       "FIRED-WRONG-TEST" if run.code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missed:
                    print(f"      expected to fail but did not: {', '.join(missed)}")
                tail = [ln for ln in run.out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original.items():
                mr.restore_exact(path, raw)

    controls: list[tuple[str, str]] = []
    for label, edits in CONTROLS:
        original = {}
        try:
            if not apply(label, edits, original):
                controls.append((label, "PATTERN"))
                continue
            run = run_suite()
            verdict = "TIMEOUT" if run.timed_out else "SURVIVED" if run.code == 0 else "BROKE"
            controls.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in run.out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original.items():
                mr.restore_exact(path, raw)

    after = run_suite()
    print(f"\nrestored tree: {'green' if after.code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and after.code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
