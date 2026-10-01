"""Shared guards for the workstation suite."""
import pytest


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    r"""Keep the audit log out of the real filesystem.

    `Settings.audit_dir` defaults to "/audit", which is right INSIDE the container (it is the
    mount point) and wrong everywhere else: an absolute POSIX path on Windows resolves to the
    root of the current drive, so running this suite from the repo silently created and grew
    D:\audit\sessions.log. It accumulated one connect_denied line per test run since
    2026-08-24 before anyone noticed, and looked enough like a live service writing platform
    logs to cost a real investigation. On Linux the same default aims at /audit.

    Every test here refuses a handshake, so every test writes an audit line -- which is exactly
    why this fixture is autouse rather than opt-in.
    """
    monkeypatch.setenv("WORKSTATION_AUDIT_DIR", str(tmp_path / "audit"))
