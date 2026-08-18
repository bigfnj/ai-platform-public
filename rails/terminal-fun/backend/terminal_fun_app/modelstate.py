"""Four-state model status for the header chips.

The contract every rail's chips render against:

    missing  RED     the model this rail asks for is not installed in Ollama
    cold     BLUE    installed, but not resident in VRAM
    warming  ORANGE  not resident yet, but a broker job for it is queued or active
    loaded   GREEN   resident in VRAM right now

Derived from three broker reads: ``/v1/roles`` (role -> concrete model), ``/v1/models``
(what is installed) and ``/v1/status`` (``loaded`` plus the live ``jobs`` queue).

ORDER MATTERS. ``loaded`` is checked before ``warming``: a resident model that also has a job
in flight is loaded-and-busy, not warming. Warming means specifically "a job is waiting on a
model that is not resident yet", which is the only window where the dot should be orange.

THREE KINDS OF REFERENCE. This rail configures its model as a plain string
(``TERMINAL_FUN_LLM_MODEL``), and that string may be an ``@role``, a concrete name, OR a
size-scoped **glob** like ``gemma4*:12b`` — the full-stack compose default is a glob. So
``_resolve_ref`` handles all three, and the glob case is matched locally with fnmatch rather
than asking the broker, which exposes no "resolve this pattern" endpoint. The broker picks the
newest matching install by mtime; ``/v1/models`` does not carry mtime, so this picks the
highest-sorting match instead. For a status chip that is close enough, and it is only ever a
different answer when two tags both match.

TAG TOLERANCE IS NOT OPTIONAL. Ollama reports an untagged pull as ``:latest``, so a reference
can resolve to ``bge-m3`` while the loaded list says ``bge-m3:latest``. The broker's own
``roles`` payload has this bug — it reports ``installed: false`` for a model that is installed
and resident. That is why this module does its own comparison, and why ``_same()`` must not be
"simplified" to ``==``.

This module is duplicated (bar the transport and settings import) in the gemini-cx and
co-worker rails. That is deliberate: rails are independent deployables with their own images
and dependency sets, and there is no shared Python package they all install. Keep the four
state names and the resolution order identical across the copies — the chips are a cross-rail
visual language, and a rail that computes "warming" differently is a lie.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Any

import httpx

from terminal_fun_app.config import settings

MISSING = "missing"
COLD = "cold"
WARMING = "warming"
LOADED = "loaded"

_TIMEOUT = 10.0
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}

_GLOB_CHARS = "*?["


class BrokerUnreachable(RuntimeError):
    """The broker could not be read. Chips degrade rather than the page failing."""


def _get(client: httpx.Client, path: str) -> Any:
    try:
        resp = client.get(path, timeout=_TIMEOUT, headers=_AUTH)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BrokerUnreachable(f"{path}: {exc}") from exc


def _same(a: str, b: str) -> bool:
    """Compare model names tolerating Ollama's implicit ``:latest``."""
    if not a or not b:
        return False
    if a == b:
        return True
    return a.split(":")[0] == b.split(":")[0] if "latest" in (a + b) else False


def _rows(payload: Any, key: str) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return payload if isinstance(payload, list) else []


def _resolve_ref(ref: str, roles: list[dict], installed: list[str]) -> str:
    """Expand ``@role``, then resolve a glob against what is installed."""
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

    ``specs`` is a list of ``(slot, label, ref)``. Returns
    ``{"broker": "ok"|"unreachable", "models": [...]}`` — never raises, because a header must
    render even when the GPU layer is down.
    """
    base = settings.broker_url.rstrip("/")
    try:
        with httpx.Client(base_url=base) as client:
            roles = _rows(_get(client, "/v1/roles"), "roles")
            installed = [str(m.get("name") or "")
                         for m in _rows(_get(client, "/v1/models"), "models")]
            status = _get(client, "/v1/status")
    except BrokerUnreachable:
        return {
            "broker": "unreachable",
            "models": [{"slot": s, "label": lb, "role": ref,
                        "model": ref, "state": MISSING} for s, lb, ref in specs],
        }

    loaded = [str(m.get("name") or "") for m in (status.get("loaded") or [])]
    # A job's model is already concrete by the time the gate holds it (load()/chat() resolve
    # before entering the gate), so comparing against resolved names is correct.
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
