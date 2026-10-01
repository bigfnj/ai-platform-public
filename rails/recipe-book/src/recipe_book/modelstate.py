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
"""
from __future__ import annotations

import fnmatch
from typing import Any

from . import broker

MISSING = "missing"
COLD = "cold"
WARMING = "warming"
LOADED = "loaded"

_GLOB_CHARS = "*?["


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


def resolve(specs: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Status for a rail's model slots.

    ``specs`` is a list of ``(slot, label, ref)`` where ref is an ``@role`` or a concrete model
    name. Returns ``{"broker": "ok"|"unreachable", "models": [...]}`` — never raises, because a
    header must render even when the GPU layer is down.
    """
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
        return {
            "broker": "unreachable",
            "models": [{"slot": s, "label": lb, "role": ref,
                        "model": ref, "state": MISSING} for s, lb, ref in specs],
        }

    loaded = [str(m.get("name") or "") for m in (status.get("loaded") or [])]
    busy = [str(j.get("model") or "") for j in (status.get("jobs") or [])
            if j.get("state") in ("waiting", "active")]

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
        elif any(_same(model, n) for n in busy):
            state = WARMING
        else:
            state = COLD
        # `upstream` rides along so the chip can say WHERE the model runs. Without it a
        # delegated slot is indistinguishable from a local one in the UI, which matters twice:
        # the operator cannot see that a green chip depends on another machine being up, and
        # the absent WARMING state — there is no local queue for a delegated call — reads as a
        # bug rather than as the documented consequence it is.
        out.append({"slot": slot, "label": label, "role": ref, "model": model, "state": state,
                    "upstream": entry.get("upstream") or "local"})
    return {"broker": "ok", "models": out}
