"""Admin icon operations: status + (re)generation.

Rendering runs on the shared GPU broker (one heavy model at a time, used by every rail), so
the triggers are **admin-gated** and **single-flight** — a second trigger while one is running
is refused, not queued, so FLUX runs never stack on the broker other rails depend on. Both run
as background tasks (a full pass loads FLUX and takes a while) and end by reloading the in-memory
catalog in-process, so the live UI reflects the new icons with no restart. Poll GET
/api/icons/status.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from recipe_book import db, icon_prompts, icons
from recipe_book.api import deps

router = APIRouter()


def _status_payload() -> dict:
    con = db.connect()
    try:
        counts = icons.status(con)
    finally:
        con.close()
    return {**counts, **icons.run_state()}


@router.get("/api/icons/status")
def icons_status() -> dict:
    """Icon counts (ready/pending/total) + current run state. Read-only, un-gated so the
    admin UI can poll progress."""
    return _status_payload()


@router.post("/api/icons/generate")
def icons_generate(background: BackgroundTasks, limit: int = 0, force: bool = False,
                   _: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Render icons for recipes missing one (or all, if ``force``) using the already-cached
    distinctive subjects — render only. Use /api/icons/repass to (re)author subjects first.
    Big ``batch`` so the FLUX load amortizes across the run. Background + single-flight."""
    if not icons.try_begin("render"):
        return {"queued": False, **_status_payload()}

    def job() -> None:
        con = db.connect()
        try:
            icons.finish(icons.generate(con, limit=limit, force=force, batch=250))
        except Exception as exc:  # noqa: BLE001 - never leave the run flag stuck
            icons.finish({"error": str(exc)})
        finally:
            con.close()

    background.add_task(job)
    return {"queued": True, **_status_payload()}


@router.post("/api/icons/repass")
def icons_repass(background: BackgroundTasks, force: bool = False,
                 _: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Full distinctive re-pass: (1) author a per-recipe icon subject with the LLM, then
    (2) render it. ``force=True`` redoes every recipe; otherwise only those missing a subject
    or an icon (so a routine run just fills in new recipes). Both stages hit the broker.
    Background + single-flight; poll /api/icons/status."""
    if not icons.try_begin("subjects"):
        return {"queued": False, **_status_payload()}

    def job() -> None:
        con = db.connect()
        try:
            icon_prompts.build(con, force=force, batch=24)
            icons.set_phase("render")
            icons.finish(icons.generate(con, force=force, batch=250))
        except Exception as exc:  # noqa: BLE001 - never leave the run flag stuck
            icons.finish({"error": str(exc)})
        finally:
            con.close()

    background.add_task(job)
    return {"queued": True, **_status_payload()}
