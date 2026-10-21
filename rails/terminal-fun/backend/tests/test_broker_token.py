"""This rail's copy of the broker-token resolver.

WHY A PER-RAIL COPY IS TESTED PER RAIL. `broker.py` is tier-3 "free" in the rail contract —
nine rails, nine implementations, deliberately (tools/rail_template.py). Nothing regenerates
this function from a template, so nothing but a test here proves this rail's copy behaves like
platform_core's. That matters most for THIS rail: it is the one whose in-container shell escape
reads `/proc/1/environ`, and so the one the file form exists for.

The module-level `_TOK` is snapshotted at import, which is the pre-existing shape and is left
alone; these tests exercise the resolver directly, which is where the logic lives.
"""
from __future__ import annotations

import pytest

from terminal_fun_app.broker import _broker_token


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("BROKER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BROKER_AUTH_TOKEN_FILE", raising=False)


def test_unset_is_empty():
    assert _broker_token() == ""


def test_reads_the_plain_variable(monkeypatch):
    """The path every deployed compose file uses today; it must not move."""
    monkeypatch.setenv("BROKER_AUTH_TOKEN", " s3cret\n")
    assert _broker_token() == "s3cret"


def test_reads_the_file(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("s3cret", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert _broker_token() == "s3cret"


def test_file_trailing_newline_is_stripped(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    assert _broker_token() == "s3cret"


def test_file_wins_over_the_variable(tmp_path, monkeypatch):
    p = tmp_path / "broker_token"
    p.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(p))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert _broker_token() == "from-file"


def test_missing_file_does_not_fall_back_to_the_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path / "nope"))
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "from-env")
    assert _broker_token() == ""


def test_blank_file_path_falls_through_to_the_variable(monkeypatch):
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", "  ")
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "s3cret")
    assert _broker_token() == "s3cret"


def test_unreadable_file_is_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_AUTH_TOKEN_FILE", str(tmp_path))
    assert _broker_token() == ""
