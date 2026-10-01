"""The validator side of BROKER_AUTH_TOKEN_FILE.

Clients gained a file form so the shared secret stops living in the process environment. The
broker has to accept the same form or the two halves of the handshake read different sources
and every rail 401s — but it must NOT copy the clients' failure behaviour, and that asymmetry
is the substance of this file.

On a client, an unreadable token file means "send no header", and the broker answers 401. Loud.
Here, an empty `auth_token` means the control plane is OPEN: `require_token()` returns early and
every /v1/* route stops requiring a bearer, with no symptom other than everything continuing to
work. A typo'd path must not be able to silently disarm the gate it was added to strengthen, so
BrokerSettings refuses to construct instead.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BrokerSettings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_no_file_leaves_the_value_form_alone():
    assert BrokerSettings(auth_token="s3cret").auth_token == "s3cret"


def test_unset_is_open():
    """Empty = open (dev / staged rollout). The pre-existing default, pinned."""
    assert BrokerSettings().auth_token == ""


def test_reads_the_file(tmp_path):
    p = tmp_path / "broker_token"
    p.write_text("s3cret", encoding="utf-8")
    assert BrokerSettings(auth_token_file=str(p)).auth_token == "s3cret"


def test_trailing_newline_is_stripped(tmp_path):
    """The comparison is `secrets.compare_digest` against a header value. A token that differs
    from the client's only by a trailing \\n rejects every rail, and the log says nothing."""
    p = tmp_path / "broker_token"
    p.write_text("s3cret\n", encoding="utf-8")
    assert BrokerSettings(auth_token_file=str(p)).auth_token == "s3cret"


def test_file_wins_over_the_value(tmp_path):
    p = tmp_path / "broker_token"
    p.write_text("from-file\n", encoding="utf-8")
    s = BrokerSettings(auth_token="from-env", auth_token_file=str(p))
    assert s.auth_token == "from-file"


def test_missing_file_refuses_to_start(tmp_path):
    """Not "" — that would leave the whole control plane unauthenticated and looking healthy."""
    with pytest.raises(ValidationError) as exc:
        BrokerSettings(auth_token="from-env", auth_token_file=str(tmp_path / "nope"))
    assert "BROKER_AUTH_TOKEN_FILE" in str(exc.value)


def test_unreadable_file_refuses_to_start(tmp_path):
    """A directory stands in for any non-ENOENT OSError (EACCES, EISDIR)."""
    with pytest.raises(ValidationError):
        BrokerSettings(auth_token_file=str(tmp_path))


def test_empty_file_is_accepted_as_open(tmp_path):
    """Distinct from a MISSING file on purpose. An empty file is a deliberate statement that
    the deployment has no token yet; a missing one is a mistake. Conflating them would either
    refuse to boot a legitimate staged rollout or accept the typo this rule exists to catch."""
    p = tmp_path / "broker_token"
    p.write_text("\n", encoding="utf-8")
    assert BrokerSettings(auth_token="from-env", auth_token_file=str(p)).auth_token == ""


def test_env_var_spelling(tmp_path, monkeypatch):
    """env_prefix is BROKER_, so the field `auth_token_file` is fed by BROKER_AUTH_TOKEN_FILE —
    the same unprefixed-from-the-rails' point of view name every client reads. Asserted rather
    than assumed: a field renamed for readability would silently stop being wired to anything."""
    p = tmp_path / "broker_token"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert BrokerSettings().auth_token == "s3cret"


def test_env_var_value_form_still_wired(monkeypatch):
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert BrokerSettings().auth_token == "s3cret"
