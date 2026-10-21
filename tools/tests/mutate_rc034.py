"""Mutation harness for RC034 (schema and docs off). NOT a pytest file -- it rewrites the tree.

Run it, do not just run pytest, before trusting a change to `rc034` or `_DOCS_OFF`:

    .venv\\Scripts\\python.exe tools\\tests\\mutate_rc034.py

RC034 holds every `FastAPI(...)` a rail builds to `openapi_url=None` and `docs_url=None`. Two
rails served /docs and /openapi.json for weeks while every other rail had closed them, because
the contract said so and nothing checked it. A checker can be wrong both ways, so both are
mutated here, on published rails only (this harness ships with the snapshot):

  * it must FIRE, naming the rail, when a rail's app opens its docs again: all three keywords
    dropped, only `openapi_url` dropped, or the docs left on in a rail's SECOND app (openmaic's
    LLM shim), which a rule that stopped at the first app or read the file whole would miss;
  * it must STAY QUIET when a rail drops `redoc_url` and reorders the other two, which leaves
    the docs exactly as closed (FastAPI adds /redoc only when openapi_url is set);
  * and the vacuity proof reads backwards: with a real hole open AND the None check removed,
    the rule must go quiet. A finding there would mean something other than the check was
    catching the hole, and the check is decoration.
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

REPO = Path(__file__).resolve().parents[2]
# The interpreter running THIS harness, and this tree's own source roots, so a worktree checks
# itself rather than the checkout the venv's editable installs point at.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
CHECKER = REPO / "tools" / "rail_conformance.py"
TIMEOUT = 300

ATLAS = "rails/meeting-atlas/backend/meeting_atlas_app/main.py"
WATCH = "rails/watch-builder/backend/watch_builder/api/app.py"
SHIM = "rails/openmaic/backend/openmaic_app/api/llm.py"
# RC034's rule file, which tools/rail_conformance.py runs (its "where the rules live").
RC = "tools/rail_conformance_rules/rc028_rc035.py"

HOLE = (ATLAS, "              docs_url=None, redoc_url=None, openapi_url=None,\n", "")

# (label, [(file, old_exact, new), ...], expect): expect is the rail id RC034 must fail, and
# only that one, or None when the run must produce no finding at all.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], str | None]] = [
    (
        "meeting-atlas drops all three docs keywords",
        [HOLE],
        "meeting-atlas",
    ),
    (
        "watch-builder drops only openapi_url (its docs_url=None stays)",
        [(WATCH, "    openapi_url=None,\n", "")],
        "watch-builder",
    ),
    (
        "openmaic's second app, the LLM shim, opens its docs while its main app stays closed",
        [(SHIM, 'shim = FastAPI(title="OpenMAIC LLM shim", docs_url=None, redoc_url=None, '
                'openapi_url=None,',
          'shim = FastAPI(title="OpenMAIC LLM shim",')],
        "openmaic",
    ),
    (
        "CONTROL: meeting-atlas drops redoc_url and reorders the other two",
        [(ATLAS, "docs_url=None, redoc_url=None, openapi_url=None,",
          "openapi_url=None, docs_url=None,")],
        None,
    ),
    (
        "VACUITY: the None check is removed while meeting-atlas has a real hole",
        [HOLE,
         (RC, "                if isinstance(value, ast.Constant) and value.value is None:\n"
              "                    continue\n",
          "                if True:\n"
              "                    continue\n")],
        None,  # inverted: see _INVERTED
    ),
]

#: The vacuity case opens a hole AND removes the check, so the REQUIRED outcome is silence.
_INVERTED = {4}


def rc034() -> tuple[int, str]:
    try:
        p = mr.run_tree([str(PY), str(CHECKER), "--rule", "RC034"], cwd=REPO,
                        capture_output=True, text=True, env=_TREE_ENV, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return -1, f"TIMEOUT: the checker ran past {exc.timeout:g} s and its tree was killed"
    return p.returncode, p.stdout + p.stderr


def failing_rails(out: str) -> set[str]:
    """The rail ids on RC034's FAIL lines (`FAIL RC034  <rail> <message>`)."""
    return {ln.split()[2] for ln in out.splitlines() if ln.startswith("FAIL RC034")}


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    code, out = rc034()
    if code != 0 or "no findings" not in out:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out)
        return 2
    print("baseline: RC034 green across every rail\n")

    results: list[tuple[str, str]] = []
    for i, (label, edits, expect) in enumerate(MUTATIONS):
        originals: dict[Path, bytes] = {}
        staged: dict[Path, bytes] = {}
        ok = True
        for relpath, old, new in edits:
            path = REPO / relpath
            originals.setdefault(path, path.read_bytes())
            n, staged[path] = mr.mutate_bytes(staged.get(path, originals[path]), old, new)
            if n != 1:
                print(f"  ERROR     {label}: pattern found {n} times in {relpath}, expected 1")
                ok = False
        if not ok:
            results.append((label, "PATTERN"))
            continue
        try:
            for path, raw in staged.items():
                path.write_bytes(raw)
            code, out = rc034()
            failed = failing_rails(out)
            if code == -1:
                verdict, detail = "TIMEOUT", out.strip()
            elif i in _INVERTED:
                verdict = "FIRED" if code == 0 and not failed else "SURVIVED"
                detail = "quiet on a real hole once the check is gone, as it must be"
            elif expect is None:
                verdict = "FIRED" if code == 0 and "no findings" in out else "SURVIVED"
                detail = "no finding, as required for docs that stay closed"
            else:
                verdict = "FIRED" if code != 0 and failed == {expect} else "SURVIVED"
                detail = f"failed {sorted(failed) or 'nothing'}, required [{expect!r}]"
            results.append((label, verdict))
            print(f"  {verdict:9} {label}\n            ({detail})")
            if verdict != "FIRED":
                print("            " + "\n            ".join(out.strip().splitlines()[-8:]))
        finally:
            for path, raw in originals.items():
                mr.restore_exact(path, raw)

    code, out = rc034()
    clean = code == 0 and "no findings" in out
    print(f"\nrestored tree: {'green' if clean else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations behaved as required")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and clean else 1


if __name__ == "__main__":
    sys.exit(main())
