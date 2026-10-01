"""The broker token's two spellings — value vs file — and which one wins.

WHY THIS SUITE EXISTS. The token moved off the process environment because an in-container
shell escape on one rail can read `/proc/1/environ` and lift the single credential every
broker client shares (docs/BACKLOG.md, audit 2026-09-10). That move only helps if the file
form actually works, and only stays safe if the value form keeps working while nine rails and
two compose files migrate at whatever pace their operator chooses. Both halves are asserted
here, plus the three ways the file form goes quietly wrong: the newline `echo` leaves behind,
a path that resolves to nothing, and both variables set at once.

`broker_token()` is deliberately env-driven rather than argument-driven, so every test sets
the environment through monkeypatch and never leaks state into the next.
"""
from __future__ import annotations

import pytest

from platform_core.broker_client import _auth_headers, broker_token


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neither name set, whatever the developer's shell has in it."""
    monkeypatch.delenv("BROKER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BROKER_AUTH_TOKEN_FILE", raising=False)


# --- the pre-existing path, which must not move ---------------------------------------------

def test_unset_is_empty():
    assert broker_token() == ""


def test_reads_the_plain_variable(monkeypatch):
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert broker_token() == "s3cret"


def test_plain_variable_is_stripped(monkeypatch):
    """The behaviour the previous one-liner had, kept: `.strip()` was always there."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "  s3cret\n")
    assert broker_token() == "s3cret"


# --- the file form ---------------------------------------------------------------------------

def test_reads_the_file(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("s3cret", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert broker_token() == "s3cret"


def test_file_trailing_newline_is_stripped(tmp_path, monkeypatch):
    """`echo tok > secret` and every text editor add one. A bearer with a trailing \\n is a
    401 whose cause is invisible in a log, so this is the single most likely way to get the
    file form wrong."""
    p = tmp_path / "broker_token"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert broker_token() == "s3cret"


def test_file_crlf_and_surrounding_whitespace_stripped(tmp_path, monkeypatch):
    """A secret authored on Windows and mounted into Linux carries \\r\\n."""
    p = tmp_path / "broker_token"
    p.write_bytes(b"  s3cret \r\n")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert broker_token() == "s3cret"


def test_empty_file_is_empty_token(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert broker_token() == ""


def test_blank_file_path_falls_through_to_the_variable(monkeypatch):
    """`BROKER_AUTH_TOKEN_FILE: ${...:-}` in compose expands to an empty string when unset, so
    "set but empty" has to mean "not configured" or every rail breaks the moment the key is
    added to a compose file ahead of the secret."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", "   ")
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert broker_token() == "s3cret"


# --- precedence and failure ------------------------------------------------------------------

def test_file_wins_over_the_variable(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert broker_token() == "from-file"


def test_missing_file_does_not_fall_back_to_the_variable(tmp_path, monkeypatch):
    """The deliberate choice, and the one worth pinning. Falling back would leave a stack that
    believes it has moved off the environment still reading the secret from it, and would hide
    a typo'd path behind a rail that looks healthy. "" sends no header and earns a 401."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path / "nope"))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert broker_token() == ""


def test_unreadable_file_is_not_an_exception(tmp_path, monkeypatch):
    """A directory stands in for any OSError that is not ENOENT (EACCES, EISDIR). The client
    must degrade to "no token", never crash the rail at import time."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path))
    assert broker_token() == ""


# --- the header the rest of the client actually sends ----------------------------------------

def test_auth_header_uses_the_file(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert _auth_headers() == {"Authorization": "Bearer s3cret"}


def test_auth_header_absent_when_no_token():
    assert _auth_headers() == {}
