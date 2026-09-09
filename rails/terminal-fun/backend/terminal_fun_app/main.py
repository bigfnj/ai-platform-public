"""Terminal Fun — FastAPI backend.

Routes:
  GET  /api/healthz          liveness (+ whether the broker/AI is reachable)
  GET  /api/catalog          picker sections + display items (label/icon/watch/tunable/info)
  GET  /api/tunables/{id}    the tunable-parameter schema + defaults for a toy (or 404)
  POST /api/chat             the page assistant: answers questions + can return a validated
                             `set_params` action for the active tunable toy
  WS   /ws/{item_id}         a browser terminal ⇆ a local PTY; a 0x02 frame relaunches the
                             toy with new (validated) settings without dropping the socket

The gateway authenticates the WS handshake (session cookie + entitlement) and injects
x-platform-user, so this backend is never directly reachable by a browser.

WS frame protocol:
  client → server:  0x00 + bytes  = terminal input
                    0x01 + JSON    = {"cols":N,"rows":N} resize
                    0x02 + JSON    = {params...} apply new settings + relaunch (tunable toys)
  server → client:  0x00 + bytes  = PTY output
                    (text) 0x04 + str = a human status line
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from terminal_fun_app import broker, modelstate, saves
from terminal_fun_app.catalog import CATEGORIES, ITEMS, item_by_id, public_catalog
from terminal_fun_app.config import settings
from terminal_fun_app.identity import Identity, identity, standalone, ws_user
from terminal_fun_app.tunables import build_launch, defaults, is_tunable, schema, validate_params

if TYPE_CHECKING:  # pty_session is Linux-only (pty/fcntl/termios); imported lazily below so that
    from terminal_fun_app.pty_session import PtySession  # importing this app works off-container

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("terminal-fun")

# Identity is required app-wide (bouquet's precedent). Nothing here was gated: the caller was
# read as `Header(default="?")`, so a request with no gateway header was not refused — it was
# handed the literal user "?", which saves.py maps to a shared "anon" bucket. A sibling
# container could therefore read and DELETE saves out of that bucket, spend broker time on
# /api/chat, and open a PTY on the toy container, with every audit line naming "?". An
# app-level dependency is used rather than one per route so a new route cannot be added
# un-gated by omission.
#
# Docs are disabled with it: FastAPI's /docs and /openapi.json bypass app-level dependencies,
# so leaving them on would publish the whole route list to an un-gated caller.
app = FastAPI(title="Terminal Fun", version="0.2.0",
              docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(identity)])

_sessions: Counter[str] = Counter()

_IN = 0x00
_RESIZE = 0x01
_APPLY = 0x02
_STATUS = "\x04"


def _audit(event: str, **fields: Any) -> None:
    _log.info(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}))


def _webtoys_dir() -> str | None:
    """Resolve the static web-toys directory: the configured path baked into the image, else
    the repo rootfs copy for local dev. Returns None if neither exists (mount is skipped)."""
    candidates = [
        settings.webtoys_dir,
        str(Path(__file__).resolve().parents[2] / "deploy" / "rootfs" / "opt" / "fun" / "webtoys"),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


class _GatedStaticFiles(StaticFiles):
    """StaticFiles behind the same gate as every route.

    A mount is not an APIRoute, so the app-level dependency never runs for it — mounting plain
    StaticFiles under a fail-closed app leaves one path that still answers anybody, and a gate
    with one hole in it is the kind that gets trusted anyway. The check is inlined rather than
    reusing identity() because a dependency cannot be attached to a mount at all.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not Headers(scope=scope).get("x-platform-user") and not standalone():
            await JSONResponse({"detail": "unauthenticated (no platform identity)"},
                               status_code=401)(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


# Graphical "web toys" (e.g. Buzzed Eyes) are static HTML/canvas, not terminal programs, so
# they're served as files rather than over a PTY: the frontend embeds /api/webtoys/<id>/index.html
# in a sandboxed iframe. The gateway proxies /{app}/api/* to this backend, so this is reachable
# at /terminal-fun/api/webtoys/<id>/. Mount is skipped if no asset dir is present.
_WEBTOYS_DIR = _webtoys_dir()
if _WEBTOYS_DIR:
    app.mount("/api/webtoys", _GatedStaticFiles(directory=_WEBTOYS_DIR, html=True), name="webtoys")


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "app": settings.app_name, "items": len(ITEMS), "ai": broker.up()}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {
        "categories": [{"id": cid, "label": label} for cid, label in CATEGORIES],
        "items": public_catalog(),
    }


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    """Four-state status for the header chip: the in-terminal assistant model (@terminal-fun, the
    role the admin Rails panel repoints). Resolved live from the broker each call, so the chip
    tracks the current admin selection + GPU residency."""
    return modelstate.resolve([("assistant", "Assistant", settings.llm_model)])


@app.get("/api/saves")
def saves_list(ident: Identity = Depends(identity)) -> dict[str, Any]:
    """Which saveable games this owner has an in-progress save for (drives the Resume label)."""
    return {"saves": saves.list_saves(ident.user)}


@app.delete("/api/saves/{item_id}")
def saves_discard(item_id: str, ident: Identity = Depends(identity)) -> dict[str, Any]:
    """Discard this owner's stored save for a game (start fresh next time)."""
    if not saves.is_saveable(item_id):
        raise HTTPException(status_code=404, detail="not a saveable game")
    saves.discard(ident.user, item_id)
    return {"ok": True}


@app.get("/api/tunables/{item_id}")
def tunables(item_id: str) -> dict[str, Any]:
    sch = schema(item_id)
    if sch is None:
        raise HTTPException(status_code=404, detail="not tunable")
    return {"schema": sch, "defaults": defaults(item_id)}


# --- AI page assistant ------------------------------------------------------

class ChatBody(BaseModel):
    message: str
    history: list[dict] = []
    item: str | None = None
    params: dict | None = None


def _catalog_brief() -> str:
    # Full how-to-play for every item (the same text as the ⓘ panels) so the assistant
    # answers controls/quit from the source of truth, not the base model's memory.
    blocks = []
    for i in ITEMS:
        kind = "watch" if not i.allow_input else "play"
        info = (i.info or "").strip()
        blocks.append(f"### {i.label} [{i.category}, {kind}]\n{info}")
    return "\n\n".join(blocks)


def _system_prompt(item_id: str | None, params: dict | None) -> str:
    parts = [
        "You are the assistant for \"Terminal Fun\", a page of self-hosted terminal games and "
        "toys (ASCII Star Wars, matrix rain, a bonsai generator, roguelikes like NetHack, arcade "
        "games, etc.). Be brief, friendly, and concrete. Answer questions about what's on the page, "
        "what each item is, how to play, and what can be changed.",
        "",
        "The games and toys on the page — with exactly how to play each one. When asked how to "
        "play, what the controls are, or how to quit, use THESE instructions as the source of truth. "
        "State only the keys, controls, and tips written here for that game — do NOT add, invent, or "
        "borrow keys or tips from memory or from other games:",
        _catalog_brief(),
    ]
    it = item_by_id(item_id) if item_id else None
    if it:
        parts += ["", f'IMPORTANT — the user is RIGHT NOW viewing "{it.label}" on screen. The words '
                      f'"it", "this", "that", "the game", "the toy", "change it", and questions like '
                      f'"how do I play/quit" ALL refer to {it.label} — even if earlier messages in this '
                      f'chat were about a different item. Do NOT answer about any other game unless the '
                      f'user explicitly names it.']
    else:
        parts += ["", "The user is on the main menu (no game currently open)."]
    tune = schema(item_id) if item_id else None
    if tune and item_id:
        cur = validate_params(item_id, params)
        lines = []
        for p in tune["params"]:
            opts = ""
            if p["kind"] in ("enum", "multienum") and p["choices"]:
                opts = f" (options: {', '.join(p['choices'])})"
            elif p["kind"] == "int":
                opts = f" (integer {p['min']}–{p['max']})"
            elif p["kind"] == "bool":
                opts = " (true/false)"
            lines.append(f"  - {p['key']}: {p['label']}{opts} — currently {cur.get(p['key'])!r}")
        parts += [
            "",
            f"The user is currently viewing a TUNABLE toy (id \"{item_id}\"). You can change its "
            f"settings live. Its parameters:",
            "\n".join(lines),
            "",
            "To change settings, put ONLY the parameters you want to change in `set_params`, using "
            "allowed values exactly. Do not invent parameters or values. If the user is just asking "
            "a question, set `set_params` to null.",
        ]
    else:
        parts += [
            "",
            "The user is not currently viewing a tunable toy, so `set_params` must be null. If they "
            "ask to change a toy's settings, tell them to open that toy first.",
        ]
    parts += [
        "",
        "STAY ON TOPIC: you ONLY help with THIS page — the games/toys listed above, how to play "
        "them, and their settings. If the user asks for anything unrelated (writing code, general "
        "knowledge, math, trivia, current events, etc.), do NOT answer it. Instead briefly say you're "
        "the Terminal Fun helper and can only help with the games and toys on this page.",
        "",
        "Respond ONLY as JSON: {\"reply\": \"<your short answer>\", \"set_params\": {<param>: <value>, ...} or null}",
    ]
    return "\n".join(parts)


@app.post("/api/chat")
def chat(body: ChatBody) -> dict[str, Any]:
    msgs = [{"role": "system", "content": _system_prompt(body.item, body.params)}]
    for m in body.history[-6:]:
        role = m.get("role")
        content = str(m.get("content", ""))[:2000]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": (body.message or "")[:1000]})

    try:
        data = broker.chat_json(msgs)
    except broker.BrokerError as exc:
        return {"reply": f"(The assistant is unavailable right now — {exc})", "action": None}

    reply = str(data.get("reply") or data.get("answer") or data.get("message") or "").strip()
    if not reply:
        reply = "Sorry, I didn't catch that — try asking again."

    action = None
    sp = data.get("set_params")
    if isinstance(sp, dict) and sp and body.item and is_tunable(body.item):
        merged = {**(body.params or {}), **sp}
        clean = validate_params(body.item, merged)
        changed = {k: v for k, v in clean.items() if (body.params or {}).get(k) != v}
        if changed:
            action = {"type": "set_params", "item": body.item, "params": clean, "changed": changed}
    return {"reply": reply, "action": action}


# --- terminal websocket -----------------------------------------------------

def _dim(ws: WebSocket, name: str, default: int) -> int:
    try:
        return max(1, min(500, int(ws.query_params.get(name, default))))
    except (TypeError, ValueError):
        return default


async def _status(ws: WebSocket, text: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_text(_STATUS + text)


@app.websocket("/ws/{item_id}")
async def ws_terminal(ws: WebSocket, item_id: str) -> None:
    # Identity FIRST, before the item is even looked up: this socket spawns a real process in
    # the container and owns that owner's save files, so an un-gated caller must not get as far
    # as learning which toys exist by reading close codes. The app-level dependency already
    # refuses the handshake, and this is the backstop that survives it being refactored away —
    # a websocket cannot carry a 401 body, so here the rejection is a close code, sent before
    # accept() so nothing is ever established. ws_user() is also how the owner is read at all
    # now: the old `headers.get(..., "?")` handed an un-gated caller a real save bucket.
    user = ws_user(ws)
    if user is None:
        _audit("connect_denied", user="-", item=item_id,
               error="no platform identity on the handshake")
        await ws.close(code=4401)
        return

    item = item_by_id(item_id)
    if item is None:
        await ws.close(code=4404)
        return
    if item.web:
        # Web toys have no PTY — they're served as static files, not over this socket.
        await ws.close(code=4400)
        return

    # Deferred: the PTY layer is Linux-only, and only this route ever spawns one. Importing it
    # at module scope made the whole app un-importable — and so un-testable — off-container.
    from terminal_fun_app.pty_session import PtySession

    dims = {"cols": _dim(ws, "cols", 80), "rows": _dim(ws, "rows", 24)}
    # In standalone dev ws_user() yields "", never a placeholder username: saves._owner_key
    # turns that into the shared "anon" bucket rather than persisting a fake owner as real data.
    if _sessions[user] >= settings.max_sessions_per_user:
        await ws.accept()
        await _status(ws, "too many open sessions — close one and try again")
        await ws.close(code=4409)
        return
    await ws.accept()

    tunable = is_tunable(item_id)
    params: dict = validate_params(item_id, {}) if tunable else {}

    def launch() -> tuple[list[str], dict[str, str]]:
        if tunable:
            argv, env = build_launch(item_id, params)  # type: ignore[misc]
            return argv, env
        argv = list(item.argv)
        if item_id == "nethack":
            argv += saves.nethack_extra_argv(user)  # run under this owner's unique save name
        return argv, {}

    _sessions[user] += 1
    started = time.monotonic()
    clock = {"last_input": started}
    relaunch = asyncio.Event()
    state: dict[str, PtySession | None] = {"session": None}
    end_reason: str | None = None
    idle = item.idle_timeout

    async def pump_in() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                raw = msg.get("bytes")
                if raw is None and msg.get("text") is not None:
                    raw = msg["text"].encode()
                if not raw:
                    continue
                tag, body = raw[0], raw[1:]
                if tag == _IN:
                    clock["last_input"] = time.monotonic()
                    sess = state["session"]
                    if item.allow_input and sess is not None:
                        sess.write(bytes(body))
                elif tag == _RESIZE:
                    with contextlib.suppress(Exception):
                        size = json.loads(body)
                        c, r = int(size["cols"]), int(size["rows"])
                        dims["cols"], dims["rows"] = c, r
                        sess = state["session"]
                        if sess is not None:
                            sess.resize(c, r)
                elif tag == _APPLY and tunable:
                    with contextlib.suppress(Exception):
                        incoming = json.loads(body)
                        params.clear()
                        params.update(validate_params(item_id, incoming))
                        relaunch.set()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001
            return

    async def watchdog() -> None:
        nonlocal end_reason
        hard = settings.max_secs
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if hard and now - started >= hard:
                end_reason = "time limit reached"
                return
            if idle and now - clock["last_input"] >= idle:
                end_reason = "idle timeout"
                return

    # Save/resume (NetHack + Crawl): a fresh sandbox HOME wiped on exit loses game state, so we
    # own the HOME here, seed it with this owner's stored save before launch, and capture it back
    # after the session ends (below, in finally). Non-saveable games keep the default ephemeral HOME.
    saveable = saves.is_saveable(item_id)
    save_home: str | None = None
    if saveable:
        save_home = tempfile.mkdtemp(prefix="ftsave-")
        with contextlib.suppress(Exception):
            saves.restore(user, item_id, save_home)

    in_task = asyncio.create_task(pump_in())
    wd_task = asyncio.create_task(watchdog())
    _audit("start", user=user, item=item_id, tunable=tunable)

    async def pump_out(sess: PtySession) -> None:
        while True:
            data = await sess.read()
            if not data:
                break
            try:
                await ws.send_bytes(bytes([_IN]) + data)
            except Exception:  # noqa: BLE001
                break

    try:
        first = True
        while True:
            argv, env = launch()
            session = PtySession(argv, settings.term_type, dims["cols"], dims["rows"],
                                 env_extra=env, home=save_home)
            try:
                await session.start()
            except Exception as exc:  # noqa: BLE001
                _audit("start_failed", user=user, item=item_id, error=str(exc))
                await _status(ws, f"failed to start: {exc}")
                break
            state["session"] = session
            if not first:
                await _status(ws, "↻ settings applied")
            first = False
            out_task = asyncio.create_task(pump_out(session))
            rl_task = asyncio.create_task(relaunch.wait())
            done, _pending = await asyncio.wait(
                {out_task, rl_task, in_task, wd_task}, return_when=asyncio.FIRST_COMPLETED
            )
            did_relaunch = rl_task in done and relaunch.is_set()
            for t in (out_task, rl_task):
                if not t.done():
                    t.cancel()
            await session.close()
            state["session"] = None

            if did_relaunch and in_task not in done and wd_task not in done:
                relaunch.clear()
                continue
            break
    finally:
        for t in (in_task, wd_task):
            t.cancel()
        if state["session"] is not None:
            await state["session"].close()
        # Persist the save, then wipe our owner-owned HOME (PtySession left it for us).
        if saveable and save_home:
            with contextlib.suppress(Exception):
                saves.capture(user, item_id, save_home)
            shutil.rmtree(save_home, ignore_errors=True)
        _sessions[user] -= 1
        if _sessions[user] <= 0:
            del _sessions[user]
        _audit("end", user=user, item=item_id, duration_s=round(time.monotonic() - started, 1), reason=end_reason)
        await _status(ws, f"session ended{(' — ' + end_reason) if end_reason else ''}")
        with contextlib.suppress(Exception):
            await ws.close()
