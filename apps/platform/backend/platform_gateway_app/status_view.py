"""What each user may see of the broker's status, on GET /api/platform/status.

WHY THIS EXISTS. That route is open to ANY logged-in user, because the shell's top-bar widget
(web/src/ModelWidget.tsx) polls it every 5 s for everyone. The broker's /v1/status behind it
was written for operators and for BrokerTray, both of which hold a broker token: its ``media``
block names the absolute path of every worker interpreter (image, speech, Kokoro, Whisper),
and it carries the Ollama version, each resident model's expiry time and each job's start
time. None of that is drawn by the widget, and the paths are a map of the host's disk handed
to every browser. cli_view.py already did this for the ``cli`` block; this module does it for
the rest of the response, the same way.

The shapes, by who is asking:

  admin      Everything, as a copy, with ``cli`` as cli_view gives an admin (the whole block).
             Admins operate the broker, and "which interpreter is the speech worker using" is
             exactly their question. Admin means ``User.is_admin``, the flag ``require_admin``
             checks; only a literal ``True`` counts, so something merely truthy fails closed.

  anyone     What the widget draws, and nothing else. Every level is an ALLOWLIST: a field the
  else       broker adds tomorrow reaches admins and nobody else until it is admitted here on
             purpose. Leaves are admitted only as scalars, so an allowed NAME cannot smuggle a
             nested object out under it. ``cli`` is cli_view's owner view, or omitted.

What a non-admin STILL sees, on purpose: the job queue (each job's rail and model) and the
last model fallback (the role, both model names and the reason). The widget shows both to every
user by design. "How many jobs are ahead of mine" is why the queue is in the widget at all, and
a fallback badge exists so a user is not left wondering why a model nobody asked for is
running. Neither names a user, a path or a token. Removing them would not be a privacy fix,
it would break the widget for the people it is there for.

A field the widget starts reading has to be added to ``_SHOWN`` below too, or a non-admin will
simply never receive it. tests/test_status_view.py fails if ModelWidget.tsx reads a top-level
status key this module does not admit, so that cannot happen quietly.

Pure and total: it never raises. It runs on every poll from every user, so a malformed status
from a half-upgraded broker must degrade to a smaller answer, never to a 500 that takes the
GPU meter down with it.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from platform_gateway_app.cli_view import cli_view

# One resident model, as the widget's Loaded list draws it. Not `expires_at`.
_LOADED_KEYS = ("name", "class", "size_vram")
# The VRAM meter and the popover heading. All four are in web/src/types.ts's Gpu.
_GPU_KEYS = ("total_mib", "used_mib", "free_mib", "gpu_name")
_QUEUE_KEYS = ("active", "waiting")
# One queued job. `seq` is the row's React key (cancelling is admin-only, and admins get the
# full status anyway). Not `since`: the widget shows queue position, not how long a job ran.
_JOB_KEYS = ("seq", "model", "source", "state")
# A model substitution, on a job or as fallbacks.last; web/src/types.ts's ModelFallback.
_FALLBACK_KEYS = ("model", "requested", "original", "reason", "category")


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _pick(d: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    """The allowed keys of ``d`` whose values are scalars, in the broker's own order."""
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if k in keys and _scalar(v)}


def _each(v: Any, fn: Callable[[dict], dict]) -> list[dict]:
    """``fn`` over the dict entries of a list; anything that is not a dict is dropped."""
    if not isinstance(v, list):
        return []
    return [fn(e) for e in v if isinstance(e, dict)]


def _fallback(v: Any) -> dict[str, Any] | None:
    return _pick(v, _FALLBACK_KEYS) if isinstance(v, dict) else None


def _job(job: dict) -> dict[str, Any]:
    out = _pick(job, _JOB_KEYS)
    if "fallback" in job:
        out["fallback"] = _fallback(job["fallback"])
    return out


def _fallbacks(v: Any) -> dict[str, Any]:
    out = _pick(v, ("count",))
    if isinstance(v, dict) and "last" in v:
        out["last"] = _fallback(v["last"])
    return out


# Top-level key -> how a non-admin sees its value. A key not named here is not sent to them.
_SHOWN: dict[str, Callable[[Any], Any]] = {
    "ollama_reachable": lambda v: v is True,
    "loaded": lambda v: _each(v, lambda m: _pick(m, _LOADED_KEYS)),
    "heavy_loaded": lambda v: [n for n in v if isinstance(n, str)] if isinstance(v, list) else [],
    "gpu": lambda v: _pick(v, _GPU_KEYS) if isinstance(v, dict) else None,
    "queue": lambda v: _pick(v, _QUEUE_KEYS),
    "jobs": lambda v: _each(v, _job),
    "fallbacks": _fallbacks,
}


def status_view(status: Any, username: str | None, is_admin: bool) -> dict[str, Any]:
    """The part of the broker's status this user may see, in the broker's key order.

    ``cli`` keeps its place when shown and is left out when cli_view says this user gets
    nothing; ``username`` is only consulted for that block.
    """
    if not isinstance(status, dict):
        return {}
    admin = is_admin is True
    out: dict[str, Any] = {}
    for key, value in status.items():
        if key == "cli":
            view = cli_view(value, username, admin)
            if view is not None:
                out[key] = view
        elif admin:
            # A copy, so the route can hand it to a serializer and nothing the caller does to
            # it can reach the broker client's dict.
            out[key] = copy.deepcopy(value)
        elif key in _SHOWN:
            out[key] = _SHOWN[key](value)
    return out
