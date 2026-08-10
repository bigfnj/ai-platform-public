"""Platform identity from the gateway's trusted headers.

The gateway authenticates every request and forwards the verified identity as
``X-Platform-User`` / ``X-Platform-Admin`` (stripping any client-supplied copy first, so
they can't be spoofed). A request with no ``X-Platform-User`` did NOT arrive through the
gateway (standalone dev / tests) and resolves to a null owner (shared/default scope).
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from ai_playground import config


class Identity:
    __slots__ = ("user", "is_admin")

    def __init__(self, user: str | None, is_admin: bool) -> None:
        self.user = user
        self.is_admin = is_admin


def identity(
    x_platform_user: str | None = Header(default=None),
    x_platform_admin: str | None = Header(default=None),
) -> Identity:
    # Fail closed: behind the gateway every request carries X-Platform-User, so a missing header
    # means a direct-to-rail call (a sibling container) — reject it rather than run as a null owner.
    # Only standalone dev/tests (no gateway) are allowed the header-less null-owner path.
    if x_platform_user is None and not config.STANDALONE:
        raise HTTPException(status_code=401, detail="unauthenticated (no platform identity)")
    return Identity(
        x_platform_user or None,
        (x_platform_admin or "").strip().lower() in ("1", "true", "yes"),
    )


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route. Requires a real, admin identity (identity() already rejects the
    header-less case unless standalone); a non-admin user is 403, and a null owner is never admin."""
    if not ident.is_admin or (ident.user is None and not config.STANDALONE):
        raise HTTPException(status_code=403, detail="admin only")
    return ident
