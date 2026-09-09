"""Shared request dependencies: platform identity + owner resolution.

The gateway authenticates every request and forwards the verified identity as trusted headers
(``X-Platform-User`` / ``X-Platform-Admin``); it strips any client-supplied copy first, so they
cannot be spoofed. A request with no ``X-Platform-User`` therefore did NOT arrive through the
gateway — it is a sibling container talking to this rail directly.

That case used to resolve to the DEFAULT OWNER and pass ``require_admin``. Both were wrong in
the same direction: an un-gated caller read and wrote admin's pantry, meal plan and ratings
(``users.id=1`` is claimed by ``RECIPE_BOOK_PRIMARY_USER``), and could trigger the admin-only
rebuild, reindex, purge and icon-generation routes. It now fails closed with a 401.

The escape hatch is ``PLATFORM_STANDALONE`` (one name across every rail), read at CALL time so a
test can set it without controlling import order.
"""
from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException, Query

from recipe_book import db


def standalone() -> bool:
    """True when this rail is running without a gateway in front (local dev, tests)."""
    return os.getenv("PLATFORM_STANDALONE", "").strip().lower() in ("1", "true", "yes")


class Identity:
    __slots__ = ("user", "is_admin")

    def __init__(self, user: str | None, is_admin: bool) -> None:
        self.user = user
        self.is_admin = is_admin


def identity(
    x_platform_user: str | None = Header(default=None),
    x_platform_admin: str | None = Header(default=None),
) -> Identity:
    if x_platform_user is None:
        if standalone():
            # user=None, never the literal "standalone": this rail persists the caller in an
            # owner column, so a placeholder username there becomes real-looking data.
            return Identity(None, True)
        raise HTTPException(status_code=401, detail="unauthenticated (no platform identity)")
    return Identity(x_platform_user,
                    (x_platform_admin or "").strip().lower() in ("1", "true", "yes"))


def owner_id(
    ident: Identity = Depends(identity),
    owner: str | None = Query(default=None),
) -> int:
    """The owner_id whose data this request reads/writes — normally the caller's own.

    An ADMIN may act on another user's data by passing ``?owner=<username>``; the flag is
    ignored for non-admins, so it cannot be abused (is_admin comes from the trusted header).
    Only a standalone run reaches the default owner now, and only because identity() has
    already decided this is a gateway-less dev process.
    """
    if ident.user is None:
        return db.OWNER_ID
    target = owner.strip() if (ident.is_admin and owner and owner.strip()) else ident.user
    con = db.connect()
    try:
        return db.resolve_owner(con, target)
    finally:
        con.close()


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route (the user picker, rebuild, reindex, purge, icon generation).

    Previously ``ident.user is not None and not ident.is_admin`` — which let a header-less
    caller through and only stopped a NAMED non-admin, exactly backwards. identity() now
    rejects the header-less case, so this is a plain admin check.
    """
    if not ident.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return ident
