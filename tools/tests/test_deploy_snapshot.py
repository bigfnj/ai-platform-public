"""tools/deploy_snapshot.py - the offline parts: env parsing, body reduction, and the diff.

The diff is what gates a deployment phase, so it has to report every change and nothing else:
a missed key means a regression waved through, a spurious one means a gate people learn to skip.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy_snapshot", TOOLS / "deploy_snapshot.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)


def test_read_env_skips_comments_and_strips_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text('﻿# c\nA=1\n\nB="two words"\nC = x=y\n', encoding="utf-8")
    assert ds.read_env(f) == {"A": "1", "B": "two words", "C": "x=y"}


def test_scalars_keeps_facts_and_drops_bulk():
    body = {"chunks": 5836, "models": [1, 2], "path": "x" * 200, "deep": {"a": {"b": {"c": 1}}}}
    assert ds.scalars(body) == {"chunks": 5836, "models": "[2 items]", "path": "<str 200>",
                                "deep": {"a": {"b": "{...}"}}}


def test_diff_reports_added_removed_and_changed():
    a = {"roles": {"chat": {"resolved": None}, "old": {}}, "gateway": 200}
    b = {"roles": {"chat": {"resolved": "gemma3:4b"}, "new": {}}, "gateway": 200}
    out = ds.diff(a, b)
    assert '~ roles.chat.resolved: null -> "gemma3:4b"' in out
    assert any(c.startswith("- roles.old") for c in out)
    assert any(c.startswith("+ roles.new") for c in out)
    assert len(out) == 3


def test_identical_snapshots_diff_empty():
    s = {"rails": {"x": {"status": 200}}, "containers": {"c": {"id": "abc"}}}
    assert ds.diff(s, s) == []


def test_read_env_reads_like_compose(tmp_path):
    f = tmp_path / ".env"
    f.write_text("export A=1\nPW=hunter2  # rotate me\nQ=\"has # hash\" # c\nS='x'\n", encoding="utf-8")
    assert ds.read_env(f) == {"A": "1", "PW": "hunter2", "Q": "has # hash", "S": "x"}


def test_scalars_redacts_credential_keys_whatever_their_length():
    body = {"broker_token": "sk-live-abc", "nested": {"api_key": "k", "Password": "p", "ok": 1}}
    assert ds.scalars(body) == {"broker_token": "<redacted>",
                                "nested": {"Password": "<redacted>", "api_key": "<redacted>", "ok": 1}}


def test_main_refuses_one_file_for_out_and_diff(tmp_path, monkeypatch):
    base = tmp_path / "s.json"
    base.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ds, "snapshot", lambda *a: {"login": 200})
    monkeypatch.setattr("sys.argv", ["x", "--env", str(tmp_path / ".env"),
                                     "--out", str(base), "--diff", str(base)])
    with pytest.raises(SystemExit) as e:
        ds.main()
    assert e.value.code == 2
    assert base.read_text(encoding="utf-8") == "{}"          # the baseline was not overwritten


def test_main_diffs_against_the_baseline_not_its_own_output(tmp_path, monkeypatch, capsys):
    """The a2d48b9 regression: with the baseline read AFTER writing, the diff compared the new
    snapshot with itself and reported 0 changes, a gate that always passed."""
    base, out = tmp_path / "before.json", tmp_path / "after.json"
    base.write_text(json.dumps({"login": 200, "rails": {}, "roles": {}, "containers": {},
                                "gateway_healthz": {"status": 200}}), encoding="utf-8")
    monkeypatch.setattr(ds, "snapshot", lambda *a: {"login": 401, "rails": {}, "roles": {},
                                                    "containers": {}, "gateway_healthz": {"status": 200}})
    monkeypatch.setattr("sys.argv", ["x", "--env", str(tmp_path / ".env"),
                                     "--out", str(out), "--diff", str(base)])
    assert ds.main() == 1
    assert "~ login: 200 -> 401" in capsys.readouterr().out
