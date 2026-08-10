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
