"""The second uid: deciding whether games can run as someone other than the backend, and
preparing the one directory they have to share.

WHY THIS EXISTS. The games include roguelikes with a working `!` shell escape, and this rail
accepted that on the grounds that "the container holds no secrets". It held one. uvicorn's
environment carries BROKER_AUTH_TOKEN — the single credential deploy/docker-compose.yml hands
all nine broker-client rails — and while pty_session replaces the child environment wholesale,
the PID namespace is shared and the game ran as the SAME uid, so
``tr '\\0' '\\n' < /proc/1/environ`` handed any player the platform's broker credential. The
same shell could read and delete every other player's saves on /data. Both were found by the
2026-09-10 rail-backend audit (docs/BACKLOG.md).

Running the game as ``gameuser`` while the backend stays ``funuser`` closes both: /proc/1
belongs to another uid, so ptrace_may_access refuses it, and /data is 0700 funuser. The uid
layout, and what each one may touch, is documented in deploy/Dockerfile.

WHY IT IS A SEPARATE MODULE FROM pty_session. Two reasons, and the second is the load-bearing
one. It is about privilege, not about terminals. And pty_session cannot be imported off-Linux
at all (fcntl/termios/pty), which is why main.py imports it lazily inside one route — putting
this logic there would have made every line of it untestable on a developer machine, for a
change whose entire value is that it behaves correctly in three different configurations.
Nothing here imports a Linux-only module at file scope; ``grp`` is imported inside the one
function that needs it and its absence is a normal answer, not an error.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Sequence

# The wrapper that puts a game on the low-privileged uid. A script rather than a bare
# `setpriv` argv because the drop needs a umask as well as a uid — see the file itself,
# deploy/rootfs/usr/local/bin/fun-runas, for why that matters.
RUNAS = "/usr/local/bin/fun-runas"

# The group the two uids share, and the only thing they share. Resolved by NAME at runtime
# rather than pinned to a gid here: the Dockerfile owns the number, and two places asserting
# the same magic integer is how they come to disagree.
SHARE_GROUP = "funshare"

# CAP_SETGID and CAP_SETUID as bit positions in /proc/self/status CapEff (linux/capability.h).
CAP_SETGID, CAP_SETUID = 1 << 6, 1 << 7

# Session HOME mode under the split: rwx for owner and the shared group, nothing for the
# world, plus setgid so everything the game creates inside stays in the shared group. Without
# the setgid bit the game's own subdirectories (crawl's ~/.crawl) land in ITS primary group,
# the backend can no longer unlink what is inside them, and every saved-game session leaks a
# tmpfs directory it can never clean up.
SHARED_HOME_MODE = 0o2770

_CAPEFF_RE = re.compile(r"^CapEff:\s*([0-9a-fA-F]+)", re.M)


def parse_cap_eff(status: str) -> int:
    """The CapEff mask out of /proc/<pid>/status text, or 0 when it is not in there.

    Split out from the read so it can be tested on any platform. 0 on anything unrecognised is
    the safe answer in both directions: it means "no capabilities", which routes the caller to
    the un-split behaviour that has always worked.

    EFF, not BND or PRM, and the distinction is the whole point. The bounding set lists what
    the process could ever hold; the effective set is what it holds now. Reading CapBnd would
    report the split as available in a container that has SETUID in its bounds but not in
    hand — and the symptom would not be a security hole, it would be every game failing to
    launch because setpriv(1) returns EPERM.

    No try/except around int(): the capture group is [0-9a-fA-F]+, so anything that matched
    parses, and anything that did not match returned above. A guard there would be a branch no
    input can reach, which reads as caution and is really just a place bugs hide.
    """
    hit = _CAPEFF_RE.search(status)
    return int(hit.group(1), 16) if hit else 0


def cap_eff() -> int:
    """This process's effective capability set. 0 where there is no procfs to read."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            return parse_cap_eff(fh.read())
    except OSError:
        return 0


def share_gid() -> int | None:
    """The shared group's gid, or None if this system has no such group (dev box, or an image
    built before the split)."""
    try:
        import grp
    except ImportError:            # not a POSIX box; there is no split to activate
        return None
    try:
        return grp.getgrnam(SHARE_GROUP).gr_gid
    except KeyError:
        return None


def split_uid_active() -> bool:
    """Whether games can be put on their own uid in THIS container.

    Detected, deliberately, rather than configured. Changing uid needs CAP_SETUID; the compose
    service drops every capability; and Docker never populates the ambient set, so capabilities
    granted to a container whose USER is non-root do not reach the process at all. Whether the
    split is available is therefore a property of the deployment — two keys on one compose
    service — and not something this image can know or a setting should be able to lie about.

    All three preconditions are checked, not just the capability: the wrapper has to exist and
    the shared group has to resolve, or wrapping a game would fail at exec time and take the
    session down instead of hardening it.

    Fails towards the PRE-SPLIT behaviour. That direction is not a preference, it is the only
    safe one: an image that refuses to launch a game unless the new compose is already in place
    takes the rail down the moment it is pulled, and this is a hardening measure — worth
    having, never worth an outage to get.
    """
    if not os.path.isfile(RUNAS) or share_gid() is None:
        return False
    want = CAP_SETUID | CAP_SETGID
    return (cap_eff() & want) == want


def game_argv(argv: Sequence[str]) -> list[str]:
    """`argv`, wrapped so it lands on the game uid when this container is wired for it."""
    return [RUNAS, *argv] if split_uid_active() else list(argv)


def make_session_home(prefix: str) -> str:
    """A fresh session HOME the game uid can actually use.

    ``mkdtemp`` is 0700 by design, which is right while the game shares this process's uid and
    wrong the instant it does not — the game could not even chdir into its own HOME. Under the
    split the directory is handed to the shared group and opened to it (SHARED_HOME_MODE), so
    the game can write, and the backend can still seed the save beforehand, capture it after,
    and delete the tree.

    The chown is a GROUP change only, uid ``-1``. Not tidiness: it is the only form available.
    ``cap_drop: ALL`` leaves no CAP_CHOWN, and POSIX permits a file's owner to change its group
    only to a group it belongs to — funuser is in funshare, so this succeeds where a real owner
    change could not.

    Still closed to the world in both modes, so one player's session HOME is never readable
    from another's. And still best-effort: a failed chown/chmod leaves a working 0700 directory
    and a game that runs as the backend's uid, which is where the un-split path already is.
    """
    home = tempfile.mkdtemp(prefix=prefix)
    gid = share_gid()
    if gid is not None and split_uid_active():
        with contextlib.suppress(OSError):
            os.chown(home, -1, gid)
            os.chmod(home, SHARED_HOME_MODE)
    return home
