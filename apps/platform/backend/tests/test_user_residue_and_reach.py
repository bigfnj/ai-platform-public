"""Two admin-pane facts that were wrong about people.

1. Deleting a user removes their personal theme. The row's FK says ondelete=CASCADE, which SQLite
   never enforces without PRAGMA foreign_keys=ON, so the row survived and the next account to
   take the freed id inherited the deleted user's palette.
2. The Rail Manager's "N users entitled" counts people who can reach the rail. It counted
   Entitlement rows, so a super-admin (who reaches every rail with no row) was missed and a rail
   in daily use by its owner reported 0 users, the exact number that says "safe to switch off".
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from platform_gateway_app import main as m
from platform_gateway_app import theme
from platform_gateway_app.auth import hash_password
from platform_gateway_app.catalog import APP_IDS
from platform_gateway_app.models import Entitlement, User, UserTheme


def _user(s, name, **kw) -> User:
    u = User(username=name, password_hash=hash_password("x"), **kw)
    s.add(u)
    s.commit()
    return u


def test_deleting_a_user_deletes_their_theme_row(session):
    admin = _user(session, "owner-one", is_admin=True, is_superadmin=True)
    _user(session, "owner-two", is_admin=True)
    gone = _user(session, "bob")
    theme.set_user_override(session, gone, "dracula", "dark")
    gone_id = gone.id
    m.admin_delete_user(gone_id, admin=admin, db=session)
    assert session.execute(select(UserTheme).where(UserTheme.user_id == gone_id)
                           ).scalar_one_or_none() is None


def test_the_next_account_on_a_reused_id_starts_from_the_default_theme(session):
    admin = _user(session, "owner-one", is_admin=True, is_superadmin=True)
    gone = _user(session, "bob")
    theme.set_user_override(session, gone, "dracula", "dark")
    gone_id = gone.id
    m.admin_delete_user(gone_id, admin=admin, db=session)
    fresh = _user(session, "user-c")
    assert fresh.id == gone_id, "SQLite did not reuse the id; the scenario did not happen"
    assert theme.user_override(session, fresh) is None


def _catalog(monkeypatch, session) -> dict[str, int]:
    monkeypatch.setattr(m.app.state, "settings", SimpleNamespace(
        enabled_apps=sorted(APP_IDS), resolved_app_dists=lambda: {}), raising=False)
    monkeypatch.setattr(m.app.state, "backends", {}, raising=False)
    out = m.admin_rail_manager(admin=None, db=session)
    return {r["id"]: r["entitled_users"] for r in out["rails"]}


def test_a_super_admin_counts_for_every_rail(monkeypatch, session):
    _user(session, "owner-one", is_admin=True, is_superadmin=True)
    counts = _catalog(monkeypatch, session)
    assert set(counts.values()) == {1}, counts


def test_a_person_is_counted_once_and_rows_still_count(monkeypatch, session):
    root = _user(session, "owner-one", is_admin=True, is_superadmin=True)
    bob = _user(session, "bob")
    rail = sorted(APP_IDS)[0]
    session.add_all([Entitlement(user_id=root.id, app_id=rail),
                     Entitlement(user_id=bob.id, app_id=rail)])
    session.commit()
    counts = _catalog(monkeypatch, session)
    assert counts[rail] == 2          # the super-admin's own row does not count them twice
    assert counts[sorted(APP_IDS)[1]] == 1
