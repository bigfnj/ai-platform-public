"""Platform identity, fail-closed.

GENERATED — do not edit in place. This file is emitted by tools/rail_template.py and every
copy must match byte for byte; `rail_template.py check` fails otherwise. Change the template,
then `rail_template.py sync` to roll it out.

It is duplicated rather than imported on purpose. A rail that has to import the platform to
boot is not a component you can lift out, so the contract buys independence with consistent
duplication — consistent being the operative word, which is what the generator enforces.

The gateway authenticates every request and sets X-Platform-User, stripping any client-supplied
copy. A request arriving WITHOUT that header therefore did not come through the gateway — it is
a sibling container talking to this rail directly. Treating that as an anonymous or privileged
caller is the bug this module exists to prevent, so it is a 401 instead.

The dev escape hatch is read at CALL time, not import time, so a test can set it without having
to control import order.
"""
from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException


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
            # user=None, never the literal string "standalone": some rails persist the caller
            # in an owner column, and a placeholder username there becomes real-looking data.
            return Identity(None, True)
        raise HTTPException(status_code=401, detail="unauthenticated (no platform identity)")
    return Identity(x_platform_user,
                    (x_platform_admin or "").strip().lower() in ("1", "true", "yes"))


def require_admin(ident: Identity = Depends(identity)) -> Identity:
    """Gate an admin-only route. The platform scheduler fires with X-Platform-Admin, so it
    passes; identity() has already rejected the header-less case unless standalone."""
    if not ident.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return ident


def ws_user(ws) -> str | None:
    """The identity behind a WebSocket handshake, or None to reject.

    The gateway sets the header on the upstream handshake; a websocket cannot return a 401
    body, so the caller closes the connection instead. Kept here so the fail-closed decision
    lives in one place rather than being re-derived at each socket.
    """
    user = ws.headers.get("x-platform-user")
    if user:
        return user
    return None if not standalone() else ""
