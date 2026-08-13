#!/usr/bin/env python3
"""Keep the Co-Worker inbox bounded: archive aged items, then expire the archive.

Two tiers, deliberately separate:

    inbox/*.json          the DASHBOARD window  - short, actionable now
      | ACTIVE_DAYS
      v
    inbox/archive/*.json  the HISTORY window    - long, feeds trend deltas
      | RETENTION_DAYS
      v
    deleted

Narrative markdown (inbox/<source>/*.md) is swept on the SAME retention numbers, but with
only one tier. It never moves to archive/: it already lives in a subfolder, so the backend's
flat `inbox/*.json` glob cannot see it, and relocating it would break the `doc` paths that
drive drill-through for no benefit. It is simply deleted once past RETENTION_DAYS - unless a
live item still references it, which is always checked first.

The dashboard globs `inbox/*.json` flat, so tier 1 is what controls how many cards
render. History has to live much longer than that for the insights loop to compute
week-over-week change, but it must not live forever or the folder becomes clutter
nobody prunes. Hence two thresholds instead of one.

    python rails/co-worker/tools/prune_inbox.py --dry-run
    python rails/co-worker/tools/prune_inbox.py
    python rails/co-worker/tools/prune_inbox.py --today 2026-09-01   # test a future date
    python rails/co-worker/tools/prune_inbox.py --no-expire          # archive only, never delete

Exit codes: 0 = ok, 2 = inbox missing.

Safe to run repeatedly and concurrently with a harvest loop: it only ever acts on
items whose period is already outside the window a running loop would be writing.

Also garbage-collects .state.json entries whose items no longer exist anywhere, so
triage state doesn't grow forever.

No third-party dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# Tier 1 - how long an item stays visible on the dashboard (flat in inbox/).
# Keep these SHORT. This is the number that decides whether the grid is readable.
ACTIVE_DAYS = {
    "email": 7,        # a week of email backlog is the actionable horizon
    "calendar": 7,     # last week's meetings have already happened
    "teams": 14,       # dangling commitments deserve a rollover week
    "insights": 14,    # current + previous synthesis
}

# Tier 2 - how long an item survives in inbox/archive/ before deletion.
# This is the real data-retention policy. Long enough for trend analysis, finite.
RETENTION_DAYS = {
    "email": 30,       # daily loop -> 30 days of history
    "calendar": 182,   # weekly loop -> 26 weeks
    "teams": 182,      # weekly loop -> 26 weeks
    "insights": 182,   # weekly loop -> 26 weeks
}

# Triaged items leave the dashboard early regardless of source - they are done.
RESOLVED_ACTIVE_DAYS = 7

STATE_FILE = ".state.json"
ARCHIVE = "archive"

PERIOD_WEEK = re.compile(r"^(\d{4})W(\d{2})$")
PERIOD_DAY = re.compile(r"^(\d{8})$")

# Every brief is named with a leading ISO date: 2026-08-12.md, 2026-08-10-week.md,
# 2026-08-10-30day.md, 2026-08-10-synthesis.md
MD_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def period_to_date(period: str) -> dt.date | None:
    """Period -> the date it represents. Weeks resolve to their Monday."""
    m = PERIOD_DAY.match(period or "")
    if m:
        try:
            return dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            return None
    m = PERIOD_WEEK.match(period or "")
    if m:
        try:
            return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    return None


def load_state(inbox: Path) -> dict[str, str]:
    p = inbox / STATE_FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_meta(f: Path) -> tuple[str, dt.date] | None:
    """(source, reference date) for an item, or None if it can't be dated."""
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None
    except Exception:
        return None
    ref = period_to_date(str(d.get("period", "")))
    if ref is None:
        return None
    return str(d.get("source", "")), ref


def referenced_docs(inbox: Path) -> set[str]:
    """Every `doc` path pointed at by a still-existing item, active or archived.

    A brief that something still references is never deleted, however old it looks -
    a dead drill-through link is worse than a stale file.
    """
    refs: set[str] = set()
    dirs = [inbox]
    if (inbox / ARCHIVE).is_dir():
        dirs.append(inbox / ARCHIVE)
    for d in dirs:
        for f in d.glob("*.json"):
            if f.name.startswith("."):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("doc"):
                refs.add(str(data["doc"]).replace("\\", "/"))
    return refs


def sweep_markdown(inbox: Path, today: dt.date, dry_run: bool,
                   skipped: list[str]) -> tuple[list[tuple[Path, str]], int]:
    """Expire narrative briefs past their source's retention window."""
    refs = referenced_docs(inbox)
    doomed: list[tuple[Path, str]] = []
    kept = 0
    for source in sorted(RETENTION_DAYS):
        d = inbox / source
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            rel = f"{source}/{f.name}"
            m = MD_DATE.match(f.name)
            if not m:
                skipped.append(f"{rel} (no leading ISO date - kept)")
                kept += 1
                continue
            try:
                ref = dt.date.fromisoformat(m.group(1))
            except ValueError:
                skipped.append(f"{rel} (unparseable date - kept)")
                kept += 1
                continue
            age = (today - ref).days
            limit = RETENTION_DAYS[source]
            if age <= limit:
                kept += 1
            elif rel in refs:
                skipped.append(f"{rel} ({age}d old but still referenced by a live item - kept)")
                kept += 1
            else:
                doomed.append((f, f"{source} brief {age}d old (retention {limit}d)"))
    return doomed, kept


def prune(inbox: Path, today: dt.date, dry_run: bool, verbose: bool, expire: bool) -> int:
    if not inbox.is_dir():
        print(f"inbox not found: {inbox}", file=sys.stderr)
        return 2

    archive = inbox / ARCHIVE
    state = load_state(inbox)

    to_archive: list[tuple[Path, str]] = []
    to_delete: list[tuple[Path, str]] = []
    skipped: list[str] = []
    active = 0
    retained = 0

    # --- tier 1: inbox/ -> archive/ ---------------------------------------
    for f in sorted(p for p in inbox.glob("*.json") if not p.name.startswith(".")):
        meta = read_meta(f)
        if meta is None:
            # Malformed or undated files are validate_inbox.py's problem. Leaving them
            # in place keeps them visible instead of quietly burying the evidence.
            skipped.append(f"{f.name} (unparseable or no usable period - left in place)")
            active += 1
            continue

        source, ref = meta
        age = (today - ref).days
        status = state.get(f.stem, "open")

        if status in ("done", "dismissed") and age >= RESOLVED_ACTIVE_DAYS:
            to_archive.append((f, f"{status} and {age}d old"))
            continue

        limit = ACTIVE_DAYS.get(source)
        if limit is None:
            skipped.append(f"{f.name} (unknown source {source!r} - left in place)")
            active += 1
            continue

        if age > limit:
            to_archive.append((f, f"{source} item {age}d old (dashboard window {limit}d)"))
        else:
            active += 1

    # --- tier 2: archive/ -> gone -----------------------------------------
    if archive.is_dir():
        for f in sorted(archive.glob("*.json")):
            meta = read_meta(f)
            if meta is None:
                skipped.append(f"archive/{f.name} (no usable period - kept)")
                retained += 1
                continue
            source, ref = meta
            age = (today - ref).days
            limit = RETENTION_DAYS.get(source)
            if limit is None:
                skipped.append(f"archive/{f.name} (unknown source {source!r} - kept)")
                retained += 1
            elif age > limit:
                to_delete.append((f, f"{source} item {age}d old (retention {limit}d)"))
            else:
                retained += 1

    # --- markdown briefs ---------------------------------------------------
    md_doomed, md_kept = sweep_markdown(inbox, today, dry_run, skipped)

    # --- report + act ------------------------------------------------------
    print(f"inbox   : {inbox}")
    print(f"today   : {today}")
    print(f"active  : {active} on the dashboard   -> archiving {len(to_archive)}")
    print(f"archived: {retained} in history        -> {'expiring' if expire else 'expiring (disabled):'} {len(to_delete)}")
    print(f"briefs  : {md_kept} narrative .md       -> {'expiring' if expire else 'expiring (disabled):'} {len(md_doomed)}")

    if skipped and verbose:
        print()
        for s in skipped:
            print(f"  SKIP {s}")

    if to_archive:
        print()
        for f, reason in to_archive:
            print(f"  {'would archive' if dry_run else 'archive'} {f.name}  <- {reason}")
    if to_delete and expire:
        print()
        for f, reason in to_delete:
            print(f"  {'would delete ' if dry_run else 'delete '} archive/{f.name}  <- {reason}")
    if md_doomed and expire:
        print()
        for f, reason in md_doomed:
            print(f"  {'would delete ' if dry_run else 'delete '} {f.parent.name}/{f.name}  <- {reason}")

    if not dry_run:
        if to_archive:
            archive.mkdir(parents=True, exist_ok=True)
            for f, _ in to_archive:
                dest = archive / f.name
                if dest.exists():
                    dest.unlink()  # same deterministic id = same finding; keep the newer file
                f.rename(dest)
        if expire:
            for f, _ in to_delete:
                f.unlink()
            for f, _ in md_doomed:
                f.unlink()

    # --- garbage-collect triage state -------------------------------------
    if state:
        live = {p.stem for p in inbox.glob("*.json") if not p.name.startswith(".")}
        if archive.is_dir():
            live |= {p.stem for p in archive.glob("*.json")}
        stale = sorted(set(state) - live)
        if stale:
            print()
            print(f"triage state: {len(stale)} orphaned entr{'y' if len(stale) == 1 else 'ies'}"
                  f"{' would be' if dry_run else ''} removed")
            if verbose:
                for s in stale:
                    print(f"  - {s}")
            if not dry_run:
                for s in stale:
                    state.pop(s, None)
                (inbox / STATE_FILE).write_text(
                    json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
                )

    print()
    print("dry run - nothing changed" if dry_run else "done")
    return 0


def main() -> int:
    default = Path(__file__).resolve().parents[3] / "data" / "co-worker" / "inbox"
    ap = argparse.ArgumentParser(description="Archive aged Co-Worker items, then expire the archive.")
    ap.add_argument("--inbox", type=Path, default=default,
                    help=f"inbox directory (default: {default})")
    ap.add_argument("--dry-run", action="store_true", help="show what would move, change nothing")
    ap.add_argument("--today", type=str, default=None,
                    help="override today's date (YYYY-MM-DD) for testing retention")
    ap.add_argument("--verbose", action="store_true", help="list skipped and orphaned entries")
    ap.add_argument("--no-expire", action="store_true",
                    help="archive only; never delete from inbox/archive/")
    args = ap.parse_args()

    today = dt.date.today()
    if args.today:
        try:
            today = dt.datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError:
            print(f"bad --today {args.today!r}, expected YYYY-MM-DD", file=sys.stderr)
            return 2

    return prune(args.inbox, today, args.dry_run, args.verbose, expire=not args.no_expire)


if __name__ == "__main__":
    sys.exit(main())
