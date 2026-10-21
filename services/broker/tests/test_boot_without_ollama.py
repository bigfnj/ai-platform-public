"""A broker that comes up before Ollama does.

On a box where the broker is a service and Ollama is an app started at logon, the broker always
boots first. Two things went wrong in that window, and both were quiet:

1. A rail asking `GET /v1/models` at its own boot got a 500 and a chained httpx.ConnectError
   traceback in the broker log, once per request, because the route catches only ValueError and
   UpstreamError. The rail read "the broker is broken" about a backend that was merely not up yet.
2. The startup role audit ran once, read no model list, returned nothing and printed nothing.
   Silence is what a PASSING audit looks like, so a misconfigured role map stayed hidden until
   someone clicked a rail.

Now an Ollama that cannot be reached is a 503 naming it, logged once per outage; and a boot that
finds Ollama down says the audit is deferred, retries it in the background, and prints its
warnings once Ollama answers, or one line saying it never did.

Ollama here is an httpx.MockTransport slid beneath the OllamaClient the broker builds for
itself, so the error that reaches the route is the one httpx raises, and the client's own hooks
are the ones under test; nothing above the socket is stubbed.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import gpu
from app import main
from app.broker import Broker
from app.config import BrokerSettings

OLLAMA = "http://ollama.test:11434"
#: Installed on the fake Ollama. The role map below points @chat at something else, so an audit
#: that actually ran always has a finding to print.
INSTALLED = [{"name": "gemma3:4b", "size": 1, "digest": "d1"}]
ROLES = {"chat": "mistral-small3.1:24b", "embed": "gemma3:4b"}


class _Ollama:
    """Ollama at the transport level, switched between down (every request refused, as a closed
    port refuses it) and up."""

    def __init__(self, up: bool = False) -> None:
        self.up = up
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if not self.up:
            raise httpx.ConnectError("[WinError 10061] No connection could be made because the "
                                     "target machine actively refused it", request=request)
        answers = {"/api/version": {"version": "0.12.0"},
                   "/api/tags": {"models": INSTALLED},
                   "/api/ps": {"models": []},
                   "/api/show": {"capabilities": ["completion"]}}
        if request.url.path in answers:
            return httpx.Response(200, json=answers[request.url.path])
        return httpx.Response(404, json={"error": "not found"})


def _settings(tmp_path) -> BrokerSettings:
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps(ROLES), encoding="utf-8")
    return BrokerSettings(roles_file=str(roles), disabled_file=str(tmp_path / "disabled.json"),
                          tokens_file=str(tmp_path / "tokens.json"),
                          upstreams_file=str(tmp_path / "upstreams.json"),
                          auth_token="", ollama_base_url=OLLAMA)


#: The `_Ollama` answering at OLLAMA for the test in progress.
_BOXES: dict[str, _Ollama] = {}


def _broker(settings: BrokerSettings, box: _Ollama) -> Broker:
    """The broker as production builds it. Its OllamaClient opens its httpx client against
    OLLAMA, which `_ollama_transport` answers from `box`."""
    _BOXES[OLLAMA] = box
    return Broker(settings)


@pytest.fixture(autouse=True)
def _ollama_transport(monkeypatch):
    """Every httpx client built for OLLAMA gets a MockTransport onto the current `_Ollama`;
    any other client is built untouched."""
    real = httpx.AsyncClient

    def build(*args, **kw):
        if str(kw.get("base_url", "")).rstrip("/") == OLLAMA:
            kw["transport"] = httpx.MockTransport(lambda request: _BOXES[OLLAMA](request))
        return real(*args, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", build)
    yield
    _BOXES.clear()


@pytest.fixture(autouse=True)
def _no_gpu(monkeypatch):
    """audit_roles asks nvidia-smi for the card size; no card is part of this story."""
    async def none():
        return None
    monkeypatch.setattr(gpu, "vram", none)


def _client(tmp_path, box: _Ollama, **kw) -> TestClient:
    """The real app and routes, minus the lifespan (it would read the real environment)."""
    settings = _settings(tmp_path)
    main.app.state.settings = settings
    main.app.state.broker = _broker(settings, box)
    return TestClient(main.app, **kw)


def _outage_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "Ollama is not reachable" in ln]


# --- 1. the routes: a 503 naming Ollama, not a 500 and a traceback ------------------------------

@pytest.mark.parametrize("path", ["/v1/models", "/v1/ps"])
def test_a_route_that_needs_ollama_answers_503_naming_it_while_it_is_down(tmp_path, path):
    r = _client(tmp_path, _Ollama(up=False)).get(path)
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": f"Ollama is not reachable at {OLLAMA}"}


def test_the_same_route_answers_normally_once_ollama_is_up(tmp_path):
    box = _Ollama(up=False)
    client = _client(tmp_path, box)
    assert client.get("/v1/models").status_code == 503
    box.up = True
    r = client.get("/v1/models")
    assert r.status_code == 200 and [m["name"] for m in r.json()["models"]] == ["gemma3:4b"]


def test_an_outage_is_logged_once_and_the_next_outage_once_more(tmp_path, capsys):
    """A rail retrying at its boot is many requests in a few seconds. One line says Ollama is
    down; a traceback per request buried the log. The flag resets when Ollama answers, so the
    NEXT outage is announced too rather than inherited as already known."""
    box = _Ollama(up=False)
    client = _client(tmp_path, box)
    for _ in range(4):
        assert client.get("/v1/models").status_code == 503
    first = _outage_lines(capsys.readouterr().err)
    assert len(first) == 1, first
    assert OLLAMA in first[0]

    box.up = True
    assert client.get("/v1/models").status_code == 200
    box.up = False
    for _ in range(3):
        assert client.get("/v1/ps").status_code == 503
    second = _outage_lines(capsys.readouterr().err)
    assert len(second) == 1, f"the second outage was logged {len(second)} times: {second}"


def test_a_transport_error_from_elsewhere_is_not_blamed_on_ollama(tmp_path):
    """The 503 is for a request to OLLAMA. Any other unhandled transport failure keeps the
    ordinary 500 rather than being reported as an Ollama outage, which would send the operator
    to restart the wrong thing."""
    client = _client(tmp_path, _Ollama(up=True), raise_server_exceptions=False)

    async def elsewhere(upstream=None):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", "http://other.test/x"))
    main.app.state.broker.models_view = elsewhere
    r = client.get("/v1/models")
    assert r.status_code == 500
    assert "Ollama" not in r.text


# --- 2. the startup role audit: deferred, then run, never silently skipped ------------------------

def _boot_with(monkeypatch, tmp_path, box: _Ollama, **schedule) -> None:
    """Make the real lifespan build its broker on the fake Ollama, with a fast retry schedule.
    raising=False: the schedule constants are what this change adds, and on a tree without them
    the test should fail on what the boot PRINTS, not on a missing attribute."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(main, "BrokerSettings", lambda: settings)
    monkeypatch.setattr(main, "Broker", lambda s: _broker(s, box))
    for name, value in {"_AUDIT_RETRY_FIRST": 0.01, "_AUDIT_RETRY_CAP": 0.02,
                        **schedule}.items():
        monkeypatch.setattr(main, name, value, raising=False)


def test_a_boot_with_ollama_up_audits_at_once_as_before(monkeypatch, tmp_path, capsys):
    box = _Ollama(up=True)
    _boot_with(monkeypatch, tmp_path, box)

    async def boot():
        async with main.lifespan(main.app):
            return capsys.readouterr().err

    err = asyncio.run(boot())
    assert any("ROLE WARNING" in ln and "@chat ->" in ln for ln in err.splitlines()), err
    assert "deferred" not in err


def test_a_boot_before_ollama_defers_the_audit_and_prints_it_once_ollama_answers(
        monkeypatch, tmp_path, capsys):
    box = _Ollama(up=False)
    _boot_with(monkeypatch, tmp_path, box)

    async def boot() -> str:
        seen = ""
        async with main.lifespan(main.app):
            await asyncio.sleep(0.1)                 # still down: the retries keep failing
            seen += capsys.readouterr().err
            assert "ROLE WARNING" not in seen, "warnings printed before Ollama ever answered"
            box.up = True                            # the logon app starts
            for _ in range(300):
                await asyncio.sleep(0.01)
                seen += capsys.readouterr().err
                if "ROLE WARNING" in seen:
                    break
        return seen

    err = asyncio.run(boot())
    lines = err.splitlines()
    assert any("deferred" in ln and OLLAMA in ln for ln in lines), err
    warnings = [ln for ln in lines if "ROLE WARNING" in ln]
    assert any("@chat ->" in ln and "NOT installed" in ln for ln in warnings), (
        "Ollama answered but the role audit never printed its findings:\n" + err)
    assert box.paths.count("/api/version") >= 2, "the audit was not retried"


def test_shutdown_cancels_an_audit_still_waiting_for_ollama(monkeypatch, tmp_path, capsys):
    """A broker stopped while Ollama is still down must not leave the retry loop running (or
    print 'never answered' from a broker that is going away)."""
    box = _Ollama(up=False)
    _boot_with(monkeypatch, tmp_path, box, _AUDIT_RETRY_BUDGET=60.0)

    async def boot() -> list[asyncio.Task]:
        async with main.lifespan(main.app):
            await asyncio.sleep(0.05)
        await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()]

    left = asyncio.run(boot())
    assert left == [], f"still running after shutdown: {left}"
    assert "never answered" not in capsys.readouterr().err


def test_the_retry_backs_off_to_a_minute_and_gives_up_loudly_after_30_minutes(tmp_path, capsys):
    """2, 4, 8 ... 60 s between tries, for at most 30 minutes, then ONE line saying the audit
    never ran. Without that line a box whose Ollama never started would read exactly like a box
    whose role map passed."""
    box = _Ollama(up=False)
    broker = _broker(_settings(tmp_path), box)
    now = {"t": 0.0}
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        now["t"] += seconds

    asyncio.run(main.audit_when_ready(broker, sleep=sleep, clock=lambda: now["t"]))
    assert delays[:6] == [2, 4, 8, 16, 32, 60], delays[:8]
    assert max(delays) == 60
    assert sum(delays) == pytest.approx(30 * 60)
    assert box.paths.count("/api/version") == len(delays), "a wait without a try after it"
    err = capsys.readouterr().err
    skipped = [ln for ln in err.splitlines() if "role audit skipped" in ln]
    assert len(skipped) == 1 and "ROLE WARNING" in skipped[0] and OLLAMA in skipped[0], err


def test_the_retry_stops_at_the_first_answer_and_audits_once(tmp_path, capsys):
    box = _Ollama(up=False)
    broker = _broker(_settings(tmp_path), box)
    tries = {"n": 0}

    async def sleep(_seconds: float) -> None:
        tries["n"] += 1
        box.up = tries["n"] >= 3                   # answers on the third try

    asyncio.run(main.audit_when_ready(broker, sleep=sleep, clock=lambda: 0.0))
    assert tries["n"] == 3
    err = capsys.readouterr().err
    assert sum("@chat ->" in ln and "ROLE WARNING" in ln for ln in err.splitlines()) == 1, err
    assert "skipped" not in err
