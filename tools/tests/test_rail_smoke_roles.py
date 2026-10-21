"""The role-resolution check — the one that was missing on 2026-09-10.

gemma4:12b was removed from the Ollama store and `@chat-fast` and `@terminal-fun` silently
resolved to nothing. The full pytest suite, rail_conformance, rail_template AND all 24 content
checks stayed green, because a role with no model is not a content fact and nothing else asked.
It was found by accident, hours later, while sizing a directory for an unrelated reason.

So these tests are mostly about the check being ABLE to fail. A guard that reports `ok` when a
role is broken is worse than no guard, because it is read as evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Every test below takes the `smoke` fixture, which conftest.load_tool() reads from
# tools/rail_smoke.py — and that tool is withheld from the public snapshot (see publish.json
# paths.exclude: it is coupled to the full private fleet and to tools/smoke-baseline.json).
# Without this guard the public suite reports 16 ERRORS from a bare `assert path.is_file()`
# inside a session fixture, which reads as a broken repo rather than as a deliberate omission.
#
# Skipped rather than excluded from the snapshot: nothing in this file is private — no forbidden
# string, no withheld rail name, no real data — and the skip reason is a truer artifact than
# silence, because it tells a public reader the private repo carries a check they cannot run.
# Guarded on the FILE, not on an ImportError: the tool is loaded by path, so importorskip has no
# module name to ask for.
_RAIL_SMOKE = Path(__file__).resolve().parents[1] / "rail_smoke.py"
if not _RAIL_SMOKE.is_file():
    pytest.skip(
        "tools/rail_smoke.py is withheld from the public snapshot, so the role-resolution "
        "check it tests is not present here. This file is unchanged and runs in the private "
        "repo; see publish.json paths.exclude for why the tool does not ship.",
        allow_module_level=True,
    )


class TestImageRoles:
    """Image roles are excluded, and the exclusion comes from the contract, not a hardcoded name."""

    def test_recipe_icon_is_excluded(self, smoke):
        """flux-schnell is a diffusers model in the HF cache, not an Ollama tag, so the
        broker's `installed` flag (computed from Ollama's tag list) is always False for it.
        Reporting it every run would train the reader to ignore this check."""
        assert "recipe-icon" in smoke.image_roles()

    def test_it_is_derived_from_the_manifests_not_a_literal(self, smoke):
        """A second image role must be handled the day it is DECLARED, not the day someone
        remembers to update this checker."""
        src = (smoke.REPO / "tools" / "rail_smoke.py").read_text(encoding="utf-8")
        body = src.split("def image_roles")[1].split("def broker_token")[0]
        assert "recipe-icon" not in body, "the exclusion is hardcoded instead of read from rail.json"
        assert "rail.json" in body

    def test_chat_roles_are_not_excluded(self, smoke):
        """Over-broad exclusion is the failure mode that makes this check vacuous."""
        excluded = smoke.image_roles()
        for role in ("chat-fast", "terminal-fun", "embed", "vision"):
            assert role not in excluded


class TestBrokerToken:
    """Resolution order, against a FIXTURE deploy/.env rather than the deployment's own.

    These used to read the real `deploy/.env`, which made them assert a fact about whichever
    box ran them instead of about the code. On an install that runs the broker OPEN on purpose,
    no BROKER_AUTH_TOKEN is set and both file-backed cases failed on every run -- `core` went
    permanently red for a deliberate configuration choice, which is the fastest way to teach
    everyone to stop reading the result. Writing the .env asserts the same behaviour, more
    strictly (the exact token, not merely a truthy one), on a tokenless install and a tokened
    one alike.
    """

    @staticmethod
    def _repo_with_env(smoke, monkeypatch, tmp_path, body: str) -> None:
        """Repoint broker_token()'s REPO at a throwaway tree holding `deploy/.env`."""
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / ".env").write_text(body, encoding="utf-8")
        monkeypatch.setattr(smoke, "REPO", tmp_path)

    def test_env_wins(self, smoke, monkeypatch, tmp_path):
        """A real env var beats the file -- so the file has to be there to be beaten."""
        self._repo_with_env(smoke, monkeypatch, tmp_path, "BROKER_AUTH_TOKEN=from-file\n")
        monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
        assert smoke.broker_token() == "from-env"

    def test_falls_back_to_deploy_env(self, smoke, monkeypatch, tmp_path):
        """The token normally lives only in deploy/.env; the checker has to find it there --
        past the other keys, and past the quoting deploy/.env is written with."""
        self._repo_with_env(
            smoke, monkeypatch, tmp_path,
            'OLLAMA_HOST=127.0.0.1\nBROKER_AUTH_TOKEN="from-file"\nBROKER_TOKENS_FILE=x\n')
        monkeypatch.delenv("BROKER_AUTH_TOKEN", raising=False)
        assert smoke.broker_token() == "from-file", "no token resolved from deploy/.env"

    def test_blank_env_does_not_shadow_the_file(self, smoke, monkeypatch, tmp_path):
        self._repo_with_env(smoke, monkeypatch, tmp_path, "BROKER_AUTH_TOKEN=from-file\n")
        monkeypatch.setenv("BROKER_AUTH_TOKEN", "   ")
        assert smoke.broker_token() == "from-file", "an empty env var swallowed the real token"

    def test_a_tokenless_install_resolves_to_empty(self, smoke, monkeypatch, tmp_path):
        """An install that runs the broker open names no token in deploy/.env. That must
        resolve to "" cleanly -- not raise, and not find a stray match."""
        self._repo_with_env(smoke, monkeypatch, tmp_path, "OLLAMA_HOST=127.0.0.1\n")
        monkeypatch.delenv("BROKER_AUTH_TOKEN", raising=False)
        assert smoke.broker_token() == ""

    def test_no_env_file_at_all_resolves_to_empty(self, smoke, monkeypatch, tmp_path):
        """A bare checkout has no deploy/.env at all; broker_token() must not blow up on it."""
        monkeypatch.setattr(smoke, "REPO", tmp_path)
        monkeypatch.delenv("BROKER_AUTH_TOKEN", raising=False)
        assert smoke.broker_token() == ""


class TestUnresolvedRoles:
    """The check itself, against a stubbed broker."""

    @staticmethod
    def _stub(smoke, monkeypatch, payload, status_ok=True):
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(payload).encode()

        def fake_urlopen(_req, timeout=None):
            if not status_ok:
                raise OSError("connection refused")
            return _Resp()

        monkeypatch.setattr(smoke.urllib.request, "urlopen", fake_urlopen)

    def test_all_installed_is_empty_and_expects_empty(self, smoke, monkeypatch):
        self._stub(smoke, monkeypatch, {"roles": [
            {"role": "chat", "installed": True},
            {"role": "embed", "installed": True},
        ]})
        got = smoke.unresolved_roles()["unresolved_roles"]
        assert got["value"] == []
        assert got["expect"] == []

    def test_a_broken_role_is_reported(self, smoke, monkeypatch):
        """The 2026-09-10 regression, reproduced."""
        self._stub(smoke, monkeypatch, {"roles": [
            {"role": "chat", "installed": True},
            {"role": "chat-fast", "installed": False},
            {"role": "terminal-fun", "installed": False},
        ]})
        got = smoke.unresolved_roles()["unresolved_roles"]
        assert got["value"] == ["chat-fast", "terminal-fun"]
        assert got["value"] != got["expect"], "this must FAIL the run, not merely be recorded"

    def test_an_image_role_is_not_reported(self, smoke, monkeypatch):
        self._stub(smoke, monkeypatch, {"roles": [
            {"role": "recipe-icon", "installed": False},
            {"role": "chat", "installed": True},
        ]})
        assert smoke.unresolved_roles()["unresolved_roles"]["value"] == []

    def test_an_image_role_does_not_mask_a_real_one(self, smoke, monkeypatch):
        """Excluding flux-schnell must not swallow a genuine break in the same response."""
        self._stub(smoke, monkeypatch, {"roles": [
            {"role": "recipe-icon", "installed": False},
            {"role": "chat-fast", "installed": False},
        ]})
        assert smoke.unresolved_roles()["unresolved_roles"]["value"] == ["chat-fast"]

    def test_a_missing_installed_key_counts_as_broken(self, smoke, monkeypatch):
        """Fail closed on a shape change: absent is not the same as True."""
        self._stub(smoke, monkeypatch, {"roles": [{"role": "chat"}]})
        assert smoke.unresolved_roles()["unresolved_roles"]["value"] == ["chat"]

    def test_an_unreachable_broker_is_an_error_not_a_pass(self, smoke, monkeypatch):
        """The worst outcome would be silently reporting zero broken roles because nobody
        answered."""
        self._stub(smoke, monkeypatch, {}, status_ok=False)
        got = smoke.unresolved_roles()["unresolved_roles"]
        assert "error" in got
        assert "value" not in got

    def test_the_note_says_what_to_do(self, smoke, monkeypatch):
        self._stub(smoke, monkeypatch, {"roles": [{"role": "chat-fast", "installed": False}]})
        note = smoke.unresolved_roles()["unresolved_roles"]["note"]
        assert "roles.json" in note or "re-pull" in note


class TestWiredIntoTheRun:
    def test_report_fails_on_a_broken_role(self, smoke, capsys):
        """End to end through report(): a broken role must print FAIL and return non-zero."""
        results = {"broker": {"unresolved_roles": {
            "value": ["chat-fast"], "expect": [], "note": "n"}}}
        rc = smoke.report(results, None)
        assert rc == 1
        assert "FAIL" in capsys.readouterr().out

    def test_report_passes_when_empty(self, smoke, capsys):
        results = {"broker": {"unresolved_roles": {"value": [], "expect": [], "note": "n"}}}
        assert smoke.report(results, None) == 0
        assert "ok" in capsys.readouterr().out
