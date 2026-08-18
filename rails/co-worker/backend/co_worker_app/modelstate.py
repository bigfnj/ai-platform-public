"""Four-state model status for the header chips.

The contract every rail's chips render against:

    missing  RED     the model a role resolves to is not installed in Ollama
    cold     BLUE    installed, but not resident in VRAM
    warming  ORANGE  not resident yet, but a broker job for it is queued or active
    loaded   GREEN   resident in VRAM right now

Derived from three broker reads: ``/v1/roles`` (role -> concrete model), ``/v1/models``
(what is installed) and ``/v1/status`` (``loaded`` plus the live ``jobs`` queue).

ORDER MATTERS. ``loaded`` is checked before ``warming``: a resident model that also has a job
in flight is loaded-and-busy, not warming. Warming means specifically "a job is waiting on a
model that is not resident yet", which is the only window where the dot should be orange.

TAG TOLERANCE IS NOT OPTIONAL. Ollama reports an untagged pull as ``:latest``, so a role can
resolve to ``bge-m3`` while the loaded list says ``bge-m3:latest``. The broker's own ``roles``
payload has this bug — it reports ``installed: false`` for a model that is installed and
resident. That is why this module does its own comparison rather than trusting the broker's
``installed`` flag, and why ``_same()`` must not be "simplified" to ``==``.

Uses urllib rather than httpx on purpose: this rail's dependency set is fastapi + uvicorn +
pydantic only, and a status chip is not worth adding an HTTP client for. Same reason
synthesize.py hand-rolls its broker call.

This module is duplicated verbatim (bar the transport) in the gemini-cx and terminal-fun rails.
That is deliberate: rails are independent deployables with their own images and dependency sets,
and there is no shared Python package they all install. Keep the four state names and the
resolution order identical across the copies — the chips are a cross-rail visual language, and
a rail that computes "warming" differently is a lie.
"""
from __future__ import annotations

import fnmatch
import json
import urllib.error
import urllib.request
from typing import Any

from co_worker_app.config import settings

MISSING = "missing"
COLD = "cold"
WARMING = "warming"
LOADED = "loaded"

_TIMEOUT = 10.0
_GLOB_CHARS = "*?["


class BrokerUnreachable(RuntimeError):
    """The broker could not be read. Chips degrade rather than the page failing."""


def _get(path: str) -> Any:
    url = settings.broker_url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    token = (settings.broker_auth_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            json.JSONDecodeError) as exc:
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
    """Expand ``@role``, then resolve a size-scoped glob against what is installed.

    Globs matter because a rail may configure a model as ``gemma4*:12b`` rather than an @role
    (terminal-fun's full-stack default does). The broker exposes no "resolve this pattern"
    endpoint, so the match happens here with fnmatch. The broker picks the newest matching
    install by mtime; ``/v1/models`` does not carry mtime, so this picks the highest-sorting
    match — close enough for a status chip, and only ever different when two tags both match.
    """
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
        roles = _rows(_get("/v1/roles"), "roles")
        installed = [str(m.get("name") or "") for m in _rows(_get("/v1/models"), "models")]
        status = _get("/v1/status")
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
