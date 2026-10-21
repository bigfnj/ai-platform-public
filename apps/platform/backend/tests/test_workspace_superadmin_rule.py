"""A plain admin cannot reach a super-admin's sharing through a workspace.

A workspace decides whose owner-scoped records its members read in every rooms-aware rail, so
putting the super-admin in a room is an access grant over the super-admin's data. The Users
routes refuse a plain admin anything touching a super-admin ("only a super-admin can modify a
super-admin"); these routes now carry the same rule:

  * create or update that ADDS a super-admin as a member: 403;
  * update (rename, add, remove) or delete of a room that CURRENTLY holds a super-admin: 403;
  * a super-admin actor: unrestricted;
  * being a member of the room grants a plain admin nothing.

It replaced a gentler rule that let a plain admin edit such a room while the super-admin's row
stayed "frozen". The name bound (1-64 characters, stored stripped) is the second half of this
file. Synthetic accounts only.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from platform_gateway_app import main as m
from platform_gateway_app.auth import hash_password
from platform_gateway_app.models import User, Workspace

ROOT, ADMIN, AIDE, TEACHER = "root-a", "admin-b", "aide-c", "teacher-d"


def mk(session, name: str, *, admin: bool = False, superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("x"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    session.add(u)
    session.commit()
    return u


@pytest.fixture()
def people(session):
    return {"root": mk(session, ROOT, superadmin=True), "admin": mk(session, ADMIN, admin=True),
            "aide": mk(session, AIDE), "teacher": mk(session, TEACHER)}


def _room(session, people, members, actor="root", name="Room 1"):
    return m.admin_create_workspace(m.WorkspaceCreate(name=name, members=members),
                                    admin=people[actor], db=session)


def _refused(fn) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 403, exc.value.detail
    return exc.value


def _state(session, wid):
    session.expire_all()
    w = session.get(Workspace, wid)
    return None if w is None else (w.name, sorted(x.user.username for x in w.members))


# --- create --------------------------------------------------------------------------------

def test_a_plain_admin_cannot_create_a_room_holding_the_superadmin(session, people):
    err = _refused(lambda: _room(session, people, [ADMIN, ROOT], actor="admin"))
    assert ROOT in err.detail
    session.rollback()
    assert session.execute(select(Workspace)).scalars().all() == []


def test_a_plain_admin_still_creates_a_room_without_one(session, people):
    out = _room(session, people, [ADMIN, AIDE], actor="admin")
    assert out["members"] == sorted([ADMIN, AIDE])


# --- update --------------------------------------------------------------------------------

def test_a_plain_admin_cannot_add_the_superadmin_to_their_own_room(session, people):
    out = _room(session, people, [ADMIN, AIDE], actor="admin")
    _refused(lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(members=[ADMIN, AIDE, ROOT]),
        admin=people["admin"], db=session))
    session.rollback()
    assert _state(session, out["id"]) == ("Room 1", sorted([ADMIN, AIDE]))


def test_a_plain_admin_cannot_rename_a_room_the_superadmin_is_in(session, people):
    out = _room(session, people, [ROOT, AIDE])
    err = _refused(lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(name="Renamed"), admin=people["admin"], db=session))
    assert "super-admin" in err.detail
    session.rollback()
    assert _state(session, out["id"]) == ("Room 1", sorted([AIDE, ROOT]))


def test_a_plain_admin_cannot_remove_a_member_of_a_room_the_superadmin_is_in(session, people):
    """Leaving the super-admin OUT of the submitted list is the removal that matters: it
    evicts them from their own sharing. (A list that keeps them is refused as an addition of
    an unmanageable name already, so it could not tell this guard from that one.)"""
    out = _room(session, people, [ROOT, AIDE, TEACHER])
    _refused(lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(members=[AIDE]),
        admin=people["admin"], db=session))
    session.rollback()
    assert _state(session, out["id"]) == ("Room 1", sorted([AIDE, ROOT, TEACHER]))


def test_a_plain_admin_still_edits_their_own_rooms(session, people):
    out = _room(session, people, [ADMIN, AIDE], actor="admin")
    got = m.admin_update_workspace(out["id"],
                                   m.WorkspaceUpdate(name="Caseload", members=[ADMIN, TEACHER]),
                                   admin=people["admin"], db=session)
    assert (got["name"], got["members"]) == ("Caseload", sorted([ADMIN, TEACHER]))


# --- delete --------------------------------------------------------------------------------

def test_a_plain_admin_cannot_delete_a_room_the_superadmin_is_in(session, people):
    out = _room(session, people, [ROOT, AIDE])
    err = _refused(lambda: m.admin_delete_workspace(out["id"], admin=people["admin"],
                                                   db=session))
    assert "super-admin" in err.detail
    session.rollback()
    assert _state(session, out["id"]) is not None


def test_a_plain_admin_still_deletes_their_own_room(session, people):
    out = _room(session, people, [ADMIN, AIDE], actor="admin")
    assert m.admin_delete_workspace(out["id"], admin=people["admin"], db=session) == {"ok": True}


# --- the actor is a member ------------------------------------------------------------------

def test_membership_grants_a_plain_admin_nothing_over_the_superadmins_room(session, people):
    out = _room(session, people, [ROOT, ADMIN])
    _refused(lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(name="Mine now"), admin=people["admin"], db=session))
    _refused(lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(members=[ADMIN]), admin=people["admin"], db=session))
    _refused(lambda: m.admin_delete_workspace(out["id"], admin=people["admin"], db=session))
    session.rollback()
    assert _state(session, out["id"]) == ("Room 1", sorted([ADMIN, ROOT]))


# --- the super-admin ----------------------------------------------------------------------

def test_the_superadmin_does_all_of_it(session, people):
    out = _room(session, people, [ROOT, ADMIN, AIDE])
    got = m.admin_update_workspace(out["id"],
                                   m.WorkspaceUpdate(name="Renamed", members=[ROOT, TEACHER]),
                                   admin=people["root"], db=session)
    assert (got["name"], got["members"]) == ("Renamed", sorted([ROOT, TEACHER]))
    assert m.admin_delete_workspace(out["id"], admin=people["root"], db=session) == {"ok": True}


def test_the_superadmin_may_put_a_plain_admin_in_any_room(session, people):
    out = _room(session, people, [AIDE])
    got = m.admin_update_workspace(out["id"], m.WorkspaceUpdate(members=[AIDE, ADMIN, ROOT]),
                                   admin=people["root"], db=session)
    assert got["members"] == sorted([ADMIN, AIDE, ROOT])


# --- the name bound -----------------------------------------------------------------------

def test_a_name_of_64_characters_is_accepted_and_stored_stripped():
    assert m.WorkspaceCreate(name="  " + "x" * 64 + " ").name == "x" * 64
    assert m.WorkspaceUpdate(name="x" * 64).name == "x" * 64


@pytest.mark.parametrize("model", [m.WorkspaceCreate, m.WorkspaceUpdate])
@pytest.mark.parametrize("name", ["x" * 65, "", "   "])
def test_a_name_outside_1_to_64_characters_is_refused(model, name):
    with pytest.raises(ValidationError):
        model(name=name)


def test_an_update_may_still_leave_the_name_alone():
    assert m.WorkspaceUpdate(members=[AIDE]).name is None
