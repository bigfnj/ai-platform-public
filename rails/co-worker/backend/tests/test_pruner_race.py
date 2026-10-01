"""An item the pruner removes mid-request must degrade, never 500 the rail.

`_item_files()` globbed the inbox and then sorted with `p.stat().st_mtime` — the stat
outside every try. tools/prune_inbox.py renames items into archive/ and unlinks expired
ones in that same directory, and its docstring says it is safe to run repeatedly and
concurrently. When it wins the race, the FileNotFoundError escaped `_item_files`, escaped
`_collect` (whose try wraps only `_read_item`), and took out /api/inbox, /api/archive,
/api/healthz, /api/models and /api/brief at once — the opposite of the degrade-never-fail
contract this rail documents. `synthesize._source_signature` had the same shape, where it
would sink a synthesis run that had already paid for its model calls.

The race is reproduced by making the glob yield a path that is already unlinked, which is
exactly what the kernel hands back when the pruner deletes between readdir and stat.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from co_worker_app import main as mainmod
from co_worker_app import synthesize as synth
from co_worker_app.config import settings
from co_worker_app.main import app

HDR = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    d = tmp_path / "inbox"
    (d / "archive").mkdir(parents=True)
    (d / "item-1.json").write_text(
        json.dumps({"title": "a thing", "source": "email", "period": "2026W34"}),
        encoding="utf-8")
    (d / "archive" / "old-1.json").write_text(
        json.dumps({"title": "an old thing", "source": "email", "period": "2026W30"}),
        encoding="utf-8")
    (d / "brief.json").write_text(
        json.dumps({"attention": [], "_source_signature": [1, 0.0]}), encoding="utf-8")
    monkeypatch.setattr(settings, "inbox_dir", str(d))
    return d


@pytest.fixture()
def pruned_mid_sort(inbox, monkeypatch):
    """Every `*.json` glob of the inbox (and archive/) also yields an already-unlinked file."""
    directories = (inbox, inbox / "archive")
    real_glob = Path.glob

    def glob_yielding_a_pruned_file(self, pattern, *args, **kwargs):
        yield from real_glob(self, pattern, *args, **kwargs)
        if pattern == "*.json" and self in directories:
            yield self / "pruned-mid-sort.json"

    monkeypatch.setattr(Path, "glob", glob_yielding_a_pruned_file)


def test_item_files_drops_the_file_instead_of_raising(inbox, pruned_mid_sort):
    assert [p.name for p in mainmod._item_files()] == ["item-1.json"]
    assert [p.name for p in mainmod._item_files(inbox / "archive")] == ["old-1.json"]


def test_every_read_route_still_answers_200(inbox, pruned_mid_sort, monkeypatch):
    # modelstate is the only broker caller among these; stub it so the test needs no GPU.
    monkeypatch.setattr(mainmod.modelstate, "resolve",
                        lambda specs: {"broker": "unreachable", "models": []})
    client = TestClient(app, raise_server_exceptions=False)

    for path in ("/api/inbox", "/api/archive", "/api/healthz", "/api/models", "/api/brief"):
        assert client.get(path, headers=HDR).status_code == 200, path

    assert [i["_id"] for i in client.get("/api/inbox", headers=HDR).json()["items"]] == ["item-1"]
    assert client.get("/api/healthz", headers=HDR).json()["inbox_items"] == 1


def test_both_signatures_skip_the_vanished_file_and_agree(inbox, pruned_mid_sort):
    """They are compared against each other by _brief_is_stale, so a file only one of them
    can count would make every brief read as stale."""
    count, newest = mainmod._inbox_signature()
    assert count == 1 and newest > 0
    assert synth._source_signature(inbox) == [count, newest]
