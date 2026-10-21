"""Mutation harness for the conformance checker's COVERAGE accounting. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_rc_coverage.py

`0 fail, 0 warn` could not tell "checked and clean" from "did not run", and 33 of this tool's
392 rule-runs were the second. The summary now reads `392 checks: 359 ran, 33 skipped`, and a
rule that skipped EVERY selected rail is called out as decoration.

A counter is the easiest thing in this file to make vacuous, because nothing it prints ever
fails a build -- so it could drift to a constant and every run would still look right. These
mutations exist to prove the number is derived from what the rules actually return:

  1. a rule that skips everything must be NAMED as inert, not just counted
  2. removing one rule's skip sentinel must move the count by exactly that rule's rail count,
     which proves the total reads return values rather than a static table
  3. the inert warning must stay quiet under --rail, where skipping everything selected is the
     expected outcome and warning would cry wolf on every scoped run
"""
from __future__ import annotations

import re
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
# The interpreter running THIS harness, not a hardcoded REPO/.venv. A git worktree has no .venv,
# so the harness died there with FileNotFoundError before mutating anything; and the venv's
# editable installs point at ONE checkout, so the child run is also handed this tree's own
# source roots (_TREE_ENV) -- otherwise it mutates one tree and tests another.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
CHECKER = REPO / "tools" / "rail_conformance.py"
RC = "tools/rail_conformance.py"
# RC012's and RC025-RC027's rule files, which the checker runs (its "where the rules live").
RC012 = "tools/rail_conformance_rules/rc012.py"
RC025_027 = "tools/rail_conformance_rules/rc025_rc027.py"


class _Timeout(Exception):
    pass


def run(*extra: str) -> str:
    try:
        p = mr.run_tree([str(PY), str(CHECKER), *extra], cwd=REPO,
                        capture_output=True, text=True, env=_TREE_ENV)
    except subprocess.TimeoutExpired as exc:
        raise _Timeout(f"the checker ran past {exc.timeout:g} s and its tree was killed") from None
    return p.stdout + p.stderr


def rails(out: str) -> int:
    """How many manifests this run selected. DERIVED, never restated: this harness hardcoded 14
    and openmaic made it 15, which turned two real mutations into SURVIVED and would have read
    as "the counter stopped working" rather than "the harness has a stale number in it"."""
    m = re.search(r"(\d+) manifest\(s\)", out)
    if not m:
        raise SystemExit(f"manifest count not found in:\n{out}")
    return int(m.group(1))


def skips_for(rule: str) -> int:
    """How many rails a given rule declines today, asked of the tool rather than assumed."""
    out = run("--verbose", "--rule", rule)
    return len([ln for ln in out.splitlines() if ln.startswith(f"SKIP {rule}")])


def counts(out: str) -> tuple[int, int]:
    m = re.search(r"(\d+) checks: (\d+) ran, (\d+) skipped", out)
    if not m:
        raise SystemExit(f"summary line not found in:\n{out}")
    return int(m.group(2)), int(m.group(3))


#: The three cases' labels, in the order main() runs them (each check() below names its own).
LABELS = ("RC012 skips every rail", "RC027's skip sentinel is deleted",
          "a scoped run must not cry wolf")
#: Why tools/tests/mutation_anchors.py does not count this harness's anchors between runs.
ANCHORS_UNREAD = ("its anchors are arguments of the check() calls inside main(), each beside the "
                  "verdict it needs, not rows of a table")


def main() -> int:
    mr.parse_harness_args(LABELS)
    base = run()
    ran0, skipped0 = counts(base)
    n_rails = rails(base)
    rc027_skips = skips_for("RC027")
    print(f"baseline: {ran0} ran, {skipped0} skipped\n")
    if "fail" not in base:
        print("BASELINE SUMMARY MISSING -- aborting")
        return 2

    results: list[tuple[str, str]] = []

    def check(label: str, file: str, old: str, new: str, verdict_fn) -> None:
        """Replace `old`, found once in `file` (repo-relative), with `new`; judge; restore."""
        path = REPO / file
        original = path.read_text(encoding="utf-8")
        original_bytes = path.read_bytes()
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {file}, expected 1")
            results.append((label, "PATTERN"))
            return
        try:
            mr.write_like(path, original.replace(old, new), original_bytes)
            try:
                ok, detail = verdict_fn()
                verdict = "FIRED" if ok else "SURVIVED"
            except _Timeout as exc:
                verdict, detail = "TIMEOUT", str(exc)
            results.append((label, verdict))
            print(f"  {verdict:9} {label}\n            ({detail})")
        finally:
            mr.restore_exact(path, original_bytes)

    # 1. A rule that declines every rail must be named as inert.
    def _inert():
        out = run()
        ran, skipped = counts(out)
        named = "RC012" in out and "checked nothing this run" in out
        return (named and skipped == skipped0 + n_rails), \
               f"skipped {skipped} (was {skipped0}, +{n_rails} rails); RC012 inert: {named}"

    check(LABELS[0], RC012,
          '    text, where = m.style_text()\n    if not text:\n',
          '    text, where = m.style_text()\n    if True:\n',
          lambda: _inert())

    # 2. Removing one rule's sentinel must move the total by exactly its rail count.
    def _drop():
        out = run()
        ran, skipped = counts(out)
        delta = skipped0 - skipped
        return delta == rc027_skips, (
            f"skip count fell by {delta}, expected exactly {rc027_skips} (RC027's rails)")

    check(LABELS[1], RC025_027,
          '        return [S("RC027", m, "not lean-installable — absent from '
          'docker-compose.installer.yml")]\n',
          '        return []\n',
          lambda: _drop())

    # 3. The inert warning must stay quiet on a scoped run.
    def _scoped():
        # With the guard removed, `--rail workstation` selects ONE rail, so every rule that
        # declines workstation now has n == len(selected) and is wrongly called inert. The
        # warning appearing here is the proof the guard was doing real work.
        out = run("--rail", "workstation")
        return "checked nothing this run" in out, \
               "guard removed -> a scoped run now cries wolf, as the mutation intends"

    check(LABELS[2], RC,
          '    inert = sorted(r for r, n in skipped_by_rule.items()\n'
          '                   if n == len(selected) and not args.rail and not args.rule)\n',
          '    inert = sorted(r for r, n in skipped_by_rule.items()\n'
          '                   if n == len(selected))\n',
          lambda: _scoped())

    after = run()
    ran1, skipped1 = counts(after)
    clean = (ran1, skipped1) == (ran0, skipped0)
    print(f"\nrestored tree: {'green' if clean else 'COUNTS MOVED -- TREE IS DIRTY'}"
          f" ({ran1} ran, {skipped1} skipped)")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations behaved as required")
    return 0 if not bad and clean else 1


if __name__ == "__main__":
    sys.exit(main())
