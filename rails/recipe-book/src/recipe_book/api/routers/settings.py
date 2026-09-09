"""Admin-editable app settings (meal-plan retention + AI recency window).

Identity is trusted from the gateway: it verifies the session and forwards
``x-platform-admin: 1`` for admin/super-admin users (a super-admin always implies admin).
The rail never sees passwords; it only honors that header. GET is readable by anyone (so
the UI can decide whether to show the gear), PUT requires admin.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from recipe_book import config, db, settings
from recipe_book.api.deps import Identity, identity, require_admin

router = APIRouter()


@router.get("/api/settings")
def get_settings(_: Identity = Depends(identity)) -> dict:
    con = db.connect()
    try:
        return {
            "plan_retention_days": settings.retention_days(con),
            "plan_recency_days": settings.recency_days(con),
            "is_admin": _is_admin(x_platform_admin),
            "defaults": {
                "plan_retention_days": config.PLAN_RETENTION_DAYS,
                "plan_recency_days": config.PLAN_RECENCY_DAYS,
            },
            "ranges": {
                "plan_retention_days": list(settings.RETENTION_RANGE),
                "plan_recency_days": list(settings.RECENCY_RANGE),
            },
        }
    finally:
        con.close()


class SettingsPatch(BaseModel):
    plan_retention_days: int | None = None
    plan_recency_days: int | None = None


def _check(name: str, val: int | None, rng: tuple[int, int]) -> int | None:
    if val is None:
        return None
    lo, hi = rng
    if not (lo <= val <= hi):
        raise HTTPException(status_code=422, detail=f"{name} must be between {lo} and {hi}")
    return val


@router.put("/api/settings")
def put_settings(req: SettingsPatch, _: Identity = Depends(require_admin)) -> dict:
    # Was a local `x_platform_admin == "1"` check that never looked at X-Platform-User at all,
    # so it read the admin flag off a request it had not established came through the gateway.
    # require_admin resolves the identity first and 401s before it ever considers the flag.
    ret = _check("Retention", req.plan_retention_days, settings.RETENTION_RANGE)
    rec = _check("Recency", req.plan_recency_days, settings.RECENCY_RANGE)
    con = db.connect()
    try:
        settings.set_plan(con, retention=ret, recency=rec)
        return {
            "plan_retention_days": settings.retention_days(con),
            "plan_recency_days": settings.recency_days(con),
        }
    finally:
        con.close()
