"""Broker facade — the canonical surface every rail exposes.

`roles()`, `models()`, `status()` and a `BrokerError`. That surface is the reason
`modelstate.py` can be byte-identical across rails instead of each one adapting to its own
transport, function names and exception type. See "The rail template" in docs/RAIL_CONTRACT.md.

urllib rather than httpx, on purpose. This rail's dependency set is fastapi + uvicorn +
pydantic only, and a status chip is not worth adding an HTTP client for — the same reason
synthesize.py hand-rolls its broker call. The facade contract is about the SHAPE of the
surface, not the transport behind it.

These helpers lived inside modelstate.py until the template work, which is exactly why this
rail's four-state resolver had drifted into one of five distinct implementations of a thing
the contract says is identical everywhere.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from co_worker_app.config import settings

_TIMEOUT = 10.0


class BrokerError(RuntimeError):
    """The broker could not be read. Callers degrade rather than failing the page."""


# The name this rail raised before the facade existed. Kept as an alias so anything still
# catching it keeps working; new code should catch BrokerError.
BrokerUnreachable = BrokerError


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
        raise BrokerError(f"{path}: {exc}") from exc


def _rows(payload: Any, key: str) -> list[dict]:
    """Tolerate both response shapes: a bare list, or a dict wrapping one under `key`."""
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return payload if isinstance(payload, list) else []


def roles() -> list[dict]:
    """Role -> concrete model, as the broker resolves it."""
    return _rows(_get("/v1/roles"), "roles")


def models() -> list[dict]:
    """The broker's raw model list (what is installed)."""
    return _rows(_get("/v1/models"), "models")


def status() -> dict:
    """Loaded models plus the live job queue."""
    got = _get("/v1/status")
    return got if isinstance(got, dict) else {}
