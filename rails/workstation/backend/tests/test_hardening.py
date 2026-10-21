"""Four items the 2026-09-10/11 audits recorded on this rail and nobody closed, re-found by the
2026-10-08 audit. This rail opens a shell on the host, so each is worth its own test:

  * terminal dimensions were unclamped on the query string AND the resize frame and reached
    the host's conpty as sent (`?cols=2000000&rows=2000000`);
  * WORKSTATION_PRESETS_JSON / _RDP_APPS_JSON were parsed per request with no error handling,
    so a malformed override 500'd every call and an entry without `label`/`alias` KeyError'd;
  * the audit-log guard wrapped makedirs but not the handler, so an existing-but-unopenable log
    crash-looped the rail instead of degrading to no-audit;
  * /docs, /openapi.json and /api/healthz answered a caller with no identity.

The SSH side is a fake: no test here opens a real session to anything.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from workstation_app import main
from workstation_app.config import WorkstationSettings

HDR = {"X-Platform-User": "owner-one", "X-Platform-Admin": "0"}


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    for var in ("WORKSTATION_PRESETS_JSON", "WORKSTATION_RDP_APPS_JSON"):
        monkeypatch.delenv(var, raising=False)


# --- dimensions --------------------------------------------------------------------------

class FakeProcess:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.sizes: list[tuple[int, int]] = []
        self.exit_status = 0
        self._done = asyncio.Event()
        self.stdout = self
        self.stdin = self

    async def read(self, n):
        await self._done.wait()
        return b""

    def write(self, data):
        pass

    def change_terminal_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def terminate(self):
        self._done.set()

    async def wait(self):
        return 0


class FakeConn:
    def __init__(self, made):
        self.made = made

    async def create_process(self, *args, **kwargs):
        p = FakeProcess(kwargs)
        self.made.append(p)
        return p

    def close(self):
        pass


@pytest.fixture
def ssh(monkeypatch):
    made: list[FakeProcess] = []

    async def fake_connect(settings):
        return FakeConn(made)

    monkeypatch.setattr(main, "_connect", fake_connect)
    return made


def test_the_query_string_size_is_clamped_before_it_reaches_the_host(ssh):
    with TestClient(main.app) as c:
        with c.websocket_connect("/ws/shell?cols=2000000&rows=-5", headers=HDR) as ws:
            ws.send_bytes(b"\x00")
    (p,) = ssh
    assert p.kwargs["term_size"] == (500, 1, 0, 0)


def test_a_resize_frame_is_clamped_too(ssh):
    with TestClient(main.app) as c:
        with c.websocket_connect("/ws/shell?cols=80&rows=24", headers=HDR) as ws:
            ws.send_bytes(b"\x01" + json.dumps({"cols": 2000000, "rows": 2000000}).encode())
            ws.send_bytes(b"\x01" + json.dumps({"cols": 120, "rows": 40}).encode())
            ws.send_bytes(b"\x00")
    (p,) = ssh
    assert p.sizes == [(500, 500), (120, 40)]


def test_the_socket_refuses_on_its_own_too(monkeypatch):
    """The in-route backstop, called directly so the app-level dependency is not what refuses."""
    class WS:
        headers: dict = {}
        app = main.app
        close_code = None

        async def close(self, code=1000):
            WS.close_code = code

    with TestClient(main.app):                  # the lifespan sets app.state.settings
        asyncio.run(main.ws_terminal(WS(), "shell"))
    assert WS.close_code == 4401


# --- the JSON overrides, validated once at startup ------------------------------------------

@pytest.mark.parametrize("raw, says", [
    ('[{"id": "x",', "not valid JSON"),
    ('{"id": "x"}', "non-empty JSON list"),
    ("[]", "non-empty JSON list"),
    ('[{"id": "x"}]', "needs a non-empty label"),
    ('["shell"]', "is not an object"),
    ('[{"id": "x", "label": "X", "command": 7}]', "must be strings"),
    ('[{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]', "repeats an id"),
])
def test_a_bad_presets_override_stops_the_rail_at_startup(raw, says):
    with pytest.raises(ValueError) as err:
        WorkstationSettings(presets_json=raw)
    assert "WORKSTATION_PRESETS_JSON" in str(err.value) and says in str(err.value)


def test_a_bad_rdp_override_stops_the_rail_at_startup():
    """`alias` is what the .rdp launcher interpolates; an entry without one KeyError'd there."""
    with pytest.raises(ValueError) as err:
        WorkstationSettings(rdp_apps_json='[{"id": "np", "label": "Notepad"}]')
    assert "WORKSTATION_RDP_APPS_JSON" in str(err.value) and "alias" in str(err.value)


def test_a_good_override_is_parsed_once_and_icon_is_optional(monkeypatch):
    monkeypatch.setenv("WORKSTATION_PRESETS_JSON", '[{"id": "x", "label": "X", "command": "top"}]')
    s = WorkstationSettings()
    calls = []
    real = json.loads
    monkeypatch.setattr(json, "loads", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert s.presets() == [{"id": "x", "label": "X", "command": "top"}]
    assert s.presets() is s.presets()
    assert calls == [], "the override was parsed again on a request"
    with TestClient(main.app) as c:
        r = c.get("/api/presets", headers=HDR)
    assert r.status_code == 200 and r.json() == {"presets": [{"id": "x", "label": "X", "icon": ""}]}


def test_no_override_still_means_the_defaults():
    s = WorkstationSettings()
    assert [p["id"] for p in s.presets()] == ["shell", "claude", "codex"]


# --- the audit log ------------------------------------------------------------------------

def test_an_unopenable_audit_log_degrades_to_no_audit(tmp_path, monkeypatch):
    """The audit's probe: the dir exists, sessions.log is a DIRECTORY, so opening it fails."""
    audit = tmp_path / "audit"
    (audit / "sessions.log").mkdir(parents=True)
    monkeypatch.setenv("WORKSTATION_AUDIT_DIR", str(audit))
    for h in list(main.logging.getLogger("workstation.audit").handlers):
        main.logging.getLogger("workstation.audit").removeHandler(h)
    with TestClient(main.app) as c:
        assert c.get("/api/healthz", headers=HDR).status_code == 200
        assert main.app.state.audit is None


def test_a_refused_handshake_reaches_this_test_s_audit_file(tmp_path):
    """The audit FILE, in a suite run. The logger got its file handler only when it had no
    handler at all, and under pytest 9 it carries capture handlers after the first test, so no
    app in a suite run wrote its audit file (2026-10-14). Both states are set up here rather
    than left to test order: a stream handler on the logger (pytest's capture handlers are
    stream handlers), and an earlier app's file handler on another path, which is replaced, so
    one trail writes one file."""
    import io

    from starlette.websockets import WebSocketDisconnect
    log = main.logging.getLogger("workstation.audit")
    capture = main.logging.StreamHandler(io.StringIO())
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    stale = main.logging.FileHandler(earlier / "sessions.log", encoding="utf-8")
    log.addHandler(capture)
    log.addHandler(stale)
    try:
        with TestClient(main.app) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws/shell") as ws:
                    ws.receive()
    finally:
        log.removeHandler(capture)
        for h in [h for h in log.handlers if isinstance(h, main.logging.FileHandler)]:
            log.removeHandler(h)
            h.close()
        stale.close()
    written = tmp_path / "audit" / "sessions.log"
    assert written.is_file(), "the app never opened its own audit file"
    lines = [json.loads(ln) for ln in written.read_text(encoding="utf-8").splitlines()]
    assert [(e["event"], e["user"], e["preset"], e["status"]) for e in lines] == [
        ("connect_denied", "-", "shell", 401)], lines
    assert (earlier / "sessions.log").read_text(encoding="utf-8") == "", (
        "an earlier app's audit file went on receiving this app's lines")


@pytest.fixture
def audit_lines():
    """Every line the app writes to its audit logger in this test, parsed, read off the logger
    itself: the order of events is what these tests check, and the file is
    test_a_refused_handshake_reaches_this_test_s_audit_file's."""
    log = main.logging.getLogger("workstation.audit")
    lines: list[dict] = []

    class Collect(main.logging.Handler):
        def emit(self, record):
            lines.append(json.loads(record.getMessage()))

    handler = Collect()
    log.addHandler(handler)
    yield lines
    log.removeHandler(handler)


def test_a_refused_handshake_writes_an_audit_line(audit_lines, ssh):
    """The app-wide identity dependency refuses a handshake before ws_terminal runs, so its own
    connect_denied line was unreachable and a refused PTY handshake was never audited (audit
    2026-10-14). Absent and blank identities are both refused, and both audited; a named one is
    a connect; an HTTP 401 is not a handshake and writes nothing."""
    from starlette.websockets import WebSocketDisconnect
    with TestClient(main.app) as c:
        for headers in ({}, {"X-Platform-User": " "}):
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect("/ws/shell", headers=headers) as ws:
                    ws.receive()
            assert getattr(exc.value, "status_code", None) == 401
        assert c.get("/api/presets").status_code == 401
        with c.websocket_connect("/ws/shell", headers=HDR) as ws:
            ws.send_bytes(b"\x00")
    got = [(e["event"], e["user"], e.get("preset"), e.get("status")) for e in audit_lines]
    assert got[:2] == [("connect_denied", "-", "shell", 401)] * 2, got
    assert [e for e, *_ in got[2:]] == ["connect", "disconnect"], got
    errors = [e["error"] for e in audit_lines[:2]]
    assert "no platform identity" in errors[0] and "blank platform identity" in errors[1], errors


# --- what an un-identified caller can read ------------------------------------------------

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_interactive_docs_are_not_served_even_to_a_named_caller(path):
    with TestClient(main.app) as c:
        assert c.get(path, headers=HDR).status_code == 404
        assert c.get(path).status_code in (401, 404)


def test_every_http_route_refuses_a_caller_with_no_identity():
    with TestClient(main.app) as c:
        for path in ("/api/healthz", "/api/presets", "/api/remoteapp", "/api/remoteapp/x.rdp"):
            assert c.get(path).status_code == 401, path
