"""Four-state model status for the header chips.

The contract every rail's chips render against:

    missing  RED     the model a role resolves to cannot run without operator action
    cold     BLUE    available, but not resident in VRAM
    warming  ORANGE  not resident yet, but a broker job for it is queued or active
    loaded   GREEN   resident in VRAM right now

ORDER MATTERS. ``loaded`` is checked before ``warming``: a resident model that also has a job
in flight is loaded-and-busy, not warming. Warming means specifically "a job is waiting on a
model that is not resident yet", which is the only window where the dot should be orange.

TAG TOLERANCE IS NOT OPTIONAL. Ollama reports an untagged pull as ``:latest``, so a role can
resolve to ``bge-m3`` while the loaded list says ``bge-m3:latest``. The broker's own ``roles``
payload has this bug — it reports ``installed: false`` for a model that is installed and
resident — so this module compares for itself and ``_same()`` must not be "simplified" to ``==``.

THIS COPY IS THE ONE THAT DIVERGES, AND IT HAS TO.
Every other rail's slots are Ollama models. This rail has three slots and one of them —
``icon`` — is an **image backend on the broker's media worker** (``flux-schnell`` /
``sdxl-turbo``), which is not an Ollama model and never appears in ``/v1/models``. Resolving it
the normal way reports MISSING forever: a red dot on a feature that works perfectly. So specs
here carry a ``kind`` and image slots are resolved against the broker's ``media`` block instead.

The image-slot mapping, and why it is not the same as a chat slot's:

    missing   media is disabled on the broker (BROKER_MEDIA_ENABLED=false). Nothing can render
              until an operator changes that — the media equivalent of "needs an ollama pull".
    loaded    a media job for this backend is running RIGHT NOW (status.media.active).
    warming   a job naming this backend is queued/active on the gate but not yet the running
              media job.
    cold      media is enabled and nothing is running.

**For an image slot, ``cold`` is the healthy steady state, not a warning.** The media worker
runs as a short-lived subprocess and *exits to reclaim VRAM* after every job (see the broker's
``_run_media``), so an image backend is resident only during a render and blue the rest of the
time. A chat slot sitting cold means "nobody has asked yet"; an image slot sitting cold means
"working as designed". The chip tooltip says so, because a permanently blue dot that nobody
explained is indistinguishable from something broken.

Duplicated in shape (not by import) across the rails; held in agreement by
tools/rail_conformance.py RC008 rather than by a shared package, because rails are independent
deployables with their own dependency sets.
"""
from __future__ import annotations

import fnmatch
from typing import Any

from recipe_book import broker

MISSING = "missing"
COLD = "cold"
WARMING = "warming"
LOADED = "loaded"

_GLOB_CHARS = "*?["

# Image backends the broker's media worker can load. These are HuggingFace pipelines, not
# Ollama tags, so they are never in /v1/models. Mirrors MEDIA_IMAGE_BACKENDS in the gateway's
# rails_models.py.
MEDIA_BACKENDS = ("flux-schnell", "sdxl-turbo")


def _same(a: str, b: str) -> bool:
    """Compare model names tolerating Ollama's implicit ``:latest``."""
    if not a or not b:
        return False
    if a == b:
        return True
    return a.split(":")[0] == b.split(":")[0] if "latest" in (a + b) else False


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


def _image_state(model: str, status: dict[str, Any], busy: list[str]) -> str:
    """State for a media-worker image backend. See the module docstring for why this differs."""
    media = status.get("media") or {}
    if not media.get("enabled"):
        return MISSING
    active = media.get("active") or {}
    if _same(model, str(active.get("model") or "")):
        return LOADED
    if any(_same(model, n) for n in busy):
        return WARMING
    return COLD


def resolve(specs: list[tuple[str, str, str] | tuple[str, str, str, str]]) -> dict[str, Any]:
    """Status for a rail's model slots.

    ``specs`` is ``(slot, label, ref)`` or ``(slot, label, ref, kind)`` where ref is an
    ``@role``, a concrete name, or a glob, and kind is "chat" / "vision" / "image" (default
    "chat"). Returns ``{"broker": "ok"|"unreachable", "models": [...]}`` — never raises,
    because a header must render even when the GPU layer is down.
    """
    norm: list[tuple[str, str, str, str]] = [
        (s[0], s[1], s[2], s[3] if len(s) > 3 else "chat") for s in specs
    ]
    try:
        roles = broker.roles()
        installed = [str(m.get("name") or "") for m in (broker.installed_models() or [])]
        status = broker.status()
    except broker.BrokerError:
        return {
            "broker": "unreachable",
            "models": [{"slot": s, "label": lb, "role": ref, "model": ref,
                        "kind": kind, "state": MISSING} for s, lb, ref, kind in norm],
        }

    loaded = [str(m.get("name") or "") for m in (status.get("loaded") or [])]
    # A job's model is already concrete by the time the gate holds it (load()/chat() resolve
    # before entering the gate), so comparing against resolved names is correct.
    busy = [str(j.get("model") or "") for j in (status.get("jobs") or [])
            if j.get("state") in ("waiting", "active")]

    out: list[dict[str, Any]] = []
    for slot, label, ref, kind in norm:
        model = _resolve_ref(ref, roles, installed)
        if kind == "image":
            state = _image_state(model, status, busy)
        elif not any(_same(model, n) for n in installed):
            state = MISSING
        elif any(_same(model, n) for n in loaded):
            state = LOADED
        elif any(_same(model, n) for n in busy):
            state = WARMING
        else:
            state = COLD
        out.append({"slot": slot, "label": label, "role": ref, "model": model,
                    "kind": kind, "state": state})
    return {"broker": "ok", "models": out}
