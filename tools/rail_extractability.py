"""Can each rail be REMOVED cleanly? An empirical modularity test.

    python tools/rail_extractability.py                 # test a representative sample
    python tools/rail_extractability.py --all           # every rail (slower)
    python tools/rail_extractability.py --rail finance

Static analysis can tell you a rail *looks* independent. This answers the operational question
instead: take the rail out, and does the platform still hold together? A rail that cannot be
removed without breaking the shell or the gateway is not a separable component, whatever its
import graph says.

It reuses tools/publish.py, which already does exactly this for the five withheld rails — the
de-wiring plus the gates that prove the result is coherent. Here the withheld set is one rail at a
time, so a failure names precisely which rail is welded in and where.

WHAT "REMOVABLE" MEANS HERE, and what it does not
-------------------------------------------------
PASS means: with the rail's directory and every registry entry gone, the gateway still imports and
agrees with the allowlist, the registries are structurally sane, the shell's three federation
surfaces still agree, and the rail contract still passes for everything left.

It does NOT mean the rail would run standalone in its own repository — that is a different
question (build context depth, `../../../web/src`, its Dockerfile's assumptions). Removability is
the necessary half: a rail nothing else depends on can be lifted; one that fails here cannot,
regardless of how tidy its own tree is.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# A spread rather than all 14 by default: the simplest rail, two recent imports, the multi-app one,
# and one with a data volume. Enough to find systemic coupling without 14 full builds.
SAMPLE = ["terminal-fun", "gemini-cx", "edu-suite", "recipe-book"]


def load_publish():
    spec = importlib.util.spec_from_file_location("publish", REPO / "tools" / "publish.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rail(pub, rail: str, out: Path) -> tuple[bool, list[str]]:
    """Build a snapshot with ONLY `rail` withheld, and run the coherence gates."""
    cfg = pub.load_config()
    manifest = REPO / "rails" / rail / "rail.json"
    if not manifest.is_file():
        return False, [f"no manifest at rails/{rail}/rail.json"]

    # Withhold exactly this rail; keep every other rail that is normally published, plus the
    # normally-withheld ones plus this one's siblings — the point is to isolate ONE removal.
    keep = [d.name for d in sorted((REPO / "rails").iterdir())
            if d.is_dir() and (d / "rail.json").is_file() and d.name != rail]
    cfg["rails"] = keep
    cfg["rails_excluded_why"] = {rail: "isolated for the extractability test"}
    # The published README is written for a fixed rail set, so skip the override here: this test
    # is about wiring coherence, not documentation accuracy.
    cfg["file_overrides"] = {}
    cfg["structural"]["files"] = [f for f in cfg["structural"]["files"] if f != "README.md"]

    files, _ = pub.select(cfg, pub.tracked_files())
    if out.exists():
        shutil.rmtree(out)
    pub.build(cfg, files, out)
    ids = pub.withheld_ids(cfg)
    pub.dewire(cfg, out, ids)

    problems: list[str] = []
    hard, _soft = pub.dangling_scan(cfg, out)
    if hard:
        files_hit = sorted({h.split(":")[0] for h in hard})
        problems.append(f"{len(hard)} structural reference(s) survive in: {', '.join(files_hit[:4])}")
    problems += pub.sanity_scan(out)
    problems += pub.import_probe(cfg, out)
    problems += pub.shell_scan(out)
    ok, report = pub.conformance(out)
    if not ok:
        tail = [l for l in report.splitlines() if l.startswith(("FAIL", "WARN"))][:3]
        problems.append("contract: " + ("; ".join(tail) if tail else "failed"))
    return not problems, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="test every rail, not the sample")
    ap.add_argument("--rail", help="test a single rail")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / ".extract-test")
    args = ap.parse_args()

    pub = load_publish()
    if args.rail:
        rails = [args.rail]
    elif args.all:
        rails = [d.name for d in sorted((REPO / "rails").iterdir())
                 if d.is_dir() and (d / "rail.json").is_file()]
    else:
        rails = SAMPLE

    print(f"testing removability of {len(rails)} rail(s)\n")
    results = []
    for r in rails:
        ok, problems = test_rail(pub, r, args.out / r)
        results.append((r, ok, problems))
        print(f"  {'PASS' if ok else 'FAIL'}  {r}")
        for p in problems[:4]:
            print(f"        {p}")
    if args.out.exists():
        shutil.rmtree(args.out, ignore_errors=True)

    bad = [r for r, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} rails remove cleanly.")
    if bad:
        print("welded in: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
