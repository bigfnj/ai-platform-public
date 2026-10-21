"""Four-state model status for the header chips.

The contract every rail's chips render against:

    missing  RED     the model a role resolves to is not installed in Ollama
    cold     BLUE    installed, but not resident in VRAM
    warming  ORANGE  not resident yet, but a broker job for it is queued or active
    loaded   GREEN   resident in VRAM right now

Derived from three broker reads: ``roles()`` (role -> concrete model), ``models()`` (what is
installed) and ``status()`` (``loaded`` plus the live ``jobs`` queue).

A role may instead be DELEGATED to another broker, which the broker's row reports via
``upstream``. None of those three reads describes such a role -- the model is installed and
resident on a DIFFERENT box -- so it is judged from its row alone. See ``_is_delegated``.

ORDER MATTERS. ``loaded`` is checked before ``warming``: a resident model that also has a job in
flight is loaded-and-busy, not warming. Warming means specifically "a job is waiting on a model
that is not resident yet", the only window where the dot should be orange.

TAG TOLERANCE IS NOT OPTIONAL, AND IS NARROW. Ollama reports an untagged pull as ``:latest``
(``@embed`` resolves to ``bge-m3`` while the loaded list says ``bge-m3:latest``), so ``_same()``
strips an explicit ``:latest`` TAG from either side before comparing, and must not be
"simplified" to ``==``. It must equally not be widened: matching on the base name whenever the
word "latest" appears anywhere made ``qwen3:8b-latest`` satisfy a role pinned to ``qwen3:4b``.

GENERATED — do not edit in place. Emitted by tools/rail_template.py; every copy must match byte
for byte, which `rail_template.py check` enforces. Rails are independent deployables with their
own images, so the resolver is duplicated rather than shared — but the four state names and the
resolution ORDER are identical everywhere, because the chips are a cross-rail visual language
and a rail that computes "warming" differently is a lie. Before the generator existed there
were FIVE distinct implementations of this function.

It reads its rail's broker facade through the canonical surface: roles(), models() -> list,
status(), and BrokerError. That surface is why this file can be identical everywhere.

WHO ANSWERS: ``backend`` AND ``busy``. Every entry carries both, in both branches, always.
``backend`` is ``"claude"`` when the broker will serve this slot's call from the owner's Claude
CLI subscription instead of the local GPU, else ``"ollama"``. ``busy`` is true while the CLI is
answering one of THIS viewer's calls for this role. The CLI takes no GPU gate and joins no job
queue, so ``warming`` can never show that work, and without ``busy`` a draft in flight looks
exactly like an idle slot.

They are separate fields and not a fifth state, for two reasons. ``state`` keeps its meaning,
local residency, and on a CLI slot that is still worth reading: it is the health of the
FALLBACK the broker serves the call from when the CLI refuses it (an image, a rate limit, a
timeout). Folding "claude" into ``state`` would throw that away. And a web bundle that has not
been rebuilt indexes its text table by ``state``, so a fifth value renders as "undefined" on
every rail at once, while a key it does not know about is simply ignored.

``backend`` is a PREDICTION of ``Broker.cli_decision`` made from ``/v1/status["cli"]``, and
``cli_serves()`` is that predicate rebuilt from what status exposes. The rails cannot import the
broker, so the predicate exists twice. services/broker/tests/test_cli_chip_equivalence.py runs
both against a REAL broker over every owner/role/requester/blocker combination; a chip that
says "claude" for a call the broker sends to Ollama is the lie the four states exist to prevent.

A slot is eligible only if the rail OPTS IN by naming it in ``cli_slots``. A role-level rule
would be wrong: one rail calls a role with a ``requester`` while another calls the same role
without one, and the broker refuses every call that has no requester. The opt-in is the rail
promising that the call site behind the slot passes ``requester`` on ``/v1/chat`` or
``/v1/chat/stream``, and that ``requester`` here is that same identity. An older broker with
no ``cli`` block, a viewer who is not an owner, and a blank viewer all read ``"ollama"``, which
is the truth for each of them.
"""
from __future__ import annotations

import fnmatch
from typing import Any

from . import broker

MISSING = "missing"
COLD = "cold"
WARMING = "warming"
LOADED = "loaded"

OLLAMA = "ollama"
CLAUDE = "claude"

_GLOB_CHARS = "*?["


def cli_serves(cli: Any, requester: Any, ref: Any) -> bool:
    """Would the broker send ``requester``'s call for ``ref`` to the Claude CLI?

    ``cli`` is ``/v1/status["cli"]``. Mirrors ``Broker.cli_decision`` clause for clause, and
    must keep doing so; see the module docstring for the test that holds the two together.
    ``ready`` already folds in every blocker the broker can see and a rail cannot (a token file
    it cannot read, a workdir that does not exist), so it is trusted rather than recomputed.

    Defensive about shape because this runs against whatever broker is deployed: ``cli`` may
    be absent on an older one, and a malformed block must read as "not served", the answer
    that is never a false claim about whose subscription pays.
    """
    if not isinstance(cli, dict) or cli.get("ready") is not True:
        return False
    # The broker strips before both its blank check and its owner lookup; a header that
    # arrives as " name " is the same person, and a "   " header is nobody.
    if not isinstance(requester, str) or not requester.strip():
        return False
    # Only an @role, never a concrete model name, exactly like the broker: a concrete name is
    # refused there however the rest of the predicate reads.
    if not isinstance(ref, str) or not ref.startswith("@"):
        return False
    owners, roles = cli.get("owners"), cli.get("roles")
    if not isinstance(owners, list) or not isinstance(roles, list):
        return False
    # Exact, case included, like the broker's owner check: a case variant is another account.
    who = requester.strip()
    if who not in {str(o).strip() for o in owners}:
        return False
    # The ref exactly as sent, like the broker: `@EDU` is not the role `edu`.
    return ref[1:] in {str(r).strip().lstrip("@").lower() for r in roles}


def _role_key(ref: Any) -> str:
    return str(ref or "").strip().lstrip("@").lower()


def _cli_busy(cli: dict, requester: str, ref: str) -> bool:
    """Is the CLI answering one of THIS viewer's calls for ``ref`` right now?

    Matched on the requester as well as the role. ``active`` lists every owner's calls, and a
    dot that lit for someone else's draft would tell this viewer their own work had started
    when it had not; it would also show one person's activity to another.
    """
    active = cli.get("active")
    if not isinstance(active, list):
        return False
    who, role = requester.strip(), _role_key(ref)
    return any(isinstance(a, dict) and _role_key(a.get("role")) == role
               and str(a.get("requester") or "").strip() == who
               for a in active)


def _strip_latest(name: str) -> str:
    """Drop an explicit ``:latest`` TAG. Only the tag -- not any tag containing the word."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _same(a: str, b: str) -> bool:
    """Compare model names tolerating Ollama's implicit ``:latest``.

    The tolerance is narrow on purpose. The old test was ``"latest" in (a + b)``, which asks
    whether the word appears anywhere in the two names CONCATENATED, and then compared only the
    part before the colon. So ``gemma3:4b`` and ``gemma3:27b-latest`` were "the same" -- a
    resident 27b turned a 4b chip green, which is the precise failure the four-state contract
    exists to prevent. Matching on the stripped tag keeps the real case (``bge-m3`` vs
    ``bge-m3:latest``) and drops the accident.
    """
    if not a or not b:
        return False
    return _strip_latest(a) == _strip_latest(b)


def _role_entry(ref: str, roles: list[dict]) -> dict:
    """The broker's row for an ``@role``, or {} for a concrete model name."""
    if not ref.startswith("@"):
        return {}
    name = ref[1:]
    for r in roles:
        if r.get("role") == name:
            return r
    return {}


def _is_delegated(entry: dict) -> bool:
    """Whether this role runs on another broker.

    Absent or ``"local"`` means this box. The check is deliberately tolerant of the key being
    MISSING rather than requiring it: a rail bundle can be newer than the broker it talks to,
    and against an older broker that never emits ``upstream`` every role must keep behaving
    exactly as it did before delegation existed.
    """
    up = entry.get("upstream")
    return bool(up) and up != "local"


def _resolve_ref(ref: str, roles: list[dict], installed: list[str]) -> str:
    """Expand ``@role``, then resolve a size-scoped glob against what is installed."""
    if ref.startswith("@"):
        role = ref[1:]
        for r in roles:
            if r.get("role") == role and r.get("resolved"):
                ref = str(r["resolved"])
                break
        else:
            return ref  # unresolvable role — reported as missing, which is honest
    if any(c in ref for c in _GLOB_CHARS):
        matches = sorted((n for n in installed if fnmatch.fnmatch(n, ref)), reverse=True)
        return matches[0] if matches else ref
    return ref


def resolve(specs: list[tuple[str, str, str]], requester: str | None = None,
            cli_slots: Any = ()) -> dict[str, Any]:
    """Status for a rail's model slots.

    ``specs`` is a list of ``(slot, label, ref)`` where ref is an ``@role`` or a concrete model
    name. Returns ``{"broker": "ok"|"unreachable", "models": [...]}`` — never raises, because a
    header must render even when the GPU layer is down.

    ``requester`` is the viewer, the same identity the rail passes as ``requester`` on the
    calls behind ``cli_slots``; ``cli_slots`` names the slots whose call site does that. Both
    default to "nobody", which reads ``"ollama"`` everywhere: the right answer for every rail
    that has not been wired, so leaving them out cannot make a chip claim the CLI.
    """
    # A bare string is one slot name, not a set of letters. `cli_slots=("writer")` is a str,
    # and `in` on a str is a substring test, so "rite" would have matched; set() of it would
    # instead have quietly matched nothing and the chip would never say claude.
    slots = {cli_slots} if isinstance(cli_slots, str) else set(cli_slots or ())
    try:
        roles = broker.roles()
        installed = [str(m.get("name") or "") for m in (broker.models() or [])]
        status = broker.status()
    # ValueError as well, and NOT a blanket `except Exception`. The docstring above promises
    # this never raises, because a header must render even when the GPU layer is down, and
    # BrokerError alone did not deliver that: a broker answering 200 with a non-JSON body
    # makes the facade's r.json() raise a JSONDecodeError, which IS a ValueError and is not a
    # BrokerError, so it escaped and 500'd /api/capabilities on every rail at once.
    #
    # Deliberately not Exception: a transport fault is already mapped to BrokerError by the
    # facade, so anything else reaching here is a bug in this file and should be seen rather
    # than rendered to a teacher as "the broker is down".
    except (broker.BrokerError, ValueError):
        # The SAME keys as the ok branch. A consumer that reads `upstream` or `backend`
        # unconditionally must not meet a KeyError on the one day the broker is down, and an
        # envelope whose shape depends on the branch is the drift this template exists to stop.
        # "local" is what the ok branch also reports for a role it knows nothing about, and
        # "ollama" is not a guess: the CLI runs inside the broker, so with the broker gone
        # nothing is being served from it either.
        return {
            "broker": "unreachable",
            "models": [{"slot": s, "label": lb, "role": ref, "model": ref, "state": MISSING,
                        "upstream": "local", "backend": OLLAMA, "busy": False}
                       for s, lb, ref in specs],
        }

    loaded = [str(m.get("name") or "") for m in (status.get("loaded") or [])]
    queued = [str(j.get("model") or "") for j in (status.get("jobs") or [])
              if j.get("state") in ("waiting", "active")]
    cli = status.get("cli")

    out: list[dict[str, Any]] = []
    for slot, label, ref in specs:
        model = _resolve_ref(ref, roles, installed)
        entry = _role_entry(ref, roles)
        if _is_delegated(entry):
            # DELEGATED: not one of the three local reads above describes this role. The model
            # is installed and resident on another box, so this broker's models() and status()
            # will never list it, and judging it against THIS box's inventory calls a perfectly
            # healthy remote model 'missing' -- a red dot on a working rail. That is the
            # four-state contract's own failure mode inverted, and worse than a merely wrong
            # state, because it sends someone to pull a model they already have elsewhere.
            #
            # The broker has already asked that box on our behalf, so the row is the only
            # honest source here: for a delegated role ``installed`` means "the remote has a
            # model the pattern resolves to" and ``loaded`` means resident on THAT card. Still
            # MISSING when the remote cannot serve it -- honest in the other direction too.
            if not entry.get("installed"):
                state = MISSING
            elif entry.get("loaded"):
                state = LOADED
            else:
                state = COLD
        elif not any(_same(model, n) for n in installed):
            state = MISSING
        elif any(_same(model, n) for n in loaded):
            state = LOADED
        elif any(_same(model, n) for n in queued):
            state = WARMING
        else:
            state = COLD
        # `upstream` rides along so the chip can say WHERE the model runs. Without it a
        # delegated slot is indistinguishable from a local one in the UI, which matters twice:
        # the operator cannot see that a green chip depends on another machine being up, and
        # the absent WARMING state — there is no local queue for a delegated call — reads as a
        # bug rather than as the documented consequence it is.
        #
        # `backend` is decided per SLOT, never per role: see the module docstring. `state`
        # above is untouched by it and still describes the local fallback.
        on_cli = slot in slots and cli_serves(cli, requester, ref)
        out.append({"slot": slot, "label": label, "role": ref, "model": model, "state": state,
                    "upstream": entry.get("upstream") or "local",
                    "backend": CLAUDE if on_cli else OLLAMA,
                    "busy": on_cli and _cli_busy(cli, requester, ref)})
    return {"broker": "ok", "models": out}
