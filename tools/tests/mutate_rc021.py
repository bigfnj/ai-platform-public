"""Mutation harness for RC021's AST gate. NOT a pytest file -- it rewrites the tree under test.

Run it, do not just run pytest, before trusting a change to `_status_route_gate` or
`_IDENTITY_DEPS`:

    .venv\\Scripts\\python.exe tools\\tests\\mutate_rc021.py

RC021 used to be `if "401" not in blob` over every .py file in a rail concatenated, so any rail
containing those three characters anywhere passed. Two rails were serving their model state to
any sibling container while the checker reported `0 fail, 0 warn`. The replacement asks the parse
tree whether the declared `status_route` is gated -- and a checker that asks a real question can
still be wrong in two directions, so both are mutated here:

  * it must FIRE on a route that loses its gate (mutations 1, 3 and 4)
  * it must STAY QUIET on a correctly gated rail, whichever of the four placements FastAPI
    honours that rail happens to use (mutation 2)

The second direction is the one that matters most. A false alarm on a correct rail is how a
checker gets switched off, and the PUBLIC copy of this rule reds finance on its first run for
exactly that reason -- its `_IDENTITY_DEPS` is missing `require_owner` because finance is
withheld from the public mirror and its author never met it.

Mutation 2 is a genuine MOVE, not a deletion, and the distinction is the whole point: finance's
`capabilities()` takes no dependency of its own and leans entirely on the app-wide gate, so
simply deleting that gate is a real hole and RC021 is right to fire. Moving it onto the handler
signature leaves the route just as gated by a different one of the four mechanisms, and a rule
that only understood one of them would cry wolf here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
CHECKER = REPO / "tools" / "rail_conformance.py"

JOB_AID = "rails/job-aid/src/job_aid/api/app.py"
FINANCE = "rails/finance/src/finance/api/app.py"
RC = "tools/rail_conformance.py"

# Each mutation is (label, [(file, old_exact, new), ...], expect) where expect is the rail id
# RC021 must name, or None when the mutation must produce NO finding at all.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], str | None]] = [
    (
        "job-aid's status route loses its gate",
        [(JOB_AID,
          "    def capabilities(ident: Identity = Depends(_identity)) -> dict:",
          "    def capabilities() -> dict:")],
        "job-aid",
    ),
    (
        "finance MOVES its gate from the app onto the handler signature",
        [(FINANCE,
          "                  dependencies=[Depends(require_owner)])",
          "                  )"),
         (FINANCE,
          "    def capabilities() -> dict:",
          "    def capabilities(_o: str = Depends(require_owner)) -> dict:")],
        None,
    ),
    (
        "require_owner is dropped from the identity set",
        [(RC,
          '_IDENTITY_DEPS = frozenset({"identity", "_identity", "require_admin", "require_owner", "owner_id"})',
          '_IDENTITY_DEPS = frozenset({"identity", "_identity", "require_admin", "owner_id"})')],
        "finance",
    ),
    (
        "the AST gate is removed, leaving only the substring floor, while a real hole exists",
        [(JOB_AID,
          "    def capabilities(ident: Identity = Depends(_identity)) -> dict:",
          "    def capabilities() -> dict:"),
         (RC, "        if found and not gated:", "        if False:")],
        None,  # inverted: see below
    ),
]

#: Mutation 4 is the vacuity proof and reads backwards from the others. It opens a real hole AND
#: deletes the check, so the REQUIRED outcome is that RC021 goes quiet. "No finding" there means
#: the gate is doing the work; a finding would mean something else was catching it and the gate
#: is decoration.
_INVERTED = {3}


def rc021() -> tuple[int, str]:
    p = subprocess.run([str(PY), str(CHECKER), "--rule", "RC021"],
                       cwd=REPO, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    code, out = rc021()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out)
        return 2
    print("baseline: RC021 green across every rail\n")

    results: list[tuple[str, str]] = []
    for i, (label, edits, expect) in enumerate(MUTATIONS):
        originals: dict[Path, str] = {}
        ok = True
        for relpath, old, _new in edits:
            path = REPO / relpath
            originals.setdefault(path, path.read_text(encoding="utf-8"))
            if originals[path].count(old) != 1:
                n = originals[path].count(old)
                print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
                ok = False
        if not ok:
            results.append((label, "PATTERN"))
            continue
        try:
            staged = dict(originals)
            for relpath, old, new in edits:
                path = REPO / relpath
                staged[path] = staged[path].replace(old, new)
            for path, text in staged.items():
                path.write_text(text, encoding="utf-8")
            code, out = rc021()
            if i in _INVERTED:
                verdict = "FIRED" if code == 0 else "SURVIVED"
                detail = "checker went quiet on a real hole, as it must when the gate is gone"
            elif expect is None:
                verdict = "FIRED" if code == 0 else "SURVIVED"
                detail = "no finding, as required for a still-gated rail"
            else:
                verdict = "FIRED" if (code != 0 and expect in out) else "SURVIVED"
                detail = f"named {expect}"
            results.append((label, verdict))
            print(f"  {verdict:9} {label}\n            ({detail})")
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")

    code, _ = rc021()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations behaved as required")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
