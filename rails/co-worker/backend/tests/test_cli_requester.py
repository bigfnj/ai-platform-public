"""Whose Claude subscription may answer a synthesis pass: the rail-level guard.

The broker serves a /v1/chat call from a platform owner's own Claude CLI subscription when the
call's `requester` names that owner, and it sees the name and nothing else. Every prompt this
rail builds is the harvested inbox: ONE person's work mail, calendar and Teams traffic, with no
owner field anywhere, read alike by every viewer the gateway admits. So the name may ride only
when the person asking is the person the inbox belongs to (CO_WORKER_INBOX_OWNER), and only on
this rail's own role. These tests drive the real route, the real background starter, every
lane and the real urllib call, and read the bodies that reach the wire.

Synthetic inbox items and neutral account names only.
"""
from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import types
import urllib.request
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from co_worker_app import broker as cw_broker
from co_worker_app import synthesize as synth
from co_worker_app.config import settings
from co_worker_app.main import app, cli_requester

OWNER = "owner-one"      # the inbox's owner, and an owner account on the broker
OTHER = "owner-two"      # another owner account on the broker, NOT this inbox's owner
ROLE = "@co-worker-synthesis"
AB_TOOL = Path(__file__).resolve().parents[2] / "tools" / "ab_synthesis.py"


def hdr(user: str) -> dict[str, str]:
    return {"X-Platform-User": user, "X-Platform-Admin": "1"}


class _Reply:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(settings, "inbox_owner", OWNER)
    monkeypatch.setattr(settings, "synthesis_model", ROLE)
    monkeypatch.setattr(settings, "auto_synthesize_min_interval_s", 0)


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    d = tmp_path / "inbox"
    d.mkdir()
    for i, src in enumerate(("email", "teams")):
        (d / f"item-{i}.json").write_text(json.dumps({
            "title": f"synthetic thing {i}", "source": src, "type": "ask",
            "client": "Client A", "period": "2026W40"}), encoding="utf-8")
    monkeypatch.setattr(settings, "inbox_dir", str(d))
    return d


@pytest.fixture()
def wire(monkeypatch):
    """Every /v1/chat body the rail sends, each answered with an empty brief."""
    sent: list[dict] = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Reply(json.dumps({"message": {"content": json.dumps({"attention": []})}})
                      .encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


@pytest.fixture()
def inline_worker(monkeypatch):
    """Run the synthesis worker on the request's own thread, so the whole path (route ->
    background starter -> every lane -> the wire) has finished when the route returns. Only
    this module's `threading` name is replaced; the real module is untouched."""
    class Inline:
        def __init__(self, target, daemon=None, name=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(synth, "threading", types.SimpleNamespace(Thread=Inline))


@pytest.fixture()
def run(inbox, wire, inline_worker):
    """POST /api/brief/refresh as `user`; return every body that reached the wire."""
    client = TestClient(app, raise_server_exceptions=False)

    def refresh(user: str, query: str = "") -> list[dict]:
        r = client.post(f"/api/brief/refresh{query}", headers=hdr(user))
        assert r.status_code == 200 and r.json()["started"] is True, r.text
        assert synth.get_status()["last_error"] is None, synth.get_status()
        return wire

    return refresh


# --- the route, end to end ------------------------------------------------------------------

def test_the_inbox_owner_refreshing_names_themselves(run):
    """The control: the person asking is the person whose mail is in the prompt."""
    sent = run(OWNER)
    assert len(sent) == 2, "one call per lane (email, teams)"
    assert [b.get("requester") for b in sent] == [OWNER, OWNER]
    assert {b["model"] for b in sent} == {ROLE}


def test_another_subscriber_refreshing_the_same_inbox_stays_local(run):
    """THE case. The other account is an owner as far as the broker knows, and may read this
    page; the mail is not theirs. No call may carry their name, or anyone's."""
    sent = run(OTHER)
    assert sent, "the premise: the run made broker calls"
    assert not any("requester" in b for b in sent), sent


def test_a_blank_inbox_owner_keeps_every_pass_local(run, monkeypatch):
    """Unset is the shipped default: nobody's refresh carries a name, the owner's included."""
    monkeypatch.setattr(settings, "inbox_owner", "   ")
    sent = run(OWNER)
    assert sent and not any("requester" in b for b in sent)


def test_names_are_compared_byte_exact(run):
    """A case variant is another account on the broker; a match here would be a guess."""
    sent = run("Owner-One")
    assert sent and not any("requester" in b for b in sent)


def test_a_foreign_role_never_carries_the_owner(run, monkeypatch):
    """CO_WORKER_SYNTHESIS_MODEL pointed at another @role would carry the owner's name past
    this rail's own switch to whichever role that is."""
    monkeypatch.setattr(settings, "synthesis_model", "@iep")
    sent = run(OWNER)
    assert sent and {b["model"] for b in sent} == {"@iep"}
    assert not any("requester" in b for b in sent)


def test_a_single_lane_refresh_carries_it_too(run):
    """The other branch of the background starter (synthesize(), not synthesize_all())."""
    sent = run(OWNER, "?source=email")
    assert [b.get("requester") for b in sent] == [OWNER]


def test_the_page_load_auto_refresh_carries_it(run):
    """auto=1 comes from the viewer's own browser, which then polls; it is not a scheduler."""
    sent = run(OWNER, "?auto=1")
    assert sent and all(b.get("requester") == OWNER for b in sent)


def test_cli_requester_is_the_one_decision():
    assert cli_requester(OWNER) == OWNER
    assert cli_requester(OTHER) is None
    assert cli_requester(None) is None      # standalone dev: no platform user at all
    assert cli_requester("") is None


def test_a_blank_setting_names_nobody(monkeypatch):
    """Unset is the shipped default. A blank owner must not "match" a blank viewer either."""
    monkeypatch.setattr(settings, "inbox_owner", "")
    assert cli_requester("") is None
    assert cli_requester(OWNER) is None


# --- work nobody is waiting on ------------------------------------------------------------

def _run_entry_point(argv: list[str]) -> None:
    with warnings.catch_warnings():
        # runpy warns that the module is already imported; re-running it is the point.
        warnings.simplefilter("ignore", RuntimeWarning)
        old = sys.argv
        sys.argv = ["co_worker_app.synthesize", *argv]
        try:
            runpy.run_module("co_worker_app.synthesize", run_name="__main__")
        finally:
            sys.argv = old


def test_the_module_entry_point_sends_no_requester(inbox, wire):
    """`python -m co_worker_app.synthesize` runs with nobody at the page."""
    _run_entry_point(["--inbox", str(inbox), "--all"])
    _run_entry_point(["--inbox", str(inbox)])
    assert len(wire) == 3, "two lanes for --all, then one combined pass"
    assert not any("requester" in b for b in wire), wire


def test_the_ab_harness_sends_no_requester(wire):
    """tools/ab_synthesis.py compares models offline; it is nobody's own request."""
    spec = importlib.util.spec_from_file_location("ab_synthesis_under_test", AB_TOOL)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    ab.call_model(ROLE, "a synthetic prompt", "http://broker.invalid", "")
    assert len(wire) == 1 and "requester" not in wire[0]


# --- the header chip --------------------------------------------------------------------------

def _chip(monkeypatch, user: str, roles=("co-worker-synthesis",)) -> dict:
    model = settings.synthesis_model
    monkeypatch.setattr(cw_broker, "roles", lambda: [
        {"role": model.lstrip("@"), "pattern": "m*", "resolved": "m:1", "installed": True}])
    monkeypatch.setattr(cw_broker, "models", lambda: [{"name": "m:1"}])
    monkeypatch.setattr(cw_broker, "status", lambda: {
        "loaded": [], "jobs": [],
        "cli": {"ready": True, "owners": [OWNER, OTHER], "roles": list(roles), "active": []}})
    r = TestClient(app).get("/api/models", headers=hdr(user))
    assert r.status_code == 200, r.text
    (chip,) = r.json()["models"]
    return chip


def test_the_owner_sees_synthesis_on_claude(inbox, monkeypatch):
    assert _chip(monkeypatch, OWNER)["backend"] == "claude"


def test_another_subscriber_sees_the_local_model(inbox, monkeypatch):
    """The broker would serve OTHER, but their refresh carries no requester, so it is local."""
    assert _chip(monkeypatch, OTHER)["backend"] == "ollama"


def test_the_chip_follows_a_blank_inbox_owner(inbox, monkeypatch):
    monkeypatch.setattr(settings, "inbox_owner", "")
    assert _chip(monkeypatch, OWNER)["backend"] == "ollama"


def test_the_chip_follows_a_foreign_role(inbox, monkeypatch):
    monkeypatch.setattr(settings, "synthesis_model", "@iep")
    assert _chip(monkeypatch, OWNER, roles=("iep",))["backend"] == "ollama"
