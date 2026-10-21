"""Shared fixtures for the openmaic suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

# An address on the compose network, as the gateway holds it there. Any address works; what
# matters is that a TestClient's peer and the stubbed lookup agree, the way the real ones do.
GATEWAY = "10.89.0.2"


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    """The gateway as this rail sees it: `settings.gateway_host` resolves to `.addrs`.

    Autouse, so no test ever sends a real DNS query for "gateway". The browser's requests reach
    this rail from the gateway, so a test that drives the app with an identity header builds its
    TestClient with `client=(gateway.address, <port>)`; Starlette's default peer, the name
    "testclient", is a stranger like any other and its identity headers are dropped
    (api/gateway_only.py). `.calls` records every lookup, `.now` is the clock, and `.addrs` may be
    set to an OSError to make the next lookup fail.
    """
    from openmaic_app.api import gateway_only

    state = SimpleNamespace(address=GATEWAY, addrs=[GATEWAY], calls=[], now=1000.0)

    def resolve(host: str) -> list[str]:
        state.calls.append(host)
        if isinstance(state.addrs, BaseException):
            raise state.addrs
        return list(state.addrs)

    peers = gateway_only.gateway_peers
    monkeypatch.setattr(peers, "resolve", resolve)
    monkeypatch.setattr(peers, "_clock", lambda: state.now)
    monkeypatch.setattr(peers, "addresses", frozenset())
    monkeypatch.setattr(peers, "local", ())
    monkeypatch.setattr(peers, "_resolved_at", None)
    # This container's own addresses: none. The rail reads them by looking its own hostname up,
    # which a test did on the box (2026-10-15) and the offline guard below refuses. The tests of
    # the own-address rule set theirs (test_gateway_peer.py).
    monkeypatch.setattr(gateway_only, "_own_addresses", lambda: frozenset())
    # A fresh drop log per test, so one test's line cannot be held back by another's.
    monkeypatch.setattr(gateway_only, "_dropped", gateway_only._DropLog(clock=lambda: state.now))
    return state


# No test outside a live class reaches the network: tools/tests/offline_guard.py, which every
# suite's conftest takes. tools/tests is appended, so nothing there shadows a module of this suite.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.append(str(next(p for p in Path(__file__).resolve().parents
                         if (p / "tools" / "tests" / "offline_guard.py").is_file())
                    / "tools" / "tests"))
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: E402,F401
