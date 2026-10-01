"""Tests for gateway-owned in-process maintenance (expired-session prune)."""
import datetime as dt

from sqlalchemy import select

from platform_gateway_app import platform_maintenance
from platform_gateway_app.models import SessionRow, User


def _user(session):
    u = User(username="t", password_hash="x")
    session.add(u)
    session.commit()
    return u


def test_prune_deletes_only_expired(session):
    u = _user(session)
    now = dt.datetime.now(dt.timezone.utc)
    session.add(SessionRow(token="live", user_id=u.id, expires_at=now + dt.timedelta(hours=1)))
    session.add(SessionRow(token="dead1", user_id=u.id, expires_at=now - dt.timedelta(hours=1)))
    session.add(SessionRow(token="dead2", user_id=u.id, expires_at=now - dt.timedelta(days=5)))
    session.commit()

    n = platform_maintenance.prune_expired_sessions(session)
    assert n == 2

    remaining = session.execute(select(SessionRow.token)).scalars().all()
    assert remaining == ["live"]


def test_prune_empty_returns_zero(session):
    assert platform_maintenance.prune_expired_sessions(session) == 0


def test_prune_keeps_all_live(session):
    u = _user(session)
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(3):
        session.add(SessionRow(token=f"live{i}", user_id=u.id, expires_at=now + dt.timedelta(hours=i + 1)))
    session.commit()
    assert platform_maintenance.prune_expired_sessions(session) == 0
    assert len(session.execute(select(SessionRow)).scalars().all()) == 3


# --- A-7: the cap used to be read at IMPORT time, unguarded --------------------------------
# A typo'd PLATFORM_DESCRIBE_CAP killed the gateway during startup, in a module `scheduler`
# imports, with a traceback naming int() rather than the variable that was wrong. Same trap
# as edu-suite's TTS_BATCH_SIZE, fixed there and not here.

def test_describe_cap_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("PLATFORM_DESCRIBE_CAP", raising=False)
    assert platform_maintenance._describe_cap() == 8


def test_describe_cap_reads_the_env_at_call_time(monkeypatch):
    """At CALL time, so a test or a compose change takes effect without controlling import
    order, and so an operator can retune it without a rebuild."""
    monkeypatch.setenv("PLATFORM_DESCRIBE_CAP", "3")
    assert platform_maintenance._describe_cap() == 3


def test_a_typod_cap_falls_back_instead_of_killing_the_gateway(monkeypatch):
    """The whole point. An unguarded int() here took the gateway down at startup."""
    monkeypatch.setenv("PLATFORM_DESCRIBE_CAP", "eight")
    assert platform_maintenance._describe_cap() == 8


def test_a_zero_or_negative_cap_still_describes_something(monkeypatch):
    """0 would silently describe nothing every sweep, which reads as "there is nothing to
    do" rather than as a misconfiguration."""
    monkeypatch.setenv("PLATFORM_DESCRIBE_CAP", "0")
    assert platform_maintenance._describe_cap() == 1
