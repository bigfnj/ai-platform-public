"""Mutation harness for the workspace delegation rule. NOT a pytest file -- it rewrites the tree.

    $env:PYTHONPATH='apps\\platform\\backend'
    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_workspace_delegation.py
    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_workspace_delegation.py --list

(--list names every case and the module it lives in, and runs nothing.)

The four workspace routes were plain `Depends(require_admin)` while the user routes directly
above them carried three layers of delegation. A workspace grant is the stronger of the two --
it decides whose DATA another account can read inside an app they can already reach -- and
without the rule a plain admin could put the super-admin in a room and read their owner-scoped
records in every rooms-aware rail.

The rule mirrors the Users routes ("only a super-admin can modify a super-admin"), and each of
its parts is tested SEPARATELY, because a single "delegation works" test passes while one part is
gone:

    addition -> 403   adding a name outside `manageable` (a super-admin) is refused loudly
    a room that holds a super-admin NOW -> 403 on any update or delete by a plain admin
    a super-admin actor -> unrestricted
    the workspace name -> 1-64 characters, stored stripped

It replaced a gentler rule that froze the super-admin's row while a plain admin edited the rest
of such a room; that freeze is gone (nothing can reach it now), and so is its mutation.

Since 2026-10-14 it also covers the Users routes' reach rule (main.py `_out_of_reach`,
test_admin_reach.py): a plain admin may reset the password of, demote or delete only a user
whose apps all lie within the admin's own grantable set, because a reset hands over the login
and every app with it. Each of the three refusals is mutated on its own, and so are the two
ways the rule can go wrong while still refusing something.

Since 2026-10-15 workspaces follow the reach rule too (test_workspace_reach.py): `_manageable`
keeps only users within reach, and a room holding a member beyond reach cannot be changed or
deleted by a plain admin (`_refuse_room_out_of_reach`). And the access gate sends peers only to
the tiles that read them (test_gate_queries.py; the rest of those cases are in
mutate_gateway_upkeep.py). Each of those two checks works the admin's grantable out once, not
once a user it asks about (test_workspace_reach.py counts the admin's entitlement reads).

WHERE A CASE LIVES. This file holds the cases whose expected tests every tree runs. A case that
expects tests only a rail's tiles let run lives in workspace_cases/<rail>.py and goes wherever
that rail goes: the public snapshot withholds the withheld rails' modules (tools/publish.json),
as mutate_gateway_upkeep.py does with upkeep_cases/. This harness wants every expected test to
fail and no other, so a case naming a test that skips there reported FIRED-WRONG-TEST in a
public run. RAIL_MODULES names every module; load_rail_cases() refuses one it does not name and,
in this repo, a missing one. In a published tree a missing module is named by its file and
skipped, and only where the tree does not carry its rail either.
"""
from __future__ import annotations

import importlib
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

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:       # imported from elsewhere, not run as a script
    sys.path.insert(0, str(HERE))

REPO = Path(__file__).resolve().parents[4]
# The interpreter running THIS harness, not a hardcoded REPO/.venv. A git worktree has no .venv,
# so the harness died there with FileNotFoundError before mutating anything; and the venv's
# editable installs point at ONE checkout, so the child run is also handed this tree's own
# source roots (_TREE_ENV) -- otherwise it mutates one tree and tests another.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
MAIN = "apps/platform/backend/platform_gateway_app/main.py"
SUITE = ["apps/platform/backend/tests/test_workspaces.py",
         "apps/platform/backend/tests/test_workspace_superadmin_rule.py",
         "apps/platform/backend/tests/test_admin_reach.py",
         "apps/platform/backend/tests/test_workspace_reach.py",
         "apps/platform/backend/tests/test_gate_queries.py"]

MUTATIONS: list[tuple[str, str, str, list[str]]] = [
    (
        "the 403 on adding an unmanageable user is removed",
        "    if refused:\n        raise HTTPException(",
        "    if False:\n        raise HTTPException(",
        # The expected lists are exact since 2026-10-14, when the "nothing else fails" half of
        # the verdict was first checked. Six were short: four of the superadmin-rule file's
        # tests of the same refusal, test_workspaces.py's super-admin test, and two reach tests.
        ["test_a_plain_admin_cannot_seed_a_room_with_a_superadmin",
         "test_a_plain_admin_cannot_add_a_superadmin_later",
         "test_a_plain_admin_cannot_create_a_room_holding_the_superadmin",
         "test_a_plain_admin_cannot_add_the_superadmin_to_their_own_room",
         "test_a_plain_admin_cannot_add_a_user_beyond_reach_to_a_room_within_it",
         "test_a_plain_admin_cannot_put_users_beyond_reach_in_a_new_room"],
    ),
    (
        # Re-anchored 2026-10-15, when _manageable took the reach rule.
        "_manageable returns everyone, so there is no delegation at all",
        "    return {u.username for u in rows\n"
        "            if not u.is_superadmin and not _out_of_reach(db, actor, u, grantable)}",
        "    return {u.username for u in rows}",
        ["test_a_plain_admin_cannot_seed_a_room_with_a_superadmin",
         "test_a_plain_admin_cannot_add_a_superadmin_later",
         "test_the_list_route_publishes_what_the_actor_may_manage",
         "test_a_plain_admin_cannot_create_a_room_holding_the_superadmin",
         "test_a_plain_admin_cannot_add_the_superadmin_to_their_own_room",
         "test_a_plain_admin_cannot_put_users_beyond_reach_in_a_new_room",
         "test_the_list_route_offers_only_users_within_reach",
         "test_a_plain_admin_cannot_add_a_user_beyond_reach_to_a_room_within_it"],
    ),
    (
        "_manageable keeps a super-admin within reach (one who holds no app beyond the admin's)",
        "            if not u.is_superadmin and not _out_of_reach(db, actor, u, grantable)}",
        "            if not _out_of_reach(db, actor, u, grantable)}",
        ["test_a_plain_admin_cannot_seed_a_room_with_a_superadmin",
         "test_a_plain_admin_cannot_add_a_superadmin_later",
         "test_the_list_route_publishes_what_the_actor_may_manage",
         "test_a_plain_admin_cannot_add_the_superadmin_to_their_own_room",
         "test_a_plain_admin_cannot_create_a_room_holding_the_superadmin",
         "test_the_list_route_offers_only_users_within_reach"],
    ),
    (
        "the update route no longer refuses a room that holds a super-admin",
        '    _refuse_superadmin_room(w, admin, "change")\n',
        "",
        ["test_a_plain_admin_cannot_rename_a_room_the_superadmin_is_in",
         "test_a_plain_admin_cannot_remove_a_member_of_a_room_the_superadmin_is_in",
         "test_membership_grants_a_plain_admin_nothing_over_the_superadmins_room"],
    ),
    (
        "the delete route no longer refuses a room that holds a super-admin",
        '    _refuse_superadmin_room(w, admin, "delete")\n',
        "",
        ["test_a_plain_admin_cannot_delete_a_room_the_superadmin_is_in",
         "test_membership_grants_a_plain_admin_nothing_over_the_superadmins_room"],
    ),
    (
        "the rule stops looking at who is in the room",
        "    if any(m.user.is_superadmin for m in w.members):",
        "    if False:",
        ["test_a_plain_admin_cannot_rename_a_room_the_superadmin_is_in",
         "test_a_plain_admin_cannot_delete_a_room_the_superadmin_is_in",
         "test_a_plain_admin_cannot_remove_a_member_of_a_room_the_superadmin_is_in",
         "test_membership_grants_a_plain_admin_nothing_over_the_superadmins_room"],
    ),
    (
        "the rule forgets the super-admin is exempt, so the owner cannot run their own room",
        "    if actor.is_superadmin:\n        return\n    if any(",
        "    if False:\n        return\n    if any(",
        ["test_the_superadmin_does_all_of_it",
         "test_a_superadmin_may_do_all_of_it"],
    ),
    (
        "the rule reads the actor as a member, so a member admin may edit the room",
        "    if actor.is_superadmin:\n        return\n    if any(",
        "    if actor.is_superadmin or any(x.user_id == actor.id for x in w.members):\n"
        "        return\n    if any(",
        ["test_membership_grants_a_plain_admin_nothing_over_the_superadmins_room"],
    ),
    (
        "the workspace name loses its upper bound",
        "max_length=_WORKSPACE_NAME_MAX)]",
        ")]",
        ["test_a_name_outside_1_to_64_characters_is_refused"],
    ),
    (
        "the workspace name is no longer stripped before it is bounded",
        "StringConstraints(strip_whitespace=True, min_length=1,",
        "StringConstraints(min_length=1,",
        ["test_a_name_of_64_characters_is_accepted_and_stored_stripped",
         "test_a_name_outside_1_to_64_characters_is_refused"],
    ),
    # --- the Users routes' reach rule (added 2026-10-14) -----------------------------------------
    (
        "a plain admin may reset the password of anyone below super-admin again",
        '        _refuse_out_of_reach(db, admin, u, "reset the password of")\n',
        "        pass\n",
        ["test_a_plain_admin_cannot_reset_the_password_of_an_admin_beyond_their_reach",
         "test_a_plain_admin_cannot_reset_a_plain_user_beyond_their_reach",
         "test_a_combined_update_is_refused_whole"],
    ),
    (
        "a plain admin may demote any admin again",
        '        _refuse_out_of_reach(db, admin, u, "demote")\n',
        "        pass\n",
        ["test_a_plain_admin_cannot_demote_an_admin_beyond_their_reach"],
    ),
    (
        "a plain admin may delete anyone below super-admin again",
        '    _refuse_out_of_reach(db, admin, u, "delete")\n',
        "",
        ["test_a_plain_admin_cannot_delete_a_user_beyond_their_reach"],
    ),
    (
        "the rule stops subtracting what the actor may grant, so nobody is within reach",
        "    return sorted(entitled_app_ids(db, target, APP_IDS) - grantable)",
        "    return sorted(entitled_app_ids(db, target, APP_IDS))",
        ["test_a_plain_admin_may_reset_demote_and_delete_within_their_reach",
         "test_an_admin_may_still_reset_their_own_password",
         "test_a_superadmin_still_can",
         "test_an_app_no_longer_in_the_catalog_puts_nobody_out_of_reach",
         "test_a_plain_admin_cannot_add_a_user_beyond_reach_to_a_room_within_it",
         "test_a_plain_admin_cannot_delete_a_room_holding_a_member_beyond_reach",
         "test_a_plain_admin_still_runs_a_room_of_users_within_reach",
         "test_a_row_for_a_rail_since_removed_puts_nobody_beyond_reach",
         "test_a_superadmin_is_unaffected",
         "test_the_list_route_offers_only_users_within_reach",
         "test_the_refusal_names_every_member_beyond_reach_and_their_apps",
         # Its room of users within reach is refused too (added 2026-10-15).
         "test_a_workspace_request_reads_the_admins_grantable_once_a_check"],
    ),
    (
        "the rule counts a row for a rail no longer in the catalog",
        "    return sorted(entitled_app_ids(db, target, APP_IDS) - grantable)",
        "    return sorted({e.app_id for e in target.entitlements} - grantable)",
        ["test_an_app_no_longer_in_the_catalog_puts_nobody_out_of_reach",
         "test_a_row_for_a_rail_since_removed_puts_nobody_beyond_reach"],
    ),
    # --- workspaces follow the reach rule (added 2026-10-15) ------------------------------------
    (
        "_manageable drops the reach check, so a plain admin places anyone below super-admin",
        "            if not u.is_superadmin and not _out_of_reach(db, actor, u, grantable)}",
        "            if not u.is_superadmin}",
        ["test_a_plain_admin_cannot_put_users_beyond_reach_in_a_new_room",
         "test_a_plain_admin_cannot_add_a_user_beyond_reach_to_a_room_within_it",
         "test_the_list_route_offers_only_users_within_reach"],
    ),
    (
        "the update route no longer refuses a room holding a member beyond reach",
        '    _refuse_room_out_of_reach(db, w, admin, "change")\n',
        "",
        ["test_a_plain_admin_cannot_add_anyone_to_a_room_holding_a_member_beyond_reach",
         "test_a_plain_admin_cannot_remove_a_member_beyond_reach_from_their_own_room",
         "test_a_plain_admin_cannot_rename_a_room_holding_a_member_beyond_reach"],
    ),
    (
        "the delete route no longer refuses a room holding a member beyond reach",
        '    _refuse_room_out_of_reach(db, w, admin, "delete")\n',
        "",
        ["test_a_plain_admin_cannot_delete_a_room_holding_a_member_beyond_reach",
         "test_the_refusal_names_every_member_beyond_reach_and_their_apps"],
    ),
    (
        "the room refusal names only the first member beyond reach",
        "                        for name, apps in beyond)",
        "                        for name, apps in beyond[:1])",
        ["test_the_refusal_names_every_member_beyond_reach_and_their_apps"],
    ),
    # --- the admin's grantable read once a check (added 2026-10-15) -----------------------------
    (
        "_manageable works the admin's grantable out again for every user",
        "            if not u.is_superadmin and not _out_of_reach(db, actor, u, grantable)}",
        "            if not u.is_superadmin and not _out_of_reach(db, actor, u)}",
        ["test_a_workspace_request_reads_the_admins_grantable_once_a_check"],
    ),
    (
        "the room refusal works the admin's grantable out again for every member",
        "              if (apps := _out_of_reach(db, actor, m.user, grantable))]",
        "              if (apps := _out_of_reach(db, actor, m.user))]",
        ["test_a_workspace_request_reads_the_admins_grantable_once_a_check"],
    ),
    # --- peers go only to the tiles that read them (added 2026-10-15): "the access gate sends
    # peers to every tile again" names tests only edu-suite's tiles run, so it lives in
    # workspace_cases/edu_suite.py (moved 2026-10-15). ------------------------------------------
]

#: How many of MUTATIONS are this file's own; the rail modules' cases follow them.
CORE_CASES = len(MUTATIONS)
#: Every module in workspace_cases/ that holds a rail's cases, by file stem (the rail's directory
#: under rails/ with `-` spelled `_`).
RAIL_MODULES = ("edu_suite",)
#: The public snapshot withholds tools/publish.py: that file's absence is how a harness knows it
#: runs there (mutate_gateway_upkeep.py reads it the same way).
PUBLISHED = not (REPO / "tools" / "publish.py").is_file()


def load_rail_cases(package: Path = HERE / "workspace_cases", published: bool = PUBLISHED,
                    modules: tuple[str, ...] = RAIL_MODULES) -> tuple[dict[str, list], list[str]]:
    """({module: its MUTATIONS}, withheld): every rail module's cases, and the file names of the
    modules this tree withholds.

    A module may be absent only from a published tree, and only where that tree does not carry
    its rail (rails/<the module, with _ spelled ->) either; anything else is a module lost by
    mistake. That, a module file `modules` does not name, and a case not in this harness's shape
    (label, old, new, expected tests) would each move cases out of every run without a word, so
    each stops every command, --list included."""
    stray = sorted(p.name for p in package.glob("*.py")
                   if p.stem not in modules and p.stem != "__init__")
    if stray:
        raise SystemExit(f"mutate_workspace_delegation: {package.name}/{', '.join(stray)} is not "
                         f"in RAIL_MODULES, so its cases would never run")
    found: dict[str, list] = {}
    withheld: list[str] = []
    for mod in modules:
        rail = mod.replace("_", "-")
        if not (package / f"{mod}.py").is_file():
            if not published or (REPO / "rails" / rail).is_dir():
                raise SystemExit(f"mutate_workspace_delegation: {package.name}/{mod}.py is "
                                 f"missing, so {rail}'s cases cannot run")
            withheld.append(f"{mod}.py")
            continue
        cases = importlib.import_module(f"{package.name}.{mod}").MUTATIONS
        wrong = [c[0] if c else c for c in cases
                 if len(c) != 4 or not all(isinstance(x, str) for x in c[:3])
                 or not isinstance(c[3], list)]
        if wrong:
            raise SystemExit(f"mutate_workspace_delegation: {package.name}/{mod}.py holds cases "
                             f"not shaped (label, old, new, expected tests): {wrong}")
        found[mod] = cases
    return found, withheld


RAIL_CASES, WITHHELD = load_rail_cases()
for _cases in RAIL_CASES.values():
    MUTATIONS += _cases


def run_suite() -> tuple[int, str]:
    env = _TREE_ENV
    try:
        p = mr.run_tree([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=env)
    except subprocess.TimeoutExpired as exc:
        return -1, f"TIMEOUT: the suite ran past {exc.timeout:g} s and its tree was killed"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    listing = mr.parse_harness_args(None).listing
    # A module this published tree withholds, with its rail, is named by its file on every run.
    # Its cases are not: they name its rail's tests.
    for name in WITHHELD:
        print(f"  SKIPPED  workspace_cases/{name}: its rail is not in this published tree")
    if listing:
        # One line per case, tab-separated: where it lives, then its label. Nothing is run.
        where = ["core"] * CORE_CASES + [mod for mod, cases in RAIL_CASES.items() for _ in cases]
        for mod, m in zip(where, MUTATIONS):
            print("\t".join((mod, m[0])))
        print(f"{len(MUTATIONS)} mutations" + (f"; {len(WITHHELD)} modules withheld" if WITHHELD else ""))
        return 0
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2000:])
        return 2
    print(f"baseline: all {len(SUITE)} delegation test files green\n")

    src = (REPO / MAIN).read_text(encoding="utf-8")
    src_bytes = (REPO / MAIN).read_bytes()
    results: list[tuple[str, str]] = []
    for label, old, new, expected in MUTATIONS:
        n = src.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            mr.write_like(REPO / MAIN, src.replace(old, new), src_bytes)
            code, out = run_suite()
            # Every expected test must fail AND nothing outside the expectation may -- an
            # over-broad mutation that reds the whole file proves nothing about which guard
            # was load-bearing. Both halves read pytest's own summary lines (the tables name
            # bare functions), and both are checked: the second was computed and never was.
            failed = {i.split("[", 1)[0].split("::")[-1] for i in mr.failed_ids(out)}
            missing = [t for t in expected if t not in failed]
            stray = sorted(failed - set(expected))
            ok = code not in (0, -1) and not missing and not stray
            verdict = ("TIMEOUT" if code == -1 else "FIRED" if ok
                       else "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:16} {label}\n"
                  f"            (named {len(expected) - len(missing)}/{len(expected)} expected tests"
                  + "".join(f"\n             did not fail: {t}" for t in missing)
                  + "".join(f"\n             also failed:  {t}" for t in stray) + ")")
        finally:
            mr.restore_exact(REPO / MAIN, src_bytes)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right tests")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
