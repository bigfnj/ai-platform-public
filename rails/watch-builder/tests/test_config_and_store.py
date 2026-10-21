"""Broker token resolution and owner-scoped storage.

The token tests mirror the four rules RC005/RC014 leave to each rail to implement, all of
which are failure modes that produce a rail that looks healthy and is not.
"""
from __future__ import annotations

import importlib

import pytest

from watch_builder import config, store


def _settings(monkeypatch, **env):
    for key in ("BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE",
                "WATCH_BUILDER_BROKER_AUTH_TOKEN",
                "WATCH_BUILDER_BROKER_AUTH_TOKEN_FILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config)
    return config.Settings()


# --- the broker token -----------------------------------------------------------------


def test_unprefixed_name_is_read():
    """BROKER_AUTH_TOKEN is one platform-wide secret, not a per-rail setting.

    A rail that only accepts its prefixed spelling invites a compose file to be bent to match
    it, and is then correct under that file and tokenless under the other. co-worker shipped
    exactly that.
    """
    s = _settings(pytest.MonkeyPatch(), BROKER_AUTH_TOKEN="plain")
    assert s.broker_auth_token == "plain"


def test_prefixed_name_wins_so_a_rail_override_is_possible(monkeypatch):
    s = _settings(monkeypatch, BROKER_AUTH_TOKEN="plain",
                  WATCH_BUILDER_BROKER_AUTH_TOKEN="specific")
    assert s.broker_auth_token == "specific"


def test_token_file_wins_over_the_value_and_is_stripped(monkeypatch, tmp_path):
    """A secret written by echo or an editor ends in a newline.

    A bearer token with a trailing \\n is a 401 with no visible cause, so the strip is not
    cosmetic.
    """
    path = tmp_path / "tok"
    path.write_text("from-file\n", encoding="utf-8")
    s = _settings(monkeypatch, BROKER_AUTH_TOKEN="from-env",
                  BROKER_AUTH_TOKEN_FILE=str(path))
    assert s.broker_auth_token == "from-file"


def test_empty_token_file_setting_means_not_configured(monkeypatch):
    """`${BROKER_AUTH_TOKEN_FILE:-}` in compose expands to "".

    A key added to a compose file ahead of the secret must not break every rail.
    """
    s = _settings(monkeypatch, BROKER_AUTH_TOKEN="from-env", BROKER_AUTH_TOKEN_FILE="")
    assert s.broker_auth_token == "from-env"


def test_unreadable_token_file_yields_empty_not_a_fallback(monkeypatch, tmp_path):
    """Falling back to the env var would hide a typo'd path behind a healthy-looking rail.

    It would also leave a deployment that believes it has moved the secret off the
    environment still reading it from there. "" sends no header and earns a visible 401.
    """
    s = _settings(monkeypatch, BROKER_AUTH_TOKEN="from-env",
                  BROKER_AUTH_TOKEN_FILE=str(tmp_path / "nope"))
    assert s.broker_auth_token == ""


def test_model_default_is_a_role_not_a_pinned_name(monkeypatch):
    """A pinned model silently ignores Admin -> Rails.

    Four dead model names survived the 2026-08-04 consolidation this way. RC013 checks the
    in-code default, not just compose.
    """
    s = _settings(monkeypatch)
    assert s.designer_model.startswith("@")


# --- owner-scoped storage -------------------------------------------------------------


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(store.settings, "data_dir", str(tmp_path))
    return tmp_path


DOC = {"schema": 1, "name": "Mine", "platforms": ["emery"], "elements": []}


def test_save_then_load_round_trips(data_dir):
    store.save("alice", "d1", DOC, name="Mine")
    got = store.load("alice", "d1")
    assert got is not None
    assert got["design"] == DOC
    assert got["name"] == "Mine"


def test_one_owner_cannot_read_anothers_design(data_dir):
    """Ownership is enforced by the path, not by a filter.

    There is no query here that could accidentally omit a WHERE clause: a design lives under
    its owner's directory, so asking for someone else's id resolves to a path that does not
    exist under yours.
    """
    store.save("alice", "d1", DOC, name="Mine")
    assert store.load("bob", "d1") is None
    assert store.listing("bob") == []


def test_standalone_gets_its_own_namespace(data_dir):
    """owner=None must not share a directory with a real user.

    Otherwise a standalone dev session sees and overwrites real designs the moment the same
    volume is mounted by a gateway-fronted container.
    """
    store.save(None, "d1", DOC, name="Dev")
    store.save("alice", "d1", DOC, name="Mine")
    assert store.load(None, "d1")["name"] == "Dev"
    assert store.load("alice", "d1")["name"] == "Mine"


def test_a_username_cannot_escape_its_directory(data_dir):
    """The gateway authenticated the name; that does not make it a safe path component."""
    store.save("../../etc", "d1", DOC, name="Evil")
    assert not (data_dir.parent / "etc").exists()
    assert store.load("../../etc", "d1") is not None


def test_a_design_id_cannot_escape_either(data_dir):
    store.save("alice", "../../../oops", DOC, name="Evil")
    written = list(data_dir.rglob("*.json"))
    assert len(written) == 1
    assert written[0].parent.name == "alice"


def test_delete_reports_whether_it_removed_anything(data_dir):
    store.save("alice", "d1", DOC, name="Mine")
    assert store.delete("alice", "d1") is True
    assert store.delete("alice", "d1") is False
    assert store.load("alice", "d1") is None


def test_created_is_preserved_across_saves(data_dir):
    first = store.save("alice", "d1", DOC, name="Mine")
    second = store.save("alice", "d1", DOC, name="Renamed")
    assert second["created"] == first["created"]
    assert second["updated"] >= first["updated"]


def test_a_corrupt_file_is_skipped_rather_than_failing_the_list(data_dir):
    """One bad document must not make the rail unusable."""
    store.save("alice", "good", DOC, name="Good")
    (data_dir / "alice" / "bad.json").write_text("{not json", encoding="utf-8")
    rows = store.listing("alice")
    assert [r["id"] for r in rows] == ["good"]


def test_listing_is_newest_first(data_dir):
    import time
    store.save("alice", "old", DOC, name="Old")
    time.sleep(0.01)
    store.save("alice", "new", DOC, name="New")
    assert [r["id"] for r in store.listing("alice")] == ["new", "old"]
