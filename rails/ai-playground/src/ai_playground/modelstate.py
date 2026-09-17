"""Four-state model status for the header chips.

The contract every rail's chips render against:

    missing  RED     the model a role resolves to is not installed in Ollama
    cold     BLUE    installed, but not resident in VRAM
    warming  ORANGE  not resident yet, but a broker job for it is queued or active
    loaded   GREEN   resident in VRAM right now

Derived from three broker reads: ``roles()`` (role -> concrete model), ``models()`` (what is
installed) and ``status()`` (``loaded`` plus the live ``jobs`` queue).

ORDER MATTERS. ``loaded`` is checked before ``warming``: a resident model that also has a job in
flight is loaded-and-busy, not warming. Warming means specifically "a job is waiting on a model
that is not resident yet", the only window where the dot should be orange.

TAG TOLERANCE IS NOT OPTIONAL. Ollama reports an untagged pull as ``:latest`` (``@embed`` resolves
to ``bge-m3`` while the loaded list says ``bge-m3:latest``), so ``_same()`` compares on the base
name when either side is ``:latest`` and must not be "simplified" to ``==``.

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
    """Drop an explicit ``:latest`` TAG. Only the tag — not any tag containing the word."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _same(a: str, b: str) -> bool:
    """Compare model names tolerating Ollama's implicit ``:latest``.

    The tolerance is narrow on purpose. The old test was ``"latest" in (a + b)``, which asks
    whether the word appears anywhere in the two names CONCATENATED, and then compared only the
    part before the colon. So ``gemma3:4b`` and ``gemma3:27b-latest`` were "the same" — a
    resident 27b turned a 4b chip green, which is the precise failure the four-state contract
    exists to prevent. Matching on the stripped tag keeps the real case (``bge-m3`` vs
    ``bge-m3:latest``) and drops the accident.
    """
    if not a or not b:
        return False
    return _strip_latest(a) == _strip_latest(b)


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
    except broker.BrokerError:
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
        if not any(_same(model, n) for n in installed):
            state = MISSING
        elif any(_same(model, n) for n in loaded):
            state = LOADED
        elif any(_same(model, n) for n in busy):
            state = WARMING
        else:
            state = COLD
        out.append({"slot": slot, "label": label, "role": ref, "model": model, "state": state})
    return {"broker": "ok", "models": out}
