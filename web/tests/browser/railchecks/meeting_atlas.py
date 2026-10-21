"""meeting-atlas's check: the meeting page draws its words-spoken chart.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .harness import REPO, NoFixture, mount, rail, serve


def meeting_atlas_payload() -> dict:
    """A real index payload: the rail's own indexer over a synthetic recordings tree, so the
    bundle renders the shape the backend serves rather than a hand-written approximation."""
    import tempfile
    sys.path.insert(0, str(REPO / "rails" / "meeting-atlas" / "backend"))
    from meeting_atlas_app import indexer
    root = Path(tempfile.mkdtemp(prefix="railcheck-ma-"))
    folder = root / "Meeting 2026-08-20_06-59-29"
    folder.mkdir()
    segs = [{"audio_start_time": float(t), "audio_end_time": float(t) + 4.0,
             "text": f"Project Falcon segment {i} about the schema cutover plan."}
            for i, t in enumerate(range(0, 1200, 20))]
    (folder / "transcripts.json").write_text(json.dumps({"segments": segs}), encoding="utf-8")
    (folder / "metadata.json").write_text(json.dumps({
        "created_at": "2026-08-20T13:59:29+00:00", "duration_seconds": 1210.0,
        "meeting_name": "Falcon sync"}), encoding="utf-8")
    return indexer.build_index(str(root), None, None)


def check_ma_activity_chart(page) -> list[str]:
    """The meeting page draws its words-spoken chart. useMeasure measured once after the first
    commit, which is always "Loading meeting…", so the chart's width stayed 0 and it never
    rendered for any meeting."""
    payload = meeting_atlas_payload()
    m = payload["meetings"][0]

    def api(method, path, q, body):
        if path == "/api/meetings":
            return {"corpus": payload["corpus"], "meetings": payload["meetings"]}
        if path == f"/api/meetings/{m['id']}":
            time.sleep(0.3)      # the loading commit happens first, as it does for real
            return {"meeting": m, "detail": payload["details"][m["id"]]}
        if path == "/api/platform/me":
            return {"username": "owner-one", "is_admin": False, "is_superadmin": False, "apps": []}
        raise NoFixture(path)

    r = rail("meeting-atlas", api)
    serve(page, r)
    mount(page, r)
    page.get_by_text(m["title"]).first.click()
    page.wait_for_selector(".chart", state="attached", timeout=10000)
    page.wait_for_timeout(800)
    svg = page.locator(".chart svg")
    width = svg.first.get_attribute("width") if svg.count() else None
    out = [] if width and float(width) > 0 else [f"no activity chart (svg width {width!r})"]
    return out + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "ma-activity-chart": check_ma_activity_chart,
}
