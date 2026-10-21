"""Platform identity from the gateway's trusted headers.

The gateway authenticates every request and forwards the verified identity as
``X-Platform-User`` / ``X-Platform-Admin`` (stripping any client-supplied copy first, so
they can't be spoofed). A request with no ``X-Platform-User`` did NOT arrive through the
gateway (standalone dev / tests) and resolves to a null owner (shared/default scope).

The null owner is a scope, never a credential: nothing it can reach is another tenant's. The
ownership checks in corpora.py / bench/querysets.py enforce that directly rather than skipping
themselves when the caller has no name.
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
    #
    # A header that is PRESENT but blank is rejected outright, standalone or not. Starlette hands
    # `X-Platform-User:` through as "" and not None, so gating on `is None` alone let a blank
    # header past and then `x_platform_user or None` normalised it to the null owner — the very
    # value the ownership checks used to read as "no owner to compare against, allow it". A caller
    # asserting an empty identity is making a claim, not omitting one; there is nothing to honour.
    if x_platform_user is not None and not x_platform_user.strip():
        raise HTTPException(status_code=401, detail="unauthenticated (blank platform identity)")
    if x_platform_user is None and not config.STANDALONE:
        raise HTTPException(status_code=401, detail="unauthenticated (no platform identity)")
    return Identity(
        (x_platform_user or "").strip() or None,
        (x_platform_admin or "").strip().lower() in ("1", "true", "yes"),
    )


def ws_user(ws) -> str | None:
    """The identity behind a WebSocket handshake, or None to reject it.

    Starlette's HTTP middleware — and the app-level identity dependency with it — is not the
    thing a socket can lean on for a 401 body, so each socket refuses by closing the handshake
    BEFORE accept(). The canonical copy of this helper is rails/terminal-fun's identity.py;
    it lives here so the fail-closed decision is made in one place rather than at each socket.

    Standalone dev yields "" — falsy, so it still resolves to a null (shared-scope) owner, but
    not None, so "no gateway in front" stays distinguishable from "refuse this handshake". It is
    never a placeholder username: this rail persists the caller in `owner` columns, and a
    stand-in there becomes real-looking data.
    """
    user = (ws.headers.get("x-platform-user") or "").strip()
    if user:
        return user
    return "" if config.STANDALONE else None


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route. Requires a real, admin identity (identity() already rejects the
    header-less case unless standalone); a non-admin user is 403, and a null owner is never admin."""
    if not ident.is_admin or (ident.user is None and not config.STANDALONE):
        raise HTTPException(status_code=403, detail="admin only")
    return ident
