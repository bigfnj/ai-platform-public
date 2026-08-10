"""Workstation terminal backend — FastAPI.

Run (dev): uvicorn workstation_app.main:app --app-dir apps/workstation/backend --port 8720

Routes:
  GET  /api/healthz            liveness (+ whether host-key checking is disabled)
  GET  /api/presets            the preset tabs the frontend renders
  WS   /ws/{preset}            a browser terminal ⇆ a PTY-over-SSH session

The gateway sits in front: it authenticates the WS handshake (session cookie +
entitlement) and forwards the verified identity as x-platform-user, so this
backend is never directly reachable by a browser.

Hardening in this backend:
  - loud warning + a healthz flag if host-key verification is disabled (P0.2)
  - idle + absolute session timeout so an abandoned terminal doesn't stay open (P1.3)
  - a daily-rotating, retention-capped audit trail of who connected when (P2.1)

WS frame protocol (both directions are binary unless noted):
  client → server:  0x00 + bytes  = terminal input
                    0x01 + JSON    = {"cols":N,"rows":N} resize
  server → client:  0x00 + bytes  = PTY output
                    (text) 0x04 + str = a human status line (connect/exit errors)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from typing import Any

import asyncssh
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from workstation_app.config import WorkstationSettings

_READ_CHUNK = 65536
# Watchdog poll cadence (seconds). Only the enforcement granularity of the idle/absolute
# timeouts, which are minutes/hours; it's an awaited sleep (zero CPU between ticks), so
# it's deliberately coarse rather than tight.
_WATCH_INTERVAL_SECS = 30
_log = logging.getLogger("workstation")


def _make_audit_logger(settings: WorkstationSettings) -> logging.Logger | None:
    """A daily-rotating audit log that keeps `audit_retention_days` files and
    auto-deletes older ones. Returns None if auditing is off or the dir is unusable
    (so a bad mount degrades to no-audit rather than crashing the backend)."""
    if not settings.audit_enabled:
        return None
    try:
        os.makedirs(settings.audit_dir, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        _log.warning("audit disabled: cannot use %s (%s)", settings.audit_dir, exc)
        return None
    log = logging.getLogger("workstation.audit")
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        handler = TimedRotatingFileHandler(
            os.path.join(settings.audit_dir, "sessions.log"),
            when="midnight", backupCount=max(1, settings.audit_retention_days), utc=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    return log


def _audit(target: FastAPI, event: str, **fields: Any) -> None:
    log: logging.Logger | None = getattr(target.state, "audit", None)
    if log is None:
        return
    log.info(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = WorkstationSettings()
    app.state.settings = settings
    if settings.insecure_skip_host_key_check:
        _log.warning(
            "SSH host-key verification is DISABLED "
            "(WORKSTATION_INSECURE_SKIP_HOST_KEY_CHECK=true). First-run convenience only — "
            "pin a known_hosts for the target and turn this off in production.")
    app.state.audit = _make_audit_logger(settings)
    yield


app = FastAPI(title="Workstation Terminal", version="0.2.0", lifespan=lifespan)


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    s: WorkstationSettings = app.state.settings
    # Expose the insecure toggle so a smoke test can assert it's off in prod.
    return {"ok": True, "app": s.app_name, "insecure_host_key": s.insecure_skip_host_key_check}


@app.get("/api/presets")
def presets() -> dict[str, Any]:
    # Don't leak the launch command to the browser — only what the tabs need.
    return {"presets": [{"id": p["id"], "label": p["label"], "icon": p["icon"]}
                        for p in app.state.settings.presets()]}


async def _connect(settings: WorkstationSettings) -> asyncssh.SSHClientConnection:
    opts: dict[str, Any] = {"host": settings.ssh_host, "port": settings.ssh_port}
    if settings.ssh_user:
        opts["username"] = settings.ssh_user
    if settings.ssh_key_path:
        opts["client_keys"] = [settings.ssh_key_path]
    if settings.insecure_skip_host_key_check:
        opts["known_hosts"] = None  # INSECURE: no host-key verification
    elif settings.known_hosts_path:
        opts["known_hosts"] = settings.known_hosts_path
    # else: asyncssh checks the container user's ~/.ssh/known_hosts (secure default;
    # an unknown host fails loudly rather than trusting silently).
    return await asyncssh.connect(**opts)


async def _send_status(ws: WebSocket, text: str) -> None:
    try:
        await ws.send_text("\x04" + text)
    except Exception:  # noqa: BLE001
        pass


@app.websocket("/ws/{preset}")
async def ws_terminal(ws: WebSocket, preset: str) -> None:
    settings: WorkstationSettings = ws.app.state.settings
    if preset not in settings.preset_ids():
        await ws.close(code=4404)
        return

    def _dim(name: str, default: int) -> int:
        try:
            return max(1, int(ws.query_params.get(name, default)))
        except (TypeError, ValueError):
            return default

    cols, rows = _dim("cols", 80), _dim("rows", 24)
    command = settings.preset_command(preset)
    # Identity is set by the gateway on the upstream handshake, never client-supplied here.
    user = ws.headers.get("x-platform-user", "?")
    await ws.accept()

    conn: asyncssh.SSHClientConnection | None = None
    process: asyncssh.SSHClientProcess | None = None
    try:
        conn = await _connect(settings)
        kwargs: dict[str, Any] = {
            "term_type": settings.term_type,
            "term_size": (cols, rows, 0, 0),
            "encoding": None,  # raw bytes both ways
        }
        # No command => an interactive login shell (asyncssh default).
        process = await (conn.create_process(command, **kwargs) if command
                         else conn.create_process(**kwargs))
    except Exception as exc:  # noqa: BLE001 — surface any connect/auth failure to the user
        _audit(ws.app, "connect_failed", user=user, preset=preset, error=str(exc))
        await _send_status(ws, f"connection failed: {exc}")
        await ws.close(code=1011)
        if conn is not None:
            conn.close()
        return

    started = time.monotonic()
    last_activity = started  # reset by keystrokes AND server output (see the pumps)
    end_reason: str | None = None
    _audit(ws.app, "connect", user=user, preset=preset, cols=cols, rows=rows)

    async def pump_out() -> None:
        nonlocal last_activity
        try:
            while True:
                data = await process.stdout.read(_READ_CHUNK)
                if not data:
                    break
                await ws.send_bytes(b"\x00" + data)
                last_activity = time.monotonic()  # output keeps an active session alive
        except Exception:  # noqa: BLE001
            pass

    async def pump_in() -> None:
        nonlocal last_activity
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                raw = msg.get("bytes")
                if raw is None and msg.get("text") is not None:
                    raw = msg["text"].encode()
                if not raw:
                    continue
                tag, body = raw[0], raw[1:]
                if tag == 0x00:
                    last_activity = time.monotonic()  # keystrokes reset the idle clock
                    process.stdin.write(bytes(body))
                elif tag == 0x01:
                    try:
                        size = json.loads(body)
                        process.change_terminal_size(int(size["cols"]), int(size["rows"]))
                    except Exception:  # noqa: BLE001
                        pass
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass

    async def watchdog() -> None:
        # Ends the session on idle (no I/O — neither keystrokes nor output) or an absolute
        # cap. Either 0 disables that check. Completing here trips FIRST_COMPLETED and runs
        # the teardown below. The poll interval only sets enforcement granularity (a session
        # can overrun its deadline by at most one interval) and costs nothing between ticks —
        # an awaited timer, not a busy loop — so it's coarse next to minute/hour timeouts.
        nonlocal end_reason
        idle, hard = settings.idle_secs, settings.max_secs
        if not idle and not hard:
            return await asyncio.Event().wait()  # nothing to watch; sleep forever
        while True:
            await asyncio.sleep(_WATCH_INTERVAL_SECS)
            now = time.monotonic()
            if hard and now - started >= hard:
                end_reason = "max duration reached"
                return
            if idle and now - last_activity >= idle:
                end_reason = "idle timeout"
                return

    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in())
    watch_task = asyncio.create_task(watchdog())
    try:
        await asyncio.wait({out_task, in_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (out_task, in_task, watch_task):
            t.cancel()
        try:
            process.terminate()
        except Exception:  # noqa: BLE001
            pass
        status = getattr(process, "exit_status", None)
        _audit(ws.app, "disconnect", user=user, preset=preset, exit=status,
               duration_s=round(time.monotonic() - started, 1), reason=end_reason)
        msg = f"session ended ({end_reason})" if end_reason else f"session ended (exit {status})"
        await _send_status(ws, msg)
        conn.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
