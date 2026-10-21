"""A plain admin places in a workspace, and edits a room holding, only users within reach.

The reach rule (`_out_of_reach`, test_admin_reach.py) stopped a plain admin resetting, demoting or
deleting a user who holds an app the admin could not grant, but the workspace rule kept anyone who
was not a super-admin. Reproduced on the real routes by the 2026-10-15 delta audit: admin A
(recipe-book only), refused a reset of teacher T (iep-goals), could still add anyone holding
iep-goals to T's room, which then showed them every child in it; put two edu-suite teachers in one
room, so each read the other's work as a peer; or remove T from T's own room. Now:

  * `_manageable` keeps, for a plain admin, a user who is not a super-admin AND within reach, so
    adding anyone else to a room is a 403 (and the list route says who may be added);
  * a room that holds a member beyond reach NOW cannot be renamed, changed or deleted by a plain
    admin (`_refuse_room_out_of_reach`), so they cannot remove T from T's own room either; the 403
    names each such member and the apps;
  * a super-admin is unaffected, and so is a pair within reach.

Rails the public snapshot ships (recipe-book, terminal-fun, workstation) stand in for the audit's
iep-goals and edu-suite, because its catalog has no withheld rail and the rule reads the catalog.
Synthetic accounts only.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from platform_gateway_app import main as m
from platform_gateway_app.auth import hash_password, peer_usernames, room_ids
from platform_gateway_app.models import Entitlement, User, Workspace


def _user(s, name: str, apps: tuple[str, ...] = (), *, admin: bool = False,
          superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("x"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    u.entitlements.extend(Entitlement(app_id=a) for a in apps)
    s.add(u)
    s.commit()
    return u


@pytest.fixture()
def box(session):
    """A holds recipe-book; T (two rails A lacks) and C (one of them) are beyond A's reach; D
    (recipe-book) and N (no apps) are within it; root is the super-admin."""
    return {
        "root": _user(session, "root-r", superadmin=True),
        "a": _user(session, "admin-a", ("recipe-book",), admin=True),
        "t": _user(session, "teacher-t", ("terminal-fun", "workstation")),
        "c": _user(session, "aide-c", ("terminal-fun",)),
        "d": _user(session, "user-d", ("recipe-book",)),
        "n": _user(session, "user-n"),
    }


def _room(session, actor: User, members: list[str], name: str = "Room 12") -> dict:
    return m.admin_create_workspace(m.WorkspaceCreate(name=name, members=members),
                                    admin=actor, db=session)


def _refused(session, fn) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 403, exc.value.detail
    session.rollback()
    return exc.value


def _state(session, wid: int):
    session.expire_all()
    w = session.get(Workspace, wid)
    return None if w is None else (w.name, sorted(x.user.username for x in w.members))


# --- adding a user beyond reach ------------------------------------------------------------------

def test_a_plain_admin_cannot_put_users_beyond_reach_in_a_new_room(session, box):
    """The audit's peer case: two teachers A could not reset, put in one room by A."""
    err = _refused(session, lambda: _room(session, box["a"], ["teacher-t", "aide-c"]))
    assert "teacher-t" in err.detail and "aide-c" in err.detail
    assert session.execute(select(Workspace)).scalars().all() == []
    assert peer_usernames(session, box["c"]) == [], "C reads T's work as a peer"


def test_a_plain_admin_cannot_add_a_user_beyond_reach_to_a_room_within_it(session, box):
    out = _room(session, box["a"], ["admin-a", "user-d"])
    err = _refused(session, lambda: m.admin_update_workspace(
        out["id"], m.WorkspaceUpdate(members=["admin-a", "user-d", "aide-c"]),
        admin=box["a"], db=session))
    assert "aide-c" in err.detail
    assert _state(session, out["id"]) == ("Room 12", ["admin-a", "user-d"])


def test_the_list_route_offers_only_users_within_reach(session, box):
    """The Workspaces tab greys out every name not in `manageable`, so the route must say it."""
    assert m.admin_list_workspaces(admin=box["a"], db=session)["manageable"] == [
        "admin-a", "user-d", "user-n"]


def test_a_row_for_a_rail_since_removed_puts_nobody_beyond_reach(session, box):
    """The reach rule's own boundary, carried over: a stale row grants nothing."""
    _user(session, "user-stale", ("recipe-book", "a-rail-since-removed"))
    out = _room(session, box["a"], ["admin-a", "user-stale"])
    assert out["members"] == ["admin-a", "user-stale"]


# --- a room that holds a member beyond reach -----------------------------------------------------

def test_a_plain_admin_cannot_add_anyone_to_a_room_holding_a_member_beyond_reach(session, box):
    """The audit's rooms case: T's classroom, made by the super-admin. Even a user A may manage
    is refused: whoever joins sees every child in the room, and the room is T's."""
    room = _room(session, box["root"], ["teacher-t"])
    err = _refused(session, lambda: m.admin_update_workspace(
        room["id"], m.WorkspaceUpdate(members=["teacher-t", "user-d"]),
        admin=box["a"], db=session))
    assert err.detail == ("you cannot change the workspace 'Room 12': 'teacher-t' holds apps you "
                          "cannot grant (terminal-fun, workstation), so only a super-admin can")
    assert _state(session, room["id"]) == ("Room 12", ["teacher-t"])
    assert room_ids(session, box["d"]) == []


def test_a_plain_admin_cannot_remove_a_member_beyond_reach_from_their_own_room(session, box):
    room = _room(session, box["root"], ["teacher-t", "user-d"])
    err = _refused(session, lambda: m.admin_update_workspace(
        room["id"], m.WorkspaceUpdate(members=["user-d"]), admin=box["a"], db=session))
    assert "'teacher-t' holds apps you cannot grant (terminal-fun, workstation)" in err.detail
    assert _state(session, room["id"]) == ("Room 12", ["teacher-t", "user-d"])


def test_a_plain_admin_cannot_rename_a_room_holding_a_member_beyond_reach(session, box):
    room = _room(session, box["root"], ["aide-c", "user-d"])
    _refused(session, lambda: m.admin_update_workspace(
        room["id"], m.WorkspaceUpdate(name="Renamed"), admin=box["a"], db=session))
    assert _state(session, room["id"]) == ("Room 12", ["aide-c", "user-d"])


def test_a_plain_admin_cannot_delete_a_room_holding_a_member_beyond_reach(session, box):
    room = _room(session, box["root"], ["teacher-t", "admin-a"])
    err = _refused(session, lambda: m.admin_delete_workspace(room["id"], admin=box["a"],
                                                             db=session))
    assert err.detail.startswith("you cannot delete the workspace 'Room 12': 'teacher-t' holds")
    assert _state(session, room["id"]) == ("Room 12", ["admin-a", "teacher-t"])


def test_the_refusal_names_every_member_beyond_reach_and_their_apps(session, box):
    room = _room(session, box["root"], ["teacher-t", "aide-c", "user-d"])
    err = _refused(session, lambda: m.admin_delete_workspace(room["id"], admin=box["a"],
                                                             db=session))
    assert err.detail == ("you cannot delete the workspace 'Room 12': 'aide-c' holds apps you "
                          "cannot grant (terminal-fun); 'teacher-t' holds apps you cannot grant "
                          "(terminal-fun, workstation), so only a super-admin can")


# --- allowed: within reach, and the super-admin -------------------------------------------------

def test_a_plain_admin_still_runs_a_room_of_users_within_reach(session, box):
    room = _room(session, box["a"], ["admin-a", "user-d"])
    got = m.admin_update_workspace(room["id"], m.WorkspaceUpdate(
        name="Caseload", members=["admin-a", "user-d", "user-n"]), admin=box["a"], db=session)
    assert (got["name"], got["members"]) == ("Caseload", ["admin-a", "user-d", "user-n"])
    assert peer_usernames(session, box["d"]) == ["admin-a", "user-n"]
    got = m.admin_update_workspace(room["id"], m.WorkspaceUpdate(members=["user-d"]),
                                   admin=box["a"], db=session)
    assert got["members"] == ["user-d"]
    assert m.admin_delete_workspace(room["id"], admin=box["a"], db=session) == {"ok": True}


def test_a_superadmin_is_unaffected(session, box):
    root = box["root"]
    room = _room(session, root, ["teacher-t", "aide-c"])
    assert peer_usernames(session, box["c"]) == ["teacher-t"]
    got = m.admin_update_workspace(room["id"], m.WorkspaceUpdate(
        name="Renamed", members=["aide-c", "admin-a"]), admin=root, db=session)
    assert (got["name"], got["members"]) == ("Renamed", ["admin-a", "aide-c"])
    assert m.admin_delete_workspace(room["id"], admin=root, db=session) == {"ok": True}
    assert m.admin_list_workspaces(admin=root, db=session)["manageable"] == sorted(
        u.username for u in box.values())


# --- the admin's grantable is read once a check (2026-10-15) -------------------------------------

def test_a_workspace_request_reads_the_admins_grantable_once_a_check(db, session, box):
    """`_manageable` and `_refuse_room_out_of_reach` asked `_out_of_reach` about each user, and it
    worked the admin's `_grantable` out afresh every time: a query a user, on top of that user's
    own, on every workspace route. Each works it out once now, so the reads of the admin's
    entitlements stay the same however many users and members there are. Counted in the SQL the
    engine runs, not in calls to a helper, so a read made any other way shows too."""
    a = box["a"]
    room = _room(session, a, ["admin-a", "user-d"])
    statements: list[tuple[str, tuple]] = []

    def note(_conn, _cursor, statement, parameters, *_rest):
        statements.append((statement.lower(), tuple(parameters or ())))

    def admins_reads(fn) -> int:
        statements.clear()
        fn()
        assert statements, "the statement counter saw nothing, so it proves nothing"
        return sum(1 for s, p in statements if "from entitlements" in s and a.id in p)

    seen = []
    event.listen(db.engine, "before_cursor_execute", note)
    try:
        # Each round adds users, and puts them in the room: the list route checks every user,
        # the update route every member it finds and then every user.
        for round_, size in enumerate((1, 4)):
            names = [f"user-{round_}-{i}" for i in range(size)]
            for name in names:
                _user(session, name, ("recipe-book",))
            members = sorted({*room["members"], *names})
            seen.append((
                admins_reads(lambda: m.admin_list_workspaces(admin=a, db=session)),
                admins_reads(lambda: m.admin_update_workspace(
                    room["id"], m.WorkspaceUpdate(members=members), admin=a, db=session))))
            room = {"id": room["id"], "members": members}
    finally:
        event.remove(db.engine, "before_cursor_execute", note)
    # (2, 4) each round today: its grantable once a check, and its own row once as one of the
    # users checked. The old code read (8, 12) then (12, 17).
    assert seen[0] == seen[1], f"the admin's entitlements were read {seen} times: more users, more reads"
