"""Per-container broker-auth probe — run INSIDE a rail container via `docker exec`.

Validates two things from the container's own environment (stdlib only, no deps):
  1. The broker is ENFORCING its token — an unauthenticated GET /v1/status returns 401.
  2. This container can AUTHENTICATE — an authenticated GET /v1/status (using the container's own
     BROKER_AUTH_TOKEN) returns 200.

Prints one status line and exits non-zero on a real failure:
  PASS  — broker enforcing + this container authenticates.
  FAIL  — broker enforcing but this container is rejected (missing/stale token, or code that never
          sends it). This is the "silently 401-ing rail" the smoke test exists to catch.
  WARN  — broker NOT enforcing (token unset platform-wide / dev): auth can't be validated here.

Note: this checks the container's *environment + the broker*, not each client method's code path.
Per-method code correctness (e.g. an embed call that forgets the header) is covered by the rail's
own unit tests; job-aid also gets a live chat+embed exercise in broker-auth-smoke.ps1.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

_URL_ENV_HINTS = (
    "BROKER_URL", "JOB_AID_BROKER_URL", "AI_PLAYGROUND_BROKER_URL", "FINANCE_BROKER_URL",
    "RECIPE_BOOK_BROKER_URL", "BOUQUET_BROKER_URL", "TERMINAL_FUN_BROKER_URL", "AI_VOICE_BROKER_URL",
)


def broker_url() -> str:
    for key in _URL_ENV_HINTS:
        val = os.environ.get(key)
        if val:
            return val.rstrip("/")
    for key, val in os.environ.items():  # any other *_BROKER_URL
        if key.endswith("BROKER_URL") and val:
            return val.rstrip("/")
    return "http://host.docker.internal:11500"


def status_code(url: str, token: str | None) -> object:
    req = urllib.request.Request(url + "/v1/status")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - report connectivity failures, don't crash the sweep
        return f"ERR:{type(exc).__name__}"


def main() -> int:
    url = broker_url()
    token = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
    unauth = status_code(url, None)
    authed = status_code(url, token) if token else "no-token"
    enforcing = unauth == 401

    if not enforcing:
        label, code = "WARN", 0  # broker open (dev/staged) — auth not validatable here
    elif token and authed == 200:
        label, code = "PASS", 0
    else:
        label, code = "FAIL", 1
    print(f"{label}  url={url}  token={'set' if token else 'MISSING'}  unauth={unauth}  authed={authed}")
    return code


if __name__ == "__main__":
    sys.exit(main())
