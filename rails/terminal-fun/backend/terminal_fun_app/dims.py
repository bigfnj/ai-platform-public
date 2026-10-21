"""Terminal dimensions, clamped on every path in.

Its own module because both sides need it and one of them cannot be imported everywhere:
pty_session.py is Linux-only (pty/fcntl/termios), while main.py reads the query string and the
0x01 resize frame and must import cleanly on any box the tests run on.

The clamp used to exist only on the query string. The resize frame bypassed it: {"cols": 70000}
was stored, struct.pack("HHHH") raised struct.error (not an OSError, so the ioctl's suppress
missed it), and the NEXT launch failed after openpty, leaking both pty fds and the session HOME
(recorded 2026-09-10/11, reproduced again by the 2026-10-08 audit).
"""
from __future__ import annotations

MAX_DIM = 500


def clamp_dim(value: object, default: int) -> int:
    """A terminal dimension in 1..MAX_DIM; anything unparseable is `default`."""
    try:
        return max(1, min(MAX_DIM, int(value)))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return default
