"""The co-worker items the 2026-09-10/11 audits recorded and nobody closed.

Each test is the item's own failure, reproduced: a read route that parsed every file twice, a
header-chip poll that parsed every file every 6 s, a 409 that spent the auto cooldown, a lane
name that created a file in the host's bind mount, a staleness signature that counted items the
run never read, a recency sort key nothing populated, and two triage writes racing on the
sidecar. Hermetic: the broker is replaced at `_call_broker` and the inbox is under tmp_path.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from co_worker_app import main as mainmod
from co_worker_app import synthesize as synth
from co_worker_app.config import settings
from co_worker_app.main import app

HDR = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


def _item(d: Path, name: str, **fields) -> Path:
    body = {"title": f"{name} title", "source": "email", "period": "2026W34",
            "type": "action", "why": "someone is waiting", **fields}
    p = d / f"{name}.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    d = tmp_path / "inbox"
    (d / "archive").mkdir(parents=True)
    _item(d, "item-1", period="2026W34")
    _item(d, "item-2", period="2026W33", source="teams")
    _item(d / "archive", "old-1", period="2026W30")
    _item(d / "archive", "old-2", period="2026W29")
    monkeypatch.setattr(settings, "inbox_dir", str(d))
    monkeypatch.setattr(mainmod, "_parse_cache", None)
    monkeypatch.setattr(mainmod.modelstate, "resolve",
                        lambda specs, **kw: {"broker": "unreachable", "models": []})
    return d


@pytest.fixture()
def client(inbox):
    return TestClient(app, raise_server_exceptions=False)


def _count_reads(monkeypatch, d: Path) -> list[str]:
    """Every Path.read_text of a file directly inside `d`, by name."""
    seen: list[str] = []
    real = Path.read_text

    def counting(self, *a, **kw):
        if self.parent == d:
            seen.append(self.name)
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting)
    return seen


# --- the read routes ----------------------------------------------------------------------------

@pytest.mark.parametrize("route, sub", [("/api/inbox", None), ("/api/archive", "archive")])
def test_a_listing_reads_each_item_once_and_still_lists_every_period(
        client, inbox, monkeypatch, route, sub):
    d = inbox / sub if sub else inbox
    reads = _count_reads(monkeypatch, d)
    r = client.get(f"{route}?period=2026W34", headers=HDR)
    assert r.status_code == 200
    items = [n for n in reads if not n.startswith(".")]
    assert sorted(items) == sorted({n for n in items}), f"read twice: {sorted(items)}"
    assert len(items) == 2
    # `periods` is the picker, so it must cover the unfiltered set, not the filtered one.
    want = ["2026W34", "2026W33"] if not sub else ["2026W30", "2026W29"]
    assert r.json()["periods"] == want


def test_the_chip_poll_does_not_reread_an_unchanged_inbox(client, inbox, monkeypatch):
    reads = _count_reads(monkeypatch, inbox)
    assert client.get("/api/models", headers=HDR).json()["items"] == 2
    first = len(reads)
    assert first == 2
    for _ in range(5):
        assert client.get("/api/models", headers=HDR).json()["items"] == 2
    assert client.get("/api/healthz", headers=HDR).json()["inbox_items"] == 2
    assert len(reads) == first, "an unchanged inbox was re-read on the 6 s poll"


def test_the_memo_follows_an_added_a_rewritten_and_a_removed_item(client, inbox):
    assert client.get("/api/models", headers=HDR).json()["items"] == 2
    bad = inbox / "item-3.json"
    bad.write_text("{not json", encoding="utf-8")
    h = client.get("/api/healthz", headers=HDR).json()
    assert (h["inbox_items"], h["skipped_files"]) == (2, ["item-3.json"])
    bad.write_text(json.dumps({"title": "fixed now", "source": "email"}), encoding="utf-8")
    h = client.get("/api/healthz", headers=HDR).json()
    assert (h["inbox_items"], h["skipped"]) == (3, 0)
    bad.unlink()
    assert client.get("/api/models", headers=HDR).json()["items"] == 2


# --- the refresh route ----------------------------------------------------------------------------

def test_an_auto_refresh_that_409s_does_not_spend_the_cooldown(client, monkeypatch):
    answers = iter([False, True])          # a manual run is in flight, then it is not
    monkeypatch.setattr(synth, "synthesize_background",
                        lambda inbox_dir, source=None, requester=None: next(answers))
    monkeypatch.setattr(settings, "auto_synthesize_min_interval_s", 900)
    monkeypatch.setattr(mainmod, "_last_auto_attempt", 0.0)

    assert client.post("/api/brief/refresh?auto=1", headers=HDR).status_code == 409
    r = client.post("/api/brief/refresh?auto=1", headers=HDR)
    assert r.status_code == 200 and r.json()["started"] is True, r.json()
    # And THAT pass did spend it.
    r = client.post("/api/brief/refresh?auto=1", headers=HDR)
    assert r.json() == {**r.json(), "started": False, "reason": "cooldown"}


def test_a_lane_no_item_carries_is_refused_and_creates_no_file(client, inbox, monkeypatch):
    started: list = []
    monkeypatch.setattr(synth, "synthesize_background",
                        lambda inbox_dir, source=None, requester=None: (started.append(source), True)[1])
    before = sorted(p.name for p in inbox.iterdir())
    for junk in ("anything", "x" * 60, "EMAIL"):
        r = client.post(f"/api/brief/refresh?source={junk}", headers=HDR)
        assert r.status_code == 404, junk
    assert started == []
    assert sorted(p.name for p in inbox.iterdir()) == before


def test_a_lane_the_harvest_uses_or_a_built_in_lane_is_still_accepted(client, monkeypatch):
    started: list = []
    monkeypatch.setattr(synth, "synthesize_background",
                        lambda inbox_dir, source=None, requester=None: (started.append(source), True)[1])
    # "teams" is carried by item-2; "calendar" by nothing, but it is a built-in lane.
    for lane in ("teams", "calendar"):
        assert client.post(f"/api/brief/refresh?source={lane}", headers=HDR).status_code == 200
    assert started == ["teams", "calendar"]


def test_a_discovered_lane_is_accepted(client, inbox, monkeypatch):
    _item(inbox, "item-9", source="insights")
    monkeypatch.setattr(synth, "synthesize_background", lambda inbox_dir, source=None, requester=None: True)
    assert client.post("/api/brief/refresh?source=insights", headers=HDR).status_code == 200


def test_no_source_runs_every_lane_and_a_source_runs_one(inbox, monkeypatch):
    calls: list = []
    monkeypatch.setattr(synth, "synthesize_all", lambda d, requester=None: calls.append(("all", None)))
    monkeypatch.setattr(synth, "synthesize", lambda d, source=None, requester=None: calls.append(("one", source)))
    for source in (None, "email"):
        assert synth.synthesize_background(inbox, source=source) is True
        with synth._lock:     # released when the background run finishes
            pass
    assert calls == [("all", None), ("one", "email")]


# --- the staleness signature ----------------------------------------------------------------------

def test_an_item_harvested_during_the_run_leaves_the_brief_stale(client, inbox, monkeypatch):
    """The signature is of what the run READ. Taken after the run, it counted the late item,
    so the brief that never saw it was called fresh and the item reached no brief at all."""
    def broker_while_the_harvest_writes(prompt, url, token, model, requester=None):
        if not (inbox / "late-arrival.json").exists():
            _item(inbox, "late-arrival", source="email")
        return json.dumps({"attention": []})

    monkeypatch.setattr(synth, "_call_broker", broker_while_the_harvest_writes)
    synth.synthesize_all(inbox)
    brief = json.loads((inbox / "brief.json").read_text(encoding="utf-8"))
    assert brief["_source_signature"][0] == 2
    served = client.get("/api/brief", headers=HDR).json()
    assert served["stale_source"] is True
    assert served["stale_reason"] == "item count changed (2 -> 3)"


def test_a_quiet_run_is_fresh(client, inbox, monkeypatch):
    """The control: with nothing written mid-run, the same brief is not stale."""
    monkeypatch.setattr(synth, "_call_broker", lambda *a, **kw: json.dumps({"attention": []}))
    synth.synthesize_all(inbox)
    assert client.get("/api/brief", headers=HDR).json()["stale_source"] is False


def test_the_cut_order_keeps_the_newer_of_two_equal_items(inbox, monkeypatch):
    """`_triage_sort_key` sorts by `_mtime` last. Nothing set it, so ties fell to glob order."""
    for p in inbox.glob("*.json"):
        p.unlink()
    old = _item(inbox, "a-older", priority=2)
    new = _item(inbox, "b-newer", priority=2)
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))
    os.utime(new, (now, now))
    items, state = synth._load_inbox(inbox)
    one_row = len(json.dumps({"_idx": 1, **{k: v for k, v in items[0].items()
                                            if k in ("type", "source", "priority", "title",
                                                     "why", "period")}},
                             separators=(",", ":")))
    monkeypatch.setattr(synth, "ITEMS_CHAR_BUDGET", one_row + 5)
    _rows, idx_to_id, n = synth._condense(items, state)
    assert n == 2
    assert list(idx_to_id.values()) == ["b-newer"]


# --- triage writes --------------------------------------------------------------------------------

def test_two_triage_writes_at_once_both_land(client, inbox, monkeypatch):
    """PATCH read-modify-writes .state.json. Two at once (two cards cleared in quick succession,
    which the optimistic UI invites) each read the old sidecar, and the second write dropped the
    first. Widened here with a slow save; 9p widens it for real."""
    real_save = mainmod._save_state

    def slow_save(state):
        time.sleep(0.2)
        real_save(state)

    monkeypatch.setattr(mainmod, "_save_state", slow_save)
    codes: list[int] = []

    def patch(item_id, status):
        codes.append(client.patch(f"/api/inbox/{item_id}", json={"status": status},
                                  headers=HDR).status_code)

    threads = [threading.Thread(target=patch, args=("item-1", "done")),
               threading.Thread(target=patch, args=("item-2", "dismissed"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200, 200]
    state = json.loads((inbox / ".state.json").read_text(encoding="utf-8"))
    assert state == {"item-1": "done", "item-2": "dismissed"}
