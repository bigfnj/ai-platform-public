"""Workstation terminal backend — FastAPI.

Run (dev): uvicorn workstation_app.main:app --app-dir apps/workstation/backend --port 8720

Routes:
  GET  /api/healthz            liveness (+ whether host-key checking is disabled)
  GET  /api/presets            the preset tabs the frontend renders
  GET  /api/remoteapp          desktop apps published from the host over RDP
  GET  /api/remoteapp/{id}.rdp a launcher that opens ONE app seamlessly via mstsc
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
from fastapi import (Depends, FastAPI, HTTPException, Response, WebSocket,
                     WebSocketDisconnect)

from workstation_app.config import WorkstationSettings
from workstation_app.identity import Identity, identity, ws_user

_READ_CHUNK = 65536
# Watchdog poll cadence (seconds). Only the enforcement granularity of the idle/absolute
# timeouts, which are minutes/hours; it's an awaited sleep (zero CPU between ticks), so
# it's deliberately coarse rather than tight.
_WATCH_INTERVAL_SECS = 30
# How long teardown waits for the SSH exit-status message before giving up and reporting
# an unknown status. Short: it only ever covers the gap between stdout EOF and the status
# message on an already-terminated channel.
_EXIT_STATUS_GRACE_SECS = 2
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
def presets(_: Identity = Depends(identity)) -> dict[str, Any]:
    # Don't leak the launch command to the browser — only what the tabs need.
    return {"presets": [{"id": p["id"], "label": p["label"], "icon": p["icon"]}
                        for p in app.state.settings.presets()]}


# --- RemoteApp: published desktop apps over RDP -----------------------------
# The browser never speaks RDP. This hands back an .rdp launcher that the client's own
# mstsc opens in RemoteApp mode, so a single application window lands on the user's
# desktop with no remote desktop around it. Enabled only when WORKSTATION_RDP_HOST is
# set, and only for apps in the configured list — nothing from the request reaches the
# generated file except the id, which is matched against that list first.


def _rdp_line(key: str, kind: str, value: Any) -> str:
    """One `key:type:value` line. An .rdp is newline-delimited directives, so a stray
    newline in a value would inject settings — strip them rather than escape."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return f"{key}:{kind}:{text}"


def _build_rdp(settings: WorkstationSettings, rdp_app: dict[str, str]) -> str:
    alias = str(rdp_app["alias"]).replace("\r", "").replace("\n", "")
    lines = [
        _rdp_line("full address", "s", f"{settings.rdp_host}:{settings.rdp_port}"),
        # RemoteApp mode. `alternate shell` carries the same ||alias and is what older
        # clients read; both are expected to be present.
        _rdp_line("remoteapplicationmode", "i", 1),
        _rdp_line("alternate shell", "s", f"||{alias}"),
        _rdp_line("remoteapplicationprogram", "s", f"||{alias}"),
        _rdp_line("remoteapplicationname", "s", rdp_app.get("label", alias)),
        _rdp_line("remoteapplicationcmdline", "s", rdp_app.get("args", "")),
        # Don't expand %VARS% in the command line on the client side.
        _rdp_line("remoteapplicationexpandcmdline", "i", 0),
        # REQUIRED on Windows Pro: the client otherwise refuses RemoteApp because the
        # target doesn't advertise the RDS role. This skips that capability probe.
        _rdp_line("disableremoteappcapscheck", "i", 1),
        # 2 = warn but allow if server auth fails. A workstation presents a self-signed
        # RDP cert, so level 1 (refuse) would block every connection until it's trusted.
        _rdp_line("authentication level", "i", 2),
        _rdp_line("prompt for credentials on client", "i", 1),
        _rdp_line("promptcredentialonce", "i", 1),
        # Redirection: clipboard is the one that matters for an editor. Drives,
        # printers, ports and smart cards stay off so the session can't reach back
        # into the client's filesystem.
        _rdp_line("redirectclipboard", "i", 1 if settings.rdp_redirect_clipboard else 0),
        _rdp_line("redirectprinters", "i", 0),
        _rdp_line("redirectcomports", "i", 0),
        _rdp_line("redirectsmartcards", "i", 0),
        _rdp_line("drivestoredirect", "s", ""),
        _rdp_line("audiomode", "i", 2),  # don't carry audio at all
        _rdp_line("session bpp", "i", 32),
        _rdp_line("compression", "i", 1),
        _rdp_line("bitmapcachepersistenable", "i", 1),
        _rdp_line("networkautodetect", "i", 1),
        _rdp_line("bandwidthautodetect", "i", 1),
        _rdp_line("connection type", "i", 7),
    ]
    if settings.rdp_username:
        lines.append(_rdp_line("username", "s", settings.rdp_username))
    # CRLF + trailing newline: mstsc is tolerant, but .rdp files are conventionally CRLF.
    return "\r\n".join(lines) + "\r\n"


@app.get("/api/remoteapp")
def remoteapp_list(_: Identity = Depends(identity)) -> dict[str, Any]:
    s: WorkstationSettings = app.state.settings
    if not s.rdp_enabled():
        # Not an error: the rail works fine without it, the frontend just hides the row.
        return {"enabled": False, "apps": []}
    return {
        "enabled": True,
        "host": s.rdp_host,
        "apps": [{"id": a["id"], "label": a["label"], "icon": a.get("icon", "")}
                 for a in s.rdp_apps()],
    }


@app.get("/api/remoteapp/{app_id}.rdp")
def remoteapp_file(app_id: str, ident: Identity = Depends(identity)) -> Response:
    s: WorkstationSettings = app.state.settings
    if not s.rdp_enabled():
        raise HTTPException(status_code=404, detail="RemoteApp is not configured")
    rdp_app = s.rdp_app(app_id)
    if rdp_app is None:
        raise HTTPException(status_code=404, detail=f"unknown published app '{app_id}'")
    # This launcher opens a session on the host, so the identity is now REQUIRED, not merely
    # recorded: the route used to read the header only to name the audit line, and defaulted
    # it to "?" when absent — which meant a header-less caller got the launcher and the audit
    # trail said "?" took it.
    _audit(app, "remoteapp.download", user=ident.user or "standalone", app=app_id)
    return Response(
        content=_build_rdp(s, rdp_app),
        media_type="application/x-rdp",
        headers={"Content-Disposition": f'attachment; filename="{app_id}.rdp"'},
    )


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
    # Identity FIRST, before the preset is validated: this socket opens a real shell on the
    # host, and an un-gated caller should not learn which presets exist by probing close codes.
    # A websocket cannot carry a 401 body, so the rejection is a close code.
    user = ws_user(ws)
    if user is None:
        _audit(ws.app, "connect_denied", user="-", preset=preset,
               error="no platform identity on the handshake")
        await ws.close(code=4401)
        return
    user = user or "standalone"
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
        # Let the channel settle before asking for the status. `exit_status` is filled in
        # only when the server's exit-status message arrives, which is AFTER stdout hits
        # EOF — and pump_out completes ON that EOF, so reading it straight away returned
        # None for EVERY clean exit. Users then read "session ended (exit None)" as a
        # crash when the program had simply quit. The wait is bounded because a session
        # torn down from the browser side leaves the process to terminate() above.
        try:
            await asyncio.wait_for(process.wait(), timeout=_EXIT_STATUS_GRACE_SECS)
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
