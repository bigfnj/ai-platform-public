"""co-worker's broker token: two names, two spellings each, one field everything reads.

This rail resolves the token differently from every other one — a pydantic-settings field with
`validation_alias` rather than an `os.environ.get` at module scope — and it is the rail this
whole class of bug was found on. It used to rely on `env_prefix`, so the field resolved to
CO_WORKER_BROKER_AUTH_TOKEN and nothing else; the installer compose was then bent to spell it
that way, which made the rail work under one compose file and silently tokenless under the
other, and nobody noticed because the token was empty everywhere at the time.

So the alias table is the thing worth pinning, and it is now four names rather than two. All of
them are asserted here, including the precedence between them, because "works under the compose
file I happened to test" is exactly the failure this rail already shipped once.
"""
from __future__ import annotations

import pytest

from co_worker_app.config import Settings

NAMES = ("BROKER_AUTH_TOKEN", "CO_WORKER_BROKER_AUTH_TOKEN",
         "BROKER_AUTH_TOKEN_FILE", "CO_WORKER_BROKER_AUTH_TOKEN_FILE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


# --- the value form, unchanged ----------------------------------------------------------

def test_unprefixed_value(monkeypatch):
    """What deploy/docker-compose.yml passes to all nine services."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert Settings().broker_auth_token == "s3cret"


def test_prefixed_value(monkeypatch):
    """What the installer compose passes. Both files have to work."""
    monkeypatch.setenv("CO_WORKER_BROKER_AUTH_TOKEN", "s3cret")
    assert Settings().broker_auth_token == "s3cret"


def test_prefixed_value_wins_over_unprefixed(monkeypatch):
    """AliasChoices order: a rail-specific override beats the platform-wide token."""
    monkeypatch.setenv("CO_WORKER_BROKER_AUTH_TOKEN", "mine")
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "shared")
    assert Settings().broker_auth_token == "mine"


def test_unset_is_empty():
    assert Settings().broker_auth_token == ""


# --- the file form ----------------------------------------------------------------------

def test_unprefixed_file(tmp_path, monkeypatch):
    p = tmp_path / "tok"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert Settings().broker_auth_token == "s3cret"


def test_prefixed_file(tmp_path, monkeypatch):
    """validation_alias bypasses env_prefix, so the prefixed spelling has to be listed by hand
    on the _FILE field exactly as it is on the value field. Easy to forget; silent if you do."""
    p = tmp_path / "tok"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("CO_WORKER_BROKER_AUTH_TOKEN_FILE", str(p))
    assert Settings().broker_auth_token == "s3cret"


def test_trailing_newline_is_stripped(tmp_path, monkeypatch):
    """Set through the ENVIRONMENT, not as an init kwarg. A field carrying a validation_alias
    can only be populated by one of its aliases unless populate_by_name is on, so
    `Settings(broker_auth_token_file=...)` is silently swallowed by extra="ignore" and the
    assertion would be testing nothing. Every test in this file goes through the env for that
    reason — which is also the only way the rail is ever actually configured."""
    p = tmp_path / "tok"
    p.write_text("  s3cret \r\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert Settings().broker_auth_token == "s3cret"


def test_file_wins_over_the_value(tmp_path, monkeypatch):
    p = tmp_path / "tok"
    p.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    monkeypatch.setenv("CO_WORKER_BROKER_AUTH_TOKEN", "from-env")
    assert Settings().broker_auth_token == "from-file"


def test_missing_file_does_not_fall_back_to_the_value(tmp_path, monkeypatch):
    """"" sends no header and earns the broker's 401. Falling back would leave a deployment
    that believes it has moved off the environment still reading the secret from it."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path / "nope"))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert Settings().broker_auth_token == ""


def test_blank_file_path_falls_through_to_the_value(monkeypatch):
    """`${BROKER_AUTH_TOKEN_FILE:-}` expands to "" when unset, so a key added to a compose file
    ahead of the secret must not disable the rail."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", "  ")
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert Settings().broker_auth_token == "s3cret"


def test_unreadable_file_does_not_raise(tmp_path, monkeypatch):
    """A directory stands in for any non-ENOENT OSError (EACCES, EISDIR). Settings() runs at
    import; raising here would take the whole rail down rather than degrade one call path."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert Settings().broker_auth_token == ""
