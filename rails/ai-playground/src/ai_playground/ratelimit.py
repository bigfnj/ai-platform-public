"""A per-caller sliding-window rate limiter, for routes that spend something real per call.

Deliberately in-process: no Redis, no shared store, no middleware. This rail runs as one
uvicorn process behind the gateway, and what is being protected is a PAID upstream call, not a
scarce lock -- so a counter in this process is the entire requirement, and a backing store would
only add a failure mode (store unreachable => route unreachable) to a convenience endpoint.

Keyed by CALLER, so one user cannot spend another user's allowance. The null owner (standalone
dev, or any caller the gateway did not name) shares a single bucket rather than getting one per
request: an unnamed caller must not earn a fresh allowance by virtue of having no name.

The map is swept, for the same reason the gateway's login throttle is: the per-key prune only
ever touches the key being asked about, so a bucket created by a caller who never returns would
otherwise live for the life of the process.
"""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException

# Sweep lapsed buckets once the map gets this big. Well above any real caller count, so the
# sweep is a bound on pathological input rather than something that runs constantly.
SWEEP_AT = 1024


class RateLimit:
    """At most ``limit`` calls per ``window_seconds``, per key. Thread-safe.

    `check()` both tests and records, which is the right shape for a limiter guarding a call
    that costs money: a separate "record" step can be skipped by an early return or an
    exception, and every such skip refunds the caller an attempt.
    """

    def __init__(self, *, limit: int, window_seconds: float, name: str) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = int(limit)
        self.window = float(window_seconds)
        self.name = name
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str | None) -> None:
        """Record a call against ``key``, or raise 429 once it is over the limit.

        Uses the monotonic clock: a wall-clock step (NTP, DST, a VM resuming) would otherwise
        either forgive every recorded call at once or freeze the window shut.
        """
        bucket = key or "-"                     # the null owner shares one bucket, not one each
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits.get(bucket, ()) if now - t < self.window]
            if len(self._hits) > SWEEP_AT:
                for stale in [k for k, v in self._hits.items()
                              if not v or now - v[-1] >= self.window]:
                    self._hits.pop(stale, None)
            if len(recent) >= self.limit:
                self._hits[bucket] = recent
                retry = max(1, int(self.window - (now - recent[0])) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=(f"{self.name}: at most {self.limit} call(s) per "
                            f"{int(self.window)}s. Retry in {retry}s."),
                    headers={"Retry-After": str(retry)})
            recent.append(now)
            self._hits[bucket] = recent

    def reset(self) -> None:
        """Drop every bucket. For tests and for a deliberate operator clear; nothing else."""
        with self._lock:
            self._hits.clear()
