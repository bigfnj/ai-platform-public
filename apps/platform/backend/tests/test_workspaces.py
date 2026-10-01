"""Workspaces: who sees whose owner-scoped records.

A teacher and her classroom aide must see each other's work; the other forty teachers on the
platform must not. Every test here is really the same question from a different angle: does
the widening stop exactly where it should.

The gateway owns the membership and resolves it per request into `X-Platform-Peers`. Rails
never learn what a workspace is -- they widen `owner = ?` to `owner IN (...)`. So the security
property lives HERE: if peer_usernames() over-returns, every rail over-shares at once.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from platform_gateway_app import main as m
from platform_gateway_app.auth import hash_password, peer_usernames, room_ids
from platform_gateway_app.models import User, Workspace, WorkspaceMember


def mk(session, name: str, admin: bool = False, superadmin: bool = False) -> User:
    u = User(username=name, password_hash=hash_password("x"),
             is_admin=admin or superadmin, is_superadmin=superadmin)
    session.add(u)
    session.commit()
    return u


def link(session, ws_name: str, *users: User) -> Workspace:
    w = Workspace(name=ws_name)
    session.add(w)
    session.flush()
    for u in users:
        session.add(WorkspaceMember(workspace_id=w.id, user_id=u.id))
    session.commit()
    return w


class TestPeerResolution:
    def test_no_workspace_means_no_peers(self, session):
        """The default for every account, and why this is safe to ship: unless an admin says
        otherwise, behaviour is exactly what it was."""
        u = mk(session, "solo")
        assert peer_usernames(session, u) == []

    def test_the_teacher_and_aide_case(self, session):
        teacher, aide = mk(session, "teacher"), mk(session, "aide")
        link(session, "Room 12", teacher, aide)
        assert peer_usernames(session, teacher) == ["aide"]
        assert peer_usernames(session, aide) == ["teacher"]

    def test_never_includes_self(self, session):
        """Self would be harmless in a rail (it already matches its own rows) but it is a lie
        about what the header means, and lies get relied on."""
        a, b = mk(session, "a"), mk(session, "b")
        link(session, "w", a, b)
        assert "a" not in peer_usernames(session, a)

    def test_a_third_teacher_is_not_a_peer(self, session):
        """The property the whole design exists to preserve."""
        teacher, aide, stranger = mk(session, "t"), mk(session, "aid"), mk(session, "other")
        link(session, "Room 12", teacher, aide)
        assert peer_usernames(session, teacher) == ["aid"]
        assert peer_usernames(session, stranger) == []
        assert "other" not in peer_usernames(session, aide)

    def test_two_workspaces_union_but_do_not_bridge(self, session):
        """A user in two rooms sees both. The two rooms do NOT thereby see each other -- that
        would silently merge unrelated classrooms through whoever subs for both."""
        sub = mk(session, "sub")
        t1, t2 = mk(session, "t1"), mk(session, "t2")
        link(session, "Room 1", sub, t1)
        link(session, "Room 2", sub, t2)
        assert peer_usernames(session, sub) == ["t1", "t2"]
        assert peer_usernames(session, t1) == ["sub"]      # NOT ["sub", "t2"]
        assert peer_usernames(session, t2) == ["sub"]

    def test_group_of_three_is_fully_symmetric(self, session):
        a, b, c = mk(session, "a"), mk(session, "b"), mk(session, "c")
        link(session, "Room", a, b, c)
        assert peer_usernames(session, a) == ["b", "c"]
        assert peer_usernames(session, b) == ["a", "c"]
        assert peer_usernames(session, c) == ["a", "b"]

    def test_deleting_a_user_removes_their_membership(self, session):
        """Ids get reused. A membership row outliving its user would hand the next account to
        take that id sight of a classroom's student data."""
        teacher, aide = mk(session, "teacher"), mk(session, "aide")
        link(session, "Room 12", teacher, aide)
        session.delete(aide)
        session.commit()
        assert len(session.execute(select(WorkspaceMember)).scalars().all()) == 1
        assert peer_usernames(session, teacher) == []

    def test_deleting_a_workspace_removes_the_sharing_only(self, session):
        teacher, aide = mk(session, "teacher"), mk(session, "aide")
        w = link(session, "Room 12", teacher, aide)
        session.delete(w)
        session.commit()
        assert peer_usernames(session, teacher) == []
        assert len(session.execute(select(User)).scalars().all()) == 2


class TestRoomIds:
    """The other half of the model, for rails whose records have a SUBJECT.

    peer_usernames answers "whose work may I see" -- right for edu-suite, which has only
    events and jobs. room_ids answers "which rooms am I in" -- right for iep-goals, where a
    child belongs to a classroom and stays there when the teacher leaves.
    """

    def test_no_rooms_by_default(self, session):
        assert room_ids(session, mk(session, "solo")) == []

    def test_returns_the_ids_the_user_belongs_to(self, session):
        t, a = mk(session, "teacher"), mk(session, "aide")
        w = link(session, "Room 12", t, a)
        assert room_ids(session, t) == [w.id]
        assert room_ids(session, a) == [w.id]

    def test_a_sub_in_two_rooms_gets_both(self, session):
        """The case peers could not express safely. Under rooms the sub sees both rooms'
        children and the two teachers still see only their own, because the CHILD carries the
        room -- there is no owner column for either teacher to match on."""
        sub, t1, t2 = mk(session, "sub"), mk(session, "t1"), mk(session, "t2")
        w1 = link(session, "Room 1", sub, t1)
        w2 = link(session, "Room 2", sub, t2)
        assert room_ids(session, sub) == sorted([w1.id, w2.id])
        assert room_ids(session, t1) == [w1.id]
        assert room_ids(session, t2) == [w2.id]

    def test_ids_not_names_so_renaming_is_free(self, session):
        """A rail stores the id. Renaming a room must not re-point anybody's data."""
        t = mk(session, "teacher")
        w = link(session, "Room 12", t)
        before = room_ids(session, t)
        w.name = "Room 14"
        session.commit()
        assert room_ids(session, t) == before

    def test_deleting_a_user_drops_only_their_own_membership(self, session):
        """The aide's row goes; the teacher stays in the room she is still in."""
        t, a = mk(session, "teacher"), mk(session, "aide")
        w = link(session, "Room 12", t, a)
        assert len(session.execute(select(WorkspaceMember)).scalars().all()) == 2
        session.delete(a)
        session.commit()
        assert len(session.execute(select(WorkspaceMember)).scalars().all()) == 1
        assert room_ids(session, t) == [w.id]


class TestHeaderEncoding:
    def test_absent_when_there_are_no_peers(self):
        """The common path. A rail that never sees the header behaves as it always has."""
        assert "x-platform-peers" not in m.ws_identity_headers("t", "0", [])

    def test_json_encoded_not_comma_joined(self):
        """Usernames are String(64) with no charset validation, so a comma is legal in one.
        Comma-joining would split it into two names that do not exist -- silently, and toward
        showing the caller too LITTLE, which gets diagnosed as 'sharing is broken' for a week
        rather than as a leak."""
        h = m.ws_identity_headers("t", "0", ["a,b", "c"])
        assert json.loads(h["x-platform-peers"]) == ["a,b", "c"]

    def test_rooms_absent_when_empty_and_present_when_not(self):
        assert "x-platform-rooms" not in m.ws_identity_headers("t", "0", [], [])
        h = m.ws_identity_headers("t", "0", [], [1, 5])
        assert json.loads(h["x-platform-rooms"]) == [1, 5]

    def test_peers_and_rooms_are_independent(self):
        """A rail reads one or the other. Sending one must never imply the other."""
        only_peers = m.ws_identity_headers("t", "0", ["a"], [])
        only_rooms = m.ws_identity_headers("t", "0", [], [3])
        assert "x-platform-rooms" not in only_peers
        assert "x-platform-peers" not in only_rooms

    def test_identity_headers_still_present(self):
        h = m.ws_identity_headers("teacher", "1", ["aide"])
        assert h["x-platform-user"] == "teacher"
        assert h["x-platform-admin"] == "1"


class TestAdminApi:
    def test_create_rename_and_set_members(self, session):
        admin = mk(session, "root", admin=True)
        t = mk(session, "teacher")
        mk(session, "aide")

        out = m.admin_create_workspace(m.WorkspaceCreate(name="Room 12", members=["teacher"]),
                                       admin=admin, db=session)
        assert out["name"] == "Room 12" and out["members"] == ["teacher"]

        out = m.admin_update_workspace(out["id"], m.WorkspaceUpdate(members=["teacher", "aide"]),
                                       admin=admin, db=session)
        assert out["members"] == ["aide", "teacher"]
        assert peer_usernames(session, t) == ["aide"]

        out = m.admin_update_workspace(out["id"], m.WorkspaceUpdate(name="Room 14"),
                                       admin=admin, db=session)
        assert out["name"] == "Room 14" and out["members"] == ["aide", "teacher"]

    def test_unknown_username_is_rejected_not_silently_dropped(self, session):
        """Silently dropping a typo is the worst outcome: the admin believes the aide can see
        the teacher, and finds out otherwise from the teacher."""
        admin = mk(session, "root", admin=True)
        mk(session, "teacher")
        with pytest.raises(Exception) as exc:
            m.admin_create_workspace(m.WorkspaceCreate(name="R", members=["teacher", "typo"]),
                                     admin=admin, db=session)
        assert "typo" in str(exc.value)

    def test_duplicate_name_is_a_conflict(self, session):
        admin = mk(session, "root", admin=True)
        m.admin_create_workspace(m.WorkspaceCreate(name="Room 12"), admin=admin, db=session)
        with pytest.raises(Exception) as exc:
            m.admin_create_workspace(m.WorkspaceCreate(name="Room 12"), admin=admin, db=session)
        assert "already exists" in str(exc.value)

    def test_removing_a_member_revokes_immediately(self, session):
        admin = mk(session, "root", admin=True)
        t = mk(session, "teacher")
        a = mk(session, "aide")
        out = m.admin_create_workspace(m.WorkspaceCreate(name="R", members=["teacher", "aide"]),
                                       admin=admin, db=session)
        assert peer_usernames(session, t) == ["aide"]
        m.admin_update_workspace(out["id"], m.WorkspaceUpdate(members=["teacher"]),
                                 admin=admin, db=session)
        assert peer_usernames(session, t) == []
        assert peer_usernames(session, a) == []

    def test_setting_the_same_members_again_is_a_no_op(self, session):
        """The diff-not-replace path: re-adding an unchanged member must not trip the unique
        constraint, which clear-and-re-add does when the INSERT is emitted first."""
        admin = mk(session, "root", admin=True)
        mk(session, "teacher")
        mk(session, "aide")
        out = m.admin_create_workspace(m.WorkspaceCreate(name="R", members=["teacher", "aide"]),
                                       admin=admin, db=session)
        again = m.admin_update_workspace(out["id"],
                                         m.WorkspaceUpdate(members=["aide", "teacher"]),
                                         admin=admin, db=session)
        assert again["members"] == ["aide", "teacher"]

    def test_delete_and_missing_workspace(self, session):
        admin = mk(session, "root", admin=True)
        out = m.admin_create_workspace(m.WorkspaceCreate(name="R"), admin=admin, db=session)
        assert m.admin_delete_workspace(out["id"], admin=admin, db=session) == {"ok": True}
        with pytest.raises(Exception):
            m.admin_delete_workspace(out["id"], admin=admin, db=session)


class TestAdminDelegation:
    """A workspace grant is STRONGER than an app grant: it decides whose records another user
    can read inside an app they can already reach. The Users routes have three layers of
    delegation; these four had none, so a plain admin could put the super-admin in a room and
    read their owner-scoped records in every rooms-aware rail -- an access grant the Users tab
    would refuse outright. This class is the rule that closes it.
    """

    def test_a_plain_admin_cannot_seed_a_room_with_a_superadmin(self, session):
        admin = mk(session, "alice", admin=True)
        mk(session, "admin", superadmin=True)
        with pytest.raises(Exception) as exc:
            m.admin_create_workspace(
                m.WorkspaceCreate(name="R", members=["alice", "admin"]),
                admin=admin, db=session)
        assert "admin" in str(exc.value)

    def test_a_plain_admin_cannot_add_a_superadmin_later(self, session):
        admin = mk(session, "alice", admin=True)
        mk(session, "admin", superadmin=True)
        out = m.admin_create_workspace(m.WorkspaceCreate(name="R", members=["alice"]),
                                       admin=admin, db=session)
        with pytest.raises(Exception) as exc:
            m.admin_update_workspace(out["id"],
                                     m.WorkspaceUpdate(members=["alice", "admin"]),
                                     admin=admin, db=session)
        assert "admin" in str(exc.value)

    def test_an_existing_superadmin_member_is_frozen_not_stripped(self, session):
        """The other half, and it must be tested separately from the 403 or one guard's test
        silently covers the other. A room a super-admin already set up keeps them when a plain
        admin edits its membership -- the edit applies, the member survives."""
        root = mk(session, "admin", superadmin=True)
        admin = mk(session, "alice", admin=True)
        mk(session, "aide")
        out = m.admin_create_workspace(
            m.WorkspaceCreate(name="R", members=["admin", "alice"]),
            admin=root, db=session)
        got = m.admin_update_workspace(out["id"],
                                       m.WorkspaceUpdate(members=["alice", "aide"]),
                                       admin=admin, db=session)
        # sorted() rather than a literal order, here and below: the routes return members
        # sorted, and the public snapshot renames these users, which reorders them.
        assert got["members"] == sorted(["aide", "alice", "admin"])

    def test_a_superadmin_may_do_all_of_it(self, session):
        root = mk(session, "admin", superadmin=True)
        mk(session, "alice", admin=True)
        out = m.admin_create_workspace(
            m.WorkspaceCreate(name="R", members=["admin", "alice"]),
            admin=root, db=session)
        assert out["members"] == sorted(["alice", "admin"])
        assert m.admin_delete_workspace(out["id"], admin=root, db=session) == {"ok": True}

    def test_a_plain_admin_cannot_delete_a_room_holding_a_superadmin(self, session):
        """Deleting the room is a destructive act on the super-admin's sharing, so the same
        rule that governs who may be added governs who may be removed wholesale."""
        root = mk(session, "admin", superadmin=True)
        admin = mk(session, "alice", admin=True)
        out = m.admin_create_workspace(
            m.WorkspaceCreate(name="R", members=["admin", "alice"]),
            admin=root, db=session)
        with pytest.raises(Exception) as exc:
            m.admin_delete_workspace(out["id"], admin=admin, db=session)
        assert "admin" in str(exc.value)

    def test_a_plain_admin_may_still_run_their_own_rooms(self, session):
        """The reason this rule is scoped rather than superadmin-only: alice has a real
        caseload room and must keep managing it."""
        admin = mk(session, "alice", admin=True)
        mk(session, "esimon99")
        out = m.admin_create_workspace(
            m.WorkspaceCreate(name="Shared Caseload", members=["alice", "esimon99"]),
            admin=admin, db=session)
        assert out["members"] == sorted(["esimon99", "alice"])
        assert m.admin_delete_workspace(out["id"], admin=admin, db=session) == {"ok": True}

    def test_the_list_route_publishes_what_the_actor_may_manage(self, session):
        """The UI mirrors the server rather than inventing a restriction, so the server has to
        say what the restriction IS -- exactly as admin_list_users returns `grantable`."""
        root = mk(session, "admin", superadmin=True)
        admin = mk(session, "alice", admin=True)
        mk(session, "aide")
        assert m.admin_list_workspaces(admin=admin, db=session)["manageable"] == sorted([
            "aide", "alice"])
        assert m.admin_list_workspaces(admin=root, db=session)["manageable"] == sorted([
            "aide", "alice", "admin"])
