#!/usr/bin/env python3
"""Validate Co-Worker inbox items against SCHEMA.md (v2).

Why this exists: the backend deliberately skips malformed files with only a log warning,
so a broken item disappears from the dashboard silently. This turns that into a loud,
scriptable failure. The harvest process is instructed to run it after every write.

    python rails/co-worker/tools/validate_inbox.py
    python rails/co-worker/tools/validate_inbox.py --inbox /path/to/inbox
    python rails/co-worker/tools/validate_inbox.py --quiet   # only errors

Exit codes: 0 = clean (warnings allowed), 1 = errors found, 2 = inbox missing.

No third-party dependencies, and no tzdata reliance — US Pacific DST is computed from
the calendar rules so this runs anywhere, including a bare slim container.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 2

SOURCES = {"calendar", "email", "teams", "insights"}
TYPES = {
    "meeting", "agenda-draft", "conflict", "prep", "email", "dangling",
    "follow-up", "reminder", "fyi", "noise", "insight", "recommendation",
}
REQUIRED = [
    "schema", "type", "source", "period", "title", "why", "body",
    "priority", "client", "when", "due", "from", "run", "doc",
]
# Optional structured fields (v2.2). All additive — an item written before these existed
# stays valid, so no rewrite is ever forced by adding one.
REL_TYPES = {"relates-to", "answers", "derives-from", "duplicates", "supersedes",
             "retracts", "blocks"}
CONFIDENCE = {"high", "medium", "low"}
VERDICTS = {"take", "drop", "defer", "delegate"}
# v2.3
VERIFICATION = {"full-read", "summary", "inferred"}
DIRECTIONS = {"up-good", "down-good", "neutral"}
RECURRENCE = {"daily", "weekly", "biweekly", "monthly", "irregular"}
# v2.4
METRIC_KINDS = {"movement", "correction"}

ISO_OFFSET = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}([+-]\d{2}):(\d{2})$")
GRAPH_ID = re.compile(r"^AA[A-Za-z0-9+/=_%-]{20,}$")
PERIOD_WEEK = re.compile(r"^(\d{4})W(\d{2})$")
PERIOD_DAY = re.compile(r"^(\d{8})$")
STEM_OK = re.compile(r"^[A-Za-z0-9_-]+$")

# Which period grain each source keys on. This is the anti-duplication mechanism:
# a loop that runs N times within its own period rewrites the same ids every time.
# `teams` is weekly (not daily) because its 30-day rolling window re-surfaces the
# same dangling commitments - a per-day key would duplicate them on every run.
WEEK_SOURCES = {"calendar", "insights", "teams"}
DAY_SOURCES = {"email"}


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th `weekday` (Mon=0) of a month, e.g. 2nd Sunday of March."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def pacific_offset(d: dt.date) -> str:
    """US Pacific UTC offset for a date. PDT from 2nd Sun Mar to 1st Sun Nov."""
    start = nth_weekday(d.year, 3, 6, 2)   # 2nd Sunday of March
    end = nth_weekday(d.year, 11, 6, 1)    # 1st Sunday of November
    return "-07" if start <= d < end else "-08"


def period_to_date(period: str) -> dt.date | None:
    m = PERIOD_DAY.match(period)
    if m:
        try:
            return dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            return None
    m = PERIOD_WEEK.match(period)
    if m:
        try:
            return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    return None


def validate(inbox: Path, quiet: bool = False) -> int:
    if not inbox.is_dir():
        print(f"inbox not found: {inbox}", file=sys.stderr)
        return 2

    # Exclude dotfiles (.state.json) and the synthesis output (brief.json is not an item).
    files = sorted(p for p in inbox.glob("*.json")
                   if not p.name.startswith(".") and p.name != "brief.json")
    errors: list[str] = []
    warnings: list[str] = []

    parsed: dict[str, dict] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{f.name}: UNPARSEABLE — the backend would silently skip this ({exc})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{f.name}: top level must be a JSON object")
            continue
        parsed[f.stem] = data

    ids = set(parsed)

    for stem, d in sorted(parsed.items()):
        def err(msg: str) -> None:
            errors.append(f"{stem}: {msg}")

        def warn(msg: str) -> None:
            warnings.append(f"{stem}: {msg}")

        # --- filename -----------------------------------------------------
        if not STEM_OK.match(stem):
            err("filename stem must be alphanumeric plus - and _")
        if ":" in stem:
            err("colon in filename (illegal on Windows)")

        parts = stem.split("_", 2)
        if len(parts) != 3:
            err(f"filename must be <period>_<source>_<slug>.json, got {stem!r}")
            fn_period = fn_source = None
        else:
            fn_period, fn_source, _slug = parts
            if fn_source not in SOURCES:
                err(f"filename segment 2 must be a SOURCE {sorted(SOURCES)}, got {fn_source!r}"
                    " (a common mistake is using the type here)")

        # --- required fields ----------------------------------------------
        for field in REQUIRED:
            if field not in d:
                err(f"missing required field '{field}'")

        if d.get("schema") != SCHEMA_VERSION:
            err(f"schema must be {SCHEMA_VERSION}, got {d.get('schema')!r}")

        src = d.get("source")
        if src not in SOURCES:
            err(f"bad source {src!r}")
        if d.get("type") not in TYPES:
            err(f"bad type {d.get('type')!r}")

        # --- period consistency (the idempotency mechanism) ---------------
        period = d.get("period")
        if fn_source and src and fn_source != src:
            err(f"filename source {fn_source!r} != source field {src!r}")
        if fn_period and period and fn_period != period:
            err(f"filename period {fn_period!r} != period field {period!r}")
        if isinstance(period, str):
            if src in WEEK_SOURCES and not PERIOD_WEEK.match(period):
                err(f"source {src!r} requires an ISO-week period like 2026W33, got {period!r}")
            if src in DAY_SOURCES and not PERIOD_DAY.match(period):
                err(f"source {src!r} requires a YYYYMMDD period, got {period!r}")
            if period_to_date(period) is None:
                err(f"period {period!r} is not a real date/week")

        # --- scalars ------------------------------------------------------
        p = d.get("priority")
        if not (isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 5):
            err(f"priority must be int 1-5, got {p!r}")
        if not isinstance(d.get("client"), bool):
            err(f"client must be bool, got {d.get('client')!r}")
        if not str(d.get("why") or "").strip():
            err("EMPTY why — every item requires a one-line why")
        if not str(d.get("body") or "").strip():
            err("empty body")
        title = str(d.get("title") or "")
        if not title.strip():
            err("empty title")
        elif len(title) > 90:
            warn(f"title is {len(title)} chars (aim under ~80)")

        if d.get("type") == "insight" and not str(d.get("evidence") or "").strip():
            err("an 'insight' requires evidence — otherwise it's an opinion")
        if d.get("type") == "dangling" and isinstance(p, int) and p > 3:
            err(f"a 'dangling' item may not be priority {p} (max 3 per SCHEMA.md)")

        # --- datetimes ----------------------------------------------------
        for field in ("when", "due"):
            v = d.get(field)
            if v is None:
                continue
            m = ISO_OFFSET.match(str(v))
            if not m:
                err(f"{field}={v!r} must be ISO-8601 with explicit offset, e.g. 2026-08-13T11:00:00-07:00")
                continue
            y, mo, day, off, _ = m.groups()
            try:
                expected = pacific_offset(dt.date(int(y), int(mo), int(day)))
            except ValueError:
                err(f"{field}={v!r} is not a real date")
                continue
            if off != expected:
                err(f"{field}={v!r} has offset {off}:00 but US Pacific on {y}-{mo}-{day} is {expected}:00")

        # --- collections --------------------------------------------------
        if not isinstance(d.get("tags", []), list):
            err("tags must be a list")
        for link in d.get("links") or []:
            if not isinstance(link, dict):
                err("links entries must be objects")
                continue
            if not str(link.get("label") or "").strip():
                err("a link is missing its label")
            if not str(link.get("url") or "").startswith("https://"):
                err(f"link url must be https, got {link.get('url')!r}")

        # `related` accepts a bare id (untyped) or {"id": ..., "rel": ...}. Both forms
        # coexist; a bare string stays valid so existing items never need rewriting.
        for entry in d.get("related") or []:
            if isinstance(entry, dict):
                ref = entry.get("id")
                rel = entry.get("rel", "relates-to")
                if rel not in REL_TYPES:
                    err(f"related rel {rel!r} is not one of {sorted(REL_TYPES)}")
                if not ref:
                    err("related object is missing its id")
                    continue
            else:
                ref = entry
            if GRAPH_ID.match(str(ref)):
                err(f"related contains a Microsoft Graph id ({str(ref)[:24]}…) — "
                    "use `links` for Graph webLinks; `related` takes co-worker item ids")
            elif ref not in ids:
                warn(f"related -> {ref!r} does not resolve (archived, or written by another loop)")
            elif ref == stem:
                warn("related references itself")

        # --- metrics ------------------------------------------------------
        metrics = d.get("metrics")
        if metrics is not None and not isinstance(metrics, list):
            err("metrics must be a list")
        for m in metrics or []:
            if not isinstance(m, dict):
                err("metrics entries must be objects")
                continue
            if not str(m.get("label") or "").strip():
                err("a metric is missing its label")
            v = m.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                err(f"metric {m.get('label')!r} value must be a number, got {v!r}")
            p = m.get("prev")
            if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float))):
                err(f"metric {m.get('label')!r} prev must be a number or null, got {p!r}")
            n = m.get("n")
            if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
                err(f"metric {m.get('label')!r} n must be a non-negative integer, got {n!r}")
            dirn = m.get("direction")
            if dirn is not None and dirn not in DIRECTIONS:
                err(f"metric {m.get('label')!r} direction must be one of {sorted(DIRECTIONS)}, got {dirn!r}")
            tgt = m.get("target")
            if tgt is not None and (isinstance(tgt, bool) or not isinstance(tgt, (int, float))):
                err(f"metric {m.get('label')!r} target must be a number or null, got {tgt!r}")
            mconf = m.get("confidence")
            if mconf is not None and mconf not in CONFIDENCE:
                err(f"metric {m.get('label')!r} confidence must be one of {sorted(CONFIDENCE)}, got {mconf!r}")
            kind = m.get("kind")
            if kind is not None and kind not in METRIC_KINDS:
                err(f"metric {m.get('label')!r} kind must be one of {sorted(METRIC_KINDS)}, got {kind!r}")
            mver = m.get("verification")
            if mver is not None and mver not in VERIFICATION:
                err(f"metric {m.get('label')!r} verification must be one of {sorted(VERIFICATION)}, got {mver!r}")
            # A delta the rail can't orient is a number with an arrow it must leave grey.
            # A correction is exempt: it is rendered as a restatement, not a trend.
            if m.get("prev") is not None and dirn is None and kind != "correction":
                warn(f"metric {m.get('label')!r} has prev but no direction — trend arrow can't be oriented")

        # --- verification / series (v2.3) -----------------------------------
        ver = d.get("verification")
        if ver is not None and ver not in VERIFICATION:
            err(f"verification must be one of {sorted(VERIFICATION)} or null, got {ver!r}")
        if ver in ("summary", "inferred") and not str(d.get("evidence") or "").strip():
            warn(f"verification is {ver!r} but evidence is empty — say what wasn't read")

        series = d.get("series")
        if series is not None:
            if not isinstance(series, dict):
                err("series must be an object")
            else:
                rec = series.get("recurrence")
                if rec not in RECURRENCE:
                    err(f"series recurrence must be one of {sorted(RECURRENCE)}, got {rec!r}")
                se = series.get("series_end")
                if se is not None and not ISO_OFFSET.match(str(se)):
                    err(f"series series_end must be ISO-8601 with offset, got {se!r}")
                occ = series.get("occurrences")
                if occ is not None and (isinstance(occ, bool) or not isinstance(occ, int) or occ < 0):
                    err(f"series occurrences must be a non-negative integer, got {occ!r}")

        # --- confidence ---------------------------------------------------
        conf = d.get("confidence")
        if conf is not None and conf not in CONFIDENCE:
            err(f"confidence must be one of {sorted(CONFIDENCE)} or null, got {conf!r}")

        # --- competing (conflict items) ------------------------------------
        competing = d.get("competing")
        if competing is not None and not isinstance(competing, list):
            err("competing must be a list")
        takes = 0
        for c in competing or []:
            if not isinstance(c, dict):
                err("competing entries must be objects")
                continue
            if not str(c.get("label") or "").strip():
                err("a competing entry is missing its label")
            verdict = c.get("verdict")
            if verdict is not None and verdict not in VERDICTS:
                err(f"competing verdict must be one of {sorted(VERDICTS)} or null, got {verdict!r}")
            if verdict == "take":
                takes += 1
            cref = c.get("ref")
            if cref and GRAPH_ID.match(str(cref)):
                err(f"competing ref is a Graph id ({str(cref)[:24]}…) — use a co-worker item id")
            elif cref and cref not in ids:
                warn(f"competing ref -> {cref!r} does not resolve")
            for k in ("start", "end"):
                if c.get(k) and not ISO_OFFSET.match(str(c[k])):
                    err(f"competing {k} must be ISO-8601 with offset, got {c[k]!r}")
        if competing:
            if takes == 0:
                warn("competing has no entry marked verdict 'take' — no winner declared")
            elif takes > 1:
                err(f"competing has {takes} entries marked 'take'; exactly one should win")
        if d.get("type") == "conflict" and not competing:
            warn("conflict item has no `competing` — the colliding events are prose only")

        # Cross-source edges are allowed from any loop, but they must resolve. A loop can
        # only construct another loop's id by reading the inbox first, so an unresolved
        # cross-source ref means it guessed. (The unresolved case is already warned above.)

        doc = d.get("doc")
        if doc is not None:
            if not str(doc).endswith((".md", ".markdown")):
                err(f"doc must point at a markdown file, got {doc!r}")
            elif not (inbox / str(doc)).is_file():
                warn(f"doc {doc!r} does not exist on disk — drill-through will 404")

    # --- cross-item checks ------------------------------------------------
    seen: dict[str, str] = {}
    for stem, d in sorted(parsed.items()):
        key = str(d.get("title", "")).strip().lower()
        if not key:
            continue
        if key in seen:
            warnings.append(f"{stem}: duplicate title with {seen[key]} — possible un-deduplicated rerun")
        else:
            seen[key] = stem

    # --- report -----------------------------------------------------------
    if not quiet:
        print(f"inbox   : {inbox}")
        print(f"files   : {len(files)}  parsed: {len(parsed)}")
        by_src: dict[str, int] = {}
        for d in parsed.values():
            by_src[str(d.get("source"))] = by_src.get(str(d.get("source")), 0) + 1
        print(f"sources : {dict(sorted(by_src.items()))}")
        archive = inbox / "archive"
        if archive.is_dir():
            print(f"archived: {len(list(archive.glob('*.json')))}")
        print()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    if not quiet or errors:
        print()
        print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
        if not errors:
            print("VALID")

    return 1 if errors else 0


def main() -> int:
    default = Path(__file__).resolve().parents[3] / "data" / "co-worker" / "inbox"
    ap = argparse.ArgumentParser(description="Validate Co-Worker inbox items (SCHEMA.md v2).")
    ap.add_argument("--inbox", type=Path, default=default,
                    help=f"inbox directory (default: {default})")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args()
    return validate(args.inbox, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
