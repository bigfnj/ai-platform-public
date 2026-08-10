"""Scan the shared curriculum content pool and emit a manifest.

Walks ``<root>/<grade>/<unit>/Week N/*.pdf`` (grade folder optional), classifies
each PDF by subject and type from filename heuristics, and writes
``content/manifest.json``. This is the suite's single inventory of source
material — the "auto-import new units" automation the teachtown project wanted:
drop a unit into content/, re-run this, and every app can see what's available.

Usage (from the repo root):
    python scripts/ingest_content.py                # scan content/
    python scripts/ingest_content.py --root <dir>   # scan elsewhere
    python scripts/ingest_content.py --print        # print summary, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> edu-suite
_CONTENT = _ROOT / "content"
_WEEK_RE = re.compile(r"week\s*0*(\d+)", re.IGNORECASE)
_GRADES = {"middle-school", "high-school"}

# Subject heuristics, checked in order (first hit wins).
_SUBJECT_RULES = [
    ("Math", ("math", "warm up", "warmup", "warm-up", "area", "coordinate")),
    ("Science", ("science", "food and water", "nutrient", "molecule")),
    ("Social Studies", ("social", "imperialism", "nationalism", "unif", "history")),
    ("ELA", ("ela", "reading", "comp", "context", "suffix", "hyperbole",
             "vocab", "writing", "story", "matching")),
]

# Type heuristics, checked in order.
_TYPE_RULES = [
    ("answer-key", ("answer", "key")),
    ("teacher-guide", ("teacher", "guide", "lesson plan")),
    ("warmup", ("warm up", "warmup", "warm-up")),
    ("reading", ("reading", "comp", "r. comp", "context clues")),
    ("companion", ("companion", "story", "text")),
]


def _match(name: str, rules) -> str | None:
    low = name.lower()
    for label, keys in rules:
        if any(k in low for k in keys):
            return label
    return None


def classify_file(path: Path) -> dict:
    name = path.name
    return {
        "file": name,
        "subject": _match(name, _SUBJECT_RULES) or "Unknown",
        "type": _match(name, _TYPE_RULES) or "worksheet",
    }


def _week_of(folder_name: str) -> str | None:
    m = _WEEK_RE.search(folder_name)
    return m.group(1) if m else None


def _scan_unit(unit_dir: Path, grade: str | None, root: Path) -> dict:
    weeks: dict[str, list] = {}
    loose: list = []
    for pdf in sorted(unit_dir.rglob("*.pdf")):
        rel = pdf.relative_to(root).as_posix()
        info = classify_file(pdf)
        info["path"] = rel
        # Which "Week N" folder (if any) is this file under, relative to the unit?
        week = None
        for part in pdf.relative_to(unit_dir).parts[:-1]:
            w = _week_of(part)
            if w:
                week = w
                break
        if week:
            weeks.setdefault(week, []).append(info)
        else:
            loose.append(info)
    return {
        "unit": unit_dir.name,
        "grade": grade,
        "path": unit_dir.relative_to(root).as_posix(),
        "weeks": dict(sorted(weeks.items(), key=lambda kv: int(kv[0]))),
        "loose": loose,
    }


def _iter_units(root: Path):
    """Yield (unit_dir, grade). Supports <root>/<grade>/<unit> and <root>/<unit>."""
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if child.name in _GRADES:
            for unit in sorted(p for p in child.iterdir() if p.is_dir()):
                yield unit, child.name
        else:
            yield child, None


def build_manifest(root: Path) -> dict:
    units = []
    by_subject: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_files = 0
    for unit_dir, grade in _iter_units(root):
        u = _scan_unit(unit_dir, grade, root)
        n = sum(len(v) for v in u["weeks"].values()) + len(u["loose"])
        if n == 0:
            continue  # skip non-content dirs (e.g. an images/ folder)
        units.append(u)
        for group in list(u["weeks"].values()) + [u["loose"]]:
            for f in group:
                total_files += 1
                by_subject[f["subject"]] = by_subject.get(f["subject"], 0) + 1
                by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": root.relative_to(_ROOT).as_posix() if root.is_relative_to(_ROOT) else str(root),
        "units": units,
        "summary": {
            "units": len(units),
            "files": total_files,
            "by_subject": dict(sorted(by_subject.items())),
            "by_type": dict(sorted(by_type.items())),
        },
    }


def print_summary(manifest: dict) -> None:
    s = manifest["summary"]
    print(f"Scanned {manifest['root']} — {s['units']} unit(s), {s['files']} file(s)")
    for u in manifest["units"]:
        g = f"[{u['grade']}] " if u["grade"] else ""
        weeks = ", ".join(f"wk{w}({len(f)})" for w, f in u["weeks"].items())
        extra = f" +{len(u['loose'])} loose" if u["loose"] else ""
        print(f"  {g}{u['unit']}: {weeks or 'no weeks'}{extra}")
    print(f"  by subject: {s['by_subject']}")
    print(f"  by type:    {s['by_type']}")


def main() -> None:
    p = argparse.ArgumentParser(description="Scan the content pool and emit a manifest")
    p.add_argument("--root", type=Path, default=_CONTENT, help="Content root (default: content/)")
    p.add_argument("--out", type=Path, default=None, help="Manifest path (default: <root>/manifest.json)")
    p.add_argument("--print", dest="print_only", action="store_true", help="Print summary, write nothing")
    args = p.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"content root not found: {root}")

    manifest = build_manifest(root)
    print_summary(manifest)

    if not args.print_only:
        out = args.out or (root / "manifest.json")
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
