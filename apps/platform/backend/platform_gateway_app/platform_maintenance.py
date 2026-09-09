"""Gateway-owned maintenance the central scheduler runs in-process.

Most scheduled tasks are HTTP calls to a rail's backend, but a few belong to the gateway itself.
The scheduler special-cases ``rail == "platform"``:

- Simple DB chores (prune expired sessions) are SYNC handlers in ``HANDLERS`` — the scheduler
  calls them with a DB session.
- The model-pool scan is ASYNC (it awaits the broker), so the scheduler routes it to
  ``run_model_scan`` directly rather than through ``HANDLERS``.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from platform_gateway_app import model_catalog
from platform_gateway_app.models import GeneratedModelDesc, SessionRow


def prune_expired_sessions(db: OrmSession) -> int:
    """Delete every session whose ``expires_at`` is in the past. Returns the row count.

    SQLite stores these datetimes naive (see ``auth.user_for_token``, which treats a naive value
    as UTC), so we compare against a naive-UTC ``now`` to match the stored format exactly."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    res = db.execute(delete(SessionRow).where(SessionRow.expires_at < now))
    db.commit()
    return int(res.rowcount or 0)


# task_id -> SYNC handler(db) -> row count. The scheduler dispatches these "platform" tasks here.
# (The async model-scan is routed separately — see scheduler._fire_platform / run_now / tick.)
HANDLERS = {
    "prune-sessions": prune_expired_sessions,
}


# --- model-pool scan: describe newly-seen models with the broker LLM, cached ------------------

_DESCRIBE_MODEL = os.environ.get("PLATFORM_DESCRIBE_MODEL", "@chat-fast")
_DESCRIBE_CAP = int(os.environ.get("PLATFORM_DESCRIBE_CAP", "8"))


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of an LLM reply, tolerating code fences / stray prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    a, b = s.find("{"), s.rfind("}")
    return json.loads(s[a:b + 1]) if a >= 0 and b > a else {}


async def _describe_one(broker: Any, m: dict[str, Any]) -> dict[str, str] | None:
    """Ask the broker's chat model to classify + one-line a single model. None on any failure —
    a model that fails to describe is simply left for the next scan, never fatal."""
    cats = ", ".join(sorted(model_catalog.VALID_CATEGORIES - {"other"}))
    system = (
        "You label locally-installed LLMs for an admin console. Given a model's name and stats, "
        f"pick exactly ONE category from: {cats}. Then write ONE concise sentence (max 160 chars) "
        "on what it is good at and why. Reply with ONLY compact JSON, no prose and no code fences: "
        '{"category": "...", "blurb": "..."}'
    )
    user = (f"name={m.get('name')} family={m.get('family') or m.get('class') or '?'} "
            f"params={m.get('parameter_size') or '?'} vision={bool(m.get('vision'))}")
    try:
        resp = await broker.chat(
            _DESCRIBE_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            options={"num_predict": 180, "temperature": 0.2},
            keep_alive="2m", format="json", think=False,
        )
        obj = _extract_json(((resp or {}).get("message") or {}).get("content") or "")
    except Exception:  # noqa: BLE001 — broker/parse failure: skip this model, try again next scan
        return None
    cat = str(obj.get("category", "")).strip().lower()
    if cat not in model_catalog.VALID_CATEGORIES:
        cat = "other"
    blurb = str(obj.get("blurb", "")).strip().replace("\n", " ")[:240]
    return {"category": cat, "blurb": blurb} if blurb else None


async def run_model_scan(broker: Any, db: OrmSession) -> str:
    """Re-scan the broker's installed models and LLM-describe any that have neither a curated nor
    a cached description (capped per run). The Models tab reads the pool live, so this doesn't
    make models *appear* — it's the hands-off step that fills in a category + one-liner for a
    model pulled outside the UI, cached so each is described once. Off-hours by default (the
    describe call loads a small model briefly). Returns a summary for the Schedule tab."""
    models = (await broker.models()).get("models", [])
    have = {r.name for r in db.execute(select(GeneratedModelDesc)).scalars().all()}
    undescribed = [m for m in models
                   if m.get("name") and model_catalog.curated(m["name"]) is None and m["name"] not in have]
    made = 0
    for m in undescribed[:_DESCRIBE_CAP]:
        got = await _describe_one(broker, m)
        if got:
            db.merge(GeneratedModelDesc(name=m["name"], category=got["category"], blurb=got["blurb"]))
            made += 1
    if made:
        db.commit()
    return f"{len(models)} models · {made} newly described · {len(undescribed) - made} undescribed"
