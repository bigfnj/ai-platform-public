"""A plain admin may reset, demote or delete only a user within their reach.

`admin_update_user` refused a password reset only for a super-admin target, and the rule that
keeps an admin from granting an app they do not hold (`_grantable`) was never applied to one.
Verified before the fix: admin A (one rail) reset the password of admin B (two rails A did not
hold), logged in as B, and granted A both; against a plain user the reset alone reached their apps
and their records. Now a plain admin may reset, demote or delete only a user whose apps all lie
within the admin's own grantable set (`_out_of_reach`), and anything else is a 403 naming the
apps, refused before anything changes. A super-admin is unchanged.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from platform_gateway_app import main as m
from platform_gateway_app.auth import create_session, hash_password, verify_password
from platform_gateway_app.models import Entitlement, SessionRow, User


def _user(s, name: str, apps: tuple[str, ...] = (), *, admin: bool = False,
          superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("old"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    u.entitlements.extend(Entitlement(app_id=a) for a in apps)
    s.add(u)
    s.commit()
    return u


@pytest.fixture()
def box(session):
    """A (recipe-book) and B (terminal-fun, workstation) are plain admins; C is a plain user on
    terminal-fun; D a plain user on recipe-book alone; root the super-admin. Rails the public
    snapshot ships, because its catalog has no withheld rail and entitled_app_ids reads it."""
    return {
        "root": _user(session, "root", superadmin=True),
        "a": _user(session, "admin-a", ("recipe-book",), admin=True),
        "b": _user(session, "admin-b", ("terminal-fun", "workstation"), admin=True),
        "c": _user(session, "user-c", ("terminal-fun",)),
        "d": _user(session, "user-d", ("recipe-book",)),
    }


def _refused(fn) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 403, exc.value.detail
    return exc.value


# --- refused: a target who holds an app the admin cannot grant ---------------------------------

def test_a_plain_admin_cannot_reset_the_password_of_an_admin_beyond_their_reach(session, box):
    a, b = box["a"], box["b"]
    create_session(session, b, 168)
    err = _refused(lambda: m.admin_update_user(b.id, m.UserUpdate(password="x"), admin=a,
                                               db=session))
    assert "reset the password of 'admin-b'" in err.detail
    assert "terminal-fun, workstation" in err.detail, "the refusal names the apps that put B out of reach"
    session.expire_all()
    assert verify_password("old", session.get(User, b.id).password_hash), "B's password changed"
    assert session.execute(select(SessionRow).where(SessionRow.user_id == b.id)).first(), (
        "a refused reset still revoked B's sessions")


def test_a_plain_admin_cannot_reset_a_plain_user_beyond_their_reach(session, box):
    _refused(lambda: m.admin_update_user(box["c"].id, m.UserUpdate(password="x"),
                                         admin=box["a"], db=session))
    session.expire_all()
    assert verify_password("old", session.get(User, box["c"].id).password_hash)


def test_a_plain_admin_cannot_demote_an_admin_beyond_their_reach(session, box):
    b = box["b"]
    err = _refused(lambda: m.admin_update_user(b.id, m.UserUpdate(is_admin=False),
                                               admin=box["a"], db=session))
    assert "demote 'admin-b'" in err.detail
    session.expire_all()
    assert session.get(User, b.id).is_admin


def test_a_plain_admin_cannot_delete_a_user_beyond_their_reach(session, box):
    for target in (box["b"], box["c"]):
        err = _refused(lambda: m.admin_delete_user(target.id, admin=box["a"], db=session))
        assert f"delete '{target.username}'" in err.detail
    session.expire_all()
    assert session.get(User, box["b"].id) is not None and session.get(User, box["c"].id) is not None


def test_a_combined_update_is_refused_whole(session, box):
    """A reset sent with an app edit changes neither: the refusal comes before any write."""
    a, c = box["a"], box["c"]
    _refused(lambda: m.admin_update_user(c.id, m.UserUpdate(password="x",
                                                        apps=["terminal-fun", "recipe-book"]),
                                         admin=a, db=session))
    session.expire_all()
    assert sorted(e.app_id for e in session.get(User, c.id).entitlements) == ["terminal-fun"]


# --- allowed: within reach, or a super-admin ---------------------------------------------------

def test_a_plain_admin_may_reset_demote_and_delete_within_their_reach(session, box):
    a, d = box["a"], box["d"]
    m.admin_update_user(d.id, m.UserUpdate(password="new"), admin=a, db=session)
    assert verify_password("new", session.get(User, d.id).password_hash)
    e = _user(session, "admin-e", ("recipe-book",), admin=True)
    m.admin_update_user(e.id, m.UserUpdate(is_admin=False), admin=a, db=session)
    assert not session.get(User, e.id).is_admin
    nobody = _user(session, "user-none")          # no apps at all: within anyone's reach
    m.admin_update_user(nobody.id, m.UserUpdate(password="new"), admin=a, db=session)
    assert m.admin_delete_user(d.id, admin=a, db=session) == {"ok": True}


def test_an_admin_may_still_reset_their_own_password(session, box):
    a = box["a"]
    m.admin_update_user(a.id, m.UserUpdate(password="mine"), admin=a, db=session)
    assert verify_password("mine", session.get(User, a.id).password_hash)


def test_a_superadmin_still_can(session, box):
    root, b = box["root"], box["b"]
    m.admin_update_user(b.id, m.UserUpdate(password="new"), admin=root, db=session)
    assert verify_password("new", session.get(User, b.id).password_hash)
    m.admin_update_user(b.id, m.UserUpdate(is_admin=False), admin=root, db=session)
    assert not session.get(User, b.id).is_admin
    assert m.admin_delete_user(box["c"].id, admin=root, db=session) == {"ok": True}


def test_promotion_and_grants_are_not_this_rule(session, box):
    """Out of reach blocks the three that take a user's login or standing. A grant still works
    under the frozen-apps rule, and a promotion is not one of the three."""
    a, c = box["a"], box["c"]
    out = m.admin_update_user(c.id, m.UserUpdate(apps=["recipe-book"]), admin=a, db=session)
    assert out["apps"] == ["recipe-book", "terminal-fun"], "terminal-fun frozen, recipe-book added"
    assert m.admin_update_user(c.id, m.UserUpdate(is_admin=True), admin=a, db=session)["is_admin"]


def test_an_app_no_longer_in_the_catalog_puts_nobody_out_of_reach(session, box):
    """A row for a rail that is gone grants nothing (entitled_app_ids drops it), so it does not
    count; the shell drops it the same way (user_rule.ts reads only catalog ids)."""
    stale = _user(session, "user-stale", ("recipe-book", "a-rail-since-removed"))
    m.admin_update_user(stale.id, m.UserUpdate(password="new"), admin=box["a"], db=session)
    assert verify_password("new", session.get(User, stale.id).password_hash)


def test_the_list_route_carries_what_the_shell_judges_reach_by(session, box):
    """The Users tab greys the three controls out from `apps` and `grantable`; both are here."""
    out = m.admin_list_users(admin=box["a"], db=session)
    assert out["grantable"] == ["recipe-book"]
    rows = {u["username"]: u for u in out["users"]}
    assert rows["admin-b"]["apps"] == ["terminal-fun", "workstation"]
