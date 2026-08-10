"""The registry of schedulable maintenance tasks, grouped by rail.

Like ``rails_models.RAIL_MODEL_SLOTS`` is the source of truth for the Rails tab, this is the source
of truth for the Schedule tab. Each task names the rail that owns it, the backend endpoint the
central scheduler fires (via the rail's internal URL), a human label/description, and the default
recurrence used to seed its row on first boot. Adding a schedulable task is one entry here plus the
endpoint on the rail.
"""
from __future__ import annotations

from typing import Any

# rail id (matches APP_CATALOG / enabled_apps) -> ordered scheduled tasks.
SCHEDULED_TASKS: list[dict[str, Any]] = [
    {
        "rail": "ai-playground", "task_id": "model-repull", "label": "Embedding model update",
        "icon": "🛝", "method": "POST", "path": "/api/bench/refresh/repull-all",
        "description": "Re-pull the broker embedding models so a same-name upstream update (new "
                       "digest under the same tag) is picked up automatically.",
        "default": {"freq": "weekly", "interval": 1, "byweekday": [6], "at": "04:00",
                    "tz": "America/Los_Angeles"},
    },
    {
        "rail": "recipe-book", "task_id": "reindex", "label": "Semantic re-index", "icon": "🍳",
        "method": "POST", "path": "/api/search/reindex", "async": True,
        "description": "Rebuild the recipe semantic search index (embed every recipe via the "
                       "broker) so newly added recipes become searchable. Fire-and-forget.",
        "default": {"freq": "weekly", "interval": 1, "byweekday": [6], "at": "04:30",
                    "tz": "America/Los_Angeles"},
    },
    {
        "rail": "recipe-book", "task_id": "icons-repass", "label": "Fill recipe icons", "icon": "🍳",
        "method": "POST", "path": "/api/icons/repass", "async": True,
        "description": "Author + render icons for recipes still missing one (force=False, so a "
                       "routine run just fills in new recipes). Hits the broker; fire-and-forget.",
        "default": {"freq": "weekly", "interval": 1, "byweekday": [6], "at": "05:00",
                    "tz": "America/Los_Angeles"},
    },
    {
        "rail": "recipe-book", "task_id": "purge", "label": "Meal-plan purge", "icon": "🍳",
        "method": "POST", "path": "/api/maintenance/purge",
        "description": "Trim meal-plan entries past the retention window. Synchronous — the run "
                       "status shows the number purged. Replaces recipe-book's nightly loop.",
        "default": {"freq": "daily", "interval": 1, "at": "02:00", "tz": "America/Los_Angeles"},
    },
    {
        "rail": "bouquet", "task_id": "sweep", "label": "Cleanup sweep", "icon": "💐",
        "method": "POST", "path": "/api/maintenance/sweep",
        "description": "Delete abandoned pending uploads and stray orphan files (older than the "
                       "age guard). Synchronous — the run status shows the counts deleted. "
                       "Replaces bouquet's weekly in-process loop.",
        "default": {"freq": "weekly", "interval": 1, "byweekday": [6], "at": "03:00",
                    "tz": "America/Los_Angeles"},
    },
    {
        "rail": "edu-suite", "task_id": "expire", "label": "Job retention sweep", "icon": "🎓",
        "method": "POST", "path": "/api/maintenance/expire",
        "description": "Delete done/failed edu-suite jobs (files + rows) older than the retention "
                       "window (default 365 days). The run status shows the number removed. The IEP "
                       "instance keeps its own 30-day in-process sweep, separate from this.",
        "default": {"freq": "daily", "interval": 1, "at": "02:30", "tz": "America/Los_Angeles"},
    },
    # ---- gateway-owned ("platform") tasks: run in-process by the scheduler, no rail HTTP call ----
    {
        "rail": "platform", "task_id": "prune-sessions", "label": "Prune expired sessions",
        "icon": "⚙️", "method": "LOCAL", "path": "-",
        "description": "Delete expired login sessions from the gateway DB. Otherwise they're only "
                       "removed lazily (when an expired token happens to be presented again).",
        "default": {"freq": "daily", "interval": 1, "at": "03:30", "tz": "America/Los_Angeles"},
    },
]

# (rail, task_id) -> task, and the set of valid ids for validation.
TASKS_BY_KEY: dict[tuple[str, str], dict[str, Any]] = {
    (t["rail"], t["task_id"]): t for t in SCHEDULED_TASKS
}


def tasks_for(enabled: set[str]) -> list[dict[str, Any]]:
    """Registered tasks whose rail is installed here. Gateway-owned ('platform') tasks always
    install — they run in-process and don't depend on any rail being enabled."""
    return [t for t in SCHEDULED_TASKS if t["rail"] == "platform" or t["rail"] in enabled]
