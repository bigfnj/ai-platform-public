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
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
CHECKER = REPO / "tools" / "rail_conformance.py"
RC = "tools/rail_conformance.py"


def run(*extra: str) -> str:
    p = subprocess.run([str(PY), str(CHECKER), *extra], cwd=REPO,
                       capture_output=True, text=True)
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


def main() -> int:
    base = run()
    ran0, skipped0 = counts(base)
    n_rails = rails(base)
    rc027_skips = skips_for("RC027")
    print(f"baseline: {ran0} ran, {skipped0} skipped\n")
    if "fail" not in base:
        print("BASELINE SUMMARY MISSING -- aborting")
        return 2

    original = (REPO / RC).read_text(encoding="utf-8")
    results: list[tuple[str, str]] = []

    def check(label: str, old: str, new: str, verdict_fn) -> None:
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times, expected 1")
            results.append((label, "PATTERN"))
            return
        try:
            (REPO / RC).write_text(original.replace(old, new), encoding="utf-8")
            ok, detail = verdict_fn()
            results.append((label, "FIRED" if ok else "SURVIVED"))
            print(f"  {'FIRED' if ok else 'SURVIVED':9} {label}\n            ({detail})")
        finally:
            (REPO / RC).write_text(original, encoding="utf-8")

    # 1. A rule that declines every rail must be named as inert.
    def _inert():
        out = run()
        ran, skipped = counts(out)
        named = "RC012" in out and "checked nothing this run" in out
        return (named and skipped == skipped0 + n_rails), \
               f"skipped {skipped} (was {skipped0}, +{n_rails} rails); RC012 inert: {named}"

    check("RC012 skips every rail",
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

    check("RC027's skip sentinel is deleted",
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

    check("a scoped run must not cry wolf",
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
