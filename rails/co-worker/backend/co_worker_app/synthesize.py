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
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("co-worker.synthesize")

BROKER_ROLE = "co-worker-synthesis"
BRIEF_FILE = "brief.json"
STATE_FILE = ".state.json"

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


def _build_prompt(
    items: list[dict],
    state: dict[str, str],
    now_iso: str,
    today: str,
    period: str,
) -> str:
    n_total = len(items)
    n_triaged = sum(
        1 for i in items
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    )

    # Condense to fields the model needs — keeps context tight (~100 tokens/item)
    KEEP = ("_id", "type", "source", "priority", "title", "why", "from",
            "when", "due", "client", "period", "tags", "evidence", "body")
    unresolved = [
        {k: item[k] for k in KEEP if k in item and item[k] is not None}
        for item in items
        if state.get(str(item.get("_id", "")), "open") not in ("done", "dismissed")
    ]

    schema_example = json.dumps({
        "generated": now_iso,
        "period": period,
        "items_considered": n_total,
        "items_triaged": n_triaged,
        "attention": [
            {
                "id": "<_id of an inbox item>",
                "category": "client|dangling|missed|agenda-gap|other",
                "headline": "<direct instruction — what to do and why urgent>",
                "urgency": "today|this-week|soon",
                "why": "<one sentence: consequence of not acting>",
            }
        ],
        "client_pulse": "<2-3 sentences on overall state of client threads>",
        "dangling": ["<specific commitment not yet resolved>"],
        "missed": ["<thread where someone is waiting — include sender + topic>"],
        "agenda_gaps": ["<meeting you own with no agenda — include name and date>"],
        "suppressed": n_total - n_triaged - 10,
        "synthesis_note": "<optional: anything unusual, or null>",
    }, indent=2)

    items_block = json.dumps(unresolved, separators=(",", ":"), default=str)

    return f"""\
Today is {today}. Period: {period}.

{n_total} items harvested. {n_triaged} already triaged (done/dismissed) — excluded below.

Produce a JSON executive brief with this EXACT schema:
{schema_example}

Attention list rules:
- MAX 10 items, ordered by urgency: today → this-week → soon
- Priority order: client > dangling > missed > agenda-gap > other
- Headline: direct instruction, never a description
  GOOD: "Reply to Priya Sharma re: SOW milestone 3 — asked Tuesday, no response yet"
  BAD: "Email from Priya about SOW"
- Only include items where inaction has a real consequence THIS WEEK

Do NOT surface:
- FYIs, newsletters, automated digests, or status emails with no action implied
- Recurring meetings with established purpose (standups, weekly syncs with agendas)
- Internal items older than 14 days (client items: no age limit)
- Items already triaged done/dismissed

Set "suppressed" to (items_considered - items_triaged - len(attention)).

Unresolved inbox items ({len(unresolved)} items):
{items_block}"""


# --- broker call -------------------------------------------------------------

def _call_broker(prompt: str, broker_url: str, auth_token: str) -> str:
    url = f"{broker_url.rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": BROKER_ROLE,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": 2048,
    }).encode("utf-8")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"broker HTTP {exc.code}: {body[:400]}") from exc
    return data["choices"][0]["message"]["content"]


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

    prompt = _build_prompt(items, state, now_iso, today, period)
    _log.info("synthesis: %d items total, prompt %d chars", len(items), len(prompt))

    if dry_run:
        print("=== SYSTEM ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER ===")
        print(prompt)
        return {}

    raw = _call_broker(prompt, settings.broker_url, settings.broker_auth_token)
    _log.info("synthesis: broker returned %d chars", len(raw))

    brief = _extract_json(raw)

    # Guarantee required top-level keys regardless of model compliance
    brief.setdefault("generated", now_iso)
    brief.setdefault("period", period)
    brief.setdefault("items_considered", len(items))
    brief.setdefault("items_triaged", sum(
        1 for i in items
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    ))
    brief.setdefault("attention", [])
    brief.setdefault("suppressed", 0)

    # Atomic write
    p = inbox / BRIEF_FILE
    fd, tmp = tempfile.mkstemp(dir=str(inbox), prefix=".brief-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(brief, f, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise

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
