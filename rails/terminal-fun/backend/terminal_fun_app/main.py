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
x-platform-user, so this backend is never directly reachable by a browser. It IS reachable by
the shell a game hands a player, so x-platform-* headers count only when they arrive from the
gateway's own address (GatewayOnlyIdentity, below).

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
import functools
import ipaddress
import json
import logging
import os
import shutil
import socket
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from terminal_fun_app import broker, modelstate, saves
from terminal_fun_app.catalog import CATEGORIES, ITEMS, item_by_id, public_catalog
from terminal_fun_app.config import settings
from terminal_fun_app.dims import clamp_dim
from terminal_fun_app.identity import Identity, identity, standalone, ws_user
# sandbox is NOT lazy like pty_session: it deliberately imports nothing Linux-only, so the
# session HOME is prepared by the same code path whether or not this box can spawn a PTY.
from terminal_fun_app.sandbox import make_session_home
from terminal_fun_app.tunables import build_launch, defaults, is_tunable, schema, validate_params

if TYPE_CHECKING:  # pty_session is Linux-only (pty/fcntl/termios); imported lazily below so that
    from terminal_fun_app.pty_session import PtySession  # importing this app works off-container

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("terminal-fun")

# The header chip this rail can opt into the Claude CLI: the most it can ever claim. One
# module-level literal, which the gateway's test_rails_cli_switch.py reads, so RAIL_MODEL_SLOTS'
# "cli" flag cannot drift from it (docs/RAIL_CONTRACT.md). /api/chat passes the viewer as
# `requester` on its one buffered call, on this slot's role only (broker._cli_requester).
CLI_SLOTS = ("assistant",)

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


# --- platform identity counts only from the gateway's address ----------------------------------
#
# Every rail trusts X-Platform-User / X-Platform-Admin when they are present. That holds because
# the gateway strips any client copy and sets its own, so a header that arrives was set either by
# the gateway or by something else on the network, and for every other rail "something else" is
# a sibling backend running this repo's code. Not here. This container hands players a real bash
# on purpose (NetHack's `!`, tmux and byobu panes; pty_session.py), and that shell can open
# sockets. Until 2026-10-09 it sat on the compose network beside every rail and could be anyone,
# admin included, to all of them. Measured from inside this container as gameuser:
# `curl http://recipe-book:8830/api/capabilities` answered 401, and the same request with
# `-H "X-Platform-User: admin"` answered 200.
#
# Two layers, independent on purpose. The compose files attach this container ONLY to a network
# it shares with the gateway (`terminal_fun_net`), so no other rail resolves or routes from here.
# The network cannot cover the last hop: a player's shell reaching THIS backend over loopback, or
# at this container's own address, and asserting another player's identity to read their saves
# or spend the assistant as them. That is what GatewayOnlyIdentity refuses: any x-platform-*
# header from a peer that is not one of the gateway's addresses gets a 403, or on a websocket a
# close before accept. A request carrying NO such header passes untouched, meets the same
# identity gate as before (401 unless standalone), and costs no lookup.
#
# Middleware rather than a dependency for the reason _GatedStaticFiles exists: it has to cover
# every route, mount and socket, including ones nobody has written yet. And here rather than in
# identity.py, because identity.py is generated and byte-identical across rails, and this is the
# one rail that runs code its users choose.

_PLATFORM_HEADER = b"x-platform-"
_FORWARDED_FOR = b"x-forwarded-for"


def _canonical_ip(host: object) -> str | None:
    """`host` as one canonical address string, or None when it is not an address at all.

    IPv4-mapped IPv6 (`::ffff:172.18.0.22`, what a dual-stack listener reports for an IPv4 peer)
    folds to the IPv4 form, so the gateway is the same string whichever way a socket was opened;
    IPv6 is compressed, a scope id and brackets are dropped. A host NAME is not an address and is
    never matched: the peer of a TCP socket is always an address, and a name can only have come
    from somebody's header.
    """
    if not isinstance(host, str):
        return None
    raw = host.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    raw = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Raises OSError (socket.gaierror) when it does not."""
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


@functools.lru_cache(maxsize=1)
def _own_addresses() -> frozenset[str]:
    """This container's own addresses, from its hostname (Docker maps it to the container's
    address in /etc/hosts). Empty when the hostname does not resolve."""
    try:
        return frozenset(a for a in map(_canonical_ip, _resolve(socket.gethostname())) if a)
    except OSError:
        return frozenset()


def _is_local(addr: str) -> bool:
    return ipaddress.ip_address(addr).is_loopback or addr in _own_addresses()


class GatewayPeers:
    """The gateway's addresses, as this container resolves `settings.gateway_host`.

    Cached, because the answer changes only when the gateway is recreated, and re-resolved on a
    MISS (a peer not in the cache) at most once per `gateway_resolve_interval`. A miss is the
    gateway back with a new IP after a redeploy, or somebody else: the first is recognised within
    one interval, the second costs one lookup per interval however hard it tries. A lookup
    REPLACES the set, so an address the gateway has given up stops counting at the next one. A
    lookup that fails keeps the last answer: a DNS hiccup must not cut the gateway off, and an
    address it held a moment ago is no more dangerous than it was then.

    Loopback and this container's own addresses are refused like any other stranger, because they
    are not in the set, unless the gateway's name resolves to one of them. That is a dev box with
    the gateway on localhost: it works, and it says it is degraded (mode(), /api/healthz, and a
    warning on every lookup), because there any process in here can assert an identity.

    The lookup is synchronous on purpose. Docker's DNS answers a known name in well under a
    millisecond, at most once per interval; an async lookup would let the requests that arrive
    while it runs (a shell page fires several at once) see the stale set and 403 after every
    gateway redeploy.
    """

    def __init__(self, host: Callable[[], str], interval: Callable[[], float], *,
                 resolve: Callable[[str], Iterable[str]] = _resolve,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._host = host
        self._interval = interval
        self.resolve = resolve
        self._clock = clock
        self.addresses: frozenset[str] = frozenset()
        self.local: tuple[str, ...] = ()
        self._resolved_at: float | None = None

    def allows(self, peer: object) -> bool:
        ip = _canonical_ip(peer)
        if ip is None:
            return False
        if ip in self.addresses:
            return True
        now = self._clock()
        if self._resolved_at is not None and now - self._resolved_at < self._interval():
            return False
        self.refresh(now)
        return ip in self.addresses

    def refresh(self, now: float) -> None:
        host = self._host()
        self._resolved_at = now
        try:
            found = frozenset(a for a in map(_canonical_ip, self.resolve(host)) if a)
        except OSError as exc:
            _log.warning(f"terminal-fun: cannot resolve the gateway host {host!r} ({exc}); "
                         f"identity headers are accepted only from the last answer, "
                         f"{sorted(self.addresses) or 'nobody'}")
            return
        self.addresses = found
        self.local = tuple(sorted(a for a in found if _is_local(a)))
        if self.local:
            _log.warning(f"terminal-fun: DEGRADED identity check: the gateway host {host!r} "
                         f"resolves to {', '.join(self.local)}, a loopback or own address of this "
                         f"container, so any process in here (a player's shell included) can "
                         f"assert a platform identity. Right for a dev box with the gateway on "
                         f"localhost; never in a deployment.")

    def mode(self) -> str:
        """What /api/healthz reports: "on", "degraded", "unresolved" or "off (PLATFORM_STANDALONE)".

        It looks the gateway up first when nothing has yet, so the answer never depends on
        whether an identity-bearing request happened to arrive before it (/api/healthz needs an
        identity today, so one always has; this does not rely on that). "unresolved" means no
        lookup has ever answered, so nobody is trusted."""
        if standalone():
            return "off (PLATFORM_STANDALONE)"
        if self._resolved_at is None:
            self.refresh(self._clock())
        if not self.addresses:
            return "unresolved"
        return "degraded" if self.local else "on"


def _forwarded_for(headers: Iterable[tuple[bytes, bytes]]) -> set[str]:
    """Every address this request's X-Forwarded-For headers name, canonical.

    For one check. uvicorn rewrites scope["client"] from X-Forwarded-For when the socket's peer is
    on its trusted list, and that list is 127.0.0.1 by default: a player on loopback sending
    `X-Forwarded-For: <the gateway>` would BE the gateway by the time this middleware looked. The
    image runs uvicorn with --no-proxy-headers (deploy/Dockerfile), which is the fix. This keeps
    the guard honest if that flag is ever lost: a rewritten client always comes out of this
    header, and the gateway's own address never legitimately appears in it (the gateway forwards
    what caddy wrote there, which is the BROWSER's side of the connection).
    """
    out: set[str] = set()
    for name, value in headers:
        if name.lower() != _FORWARDED_FOR:
            continue
        for item in value.decode("latin-1").split(","):
            item = item.strip()
            if item.startswith("[") and "]" in item:
                item = item[1:item.index("]")]
            elif item.count(":") == 1:
                item = item.split(":", 1)[0]
            ip = _canonical_ip(item)
            if ip:
                out.add(ip)
    return out


class _RefusalLog:
    """At most one `identity_refused` line a second, carrying a count of the ones it held back. A
    player can forge requests in a loop, and this container's log is a file on the host's disk."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None
        self._held = 0

    def __call__(self, **fields: Any) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < 1.0:
            self._held += 1
            return
        held, self._held, self._last = self._held, 0, now
        _audit("identity_refused", suppressed=held, **fields)


_refused = _RefusalLog()


class GatewayOnlyIdentity:
    """ASGI middleware: an x-platform-* header is accepted from the gateway's address only."""

    def __init__(self, app: ASGIApp, peers: GatewayPeers) -> None:
        self.app = app
        self.peers = peers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Read at call time, like identity.standalone() everywhere else: standalone dev has no
        # gateway in front, and keeps exactly the behaviour it had before this check existed.
        if scope["type"] not in ("http", "websocket") or standalone():
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or ()
        if not any(name.lower().startswith(_PLATFORM_HEADER) for name, _ in headers):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else None
        if not self.peers.allows(peer):
            reason = "not the gateway"
        elif _canonical_ip(peer) in _forwarded_for(headers):
            reason = "the peer address came from X-Forwarded-For"
        else:
            await self.app(scope, receive, send)
            return
        _refused(peer=peer or "-", kind=scope["type"], path=scope.get("path", ""), reason=reason)
        if scope["type"] == "websocket":
            # Take the handshake's connect event, then close BEFORE accept: nothing is ever
            # established, and the server answers the upgrade with a 403.
            await receive()
            await send({"type": "websocket.close", "code": 4403})
            return
        await JSONResponse({"detail": "platform identity is accepted from the gateway only"},
                           status_code=403)(scope, receive, send)


gateway_peers = GatewayPeers(lambda: settings.gateway_host,
                             lambda: settings.gateway_resolve_interval)
app.add_middleware(GatewayOnlyIdentity, peers=gateway_peers)


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
    # peer_check: whether identity headers are being held to the gateway's address. The fleet
    # smoke check (maintainer tooling) pins it to "on", so a deployment running degraded or
    # standalone says so on every sweep.
    return {"ok": True, "app": settings.app_name, "items": len(ITEMS), "ai": broker.up(),
            "peer_check": gateway_peers.mode()}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {
        "categories": [{"id": cid, "label": label} for cid, label in CATEGORIES],
        "items": public_catalog(),
    }


@app.get("/api/capabilities")
def capabilities(ident: Identity = Depends(identity)) -> dict[str, Any]:
    """Four-state status for the header chip: the in-terminal assistant model (@terminal-fun, the
    role the admin Rails panel repoints). Resolved live from the broker each call, so the chip
    tracks the current admin selection + GPU residency.

    Opted into the Claude CLI (CLI_SLOTS): /api/chat passes the viewer as `requester` on its
    one buffered call, the same `ident.user` judged here."""
    return modelstate.resolve([("assistant", "Assistant", settings.llm_model)],
                              requester=ident.user, cli_slots=CLI_SLOTS)


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
def chat(body: ChatBody, ident: Identity = Depends(identity)) -> dict[str, Any]:
    msgs = [{"role": "system", "content": _system_prompt(body.item, body.params)}]
    for m in body.history[-6:]:
        role = m.get("role")
        content = str(m.get("content", ""))[:2000]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": (body.message or "")[:1000]})

    try:
        # The viewer's own question over the public catalogue: theirs to send. Standalone dev
        # has no user (None), which sends no requester.
        data = broker.chat_json(msgs, requester=ident.user)
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
    return clamp_dim(ws.query_params.get(name, default), default)


async def _status(ws: WebSocket, text: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_text(_STATUS + text)


# Written into the player's terminal itself: the page shows a status line only while the socket
# is closed, and a curses game clears the screen as it starts, so the session waits for a key.
_SAVE_KEPT = (
    "\r\n  Your saved game could not be loaded this time. It is kept safe, exactly as it was.\r\n"
    "  This session is a new game, and it will not be saved over the old one.\r\n"
    "  Leave and open this game again later to resume your save.\r\n\r\n"
    "  Press any key to start a new game.\r\n"
)


async def _say(ws: WebSocket, text: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_bytes(bytes([_IN]) + text.encode())


async def _first_key(keyed: asyncio.Event, *ends: asyncio.Task) -> bool:
    """Wait for a key, or for any of `ends` (the socket gone, the watchdog's limit). True on a key."""
    key = asyncio.create_task(keyed.wait())
    try:
        await asyncio.wait({key, *ends}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        key.cancel()
    return keyed.is_set()


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
    #
    # accept() FIRST, then the cap check, then (further down) the reservation, with no await
    # anywhere between the check and the `+= 1`. The check used to come before accept(), which
    # awaits, so N handshakes from one user arriving together all read the same count, all
    # passed, and the per-user cap admitted N sessions.
    await ws.accept()
    if _sessions[user] >= settings.max_sessions_per_user:
        await _status(ws, "too many open sessions — close one and try again")
        await ws.close(code=4409)
        return

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

    started = time.monotonic()
    clock = {"last_input": started}
    keyed = asyncio.Event()     # any key from the player; read only before a failed restore's game
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
                    keyed.set()
                    sess = state["session"]
                    if item.allow_input and sess is not None:
                        sess.write(bytes(body))
                elif tag == _RESIZE:
                    with contextlib.suppress(Exception):
                        size = json.loads(body)
                        # Clamped like the query string. This frame used to store the raw
                        # numbers, and a 70000-column frame broke the next launch (dims.py).
                        c = clamp_dim(size["cols"], dims["cols"])
                        r = clamp_dim(size["rows"], dims["rows"])
                        dims["cols"], dims["rows"] = c, r
                        sess = state["session"]
                        if sess is not None:
                            sess.resize(c, r)
                elif tag == _APPLY and tunable:
                    with contextlib.suppress(Exception):
                        incoming = json.loads(body)
                        # Validate BEFORE touching the live params, and only an object: a valid
                        # JSON list used to clear() them and then fail in validation. Inert in
                        # practice (the next apply rebuilt them in full), but no longer reachable.
                        if not isinstance(incoming, dict):
                            continue
                        clean = validate_params(item_id, incoming)
                        params.clear()
                        params.update(clean)
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
    restored = True
    if saveable:
        # make_session_home, not mkdtemp: under the uid split the game is a different uid and
        # a 0700 directory is one it cannot even enter. Both HOME creation sites must agree —
        # PtySession's ephemeral one and this owner-managed one — or saveable games break
        # while everything else works, which is the hardest shape of this bug to see.
        save_home = make_session_home("ftsave-")
        # A False or a raise is a failed restore. Such a session is never captured (the finally
        # below): it holds a new game, and capturing that would replace the save that could not
        # be loaded. A full /tmp (its tmpfs is sized) is enough to fail one.
        restored = False
        with contextlib.suppress(Exception):
            restored = saves.restore(user, item_id, save_home)

    # The reservation. Everything since the cap check above is synchronous, so no other
    # handshake can have read the count in between; and nothing from here to the try below
    # can raise, so the finally that releases it always runs.
    _sessions[user] += 1
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
        if not restored:
            _audit("restore_failed", user=user, item=item_id)
            await _say(ws, _SAVE_KEPT)
            if not await _first_key(keyed, in_task, wd_task):
                return
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
                # start() releases what it opened before raising; close() as well, because
                # this break skips the `state["session"]` hand-off that would otherwise be
                # the only way the finally below could ever reach this session.
                await session.close()
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
        # Persist the save, then wipe our owner-owned HOME (PtySession left it for us). Never
        # after a failed restore: see `restored` above.
        if saveable and save_home:
            if restored:
                with contextlib.suppress(Exception):
                    if not saves.capture(user, item_id, save_home):
                        _audit("capture_failed", user=user, item=item_id)
            else:
                saves.abandon(user, item_id)
            shutil.rmtree(save_home, ignore_errors=True)
        _sessions[user] -= 1
        if _sessions[user] <= 0:
            del _sessions[user]
        _audit("end", user=user, item=item_id, duration_s=round(time.monotonic() - started, 1), reason=end_reason)
        await _status(ws, f"session ended{(' — ' + end_reason) if end_reason else ''}")
        with contextlib.suppress(Exception):
            await ws.close()
