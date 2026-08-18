"""Executive brief synthesis for the Co-Worker rail.

Reads every unresolved inbox item, calls the broker with a curated synthesis
prompt, and atomically writes inbox/brief.json.

This module is called by the /api/brief/refresh endpoint and can also be run
as a standalone script:

    python -m co_worker_app.synthesize --inbox /data/inbox
    python -m co_worker_app.synthesize --dry-run   # print prompt, no write

One synthesis run at a time is enforced via a threading.Lock. The endpoint
returns immediately; the caller polls /api/brief/status.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from co_worker_app.atomicio import write_json

_log = logging.getLogger("co-worker.synthesize")

BROKER_ROLE = "co-worker-synthesis"
BRIEF_FILE = "brief.json"
STATE_FILE = ".state.json"

# The sources a per-source pass is run for. A combined pass over every unresolved item
# does not fit: at ~450 chars/item, 200+ items is ~23K tokens, and a 4b model reading
# 23K tokens attends to maybe a third of it. Split by source and each lane lands at
# ~8K tokens, inside the range where the model actually reads everything it is given.
SOURCES = ("email", "calendar", "teams")

# Context budget for the items payload, in characters (~4 chars/token).
# The local role is a small model on a modest GPU: a full week of items with bodies
# runs ~245K chars (~61K tokens), which silently truncates and yields an empty brief.
# So we drop bodies, clip prose, and cut lowest-value items first until we fit.
#
# This is deliberately well under NUM_CTX. The binding constraint is not what the model
# can hold, it is what it can still reason over — raising this to "fit everything" trades
# a visible truncation notice for invisible inattention, which is strictly worse.
ITEMS_CHAR_BUDGET = 48_000
WHY_CLIP = 220
TITLE_CLIP = 160
NUM_CTX = 32_768

# Merge ordering for the combined brief: the same precedence the prompt asks for.
_URGENCY_RANK = {"today": 0, "this-week": 1, "soon": 2}
_CATEGORY_RANK = {"client": 0, "dangling": 1, "missed": 2, "agenda-gap": 3, "other": 4}


def brief_filename(source: str | None) -> str:
    """brief.json for the combined pass, brief.<source>.json for a single lane."""
    return BRIEF_FILE if not source else f"brief.{source}.json"


def is_brief_file(name: str) -> bool:
    """True for brief.json and every brief.<source>.json.

    Synthesis output lives beside the items it summarises, so every glob over the
    inbox has to exclude it or the brief renders as a phantom item card — and, worse,
    gets fed back into the next synthesis pass as input.
    """
    return name == BRIEF_FILE or (name.startswith("brief.") and name.endswith(".json"))

# --- concurrency guard -------------------------------------------------------

_lock = threading.Lock()
_running = False
_last_started: float = 0.0
_last_finished: float = 0.0
_last_error: str = ""


def get_status() -> dict[str, Any]:
    return {
        "running": _running,
        "last_started": _last_started or None,
        "last_finished": _last_finished or None,
        "last_error": _last_error or None,
    }


# --- prompts -----------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior executive assistant for a Principal-level management consultant \
at a major professional services firm. Your client is in active delivery across \
multiple engagements. Client relationship and revenue risk rank above everything else.

Your job: synthesize this week's email, calendar, and Teams data into a concise \
executive brief — only what needs the consultant's attention, in order of importance. \
Be ruthless. A false positive wastes executive attention, which is the resource being protected.

Output ONLY valid JSON matching the schema provided. No prose outside the JSON object. \
No markdown code fences. If you cannot produce valid JSON, return {"error": "synthesis failed"}.\
"""


def _is_self(from_val: Any, user_name: str, user_email: str) -> bool:
    """True when the item's `from` field refers to the inbox owner."""
    if not (user_name or user_email):
        return False
    s = str(from_val or "").lower().strip()
    if not s:
        return False
    if user_name and user_name.lower() in s:
        return True
    if user_email and user_email.lower() in s:
        return True
    return False


def _triage_sort_key(item: dict) -> tuple:
    """Cut order when we don't fit: keep clients, then real priorities, then recency.

    Noise and FYIs go first — they were never going to make the attention list, and
    spending context on them is what pushes a genuine client thread out of the window.
    """
    noise = 1 if item.get("type") in ("noise", "fyi") else 0
    client = 0 if item.get("client") else 1
    prio = item.get("priority")
    prio = prio if isinstance(prio, (int, float)) else 9
    mtime = item.get("_mtime") or 0
    return (noise, client, prio, -float(mtime))


def _condense(
    items: list[dict],
    state: dict[str, str],
    today: "date | None" = None,
    source: str | None = None,
    user_name: str = "",
    user_email: str = "",
) -> tuple[list[dict], dict[int, str], int]:
    """Unresolved items, numbered 1..N, stripped to decision-relevant fields, within budget.

    Returns (payload_rows, idx_to_id, n_eligible) where payload_rows carry integer _idx
    instead of _id — the model emits a number, Python maps it back. Eliminates
    verbatim-copy errors that cause unresolved_id warnings in the UI.

    `n_eligible` is the count that survived the hard filters, BEFORE the context budget
    was applied. The caller needs both numbers to tell two very different things apart:
    items excluded on purpose (noise/FYI/stale) versus items the model never got to see
    because it ran out of room. Only the second is a limitation worth reporting.

    `source` narrows to one lane (email/calendar/teams). Each lane is numbered from 1
    independently; ids are resolved back to real _ids before any merge, so the numbering
    never collides across passes.

    Hard-filtered before the model sees anything (not just deprioritised):
    - noise / fyi items
    - non-client items whose `when` date is more than 14 days ago
    """
    KEEP = ("type", "source", "priority", "title", "why", "from",
            "when", "due", "client", "period")

    def _age_days(item: dict) -> int:
        if today is None:
            return 0
        w = item.get("when")
        if not w:
            return 0
        try:
            d = date.fromisoformat(str(w)[:10])
            return max(0, (today - d).days)
        except Exception:
            return 0

    live = [
        item for item in items
        if state.get(str(item.get("_id", "")), "open") not in ("done", "dismissed")
        and item.get("type") not in ("noise", "fyi")
        and (item.get("client") or _age_days(item) <= 14)
        and (source is None or str(item.get("source") or "") == source)
    ]
    live.sort(key=_triage_sort_key)

    out: list[dict] = []
    idx_to_id: dict[int, str] = {}
    used = 0
    for idx, item in enumerate(live, start=1):
        row: dict = {"_idx": idx}
        row.update({k: item[k] for k in KEEP if k in item and item[k] is not None})
        # Replace user's own name/email with "(self)" so the model never generates
        # "Reply to [user]" attention items for the user's own sent messages or notes.
        if _is_self(row.get("from"), user_name, user_email):
            row["from"] = "(self)"
        if isinstance(row.get("why"), str) and len(row["why"]) > WHY_CLIP:
            row["why"] = row["why"][:WHY_CLIP] + "…"
        if isinstance(row.get("title"), str) and len(row["title"]) > TITLE_CLIP:
            row["title"] = row["title"][:TITLE_CLIP] + "…"
        cost = len(json.dumps(row, separators=(",", ":"), default=str))
        if used + cost > ITEMS_CHAR_BUDGET:
            break
        out.append(row)
        idx_to_id[idx] = str(item.get("_id", ""))
        used += cost

    if len(out) < len(live):
        _log.warning(
            "synthesis%s: context budget trimmed to %d of %d eligible items "
            "(lowest-priority dropped first)",
            f" [{source}]" if source else "", len(out), len(live),
        )
    return out, idx_to_id, len(live)


_SOURCE_LENS = {
    "email": "This pass covers EMAIL ONLY. Focus on unanswered threads, commitments made "
             "in replies, and senders left waiting.",
    "calendar": "This pass covers CALENDAR ONLY. Focus on meetings you organised without "
                "an agenda, conflicts, and prep that has not happened.",
    "teams": "This pass covers TEAMS CHATS ONLY. Focus on direct asks left hanging, "
             "decisions taken in chat with no follow-through, and people awaiting a reply.",
}


def _build_prompt(
    n_items: int,
    n_triaged: int,
    payload: list[dict],
    now_iso: str,
    today: str,
    period: str,
    source: str | None = None,
) -> str:
    schema_example = json.dumps({
        "generated": now_iso,
        "period": period,
        "items_considered": n_items,
        "items_triaged": n_triaged,
        "attention": [
            {
                "id": "<INTEGER _idx FROM ITEM LIST>",
                "category": "<client|dangling|missed|agenda-gap|other>",
                "headline": "<DIRECT ACTION: verb + who + what + why urgent>",
                "urgency": "<today|this-week|soon>",
                "why": "<ONE SENTENCE: consequence of not acting>",
            }
        ],
        "client_pulse": "<2-3 SENTENCES: overall state of client threads>",
        "dangling": ["<SPECIFIC COMMITMENT with no resolution signal>"],
        "missed": ["<PERSON (role) — topic, last contact date>"],
        "agenda_gaps": ["<MEETING NAME date — you organised it, no agenda>"],
        "suppressed": 0,
        "synthesis_note": None,
    }, indent=2)

    items_block = json.dumps(payload, separators=(",", ":"), default=str)
    lens = _SOURCE_LENS.get(source or "", "")
    lens_block = f"\n{lens}\n" if lens else ""

    return f"""\
Today is {today}. Period: {period}.
{lens_block}
{n_items} items harvested. {n_triaged} already triaged (done/dismissed). \
Items below are the unresolved set after pre-filtering: noise, FYIs, and \
non-client items older than 14 days have been removed.

Produce a JSON executive brief matching this schema EXACTLY.
CRITICAL: The schema below shows STRUCTURE ONLY. Every <...> placeholder \
and quoted string is a template — replace ALL of them with real content \
from the inbox items. Never copy schema text into your output.

{schema_example}

Field rules:
- "id" must be the INTEGER _idx from the item list below (e.g. 3, 17, 42)
- "category": exactly one of client | dangling | missed | agenda-gap | other
- "urgency": exactly one of today | this-week | soon
- "headline": a direct instruction — verb + who + what + why urgent
  EXAMPLE PATTERN: "Reply to [name] re: [topic] — [urgency signal]"
  NOT a description: never "Email from X about Y"
  SELF-AUTHORED: items where from="(self)" are the user's own sent messages, events, or notes — \
never generate a "Reply to yourself" action for these; pick a different verb (Follow up / Add agenda / Resolve)

Attention list: include {"3–6" if source else "5–10"} items where inaction costs \
something THIS WEEK. Prioritise client > dangling > missed > agenda-gap > other.

Unresolved inbox items ({len(payload)} items):
{items_block}"""


# --- broker call -------------------------------------------------------------

def _call_broker(prompt: str, broker_url: str, auth_token: str) -> str:
    """POST /v1/chat. The broker is Ollama-native, NOT OpenAI-compatible:
    sampling params live under `options` (num_predict, not max_tokens), the role
    needs an `@` prefix to resolve via roles.json, and the reply is Ollama's
    {"message": {"content": ...}} shape. `format: json` constrains decoding to
    valid JSON, which matters a lot on a small local model."""
    url = f"{broker_url.rstrip('/')}/v1/chat"
    payload = json.dumps({
        "model": f"@{BROKER_ROLE}",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "format": "json",
        "options": {
            "temperature": 0.15,
            "num_predict": 2048,
            "num_ctx": NUM_CTX,
        },
    }).encode("utf-8")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"broker HTTP {exc.code}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"broker unreachable at {url}: {exc.reason}") from exc

    content = (data.get("message") or {}).get("content", "") or ""
    if not content.strip():
        raise RuntimeError(f"broker returned no content: {str(data)[:300]}")
    return content


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of model output, stripping code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(lines[1:])
        if inner.rstrip().endswith("```"):
            inner = inner.rstrip()[:-3].rstrip()
        text = inner
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"no JSON object found in model output: {text[:200]!r}")
    return json.loads(text[start:end])


# --- item loading ------------------------------------------------------------

CATEGORIES = ("client", "dangling", "missed", "agenda-gap", "other")
URGENCIES = ("today", "this-week", "soon")


def _pick_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """Coerce a model-supplied enum to a legal value.

    Small models like to echo the schema placeholder back ("client|dangling|missed"),
    which would render as a garbage chip. Take the first legal token we recognise.
    """
    s = str(value or "").strip().lower()
    if s in allowed:
        return s
    for a in allowed:                      # "client|dangling" -> "client"
        if a in s:
            return a
    return default


def _normalize(brief: dict, idx_to_id: dict[int, str]) -> dict:
    """Make the model's output safe to render. Resolves integer _idx back to item _id."""
    valid_ids = set(idx_to_id.values())
    clean: list[dict] = []
    dropped = 0
    for raw in brief.get("attention") or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        headline = str(raw.get("headline") or "").strip()
        if not headline:
            dropped += 1
            continue
        # Model returns the integer _idx; resolve to the actual _id for triage round-trip.
        raw_id = raw.get("id")
        try:
            item_id = idx_to_id.get(int(raw_id), "")
        except (TypeError, ValueError):
            item_id = str(raw_id or "").strip()
        resolved = bool(item_id) and item_id in valid_ids
        clean.append({
            "id": item_id,
            "category": _pick_enum(raw.get("category"), CATEGORIES, "other"),
            "urgency": _pick_enum(raw.get("urgency"), URGENCIES, "soon"),
            "headline": headline,
            "why": str(raw.get("why") or "").strip() or None,
            "unresolved_id": not resolved,
        })
        if not resolved:
            _log.warning("synthesis: attention id %r (raw_idx=%r) not resolved", item_id, raw_id)

    brief["attention"] = clean[:10]
    if dropped:
        brief["malformed_attention"] = dropped

    for key in ("dangling", "missed", "agenda_gaps"):
        v = brief.get(key)
        brief[key] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    for key in ("client_pulse", "synthesis_note"):
        v = brief.get(key)
        brief[key] = str(v).strip() if isinstance(v, str) and v.strip() else None

    return brief


def _load_inbox(inbox: Path) -> tuple[list[dict], dict[str, str]]:
    items: list[dict] = []
    for f in inbox.glob("*.json"):
        if f.name.startswith(".") or is_brief_file(f.name):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("_id", f.stem)
                items.append(data)
        except Exception:
            pass

    state: dict[str, str] = {}
    state_path = inbox / STATE_FILE
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass

    return items, state


def _dominant_period(items: list[dict], now: datetime) -> str:
    seen = [str(i["period"]) for i in items if i.get("period")]
    if seen:
        return max(set(seen), key=seen.count)
    iso = now.isocalendar()
    return f"{iso.year}W{iso.week:02d}"


# --- core synthesis ----------------------------------------------------------

def _source_signature(inbox: Path) -> list:
    """(item_count, newest_mtime) of the synthesis INPUT set.

    Stored in the brief so the staleness check compares against what was actually
    summarised, not whatever is on disk at read time. Brief files are excluded —
    counting our own output would make every brief instantly stale.
    """
    item_files = [p for p in inbox.glob("*.json")
                  if not p.name.startswith(".") and not is_brief_file(p.name)]
    return [len(item_files), max((p.stat().st_mtime for p in item_files), default=0.0)]


def _sources_present(items: list[dict], state: dict[str, str]) -> list[str]:
    """Sources carrying at least one unresolved item, known lanes first.

    Discovered from the data rather than hardcoded: the inbox carries `insights`
    items alongside email/calendar/teams, and a fixed list would drop them silently
    — the exact failure this split exists to eliminate.
    """
    seen = {
        str(i.get("source") or "").strip()
        for i in items
        if state.get(str(i.get("_id", "")), "open") not in ("done", "dismissed")
    }
    seen.discard("")
    return [s for s in SOURCES if s in seen] + sorted(seen - set(SOURCES))


def _run_pass(
    items: list[dict],
    state: dict[str, str],
    now: datetime,
    period: str,
    source: str | None,
    dry_run: bool = False,
) -> tuple[dict, int, int]:
    """One synthesis pass over one lane (or everything when source is None).

    Returns (brief, n_scope, n_read) where n_scope is how many items were in scope for
    this pass and n_read is how many the model actually saw.
    """
    from co_worker_app.config import settings  # late import avoids circular

    now_iso = now.isoformat()
    today = now.strftime("%A, %B %d %Y")

    scope = [i for i in items
             if source is None or str(i.get("source") or "") == source]
    n_scope = len(scope)
    n_triaged = sum(
        1 for i in scope
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    )

    payload, idx_to_id, n_eligible = _condense(
        scope, state, today=now.date(), source=source,
        user_name=settings.user_name, user_email=settings.user_email)
    prompt = _build_prompt(n_scope, n_triaged, payload, now_iso, today, period, source)
    _log.info(
        "synthesis%s: %d in scope, %d eligible after filters, %d read, prompt %d chars",
        f" [{source}]" if source else "", n_scope, n_eligible, len(payload), len(prompt),
    )

    if dry_run:
        print(f"=== SYSTEM{f' [{source}]' if source else ''} ===")
        print(SYSTEM_PROMPT)
        print(f"\n=== USER{f' [{source}]' if source else ''} ===")
        print(prompt)
        return {}, n_scope, len(payload)

    if not payload:
        # Nothing unresolved in this lane. A broker call would burn ~15s to be told so.
        brief = _normalize({"attention": []}, idx_to_id)
    else:
        raw = _call_broker(prompt, settings.broker_url, settings.broker_auth_token)
        _log.info("synthesis%s: broker returned %d chars",
                  f" [{source}]" if source else "", len(raw))
        brief = _normalize(_extract_json(raw), idx_to_id)

    # Override every counted field in Python — do not trust model arithmetic.
    brief["generated"] = now_iso
    brief["period"] = period
    brief["source"] = source
    brief["items_considered"] = n_scope
    brief["items_triaged"] = n_triaged
    brief.setdefault("attention", [])
    brief["suppressed"] = max(0, n_scope - n_triaged - len(brief["attention"]))
    brief["items_read"] = len(payload)
    brief["items_eligible"] = n_eligible
    # Excluded on purpose: noise, FYIs, and stale non-client items. Not a shortfall.
    brief["items_filtered"] = max(0, (n_scope - n_triaged) - n_eligible)
    # Never reached the model because the budget ran out. This IS a shortfall.
    if len(payload) < n_eligible:
        brief["truncated"] = n_eligible - len(payload)

    return brief, n_eligible, len(payload)


def _merge_briefs(per_source: dict[str, dict], now_iso: str, period: str) -> dict:
    """Fold per-lane briefs into one combined brief.

    Attention items are tagged with the lane they came from, ranked by the same
    precedence the prompt asks for (urgency, then category), and capped at 10. Every
    lane is read in full, so unlike the old single pass nothing is dropped for context
    — the cap is an attention budget, not a truncation.
    """
    attention: list[dict] = []
    seen_ids: set[str] = set()
    for src, b in per_source.items():
        for a in b.get("attention") or []:
            item_id = str(a.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            attention.append({**a, "source": src})

    attention.sort(key=lambda a: (
        _URGENCY_RANK.get(str(a.get("urgency") or ""), 9),
        _CATEGORY_RANK.get(str(a.get("category") or ""), 9),
    ))

    def _union(key: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for b in per_source.values():
            for v in b.get(key) or []:
                s = str(v).strip()
                if s and s.casefold() not in seen:
                    seen.add(s.casefold())
                    out.append(s)
        return out

    pulses = [
        f"{src.capitalize()} — {b['client_pulse'].strip()}"
        for src, b in per_source.items()
        if isinstance(b.get("client_pulse"), str) and b["client_pulse"].strip()
    ]

    return {
        "generated": now_iso,
        "period": period,
        "source": None,
        "attention": attention[:10],
        "client_pulse": "  ".join(pulses) or None,
        "dangling": _union("dangling"),
        "missed": _union("missed"),
        "agenda_gaps": _union("agenda_gaps"),
        "synthesis_note": (
            "Merged from per-source passes ("
            + ", ".join(f"{s}: {len(b.get('attention') or [])}" for s, b in per_source.items())
            + ")."
        ),
        "passes": {
            s: {
                "items_considered": b.get("items_considered", 0),
                "items_eligible": b.get("items_eligible", 0),
                "items_read": b.get("items_read", 0),
                "items_filtered": b.get("items_filtered", 0),
                "attention": len(b.get("attention") or []),
                "truncated": b.get("truncated", 0),
            }
            for s, b in per_source.items()
        },
    }


def synthesize(inbox: Path, dry_run: bool = False, source: str | None = None) -> dict:
    """Run a single synthesis pass; write its brief file; return the brief dict.

    `source=None` runs one combined pass over every unresolved item — retained for
    the standalone/dry-run path, but synthesize_all() is what the API uses.
    """
    items, state = _load_inbox(inbox)
    now = datetime.now(timezone.utc)
    period = _dominant_period(items, now)

    brief, _, _ = _run_pass(items, state, now, period, source, dry_run)
    if dry_run:
        return {}

    brief["_source_signature"] = _source_signature(inbox)
    name = brief_filename(source)
    write_json(inbox / name, brief)
    _log.info("synthesis: wrote %s (%d attention items, %d suppressed)",
              name, len(brief.get("attention", [])), brief.get("suppressed", 0))
    return brief


def synthesize_all(inbox: Path, dry_run: bool = False) -> dict:
    """Run one pass per source, write each brief.<source>.json, merge into brief.json.

    This is the fix for the partial-pass problem: a single combined pass could only fit
    ~half the unresolved items in the context budget, so the rest were dropped unread.
    Split by lane, every item is read by some pass.
    """
    items, state = _load_inbox(inbox)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    period = _dominant_period(items, now)
    sources = _sources_present(items, state)

    if not sources:
        _log.warning("synthesis: no unresolved items in any source")
        sources = []

    n_items = len(items)
    n_triaged = sum(
        1 for i in items
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    )

    per_source: dict[str, dict] = {}
    total_read = 0
    total_eligible = 0
    failed: list[str] = []
    for src in sources:
        try:
            brief, n_eligible, n_read = _run_pass(items, state, now, period, src, dry_run)
        except Exception as exc:
            # One lane failing must not lose the other three. Record it and carry on.
            _log.error("synthesis [%s] failed: %s", src, exc, exc_info=True)
            failed.append(src)
            continue
        if dry_run:
            continue
        total_read += n_read
        total_eligible += n_eligible
        per_source[src] = brief
        brief["_source_signature"] = _source_signature(inbox)
        write_json(inbox / brief_filename(src), brief)

    if dry_run:
        return {}

    if not per_source and failed:
        raise RuntimeError(f"every synthesis pass failed ({', '.join(failed)})")

    combined = _merge_briefs(per_source, now_iso, period)
    combined["items_considered"] = n_items
    combined["items_triaged"] = n_triaged
    combined["items_read"] = total_read
    combined["suppressed"] = max(0, n_items - n_triaged - len(combined["attention"]))
    if failed:
        combined["failed_sources"] = failed
        combined["synthesis_note"] = (
            (combined.get("synthesis_note") or "")
            + f" Passes that failed and are NOT represented: {', '.join(failed)}."
        ).strip()
    n_unresolved = n_items - n_triaged
    combined["items_eligible"] = total_eligible
    combined["items_filtered"] = max(0, n_unresolved - total_eligible)
    # Only a genuine context shortfall sets this — items filtered as noise/FYI/stale
    # were excluded deliberately and must not be reported as something the model missed.
    if total_read < total_eligible:
        combined["truncated"] = total_eligible - total_read
    combined["_source_signature"] = _source_signature(inbox)

    write_json(inbox / BRIEF_FILE, combined)
    _log.info(
        "synthesis: wrote brief.json from %d passes (%d attention items, "
        "%d/%d eligible read, %d filtered as noise/stale)",
        len(per_source), len(combined["attention"]), total_read, total_eligible,
        combined["items_filtered"],
    )
    return combined


def synthesize_background(inbox: Path, source: str | None = None, every: bool = True) -> bool:
    """Start synthesis in a daemon thread. Returns False if already running.

    `every=True` (the default) runs the per-source split; pass a `source` to refresh
    just one lane, or every=False for a single combined pass.
    """
    global _running, _last_started, _last_error

    if not _lock.acquire(blocking=False):
        return False

    _running = True
    _last_started = time.time()
    _last_error = ""

    def _run() -> None:
        global _running, _last_finished, _last_error
        try:
            if source is None and every:
                synthesize_all(inbox)
            else:
                synthesize(inbox, source=source)
        except Exception as exc:
            _last_error = str(exc)
            _log.error("synthesis failed: %s", exc, exc_info=True)
        finally:
            _running = False
            _last_finished = time.time()
            _lock.release()

    threading.Thread(target=_run, daemon=True, name="co-worker-synthesis").start()
    return True


# --- standalone entry point --------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Synthesize co-worker executive brief.")
    ap.add_argument("--inbox", type=Path,
                    default=Path(__file__).resolve().parents[3] / "data" / "co-worker" / "inbox")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and exit without writing")
    ap.add_argument("--all", action="store_true",
                    help="one pass per source, merged into brief.json (recommended: "
                         "a single combined pass cannot fit every item in context)")
    ap.add_argument("--source", default=None,
                    help="refresh a single lane (email/calendar/teams/...)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.all:
        result = synthesize_all(args.inbox, dry_run=args.dry_run)
    else:
        result = synthesize(args.inbox, dry_run=args.dry_run, source=args.source)
    if not args.dry_run:
        print(f"brief written — {len(result.get('attention', []))} attention items")
