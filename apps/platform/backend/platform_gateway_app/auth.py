"""Password hashing, server-side sessions, and entitlement helpers.

Battle-tested primitives, not hand-rolled crypto:
  - passwords hashed with pwdlib's recommended hasher (Argon2id),
  - sessions are opaque random tokens stored server-side (revocable, with expiry),
    delivered in an HTTP-only cookie (Secure + SameSite set by config).

These functions are pure (they take a SQLAlchemy session); the FastAPI
dependencies that resolve the current user from the request live in main.py.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .models import Entitlement, SessionRow, User, WorkspaceMember

_hasher = PasswordHash.recommended()  # Argon2id


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(password, hashed)
    except Exception:  # noqa: BLE001 - a malformed/legacy hash must read as "no match", never 500
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def create_session(db: OrmSession, user: User, ttl_hours: int) -> SessionRow:
    row = SessionRow(
        token=new_session_token(),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
    )
    db.add(row)
    db.commit()
    return row


def user_for_token(db: OrmSession, token: str | None) -> User | None:
    """Resolve a session token to its user, or None if missing/expired. Expired
    sessions are deleted as they're encountered."""
    if not token:
        return None
    row = db.get(SessionRow, token)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite hands back naive datetimes; treat as UTC
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return db.get(User, row.user_id)


def delete_session(db: OrmSession, token: str | None) -> None:
    if not token:
        return
    row = db.get(SessionRow, token)
    if row is not None:
        db.delete(row)
        db.commit()


def entitled_app_ids(db: OrmSession, user: User, all_ids: set[str],
                     *, all_access: bool = False) -> set[str]:
    """The app ids this user may see: exactly their explicit entitlements (intersected
    with the known catalog). `all_access` (the platform root/owner) sees everything.

    Being an admin no longer implies every app — admin governs the management panel,
    but reaching an app (a host shell included) needs an explicit grant. Only the seed
    owner is all-access, so it stays the root of trust that can bootstrap new grants."""
    if all_access:
        return set(all_ids)
    rows = db.execute(
        select(Entitlement.app_id).where(Entitlement.user_id == user.id)
    ).scalars().all()
    return {r for r in rows if r in all_ids}


def peer_usernames(db: OrmSession, user: User) -> list[str]:
    """Other users who share at least one workspace with this one, sorted, never including self.

    This is the whole of the sharing model: a teacher and her aide are put in a workspace, and
    from then on each sees the other's owner-scoped records as their own. The gateway resolves
    it once per request and forwards the result; rails never learn what a workspace IS, they
    only widen an `owner = ?` filter to `owner IN (...)`.

    Returns [] for a user in no workspace, which is every user until an admin says otherwise --
    so the default is unchanged behaviour, and a rail that ignores the header is simply the
    platform as it was.

    Admins are not special-cased here. They already see everything through their own code path,
    and giving them a peer list too would only add a second, redundant way to reach the same
    rows -- one worth getting wrong.
    """
    mine = select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    rows = db.execute(
        select(User.username)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id.in_(mine), User.id != user.id)
        .distinct()
    ).scalars().all()
    return sorted(rows)


def room_ids(db: OrmSession, user: User) -> list[int]:
    """The workspace ids this user belongs to, sorted.

    The other half of the sharing model, and the one that suits records with a SUBJECT.
    `peer_usernames` answers "whose work may I see", which is right for a rail whose records
    belong to whoever made them (edu-suite has only events and jobs -- no child entity for a
    room to attach to). This answers "which rooms am I in", which is right for a rail where a
    record belongs to a CLASSROOM: a child stays in Room 12 when the teacher leaves.

    Rooms also make the cross-workspace leak structurally impossible rather than merely
    guarded. Under peers, a sub in two rooms owns jobs about both teachers' children, and
    matching on the owner alone handed either teacher the other room's kids. A child carrying
    exactly one room cannot do that: the sub sees both rooms, and neither teacher sees the
    other's.

    Ids, not names, so renaming a room is free and cannot silently re-point anybody's data.
    """
    return sorted(db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    ).scalars().all())


def count_admins(db: OrmSession) -> int:
    return db.execute(
        select(User).where(User.is_admin.is_(True))
    ).scalars().unique().all().__len__()


def count_superadmins(db: OrmSession) -> int:
    return db.execute(
        select(User).where(User.is_superadmin.is_(True))
    ).scalars().unique().all().__len__()
