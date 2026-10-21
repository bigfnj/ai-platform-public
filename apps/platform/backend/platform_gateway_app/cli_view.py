"""What each user may see of the broker's ``cli`` status block, on GET /api/platform/status.

WHY THIS EXISTS. That route is open to ANY logged-in user, because the shell's top-bar widget
(web/src/ModelWidget.tsx) polls it every 5 s for everyone. It used to pass the broker's
/v1/status through untouched, which was harmless while the status held only GPU and queue facts.
Then the broker grew a ``cli`` block for its owner-only Claude CLI backend, and that block was
written for an operator reading a token-gated endpoint: it names the owner accounts, the CLI's
exe path, its working directory, the text of any token problem, and (with ``active``) which
account is running which role right now. The broker's own comment justifies listing owners with
"/v1/status is itself token-gated". That stopped being true at this hop, and every user's
browser received all of it.

The shapes, by who is asking:

  admin      The whole block, as a copy. Admins run the platform and are who the block was
             written for: "why is the CLI not being used" needs blockers, exe, workdir and
             token_problem side by side. Admin means ``User.is_admin``, the flag
             ``require_admin`` checks, so nobody sees more here than the admin pane already
             gives them. Only a literal ``True`` counts, so a caller passing something merely
             truthy fails closed.

  owner      A non-admin whose username is in ``owners`` gets the one answer the widget has
             for them: is my off-box backend ready, which roles use it, is a call of mine
             running, how did the last one go. Their OWN ``active`` entries and their OWN
             ``last_by_role`` outcomes only, with ``requester`` gone from both (it is them).
             Never ``owners``: a second owner's account name
             is not theirs to learn, and the machine config (exe, workdir, token_problem,
             blockers) is an admin's to act on, not theirs.

  anyone     Nothing. The ``cli`` key is OMITTED rather than sent as ``{"for_you": false}``.
  else       A negative still tells a non-owner that the feature exists, that some accounts
             route through a personal subscription, and that they are not one of them, which
             is the who-is-in-it fact this module exists to keep in. They can do nothing with
             the answer either, so it buys them nothing and costs the owner something.

Every level of the owner view is an ALLOWLIST, not a list of keys to strip. ``active`` and
``last_by_role`` are written by the broker, which is still growing them; a field added there
has to be admitted here on purpose rather than reaching every owner the day it ships.

Pure and total: it never raises. It runs on every poll from every user, so a malformed block
from a half-upgraded broker must degrade to "no cli key", never to a 500 that would take the
GPU meter down along with it.
"""

from __future__ import annotations

import copy
from typing import Any

# What one of the owner's own `active` entries may carry back to them. `requester` is absent
# on purpose: it is always the viewer, and dropping it keeps any account name out of the view.
_ACTIVE_KEYS = ("role", "rail", "since")
# What one of the owner's own `last_by_role` outcomes may carry. `requester` is absent for the
# same reason as above. `cause` is admitted on purpose: an `interrupted` outcome (a CLI stream
# that stopped after its first token) carries the failure's slug there, and the widget names it.
# A slug, never text from the answer.
_LAST_KEYS = ("ok", "at", "reason", "cause")


def _norm(name: Any) -> str:
    """A username as the broker compares it: stripped, and NOT case-folded. "" for anything
    that is not a string, so a missing or wrong-typed name can never equal a real one.

    The match is exact because the broker's owner check is exact. Folding case here once let
    an `Owner-One` account, created beside `owner-one` while usernames were unique only
    case-sensitively, see the owner's CLI status as its own."""
    return name.strip() if isinstance(name, str) else ""


def _owner_set(owners: Any) -> set[str]:
    # A list or tuple only, never "any iterable". A bare string iterates as characters, so
    # owners="owner-one" would otherwise make a one-letter account an owner.
    if not isinstance(owners, (list, tuple)):
        return set()
    return {n for n in (_norm(o) for o in owners) if n}


def _own_active(active: Any, me: str) -> list[dict[str, Any]]:
    if not isinstance(active, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in active:
        if not isinstance(entry, dict):
            continue
        # Another owner's running call is theirs. An entry with no requester belongs to no one
        # and is dropped too: _norm turns it into "", which never equals a real viewer.
        if _norm(entry.get("requester")) != me:
            continue
        out.append({k: entry[k] for k in _ACTIVE_KEYS if k in entry})
    return out


def _own_last_by_role(last: Any, me: str, by_requester: Any = None) -> dict[str, dict[str, Any]]:
    """This owner's own latest outcome per role, without ``requester``.

    ``last_by_role`` keeps one outcome per role, whoever made the call, so with two owners on a
    role the newest entry is as often the other one's. Shown unfiltered, it told owner A that
    owner B's last call fell back, and when; filtered, owner A simply lost their own outcome the
    moment B called the same role. A broker that also sends ``last_by_requester``
    (requester -> role -> outcome) keeps each owner's own latest per role, so that map is read
    in preference, and ``last_by_role`` only for a broker from before it.

    Either way every outcome must itself name this owner: an entry with no ``requester`` (a
    broker from before the field) belongs to nobody and is dropped, the rule ``active`` already
    follows. Until that broker is upgraded an owner loses the fallback badge, which is the safe
    way to be wrong.
    """
    if isinstance(by_requester, dict):
        # Matched the way the broker keys it (stripped, case kept); the per-entry check below
        # still applies, so a bucket filed under the wrong name shows nothing.
        mine = next((o for who, o in by_requester.items() if _norm(who) == me), None)
        last = mine if isinstance(mine, dict) else {}
    if not isinstance(last, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for role, outcome in last.items():
        if not isinstance(role, str) or not isinstance(outcome, dict):
            continue
        if _norm(outcome.get("requester")) != me:
            continue
        out[role] = {k: outcome[k] for k in _LAST_KEYS if k in outcome}
    return out


def cli_view(cli: dict | None, username: str | None, is_admin: bool) -> dict | None:
    """The part of the broker's ``cli`` block this user may see, or None to omit the key.

    ``username`` is matched against ``cli["owners"]`` stripped and otherwise exactly, the way
    the broker's own routing matches a requester, so the account the CLI serves and the
    account shown as "for you" are the same account, and a case variant is neither.
    """
    if not isinstance(cli, dict):
        return None
    if is_admin is True:
        return copy.deepcopy(cli)
    me = _norm(username)
    if not me or me not in _owner_set(cli.get("owners")):
        return None
    backend, roles = cli.get("backend"), cli.get("roles")
    view = {
        "backend": backend if isinstance(backend, str) else None,
        "ready": cli.get("ready") is True,
        "for_you": True,
        "roles": [r for r in roles if isinstance(r, str)] if isinstance(roles, list) else [],
        "active": _own_active(cli.get("active"), me),
        "last_by_role": _own_last_by_role(cli.get("last_by_role"), me,
                                          cli.get("last_by_requester")),
    }
    # Values were copied by reference above; deep-copy so the view and the broker's dict never
    # share a mutable object, which is what "pure" has to mean for a caller.
    return copy.deepcopy(view)
