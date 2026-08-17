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

# Context budget for the items payload, in characters (~4 chars/token).
# The local role is a small model on a modest GPU: a full week of items with bodies
# runs ~245K chars (~61K tokens), which silently truncates and yields an empty brief.
# So we drop bodies, clip prose, and cut lowest-value items first until we fit.
ITEMS_CHAR_BUDGET = 48_000
WHY_CLIP = 220
TITLE_CLIP = 160
NUM_CTX = 32_768

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
) -> tuple[list[dict], dict[int, str]]:
    """Unresolved items, numbered 1..N, stripped to decision-relevant fields, within budget.

    Returns (payload_rows, idx_to_id) where payload_rows carry integer _idx instead of
    _id — the model emits a number, Python maps it back. Eliminates verbatim-copy errors
    that cause unresolved_id warnings in the UI.

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
    ]
    live.sort(key=_triage_sort_key)

    out: list[dict] = []
    idx_to_id: dict[int, str] = {}
    used = 0
    for idx, item in enumerate(live, start=1):
        row: dict = {"_idx": idx}
        row.update({k: item[k] for k in KEEP if k in item and item[k] is not None})
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
            "synthesis: context budget trimmed to %d of %d unresolved items "
            "(lowest-priority dropped first)",
            len(out), len(live),
        )
    return out, idx_to_id


def _build_prompt(
    n_items: int,
    n_triaged: int,
    payload: list[dict],
    now_iso: str,
    today: str,
    period: str,
) -> str:
    schema_example = json.dumps({
        "generated": now_iso,
        "period": period,
        "items_considered": n_items,
        "items_triaged": n_triaged,
        "attention": [
            {
                "id": 7,
                "category": "client",
                "headline": "Reply to Priya Sharma re: SOW milestone 3 — asked Tuesday, no response yet",
                "urgency": "today",
                "why": "Milestone sign-off gates the next invoice cycle.",
            }
        ],
        "client_pulse": "Two of three client threads are waiting on a reply from you.",
        "dangling": ["Promised the architecture diagram to Dan by EOW — not sent"],
        "missed": ["Priya Sharma (client) — SOW milestone 3, last message Tuesday"],
        "agenda_gaps": ["Delivery sync Thu 2pm — you organised it, no agenda set"],
        "suppressed": 0,
        "synthesis_note": None,
    }, indent=2)

    items_block = json.dumps(payload, separators=(",", ":"), default=str)

    return f"""\
Today is {today}. Period: {period}.

{n_items} items harvested. {n_triaged} already triaged (done/dismissed). \
Items below are the unresolved set after pre-filtering: noise, FYIs, and \
non-client items older than 14 days have been removed.

Produce a JSON executive brief with this EXACT schema:
{schema_example}

Field value rules:
- "category" must be EXACTLY ONE of: client, dangling, missed, agenda-gap, other
- "urgency" must be EXACTLY ONE of: today, this-week, soon
- Never emit a pipe-separated list. Choose the single best value.
- "id" must be the integer _idx of the item from the list below.

Attention list rules:
- MAX 10 items
- Headline: direct instruction, never a description
  GOOD: "Reply to Priya Sharma re: SOW milestone 3 — asked Tuesday, no response yet"
  BAD: "Email from Priya about SOW"
- Only include items where inaction has a real consequence THIS WEEK

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
        if f.name.startswith(".") or f.name == BRIEF_FILE:
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

def synthesize(inbox: Path, dry_run: bool = False) -> dict:
    """Run synthesis; write brief.json; return the brief dict."""
    from co_worker_app.config import settings  # late import avoids circular

    items, state = _load_inbox(inbox)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.strftime("%A, %B %d %Y")
    period = _dominant_period(items, now)

    n_items = len(items)
    n_triaged = sum(
        1 for i in items
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    )
    payload, idx_to_id = _condense(items, state, today=now.date())
    prompt = _build_prompt(n_items, n_triaged, payload, now_iso, today, period)
    _log.info(
        "synthesis: %d items total, %d in payload after pre-filter, prompt %d chars",
        n_items, len(payload), len(prompt),
    )

    if dry_run:
        print("=== SYSTEM ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER ===")
        print(prompt)
        return {}

    raw = _call_broker(prompt, settings.broker_url, settings.broker_auth_token)
    _log.info("synthesis: broker returned %d chars", len(raw))

    brief = _normalize(_extract_json(raw), idx_to_id)

    # Override all counted fields in Python — do not trust model arithmetic.
    brief["generated"] = now_iso
    brief["period"] = period
    brief["items_considered"] = n_items
    brief["items_triaged"] = n_triaged
    brief.setdefault("attention", [])
    brief["suppressed"] = max(0, n_items - n_triaged - len(brief["attention"]))

    # How much the local model actually saw.
    n_unresolved = n_items - n_triaged
    brief["items_read"] = len(payload)
    if len(payload) < n_unresolved:
        brief["truncated"] = n_unresolved - len(payload)

    # Source signature so the staleness check can compare (item_count, newest_mtime)
    # against what was summarised, not whatever is on disk at read time.
    item_files = [p for p in inbox.glob("*.json")
                  if not p.name.startswith(".") and p.name != BRIEF_FILE]
    sig_count = len(item_files)
    sig_mtime = max((p.stat().st_mtime for p in item_files), default=0.0)
    brief["_source_signature"] = [sig_count, sig_mtime]

    write_json(inbox / BRIEF_FILE, brief)
    _log.info(
        "synthesis: wrote brief.json (%d attention items, %d suppressed)",
        len(brief.get("attention", [])),
        brief.get("suppressed", 0),
    )
    return brief


def synthesize_background(inbox: Path) -> bool:
    """Start synthesis in a daemon thread. Returns False if already running."""
    global _running, _last_started, _last_error

    if not _lock.acquire(blocking=False):
        return False

    _running = True
    _last_started = time.time()
    _last_error = ""

    def _run() -> None:
        global _running, _last_finished, _last_error
        try:
            synthesize(inbox)
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
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = synthesize(args.inbox, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"brief written — {len(result.get('attention', []))} attention items")
