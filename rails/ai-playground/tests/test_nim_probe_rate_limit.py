"""The rate limit on ``POST /api/nim/probe`` -- the one route on this rail that spends money.

The route was already gated: the app-level identity dependency refuses a caller with no name.
That is a gate, not a budget. A probe is a real 1-token completion against NVIDIA's hosted
endpoint on the deployment's key (it has to be -- nim.py records that being LISTED in
/v1/models is not being entitled to call it), so any named caller on the compose network, or a
UI that re-renders in a loop, could bill the account as fast as it could issue requests.

Every test here is hermetic: ``no_outbound`` fails loudly on an outbound call, so a test that
"passes" by actually reaching NVIDIA cannot.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ai_playground import config, ratelimit
from ai_playground.api import app as appmod

USER = {"X-Platform-User": "admin"}
OTHER = {"X-Platform-User": "alice"}


class TestRateLimit:
    """The primitive, away from the route."""

    def test_calls_up_to_the_limit_are_allowed(self):
        rl = ratelimit.RateLimit(limit=3, window_seconds=60, name="t")
        for _ in range(3):
            rl.check("k")

    def test_the_call_past_the_limit_is_429(self):
        rl = ratelimit.RateLimit(limit=2, window_seconds=60, name="NIM probe")
        rl.check("k")
        rl.check("k")
        with pytest.raises(HTTPException) as err:
            rl.check("k")
        assert err.value.status_code == 429
        assert "NIM probe" in err.value.detail, "the message must name what was limited"
        assert int(err.value.headers["Retry-After"]) > 0, "a 429 must say when to come back"

    def test_keys_do_not_share_an_allowance(self):
        """One user exhausting the limit must not lock out everyone else."""
        rl = ratelimit.RateLimit(limit=1, window_seconds=60, name="t")
        rl.check("alice")
        rl.check("bob")
        with pytest.raises(HTTPException):
            rl.check("alice")

    def test_the_null_owner_shares_one_bucket(self):
        """An unnamed caller must not earn a fresh allowance by having no name."""
        rl = ratelimit.RateLimit(limit=1, window_seconds=60, name="t")
        rl.check(None)
        with pytest.raises(HTTPException):
            rl.check(None)
        with pytest.raises(HTTPException):
            rl.check("")

    def test_the_window_lapses(self, monkeypatch):
        """Stepped, not slept: a test that waits a real minute gets deleted."""
        clock = {"t": 1000.0}
        monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
        rl = ratelimit.RateLimit(limit=1, window_seconds=60, name="t")
        rl.check("k")
        clock["t"] += 59.0
        with pytest.raises(HTTPException):
            rl.check("k")
        clock["t"] += 2.0
        rl.check("k")                                   # the first call has aged out

    def test_it_uses_the_MONOTONIC_clock(self, monkeypatch):
        """A wall-clock step (NTP, DST, a resumed VM) must not forgive recorded calls or
        freeze the window shut. Pinned by patching monotonic and asserting it is what is read."""
        reads = {"n": 0}

        def counted():
            reads["n"] += 1
            return 500.0
        monkeypatch.setattr(ratelimit.time, "monotonic", counted)
        ratelimit.RateLimit(limit=1, window_seconds=60, name="t").check("k")
        assert reads["n"] > 0, "check() is not reading the monotonic clock"

    def test_the_map_is_swept(self, monkeypatch):
        """Same bound as the gateway's login throttle: the per-key prune only cleans the key
        being asked about, so buckets from callers who never return must be reclaimed."""
        clock = {"t": 0.0}
        monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
        rl = ratelimit.RateLimit(limit=5, window_seconds=60, name="t")
        for i in range(ratelimit.SWEEP_AT + 50):
            rl.check(f"caller-{i}")
        assert len(rl._hits) > ratelimit.SWEEP_AT
        clock["t"] += 10_000.0
        rl.check("someone-new")
        assert set(rl._hits) == {"someone-new"}, "lapsed buckets were never reclaimed"

    def test_a_live_bucket_survives_the_sweep(self, monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
        rl = ratelimit.RateLimit(limit=5, window_seconds=60, name="t")
        for i in range(ratelimit.SWEEP_AT + 50):
            rl.check(f"caller-{i}")
        clock["t"] += 10_000.0
        rl.check("still-here")
        clock["t"] += 1.0
        rl.check("trigger-the-sweep")
        assert "still-here" in rl._hits, "the sweep dropped a bucket inside its window"

    def test_a_limit_below_one_is_rejected(self):
        """limit=0 would be "closed", which is a routing decision and not a rate limit."""
        with pytest.raises(ValueError):
            ratelimit.RateLimit(limit=0, window_seconds=60, name="t")

    def test_reset_clears_every_bucket(self):
        rl = ratelimit.RateLimit(limit=1, window_seconds=60, name="t")
        rl.check("k")
        rl.reset()
        rl.check("k")


@pytest.fixture()
def probe_client(api, monkeypatch):
    """A client over the real app with nim.probe() stubbed, and the PROCESS-WIDE limiter reset.

    The limiter deliberately outlives create_api(), so without this reset the tests here would
    leak allowance into each other and pass or fail by ordering.
    """
    calls: list[int] = []

    async def fake_probe() -> None:
        calls.append(1)
    monkeypatch.setattr(appmod.nim, "probe", fake_probe)
    appmod._NIM_PROBE_LIMIT.reset()
    monkeypatch.setattr(appmod._NIM_PROBE_LIMIT, "limit", 3)
    try:
        yield TestClient(api), calls
    finally:
        appmod._NIM_PROBE_LIMIT.reset()


class TestNimProbeRoute:
    def test_calls_up_to_the_limit_succeed(self, probe_client):
        client, calls = probe_client
        for _ in range(3):
            assert client.post("/api/nim/probe", headers=USER).status_code == 200
        assert len(calls) == 3

    def test_the_next_call_is_429_and_spends_nothing(self, probe_client):
        client, calls = probe_client
        for _ in range(3):
            client.post("/api/nim/probe", headers=USER)
        resp = client.post("/api/nim/probe", headers=USER)
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After")
        assert len(calls) == 3, "the refused request still reached NVIDIA -- it still cost money"

    def test_another_user_is_not_locked_out(self, probe_client):
        client, _ = probe_client
        for _ in range(3):
            client.post("/api/nim/probe", headers=USER)
        assert client.post("/api/nim/probe", headers=USER).status_code == 429
        assert client.post("/api/nim/probe", headers=OTHER).status_code == 200

    def test_a_FAILING_probe_is_metered_too(self, api, monkeypatch):
        """The case most likely to be retried in a loop is a bad key, and a bad key still
        costs a request. Metering only successes would leave exactly that path unbounded."""
        attempts: list[int] = []

        async def bad_probe() -> None:
            attempts.append(1)
            raise RuntimeError("401 unauthorized")
        monkeypatch.setattr(appmod.nim, "probe", bad_probe)
        appmod._NIM_PROBE_LIMIT.reset()
        monkeypatch.setattr(appmod._NIM_PROBE_LIMIT, "limit", 2)
        client = TestClient(api)
        try:
            assert client.post("/api/nim/probe", headers=USER).status_code == 502
            assert client.post("/api/nim/probe", headers=USER).status_code == 502
            assert client.post("/api/nim/probe", headers=USER).status_code == 429
            assert len(attempts) == 2
        finally:
            appmod._NIM_PROBE_LIMIT.reset()

    def test_an_unidentified_caller_is_still_refused_first(self, probe_client):
        """The limit is in addition to the gate, not instead of it: no identity is still 401,
        and it must not consume the shared allowance on the way."""
        client, calls = probe_client
        assert client.post("/api/nim/probe").status_code == 401
        assert calls == []

    def test_the_limit_is_configurable(self):
        """Wired to config so a deployment with a bigger NVIDIA budget can raise it without
        a code change -- and so this number is discoverable next to the other rail knobs."""
        assert config.NIM_PROBE_LIMIT >= 1
        assert config.NIM_PROBE_WINDOW_SECONDS > 0
