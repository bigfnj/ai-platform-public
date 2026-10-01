"""Mutation harness for the workspace delegation rule. NOT a pytest file -- it rewrites the tree.

    $env:PYTHONPATH='apps\\platform\\backend'
    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_workspace_delegation.py

The four workspace routes were plain `Depends(require_admin)` while the user routes directly
above them carried three layers of delegation. A workspace grant is the stronger of the two --
it decides whose DATA another account can read inside an app they can already reach -- and
without the rule a plain admin could put the super-admin in a room and read their owner-scoped
records in every rooms-aware rail.

The rule has two halves that fail in opposite directions, and the point of this harness is that
each is tested SEPARATELY. A single "delegation works" test would pass while one half was gone:

    addition -> 403   adding a name outside `manageable` is refused loudly
    omission -> FREEZE a member the actor cannot manage stays when they are left out

Mutation 2 is the one that matters. If `frozen` silently became an empty set, every addition
test would still pass and a plain admin editing a room would quietly evict the super-admin from
their own sharing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PY = REPO / ".venv" / "Scripts" / "python.exe"
MAIN = "apps/platform/backend/platform_gateway_app/main.py"
SUITE = "apps/platform/backend/tests/test_workspaces.py"

MUTATIONS: list[tuple[str, str, str, list[str]]] = [
    (
        "the 403 on adding an unmanageable user is removed",
        "    if refused:\n        raise HTTPException(",
        "    if False:\n        raise HTTPException(",
        ["test_a_plain_admin_cannot_seed_a_room_with_a_superadmin",
         "test_a_plain_admin_cannot_add_a_superadmin_later"],
    ),
    (
        "the freeze is dropped, so an omitted super-admin is evicted",
        "    frozen = {m.user.username for m in w.members if m.user.username not in manageable}",
        "    frozen = set()",
        ["test_an_existing_superadmin_member_is_frozen_not_stripped"],
    ),
    (
        "_manageable returns everyone, so there is no delegation at all",
        "    return {u.username for u in rows if actor.is_superadmin or not u.is_superadmin}",
        "    return {u.username for u in rows}",
        ["test_a_plain_admin_cannot_seed_a_room_with_a_superadmin",
         "test_a_plain_admin_cannot_add_a_superadmin_later",
         "test_a_plain_admin_cannot_delete_a_room_holding_a_superadmin",
         "test_the_list_route_publishes_what_the_actor_may_manage"],
    ),
    (
        "the delete route stops checking membership",
        "    beyond = sorted(m.user.username for m in w.members "
        "if m.user.username not in manageable)",
        "    beyond = []",
        ["test_a_plain_admin_cannot_delete_a_room_holding_a_superadmin"],
    ),
]


def run_suite() -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=str(REPO / "apps" / "platform" / "backend"))
    p = subprocess.run([str(PY), "-m", "pytest", SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=REPO, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2000:])
        return 2
    print("baseline: test_workspaces.py green\n")

    src = (REPO / MAIN).read_text(encoding="utf-8")
    results: list[tuple[str, str]] = []
    for label, old, new, expected in MUTATIONS:
        n = src.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            (REPO / MAIN).write_text(src.replace(old, new), encoding="utf-8")
            code, out = run_suite()
            named = [t for t in expected if t in out]
            # Every expected test must fail AND nothing outside the expectation may -- an
            # over-broad mutation that reds the whole file proves nothing about which guard
            # was load-bearing.
            failed = out.count("FAILED") or out.count("failed")
            ok = code != 0 and len(named) == len(expected)
            results.append((label, "FIRED" if ok else "SURVIVED"))
            print(f"  {'FIRED' if ok else 'SURVIVED':9} {label}\n"
                  f"            (named {len(named)}/{len(expected)} expected tests)")
        finally:
            (REPO / MAIN).write_text(src, encoding="utf-8")

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right tests")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
