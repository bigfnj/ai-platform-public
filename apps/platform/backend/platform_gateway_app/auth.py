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

from .models import Entitlement, SessionRow, User

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


def count_admins(db: OrmSession) -> int:
    return db.execute(
        select(User).where(User.is_admin.is_(True))
    ).scalars().unique().all().__len__()


def count_superadmins(db: OrmSession) -> int:
    return db.execute(
        select(User).where(User.is_superadmin.is_(True))
    ).scalars().unique().all().__len__()
