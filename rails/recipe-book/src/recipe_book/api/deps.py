"""Shared request dependencies: platform identity + owner resolution.

The gateway authenticates every request and forwards the verified identity as trusted
headers (``X-Platform-User`` / ``X-Platform-Admin``); it strips any client-supplied copy
first, so they can't be spoofed. A request with no ``X-Platform-User`` did NOT arrive
through the gateway (standalone dev / tests) and resolves to the default owner.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query

from recipe_book import db


class Identity:
    __slots__ = ("user", "is_admin")

    def __init__(self, user: str | None, is_admin: bool) -> None:
        self.user = user
        self.is_admin = is_admin


def identity(
    x_platform_user: str | None = Header(default=None),
    x_platform_admin: str | None = Header(default=None),
) -> Identity:
    return Identity(
        x_platform_user or None,
        (x_platform_admin or "").strip().lower() in ("1", "true", "yes"),
    )


def owner_id(
    ident: Identity = Depends(identity),
    owner: str | None = Query(default=None),
) -> int:
    """The owner_id whose data this request reads/writes — normally the caller's own.
    An ADMIN may act on another user's data by passing ``?owner=<username>``; the flag is
    ignored for non-admins, so it can't be abused (is_admin comes from the trusted header).
    Un-gated requests (no identity header) resolve to the default owner."""
    if ident.user is None:
        return db.OWNER_ID
    target = owner.strip() if (ident.is_admin and owner and owner.strip()) else ident.user
    con = db.connect()
    try:
        return db.resolve_owner(con, target)
    finally:
        con.close()


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route (e.g. the user picker). Un-gated (standalone) passes."""
    if ident.user is not None and not ident.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return ident
