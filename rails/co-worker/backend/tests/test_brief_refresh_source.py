"""An empty `?source=` must not be treated as a lane — it used to destroy the landing brief.

`brief_refresh` validated `source` for slashes and a leading dot only, so "" passed as a
real lane name and then, at every hop after it, meant something different:

    synthesize_background   source is not None  -> the SINGLE-lane path
    _run_pass scope filter  str(source) == ""   -> matches no item at all
    brief_filename("")      falsy               -> back to brief.json, the COMBINED brief
    synthesize()            writes the empty result over it, with a CURRENT signature

so `_brief_is_stale()` then called the wreckage fresh and the frontend's auto-refresh
never fired to rebuild it. The response was 200 {"started": true}.

Hermetic: the broker is monkeypatched at `_call_broker`; the inbox is a tmp_path directory.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from co_worker_app import synthesize as synth
from co_worker_app.config import settings
from co_worker_app.main import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    """One unresolved item per lane, plus the merged brief.json the frontend lands on."""
    d = tmp_path / "inbox"
    d.mkdir()
    for i, src in enumerate(("email", "calendar", "teams"), start=1):
        (d / f"item-{i}.json").write_text(json.dumps({
            "title": f"a {src} thing", "source": src, "period": "2026W34",
            "type": "action", "client": "Acme", "why": "someone is waiting on a reply",
        }), encoding="utf-8")
    (d / "brief.json").write_text(json.dumps({
        "generated": "2026-08-30T09:00:00+00:00",
        "period": "2026W34",
        "source": None,
        "attention": [{"id": "item-1", "category": "client", "urgency": "today",
                       "headline": "Acme is waiting on the SOW", "why": "",
                       "unresolved_id": False}],
        "items_considered": 3,
        "_source_signature": [3, 0.0],
    }), encoding="utf-8")
    monkeypatch.setattr(settings, "inbox_dir", str(d))
    return d


def _fake_broker(prompt, broker_url, auth_token, model):
    return json.dumps({"attention": [
        {"id": 1, "category": "client", "urgency": "today",
         "headline": "Acme is waiting on the SOW", "why": "thread unanswered 3 days"},
    ]})


def test_empty_source_is_normalised_to_the_all_lanes_run(inbox, monkeypatch):
    """The fix itself: "" and whitespace collapse to None; a real lane is untouched."""
    seen: list = []
    monkeypatch.setattr(synth, "synthesize_background",
                        lambda inbox_dir, source=None: (seen.append(source), True)[1])
    client = TestClient(app, raise_server_exceptions=False)

    for query in ("?source=", "?source=%20%20", "?source=email"):
        r = client.post(f"/api/brief/refresh{query}", headers=HDR)
        assert r.status_code == 200, query
    assert seen == [None, None, "email"]


def test_empty_source_does_not_destroy_the_merged_brief(inbox, monkeypatch):
    """All four hops, for real: the merged brief must survive `?source=` intact."""
    monkeypatch.setattr(synth, "_call_broker", _fake_broker)
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post("/api/brief/refresh?source=", headers=HDR)
    assert r.status_code == 200 and r.json()["started"] is True
    assert r.json()["source"] is None
    with synth._lock:      # the synthesis thread releases this when the run finishes
        pass
    assert synth.get_status()["last_error"] is None

    brief = json.loads((inbox / "brief.json").read_text(encoding="utf-8"))
    assert brief["source"] is None, "brief.json is the COMBINED brief, never a lane called ''"
    assert brief["items_considered"] == 3, "the empty-lane pass recorded 0 here"
    assert brief["attention"], "an empty `?source=` emptied the landing view"

    served = client.get("/api/brief", headers=HDR).json()
    assert served["exists"] is True and served["attention"]


def test_a_real_lane_still_writes_only_its_own_brief(inbox, monkeypatch):
    """The control: single-lane refresh must keep working, and keep off brief.json."""
    monkeypatch.setattr(synth, "_call_broker", _fake_broker)
    before = (inbox / "brief.json").read_text(encoding="utf-8")
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/api/brief/refresh?source=email", headers=HDR).status_code == 200
    with synth._lock:
        pass

    assert (inbox / "brief.email.json").exists()
    assert (inbox / "brief.json").read_text(encoding="utf-8") == before
