"""Platform gateway — FastAPI.

Run (dev): uvicorn platform_gateway_app.main:app --app-dir apps/platform/backend --port 8700

Routes, in priority order:
  /api/platform/*            auth (login/logout/me), the per-user app list, and
                             broker/GPU status the top-bar widget uses (this process)
  /api/platform/admin/*      user + entitlement management (admins only)
  /{app}/api/{path}          reverse-proxied to that app's own backend
  /{app}/...                 that app's built federation remote (static)
  /assets/*, /{path}         the unified shell SPA (client-side routing -> index.html)

Multi-tenant: every /{app}/* request (API proxy AND the app's bundle) passes an
entitlement gate — a user only reaches apps they're entitled to. The rail itself is
served per-user by /api/platform/apps, so it lists only what the user may see.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictStr, StringConstraints, model_validator
from websockets.asyncio.client import connect as ws_connect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from platform_core import BrokerClient, BrokerError
from platform_core.db import Database

from platform_gateway_app.auth import (
    count_admins,
    count_superadmins,
    create_session,
    delete_session,
    entitled_app_ids,
    hash_password,
    peer_usernames,
    room_ids,
    user_for_token,
    verify_password,
)
from platform_gateway_app import theme as theme_mod
from platform_gateway_app import model_catalog
from platform_gateway_app.catalog import APP_CATALOG, APP_IDS
from platform_gateway_app.status_view import status_view
from platform_gateway_app.config import (
    DEFAULT_MAX_REQUEST_BYTES,
    MAX_REQUEST_BYTES,
    PEER_TILES,
    ROOM_TILES,
    GatewaySettings,
)
from platform_gateway_app.rails_models import (
    CLI_SLOT_ROLES,
    CLI_WIRED_ROLES,
    IMAGE_SLOT_ROLES,
    RAIL_SLOT_ROLES,
    STREAM_SLOT_ROLES,
    VISION_SLOT_ROLES,
    build_rails_view,
    image_backend,
    media_options,
    model_options,
)
from platform_gateway_app.models import (  # noqa: F401 (SessionRow/Setting/UserTheme used via metadata)
    Base, Entitlement, GeneratedModelDesc, RailState, Schedule, SessionRow, Setting, User,
    Workspace, WorkspaceMember,
    UserTheme,
)
from platform_gateway_app import scheduler

_HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}

# The rail relay's two upstream clients (_relay_clients). WHY TWO. The relay streams both ways, so
# a request holds its upstream connection for as long as its slower end takes: an upload until its
# last byte arrives, a download until its client has read it, an event stream for its whole life.
# One client on httpx's default pool (100 connections, and at the client's 600 s timeout a 600 s
# wait for one) let a hundred of those stall every relay, plain page loads included.
#
# Seconds an idle upstream connection is kept for reuse, on both clients. Well inside the 5 s a
# rail keeps one open (uvicorn's default timeout_keep_alive, which every rail backend runs with).
# It was 5 s too, httpx's default, so a request reusing a connection idle about 5 s could go out as
# the rail closed it: "Server disconnected without sending a response", a 502 for a request the
# rail never read. The rail's clock starts when it has written its answer and ours when the relay
# has finished reading it, so by ours it fires early. A shorter expiry rather than a retry: a
# relayed POST may be a generation or a write, and a connection the rail closed unread cannot be
# told from one that died mid-answer, so a resend could run it twice. The broker's client to a
# delegated box made the same choice (services/broker/app/upstream.py _KEEPALIVE_EXPIRY).
_KEEPALIVE_EXPIRY = 2.0
# Everything that is not an upload, event streams included, goes out on a client with NO cap on
# connections. The relay holds one upstream connection per request in flight, as nginx and Caddy
# do by default, so a cap only adds a queue: the inbound side already bounds how many there are.
# Idle connections kept for reuse: httpx's default count (20), for _KEEPALIVE_EXPIRY.
_RELAY_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=20,
                             keepalive_expiry=_KEEPALIVE_EXPIRY)
# An upload (a body over _UPLOAD_FROM, or of unknown length) goes out on its own client, capped at
# 32 at once for the whole box: a backstop against a flood rather than a throttle on ordinary use,
# so a flood of slow uploads queues behind itself and nothing else. One that cannot get a
# connection within _UPLOAD_POOL_WAIT is a 503 to retry; none of its body has been read by then,
# because httpx takes the connection before it sends a byte.
_UPLOAD_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=4,
                              keepalive_expiry=_KEEPALIVE_EXPIRY)
_UPLOAD_POOL_WAIT = 30.0
# A body of known length up to this size is an ordinary write (a JSON save), not an upload: it is
# over in one round trip, and must not wait behind a queue of uploads.
_UPLOAD_FROM = 1024 * 1024


def _relay_clients() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """(the relay's client, the uploads' client), as the lifespan builds and closes them."""
    return (httpx.AsyncClient(timeout=600.0, limits=_RELAY_LIMITS),
            httpx.AsyncClient(timeout=httpx.Timeout(600.0, pool=_UPLOAD_POOL_WAIT),
                              limits=_UPLOAD_LIMITS))

# Per-IP failed-login timestamps for a simple brute-force throttle (single instance).
_login_fails: dict[str, list[float]] = {}
# Sweep lapsed buckets once the map gets this big (see the sweep in login()).
_LOGIN_FAILS_SWEEP_AT = 1024
# A real Argon2id hash of a random password nobody holds, made once at import with the same
# hasher as every account's. login() verifies an unknown name against it, so a refusal costs one
# Argon2 whether or not the name exists.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class ModelBody(BaseModel):
    model: str


class LoginBody(BaseModel):
    username: str
    password: str


def _seed_admin(db_helper: Database, settings: GatewaySettings) -> None:
    """First run only: create the admin so someone can log in. If no password is
    configured, generate a strong one and print it once — no weak default ships."""
    with db_helper.session_ctx() as db:
        if db.execute(select(User).limit(1)).first() is not None:
            return
        pw = settings.admin_password or secrets.token_urlsafe(12)
        db.add(User(username=settings.admin_user, password_hash=hash_password(pw), is_admin=True))
        db.commit()
        if settings.admin_password:
            print(f"[gateway] seeded admin user '{settings.admin_user}'", flush=True)
        else:
            print(f"[gateway] seeded admin '{settings.admin_user}' with GENERATED password: {pw}",
                  flush=True)
            print("[gateway] set PLATFORM_ADMIN_PASSWORD to control it; change it after first login.",
                  flush=True)


# Guards the one-time role migration so it runs exactly once (P1.1).
_DECOUPLE_FLAG = "migrate_admin_entitlements_v1"


def _ensure_schema(db_helper: Database) -> None:
    """Additive column migration: create_all() creates missing tables but not missing
    columns. Add is_superadmin to an existing users table if absent. SQLite only (the
    platform DB); a no-op on other dialects."""
    with db_helper.engine.begin() as conn:
        if conn.dialect.name != "sqlite":
            return
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")}
        if "is_superadmin" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT 0")
        # schedules.anchor (added Phase 3.5) on a schedules table created before it existed.
        sched = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone()
        if sched:
            scols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(schedules)")}
            if "anchor" not in scols:
                conn.exec_driver_sql("ALTER TABLE schedules ADD COLUMN anchor DATETIME")


# Usernames are unique CASE-INSENSITIVELY. The column's own unique index is case-sensitive, which
# let an admin create `Owner-One` beside `owner-one`; that mattered once the broker gave one
# account a personal Claude subscription by name. This index makes the rule the database's, so a
# race between two creates, or any future code path that adds a user, cannot break it. Created
# with raw DDL rather than declared on the model: create_all() never adds an index to a table that
# already exists, and SQLAlchemy cannot reflect an expression index to check for it either.
_USERNAME_FOLDED_INDEX = "ux_users_username_folded"


def _refuse_case_colliding_usernames(db_helper: Database) -> None:
    """Startup check: refuse to start while two accounts differ only by the case of their name.

    Login now matches case-insensitively, so such a pair would be one login box for two
    accounts, and which one answered would be up to the database. The ids are reported and the
    names never are: this goes to a service log, and the names are people's.
    """
    folded = func.lower(User.username)
    with db_helper.engine.begin() as conn:
        keys = conn.execute(select(folded).group_by(folded).having(func.count() > 1)).scalars().all()
        groups = [conn.execute(select(User.id).where(folded == k).order_by(User.id)).scalars().all()
                  for k in keys]
        if not groups:
            conn.exec_driver_sql(f"CREATE UNIQUE INDEX IF NOT EXISTS {_USERNAME_FOLDED_INDEX} "
                                 "ON users (lower(username))")
            return
    listed = "; ".join("ids " + ", ".join(str(i) for i in g) for g in groups)
    raise RuntimeError(
        f"refusing to start: {len(groups)} group(s) of user accounts have usernames that differ "
        f"only by letter case ({listed}). Usernames are now unique ignoring case, at login and "
        "for the Claude CLI owner check, so keep one account per group: rename or delete the "
        "others by id in the users table of the platform database, then start again.")


def _migrate_roles(db_helper: Database, settings: GatewaySettings) -> None:
    """One-time role migration (P1.1). Admin no longer implies every app — only a
    SUPER-ADMIN is all-access. (1) elevate the seed owner to super-admin; (2) preserve
    every existing admin's current access by granting explicit entitlements to all
    current apps. Guarded by a Setting flag so it runs once: later revocations stick,
    and newly added apps are NOT auto-granted."""
    with db_helper.session_ctx() as db:
        if db.get(Setting, _DECOUPLE_FLAG) is not None:
            return
        owner = db.execute(
            select(User).where(User.username == settings.admin_user)
        ).scalar_one_or_none()
        if owner is not None:
            owner.is_admin = True
            owner.is_superadmin = True
        admins = db.execute(select(User).where(User.is_admin.is_(True))).scalars().unique().all()
        for a in admins:
            have = {e.app_id for e in a.entitlements}
            for aid in sorted(APP_IDS - have):
                a.entitlements.append(Entitlement(app_id=aid))
        db.add(Setting(key=_DECOUPLE_FLAG, value="done"))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = GatewaySettings()
    app.state.settings = settings
    app.state.backends = settings.app_backends()
    app.state.broker = BrokerClient(settings.broker_url)
    app.state.http, app.state.http_body = _relay_clients()
    db = Database(settings.db_url)
    db.create_all(Base.metadata)
    _ensure_schema(db)
    _refuse_case_colliding_usernames(db)
    app.state.db = db
    _seed_admin(db, settings)
    _migrate_roles(db, settings)
    # Central scheduler: seed each installed rail's tasks, then run the fire loop.
    with db.session_ctx() as s:
        scheduler.seed(s, set(settings.enabled_apps))
    app.state.sched_task = asyncio.create_task(scheduler.loop(app, settings.scheduler_tick_seconds))
    try:
        yield
    finally:
        app.state.sched_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.sched_task
        await app.state.broker.aclose()
        await app.state.http.aclose()
        await app.state.http_body.aclose()


app = FastAPI(title="Platform Gateway", version="0.1.0", lifespan=lifespan)


# --- dependencies -----------------------------------------------------------


def get_db() -> Iterator[OrmSession]:
    db = app.state.db.session()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: OrmSession = Depends(get_db)) -> User | None:
    token = request.cookies.get(app.state.settings.session_cookie)
    return user_for_token(db, token)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


# --- entitlement gate: every /{app}/* request must be authorized ------------


@app.middleware("http")
async def app_access_gate(request: Request, call_next):
    """Guards BOTH an app's API proxy and its static bundle: any path whose first
    segment is a known app id requires a logged-in user entitled to that app.

    Also guards a rail's declared ROOT-origin assets (config.ROOT_ASSETS). Those paths do not
    start with an app id -- that is the whole point of them -- so without this they would miss
    the gate entirely and be served to anyone. They are the rail's content and carry the rail's
    entitlement, exactly as if they had been requested under /<id>/."""
    parts = request.url.path.split("/")
    seg = parts[1] if len(parts) > 1 else ""  # first path segment, e.g. "edu-suite"
    root_owner = None if seg in APP_IDS else app.state.settings.root_asset_owner(request.url.path)
    owner = seg if seg in APP_IDS else root_owner
    # Only a request the gateway FORWARDS to the rail carries peers and rooms: its /api/
    # proxy and its root-origin assets (both go through _forward). The rail's static bundle is
    # served from here and never reads them, yet every one of its asset requests paid the two
    # extra queries that work them out.
    forwarded = root_owner is not None or (len(parts) > 2 and parts[2] == "api")
    if owner:
        seg = owner
        token = request.cookies.get(app.state.settings.session_cookie)
        with app.state.db.session_ctx() as db:
            user = user_for_token(db, token)
            if user is None:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            if seg not in entitled_app_ids(db, user, APP_IDS, all_access=user.is_superadmin):
                return JSONResponse({"detail": f"not authorized for '{seg}'"}, status_code=403)
            # Stash the VERIFIED identity (read while the session is open) so the proxy can
            # forward it to the app backend as trusted headers. Backends scope their own data
            # by it — e.g. job-aid restricts each candidate to its owning platform user.
            request.state.platform_user = user.username
            request.state.platform_is_admin = bool(user.is_admin)
            # Who this user shares owner-scoped data with (a teacher and her classroom aide).
            # Resolved HERE, inside the same open session that verified the cookie, so a rail
            # can trust it exactly as much as it trusts x-platform-user. [] for almost everyone.
            # Two shapes because the rails need different questions answered. edu-suite's
            # content work has no child entity, so "whose work may I see" (peers) is the only
            # sensible model there; iep-goals hangs everything off a student, where "which
            # rooms am I in" is the better one -- a child belongs to a classroom, not to
            # whoever typed first. Each list goes only to the tiles whose rail reads it
            # (config.PEER_TILES, config.ROOM_TILES); every other rail gets neither header and
            # costs neither query, and a new tile gets none until it is listed.
            if forwarded:
                if seg in PEER_TILES:
                    request.state.platform_peers = peer_usernames(db, user)
                if seg in ROOM_TILES:
                    request.state.platform_rooms = room_ids(db, user)
    return await call_next(request)


@app.middleware("http")
async def revalidate_entrypoints(request: Request, call_next):
    """Force browsers to revalidate the app entrypoints so a redeploy is picked up
    on a normal refresh. Module-federation's ``remoteEntry.js`` and the shell/remote
    ``index.html`` have STABLE names but changing contents; without this they get
    heuristically cached and users keep loading the old bundle. Content-hashed asset
    chunks keep their default (cacheable) behavior."""
    resp = await call_next(request)
    path = request.url.path
    ctype = resp.headers.get("content-type", "")
    if path.endswith("remoteEntry.js") or ctype.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# The request-state key the relay sets when a rail's response breaks off mid-body
# (_RelayedResponse), with the reason as its value.
_RELAY_CUT = "relay_cut"


class _RelayCutShort(Exception):
    """Raised in place of the final body message of a relayed response whose rail broke off."""


class _CutShortGuard:
    """Keep a relayed response that broke off mid-body from being finished.

    Once a streamed response has sent its status line, the one way left to say "this body is not
    whole" is to end the connection before the body ends: no terminating chunk, or fewer bytes
    than Content-Length. The relay re-raises the rail's error to do that, but both middlewares
    above are BaseHTTPMiddleware, which catches the app's exception, ENDS THE BODY CLEANLY and
    only then re-raises it: a chunked download its rail broke off reached the browser as a
    finished, shorter file. This sits outside both (added after them, so outermost) and raises
    instead of passing on the final body message of any response the relay marked as cut; the
    server then drops the connection with the body unfinished, and the download fails visibly.

    Pure ASGI on purpose: another @app.middleware would be one more BaseHTTPMiddleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # The dict request.state writes to, shared by every layer below (Starlette keeps a
        # request's state in scope["state"], and the middlewares pass the scope on as it is).
        state = scope.setdefault("state", {})

        async def guarded(message: Message) -> None:
            if (message["type"] == "http.response.body" and not message.get("more_body", False)
                    and state.get(_RELAY_CUT)):
                raise _RelayCutShort(state[_RELAY_CUT])
            await send(message)

        await self.app(scope, receive, guarded)


app.add_middleware(_CutShortGuard)


# --- the platform's own request bodies ---------------------------------------

# The most body bytes a route of the platform's own (/api/platform/*) reads in one request. Every
# body those routes take is a small JSON document (a login, a theme, a user and their apps, a
# schedule, a token label, at most 8,000 characters to read aloud), so 1 MiB is generous for all
# of them. The one route that needs more says so in _PLATFORM_BODY_ROOM, beside its own bound.
_PLATFORM_BODY_MAX = 1024 * 1024


def _signed_in(scope: Scope) -> bool:
    """Does this request carry a live session? The same check the routes' require_user makes."""
    token = Request(scope).cookies.get(app.state.settings.session_cookie)
    if not token:
        return False
    with app.state.db.session_ctx() as db:
        return user_for_token(db, token) is not None


def _platform_body_ceiling(scope: Scope) -> int:
    """The body ceiling for one request to the platform's own routes. A route's larger room is a
    signed-in caller's only: FastAPI reads the body before it runs require_user, so a room open
    to anyone would let anyone make the gateway hold that much."""
    room = _PLATFORM_BODY_ROOM.get(scope["path"])
    if room is None or not _signed_in(scope):
        return _PLATFORM_BODY_MAX
    return room


def _platform_too_large(ceiling: int) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": (
        f"this request is larger than the {ceiling / (1024 * 1024):.0f} MB the platform "
        f"accepts here")})


class _PlatformBodyCeiling:
    """Bound the body of every request to the platform's own routes before any route reads it.

    FastAPI reads a pydantic route's body whole before it validates it or runs one dependency,
    and nothing bounded it: uvicorn sets no limit and neither Caddyfile sets `request_body`. So
    POST /api/platform/login, which needs no session, held a body of any size, and so did every
    route behind require_user, which refuses the caller only after the body is in. The rail relay
    (/<rail>/api/*) is not this path's business: it streams, with a ceiling per rail.

    A Content-Length over the ceiling is a 413 before a byte is read. Any other body is counted as
    it arrives, and one chunk past the ceiling the route is told the client has gone, so it stops
    reading; whatever it answers to that is dropped and the client gets the 413 instead. Told,
    not raised at: an exception raised out of receive() reaches FastAPI wrapped in a task group's
    ExceptionGroup (the BaseHTTPMiddleware layers read through one), and FastAPI turns anything
    but a bare HTTPException met while reading a body into a 400.

    Pure ASGI, for the reason _CutShortGuard is."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/platform/"):
            await self.app(scope, receive, send)
            return
        ceiling = _platform_body_ceiling(scope)
        length = _declared_length(Headers(scope=scope))
        if length is not None and length > ceiling:
            await _platform_too_large(ceiling)(scope, receive, send)
            return
        taken = 0
        over = False
        answered = False

        async def counted() -> Message:
            nonlocal taken, over
            if over:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                taken += len(message.get("body", b""))
                if taken > ceiling:
                    over = True
                    return {"type": "http.disconnect"}
            return message

        async def gated(message: Message) -> None:
            nonlocal answered
            if over:
                return          # the route's answer to a body it never got
            answered = True
            await send(message)

        try:
            await self.app(scope, counted, gated)
        except Exception:
            if not over:
                raise
        if over and not answered:
            await _platform_too_large(ceiling)(scope, receive, send)


app.add_middleware(_PlatformBodyCeiling)


# --- auth + per-user app list -----------------------------------------------


def _me(user: User, db: OrmSession) -> dict[str, Any]:
    return {"username": user.username, "is_admin": user.is_admin,
            "theme": theme_mod.effective(db, user)}


def _cookie_secure_for(request: Request) -> bool:
    """The Secure flag for THIS request, rather than one global answer.

    `cookie_secure=true` is right for the public HTTPS front door. But Caddy also serves
    plain `http://localhost`, and a Secure cookie is never sent back over plain HTTP — so
    every non-browser client silently loses its session. That is not theoretical: the
    installer's own e2e smoke stage detects it and SKIPS its authenticated checks, which is
    the worst outcome, a green run that verified less than it looks like it did.

    Browsers are unaffected either way, because they treat localhost as a secure context and
    will send a Secure cookie to it. So this only ever helps scripted clients.

    The relaxation is deliberately narrow: plain HTTP *and* a loopback Host. Anything reached
    over a real hostname keeps Secure, so a request that merely lost its TLS somewhere cannot
    talk the gateway out of the flag.
    """
    s = app.state.settings
    if not s.cookie_secure:
        return False
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    if proto != "http":
        return True
    raw = (request.headers.get("host") or "").strip().lower()
    # IPv6 arrives bracketed as `[::1]:1111`, so the port cannot be split off on the first
    # colon — doing that yields "[" and the loopback check silently never matches.
    host = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else raw.split(":")[0]
    return host not in ("localhost", "127.0.0.1", "::1")


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    s = app.state.settings
    response.set_cookie(
        s.session_cookie, token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True, secure=_cookie_secure_for(request), samesite=s.cookie_samesite,
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.post("/api/platform/login")
def login(body: LoginBody, request: Request, response: Response, db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    s = app.state.settings
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _login_fails.get(ip, []) if now - t < s.login_window_seconds]
    if recent:
        _login_fails[ip] = recent
    else:
        _login_fails.pop(ip, None)
    # Sweep everyone else's lapsed buckets too. The line above only ever cleans the address
    # currently logging in, so a bucket created by one failed attempt from an address that
    # never comes back used to live for the life of the process.
    if len(_login_fails) > _LOGIN_FAILS_SWEEP_AT:
        cutoff = now - s.login_window_seconds
        for stale in [k for k, v in _login_fails.items() if not v or v[-1] < cutoff]:
            _login_fails.pop(stale, None)
    if len(recent) >= s.login_max_fails:
        raise HTTPException(status_code=429, detail="too many attempts; wait a few minutes")

    # Case-insensitive, and lower() on BOTH sides so it is the database's one notion of case:
    # Python's lower() and SQLite's (ASCII-only) disagree on non-ASCII names. At most one row
    # can match, because startup refuses to run while two names collide this way. The session
    # is for the STORED account, so every rail and the broker see its one spelling.
    user = db.execute(select(User).where(
        func.lower(User.username) == func.lower(body.username))).scalar_one_or_none()
    # Verified even when no account has the name, against _DUMMY_HASH: an unknown name skipped
    # Argon2 and was refused about 45 ms sooner than a known one, so timing the login box told
    # anyone which usernames exist.
    ok = verify_password(body.password, user.password_hash if user is not None else _DUMMY_HASH)
    if user is None or not ok:
        _login_fails.setdefault(ip, []).append(now)
        raise HTTPException(status_code=401, detail="invalid username or password")

    _login_fails.pop(ip, None)
    sess = create_session(db, user, s.session_ttl_hours)
    _set_session_cookie(response, sess.token, request)
    return _me(user, db)


@app.post("/api/platform/logout")
def logout(request: Request, response: Response, db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    delete_session(db, request.cookies.get(app.state.settings.session_cookie))
    response.delete_cookie(app.state.settings.session_cookie, path="/")
    return {"ok": True}


@app.get("/api/platform/me")
def me(user: User = Depends(require_user), db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    return _me(user, db)


def disabled_rail_ids(db: OrmSession) -> set[str]:
    """Rails an admin has switched off in the Rail Manager. Absence of a row means enabled."""
    return {r.rail_id for r in db.execute(select(RailState)).scalars().all()}


@app.get("/api/platform/apps")
def apps_for_user(user: User = Depends(require_user), db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    allowed = entitled_app_ids(db, user, APP_IDS, all_access=user.is_superadmin)
    # Only surface rails actually installed here (enabled_apps), so a lean/subset install doesn't
    # show excluded rails that would 404. Roadmap 'soon' entries still show. Full deploy = no-op.
    enabled = set(app.state.settings.enabled_apps)
    # ...minus anything an admin switched off in the Rail Manager. A DB read, so it applies on
    # the next request rather than at the next restart — which is the whole reason the flag lives
    # there instead of in PLATFORM_ENABLED_APPS.
    off = disabled_rail_ids(db)
    apps = [a for a in APP_CATALOG
            if a["id"] in allowed and a["id"] not in off
            and (a["id"] in enabled or a.get("status") == "soon")]
    return {"apps": apps, "user": _me(user, db)}


# --- theme: platform default (admin) + per-user override --------------------


class ThemeBody(BaseModel):
    palette: str | None = None
    mode: str | None = None
    clear: bool = False


@app.put("/api/platform/theme")
def set_my_theme(body: ThemeBody, user: User = Depends(require_user),
                 db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    try:
        theme_mod.set_user_override(db, user, body.palette, body.mode, clear=body.clear)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return theme_mod.effective(db, user)


class AdminThemeBody(BaseModel):
    palette: str
    mode: str


@app.put("/api/platform/admin/theme")
def set_default_theme(body: AdminThemeBody, admin: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    try:
        theme_mod.set_platform_default(db, body.palette, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return theme_mod.effective(db, admin)


# --- shared platform (broker/GPU) status — now gated behind login -----------

_EMPTY_STATUS = {
    "ollama_reachable": False,
    "loaded": [],
    "heavy_loaded": [],
    "gpu": None,
    "queue": {"active": 0, "waiting": 0},
}


# HEAD too, for a monitor that probes with it: FastAPI adds no HEAD to a GET route.
@app.api_route("/api/platform/healthz", methods=["GET", "HEAD"])
async def healthz() -> dict[str, Any]:
    return {"ok": True, "app": app.state.settings.app_name, "apps": list(app.state.backends)}


def _status_for(status: dict[str, Any], user: User) -> dict[str, Any]:
    """The broker's status as THIS user may see it: all of it for an admin, and for anyone
    else only what the top-bar widget draws. It cannot simply be spread: this route serves
    every logged-in user, and the broker wrote /v1/status for token holders, so it names
    interpreter paths, owner accounts and the CLI's machine config. status_view.py has the
    whole-response rule and cli_view.py the ``cli`` block's. Same admin rule as require_admin:
    ``is_admin``, which a super-admin always carries too."""
    return status_view(status, user.username, bool(user.is_admin))


@app.get("/api/platform/status")
async def platform_status(user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        status = await app.state.broker.status()
    except BrokerError as exc:
        # `detail` is the broker client's own error text, an operator's clue rather than
        # anything the widget draws, so it goes through the same view and reaches admins only.
        return {"broker_reachable": False,
                **_status_for({"detail": str(exc), **_EMPTY_STATUS}, user)}
    return {"broker_reachable": True, **_status_for(status, user)}


@app.get("/api/platform/models")
async def platform_models(user: User = Depends(require_user)) -> Any:
    try:
        return await app.state.broker.models()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- voice: platform-wide read-aloud + dictation ---------------------------
# Deliberately NOT admin-only and NOT per-rail. These are the two capabilities every rail
# gets for free, so they live here rather than being re-plumbed into each rail's backend:
# one proxy, one place the broker token is held, no rail code involved. Both broker
# endpoints are ungated and non-evicting, so calling them mid-conversation is safe.


# The broker's own bounds (services/broker/app/schemas.py: TtsLightRequest, TranscribeRequest),
# repeated at this hop so an oversized or malformed body is a 422 naming the field HERE, on a
# route any logged-in user reaches, instead of being read whole, relayed, and answered with a
# relayed 502. tests/test_speech_bounds.py holds the two copies equal.
_TTS_LIGHT_MAX_CHARS = 8000
_TRANSCRIBE_MAX_B64 = 15_000_000
_SUFFIX_PATTERN = r"^\.[A-Za-z0-9]{1,7}$"
# The platform's routes read at most _PLATFORM_BODY_MAX of body (_PlatformBodyCeiling). Dictation
# needs more: the audio bound, plus the ordinary allowance for the rest of its JSON. A signed-in
# caller's room only.
_PLATFORM_BODY_ROOM = {"/api/platform/transcribe": _TRANSCRIBE_MAX_B64 + _PLATFORM_BODY_MAX}


class TtsLightBody(BaseModel):
    text: str = Field(max_length=_TTS_LIGHT_MAX_CHARS)
    voice: str | None = None
    lang_code: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class TranscribeBody(BaseModel):
    audio_b64: str = Field(max_length=_TRANSCRIBE_MAX_B64)
    # Reaches a filename in the broker's LocalSystem worker; the broker's pattern is the
    # authoritative check, this one fails fast with the field named.
    suffix: str | None = Field(default=None, pattern=_SUFFIX_PATTERN)
    language: str | None = None


class _ClientLeft(Exception):
    """The client went away while a route waited on the broker for it (_unless_client_leaves)."""


async def _client_left(request: Request) -> bool:
    """True once the client has gone: after a route has read its body, the next message the
    connection delivers is the disconnect. False when asking fails, which says nothing about the
    client, so the caller's call is then left to end on its own.

    Awaited, not polled. `request.is_disconnected()` asks inside a scope it has already
    cancelled, and through this app's two BaseHTTPMiddleware layers every receive opens a task
    group, which that scope cancels before the message comes back: it never says True on a
    route of this gateway (measured 2026-10-15 through the real app, a client gone for 1.5 s)."""
    try:
        while (await request.receive())["type"] != "http.disconnect":
            pass
    except Exception:  # noqa: BLE001 -- asking must never be what fails a call
        return False
    return True


async def _unless_client_leaves(request: Request, call: Awaitable[Any]) -> Any:
    """`call` (a coroutine: a broker request) in a task of its own, cancelled once the client has
    gone, which raises _ClientLeft. A plain route runs to its end after its client leaves
    (Starlette cancels a handler only for a streamed response), so a read-aloud the browser had
    aborted (Stop, a new read, an unmount: web/src/read_aloud.ts) still held its broker request
    open until the audio came back for nobody. Cancelling it closes that connection, and the
    broker stops the job when its own client goes (Broker.tts_light's `client_gone`). Returns or
    raises only once the call's task has ended, so its connection is closed by then."""
    task = asyncio.ensure_future(call)
    left = asyncio.ensure_future(_client_left(request))
    try:
        await asyncio.wait({task, left}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done() and left.result():
            raise _ClientLeft()
        return await task
    finally:
        left.cancel()
        if not task.done():
            task.cancel()
        await asyncio.wait({task, left})


@app.post("/api/platform/tts_light")
async def platform_tts_light(body: TtsLightBody, request: Request,
                             user: User = Depends(require_user)) -> Any:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        return await _unless_client_leaves(request, app.state.broker.tts_light(
            text, voice=body.voice, lang_code=body.lang_code, speed=body.speed
        ))
    except _ClientLeft:
        # Nobody is listening; 499 is nginx's "client closed request", for the access log, as
        # the broker answers a /v1/chat whose caller left.
        return Response(status_code=499)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/platform/transcribe")
async def platform_transcribe(body: TranscribeBody, user: User = Depends(require_user)) -> Any:
    if not body.audio_b64:
        raise HTTPException(status_code=400, detail="audio is required")
    try:
        return await app.state.broker.transcribe(
            body.audio_b64, suffix=body.suffix, language=body.language
        )
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# GPU control (load / unload / cancel a job) is ADMIN-ONLY: any user may VIEW status + the
# job queue, but only an admin may evict a model or cancel someone's job.
# No POST /api/platform/load: it had no caller anywhere in the repo (the shell's wrapper went
# first). GET /api/platform/models stays, because the installer's smoke test probes it.


@app.post("/api/platform/unload")
async def platform_unload(body: ModelBody, admin: User = Depends(require_admin)) -> Any:
    try:
        return await app.state.broker.unload(body.model)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class CancelBody(BaseModel):
    seq: int


@app.post("/api/platform/cancel")
async def platform_cancel(body: CancelBody, admin: User = Depends(require_admin)) -> Any:
    try:
        return await app.state.broker.cancel(body.seq)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- admin: user + entitlement management (admins only) ---------------------


# The users.username column width.
_USERNAME_MAX = 64


class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    is_superadmin: bool = False
    apps: list[str] = []


class UserUpdate(BaseModel):
    password: str | None = None
    is_admin: bool | None = None
    is_superadmin: bool | None = None
    apps: list[str] | None = None


def _role(u: User) -> str:
    return "superadmin" if u.is_superadmin else "admin" if u.is_admin else "user"


def _grantable(db: OrmSession, actor: User) -> set[str]:
    """The apps an actor may hand out: everything for a super-admin, else exactly the
    apps they hold themselves (you can't grant what you don't have)."""
    return entitled_app_ids(db, actor, APP_IDS, all_access=actor.is_superadmin)


def _out_of_reach(db: OrmSession, actor: User, target: User,
                  grantable: set[str] | None = None) -> list[str]:
    """The apps ``target`` holds that ``actor`` could not grant, sorted. None for a super-admin,
    whose ``_grantable`` is the whole catalog; a row for a rail no longer in it grants nothing
    (entitled_app_ids drops it), so it does not count. ``grantable`` is the actor's
    ``_grantable`` when the caller has it already: a check over many users works it out once
    (``_manageable``, ``_refuse_room_out_of_reach``), where it cost a query a user.

    A password reset hands the actor the target's login, and with it every app the target holds;
    a demotion or a deletion takes the target's standing away. So a plain admin may do those
    three only to a user whose apps all lie within their own ``_grantable``, the rule that already
    stops them granting an app they do not hold. Without it admin A (one rail) reset the password of
    admin B (two rails A did not hold), logged in as B, and granted A both; against a plain user
    the reset alone reached their apps and their records. The Users tab reads the same two lists
    (``apps``, ``grantable``) to grey these controls out (frontend src/user_rule.ts). Workspaces
    apply the same rule (``_manageable``, ``_refuse_room_out_of_reach``; src/workspace_rule.ts)."""
    if grantable is None:
        grantable = _grantable(db, actor)
    return sorted(entitled_app_ids(db, target, APP_IDS) - grantable)


def _refuse_out_of_reach(db: OrmSession, actor: User, target: User, action: str) -> None:
    beyond = _out_of_reach(db, actor, target)
    if beyond:
        raise HTTPException(status_code=403, detail=(
            f"you cannot {action} '{target.username}': they hold apps you cannot grant "
            f"({', '.join(beyond)}), so only a super-admin can"))


def _user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin,
            "is_superadmin": u.is_superadmin, "role": _role(u),
            "apps": sorted(e.app_id for e in u.entitlements)}


def _set_entitlements(u: User, app_ids: list[str]) -> None:
    # Diff against the current rows rather than clear-and-re-add: re-adding an
    # unchanged app as a NEW Entitlement makes SQLAlchemy emit its INSERT before
    # deleting the old row, tripping the (user_id, app_id) UNIQUE constraint (a
    # 500 on every change to a user who already has an app). Only remove what's
    # no longer wanted and insert what's genuinely new.
    want = {aid for aid in app_ids if aid in APP_IDS}
    for e in list(u.entitlements):
        if e.app_id not in want:
            u.entitlements.remove(e)  # delete-orphan cascade removes the row
    have = {e.app_id for e in u.entitlements}
    for aid in sorted(want - have):
        u.entitlements.append(Entitlement(app_id=aid))


def _apply_grant(u: User, submitted: list[str], grantable: set[str]) -> None:
    """Apply an app-grant edit under the delegation rule: the actor may only add or
    remove apps within `grantable`; any app the target already holds that the actor
    can't grant is frozen (preserved untouched)."""
    frozen = {e.app_id for e in u.entitlements if e.app_id not in grantable}
    _set_entitlements(u, sorted({a for a in submitted if a in grantable} | frozen))


@app.get("/api/platform/admin/users")
def admin_list_users(admin: User = Depends(require_admin), db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    users = db.execute(select(User).order_by(User.username)).scalars().unique().all()
    return {"users": [_user_out(u) for u in users], "catalog": APP_CATALOG,
            "grantable": sorted(_grantable(db, admin))}


@app.post("/api/platform/admin/users")
def admin_create_user(body: UserCreate, admin: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    uname = body.username.strip()
    if not uname or not body.password:
        raise HTTPException(status_code=400, detail="username and password are required")
    # Printable ASCII only, inner spaces allowed. The name travels to every rail in the
    # x-platform-user HEADER, and httpx encodes header values as ASCII: an accented name used
    # to log in fine and then 500 on every rail call, its own and its WebSocket's alike.
    if len(uname) > _USERNAME_MAX or not all(" " <= ch <= "~" for ch in uname):
        raise HTTPException(status_code=400, detail=(
            f"a username is 1-{_USERNAME_MAX} printable ASCII characters (letters, digits, "
            "punctuation, inner spaces): it is sent to every app in an HTTP header"))
    # Case-insensitive, like login: `Bob` beside `bob` would be two accounts behind one login
    # box, and a case variant of a Claude CLI owner was once enough to spend their subscription.
    taken = db.execute(select(User.id).where(
        func.lower(User.username) == func.lower(uname))).first()
    if taken is not None:
        raise HTTPException(status_code=409,
                            detail=f"user '{uname}' already exists (usernames ignore case)")
    if body.is_superadmin and not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin can grant super-admin")
    u = User(username=uname, password_hash=hash_password(body.password),
             is_admin=body.is_admin or body.is_superadmin, is_superadmin=body.is_superadmin)
    if not u.is_superadmin:  # a super-admin is all-access; explicit grants are moot
        _apply_grant(u, body.apps, _grantable(db, admin))
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        # The folded unique index caught what the check above raced past (two creates at once).
        db.rollback()
        raise HTTPException(status_code=409,
                            detail=f"user '{uname}' already exists (usernames ignore case)")
    return _user_out(u)


@app.patch("/api/platform/admin/users/{uid}")
def admin_update_user(uid: int, body: UserUpdate, admin: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="no such user")
    # A plain admin may not touch a super-admin at all.
    if u.is_superadmin and not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin can modify a super-admin")
    # Both refusals before anything changes: a reset or a demotion of a user who holds an app
    # this admin cannot grant (_out_of_reach).
    if body.password:
        _refuse_out_of_reach(db, admin, u, "reset the password of")
    if body.is_admin is False and u.is_admin:
        _refuse_out_of_reach(db, admin, u, "demote")
    if body.password:
        u.password_hash = hash_password(body.password)
        # Revoke every session this user holds, in the same transaction. An admin resets a
        # password precisely when they think the account is compromised, and without this the
        # attacker's stolen cookie stayed valid for the rest of its 7-day TTL -- the reset
        # looked like containment and was not.
        for row in db.execute(select(SessionRow).where(SessionRow.user_id == u.id)).scalars():
            db.delete(row)
    if body.is_superadmin is not None:
        if not admin.is_superadmin:
            raise HTTPException(status_code=403, detail="only a super-admin can change super-admin")
        if u.is_superadmin and not body.is_superadmin and count_superadmins(db) <= 1:
            raise HTTPException(status_code=400, detail="cannot remove the last super-admin")
        u.is_superadmin = body.is_superadmin
        if u.is_superadmin:
            u.is_admin = True  # super-admin always implies admin
    if body.is_admin is not None:
        if u.is_superadmin and not body.is_admin:
            raise HTTPException(status_code=400, detail="a super-admin is always an admin")
        if u.is_admin and not body.is_admin and count_admins(db) <= 1:
            raise HTTPException(status_code=400, detail="cannot remove the last admin")
        u.is_admin = body.is_admin
    if body.apps is not None and not u.is_superadmin:  # super-admin apps are implicit
        _apply_grant(u, body.apps, _grantable(db, admin))
    db.commit()
    return _user_out(u)


@app.delete("/api/platform/admin/users/{uid}")
def admin_delete_user(uid: int, admin: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="no such user")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="cannot delete your own account")
    if u.is_superadmin and not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin can delete a super-admin")
    _refuse_out_of_reach(db, admin, u, "delete")
    if u.is_superadmin and count_superadmins(db) <= 1:
        raise HTTPException(status_code=400, detail="cannot delete the last super-admin")
    if u.is_admin and count_admins(db) <= 1:
        raise HTTPException(status_code=400, detail="cannot delete the last admin")
    db.delete(u)
    db.commit()
    return {"ok": True}


# --- admin: per-rail model settings (the 'Rails' tab) -----------------------


# --- workspaces: who shares owner-scoped data with whom ---------------------
# Entitlements say which APPS you may reach; a workspace says whose DATA you see inside one.
# Kept apart on purpose -- granting an aide a new rail must not silently widen who can read
# the teacher's students.


# A workspace name as it is STORED: stripped, then 1-64 characters, the workspaces.name column
# width (String(64), the same as a username's). It had no bound at all, so the only limit was
# whatever the database happened to accept.
_WORKSPACE_NAME_MAX = 64
WorkspaceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                 max_length=_WORKSPACE_NAME_MAX)]


class WorkspaceCreate(BaseModel):
    name: WorkspaceName
    members: list[str] = []          # usernames


class WorkspaceUpdate(BaseModel):
    name: WorkspaceName | None = None
    members: list[str] | None = None


def _workspace_out(w: Workspace) -> dict[str, Any]:
    return {"id": w.id, "name": w.name,
            "members": sorted(m.user.username for m in w.members)}


def _manageable(db: OrmSession, actor: User) -> set[str]:
    """The usernames an actor may place in a workspace.

    The workspace twin of the Users routes' rules. Everything for a super-admin; for a plain
    admin, every user who is not a super-admin ("only a super-admin can modify a super-admin")
    AND is within the admin's reach (`_out_of_reach` is empty: every app the user holds is one
    the admin could grant). That is exactly who the admin could reset, demote or delete.

    This exists because the four workspace routes had NO delegation rule while the user routes
    directly above them had three, and a workspace grant is STRONGER than an app grant: it
    decides whose DATA you see inside an app you can already reach. Without the first half a
    plain admin could put the super-admin in a room and read their owner-scoped records in every
    rooms-aware rail. Without the second (until 2026-10-15) admin A (recipe-book only), refused a
    reset of teacher T (iep-goals), could still add anyone holding iep-goals to T's room, and
    iep-goals then showed them every child in it; or put two edu-suite teachers in one room so
    each read the other's work as a peer.
    """
    rows = db.execute(select(User)).scalars().unique().all()
    if actor.is_superadmin:
        return {u.username for u in rows}
    # The admin's grantable once, not again for every user (two queries a user until 2026-10-15).
    grantable = _grantable(db, actor)
    return {u.username for u in rows
            if not u.is_superadmin and not _out_of_reach(db, actor, u, grantable)}


def _refuse_superadmin_room(w: Workspace, actor: User, action: str) -> None:
    """403 unless the actor is a super-admin, when the workspace holds a super-admin NOW.

    The Users routes' rule, "only a super-admin can modify a super-admin", carried over to the
    rooms that decide whose records a super-admin shares. A plain admin who edits or deletes
    such a room is changing the super-admin's sharing, which is exactly what they may not do to
    the account itself. Being a member of the room grants nothing here: a member is still not
    a super-admin. Checked against the membership as it stands, before any change applies.

    It replaces a gentler rule that let a plain admin rename such a room or rewrite its other
    members while the super-admin's own row was "frozen" in place. The decision was to mirror
    the Users routes instead, and NOT to restrict workspaces to super-admins outright: a plain
    admin still runs every room the super-admin is not in.
    """
    if actor.is_superadmin:
        return
    if any(m.user.is_superadmin for m in w.members):
        raise HTTPException(
            status_code=403,
            detail=f"only a super-admin can {action} a workspace that contains a super-admin")


def _refuse_room_out_of_reach(db: OrmSession, w: Workspace, actor: User, action: str) -> None:
    """403 when the workspace holds a member beyond the actor's reach NOW (`_out_of_reach`).

    `_refuse_superadmin_room`'s sibling, the way `_refuse_out_of_reach` follows the super-admin
    check in admin_update_user. A room decides whose records its members read, so changing one
    that holds a user this admin could not reset changes that user's sharing: adding an
    iep-goals holder to teacher T's room shows them every child in it, and removing T from T's
    own room takes T's classroom away. Both are refused, as is a rename (it is the same edit
    route) and a delete. Checked against the membership as it stands, before anything applies;
    a super-admin's `_grantable` is the whole catalog, so nothing is beyond them and there is no
    separate exemption to drift. Every such member is named, with the apps.
    """
    grantable = _grantable(db, actor)       # once, not again for every member
    beyond = [(m.user.username, apps)
              for m in sorted(w.members, key=lambda x: x.user.username)
              if (apps := _out_of_reach(db, actor, m.user, grantable))]
    if beyond:
        who = "; ".join(f"'{name}' holds apps you cannot grant ({', '.join(apps)})"
                        for name, apps in beyond)
        raise HTTPException(status_code=403, detail=(
            f"you cannot {action} the workspace '{w.name}': {who}, so only a super-admin can"))


def _apply_members(db: OrmSession, w: Workspace, submitted: list[str],
                   manageable: set[str]) -> None:
    """Apply a membership edit under the delegation rule.

    Adding a name outside `manageable` (for a plain admin, a super-admin or a user beyond their
    reach) is refused loudly with 403, NOT dropped. test_unknown_username_is_rejected_not_
    silently_dropped settled that for this surface: a silent drop means the admin believes the
    aide can see the teacher and finds out otherwise from the teacher. Nothing is frozen: a
    plain admin cannot edit a room that already holds a super-admin or a member beyond their
    reach at all (_refuse_superadmin_room, _refuse_room_out_of_reach), which is everyone outside
    `manageable`, so no member they cannot manage can be in the room they are editing.
    """
    wanted = {u.strip() for u in submitted if u.strip()}
    refused = sorted(u for u in wanted if u not in manageable)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=f"not yours to add to a workspace: {refused}. A workspace decides whose "
                   f"records another user can read, so it is limited to the users you may "
                   f"already manage.")
    _set_members(db, w, sorted(wanted))


def _set_members(db: OrmSession, w: Workspace, usernames: list[str]) -> None:
    """Diff the membership rather than clear-and-re-add.

    Same reason _set_entitlements does: re-adding an unchanged member as a NEW row can have
    SQLAlchemy emit the INSERT before the DELETE and trip the unique constraint. Also means an
    unchanged member keeps its row id, so nothing downstream sees a spurious change.
    """
    wanted = {u.strip() for u in usernames if u.strip()}
    found = db.execute(select(User).where(User.username.in_(wanted))).scalars().all()
    missing = wanted - {u.username for u in found}
    if missing:
        raise HTTPException(status_code=400, detail=f"no such user(s): {sorted(missing)}")
    want_ids = {u.id for u in found}
    have = {m.user_id: m for m in w.members}
    for uid, row in have.items():
        if uid not in want_ids:
            db.delete(row)
    for uid in want_ids - set(have):
        db.add(WorkspaceMember(workspace_id=w.id, user_id=uid))


@app.get("/api/platform/admin/workspaces")
def admin_list_workspaces(admin: User = Depends(require_admin),
                          db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    rows = db.execute(select(Workspace).order_by(Workspace.name)).scalars().unique().all()
    return {"workspaces": [_workspace_out(w) for w in rows],
            "manageable": sorted(_manageable(db, admin))}


@app.post("/api/platform/admin/workspaces")
def admin_create_workspace(body: WorkspaceCreate, admin: User = Depends(require_admin),
                           db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if db.execute(select(Workspace).where(Workspace.name == name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"workspace '{name}' already exists")
    w = Workspace(name=name)
    db.add(w)
    db.flush()                      # need w.id before members reference it
    _apply_members(db, w, body.members, _manageable(db, admin))
    db.commit()
    db.refresh(w)
    return _workspace_out(w)


@app.patch("/api/platform/admin/workspaces/{wid}")
def admin_update_workspace(wid: int, body: WorkspaceUpdate,
                           admin: User = Depends(require_admin),
                           db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    w = db.get(Workspace, wid)
    if w is None:
        raise HTTPException(status_code=404, detail="no such workspace")
    _refuse_superadmin_room(w, admin, "change")
    _refuse_room_out_of_reach(db, w, admin, "change")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        clash = db.execute(select(Workspace).where(Workspace.name == name,
                                                   Workspace.id != wid)).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=409, detail=f"workspace '{name}' already exists")
        w.name = name
    if body.members is not None:
        _apply_members(db, w, body.members, _manageable(db, admin))
    db.commit()
    db.refresh(w)
    return _workspace_out(w)


@app.delete("/api/platform/admin/workspaces/{wid}")
def admin_delete_workspace(wid: int, admin: User = Depends(require_admin),
                           db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    """Deleting a workspace only removes the SHARING. Nobody's records are touched -- each
    stays owned by whoever created it, and simply stops being visible to the others."""
    w = db.get(Workspace, wid)
    if w is None:
        raise HTTPException(status_code=404, detail="no such workspace")
    # Deleting a room the super-admin is in is a destructive act on THEIR sharing, and so is
    # deleting one that holds a member this admin could not reset.
    _refuse_superadmin_room(w, admin, "delete")
    _refuse_room_out_of_reach(db, w, admin, "delete")
    db.delete(w)
    db.commit()
    return {"ok": True}


class RailModelBody(BaseModel):
    model: str  # a concrete installed model name, or a glob pattern to keep auto-resolution
    # Which registered broker runs it. "local" is this box; any other name is composed into the
    # stored role value as "<upstream>::<model>", the broker's delegation syntax. Defaulted, so
    # a frontend that predates delegation still posts {"model": ...} alone and means local.
    upstream: str = "local"


class RailCliBody(BaseModel):
    """The Rails tab's Claude switch for one role, its Claude model, or both. `enabled` is a
    JSON boolean only (StrictBool), as at the broker: a switch that parses "false" as a value
    is one somebody will mistype. `model` is a model id, or null to clear the role's own; left
    out, the model stays as it is. A body that sends neither is refused (422)."""
    enabled: StrictBool | None = None
    model: StrictStr | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _says_something(self) -> "RailCliBody":
        if self.enabled is None and "model" not in self.model_fields_set:
            raise ValueError('send "enabled" (true or false), "model" (an id, or null), or both')
        return self


class BrokerTokenBody(BaseModel):
    """A named broker token. `label` is how you find the right row to revoke later, so it has
    no default; `scope` defaults to the safer of the two."""
    label: str
    scope: str = "inference"


# --- broker access tokens ---------------------------------------------------
#
# SUPER-ADMIN ONLY, deliberately more restrictive than the rest of this pane. A broker token is
# not scoped to a rail or a user: it is direct access to the GPU and, at `full` scope, the
# ability to repoint every rail's model. A plain admin can manage users and rooms; handing out
# machine credentials for the whole platform is a different kind of authority.

def _broker_detail(exc: BrokerError) -> str:
    """Name the box that actually failed.

    Every BrokerError here used to be reported as "broker unreachable", which was true until
    roles could be delegated. Now a failure reaching a REMOTE broker arrives by the same route:
    the local broker is answering perfectly well, it is the upstream that is down, and an admin
    reading "broker unreachable" goes and looks at the wrong machine. The broker's UpstreamError
    already names the box in its message, so the distinction only has to be surfaced.
    """
    s = str(exc)
    if "upstream '" in s:
        return f"a delegated upstream failed (this broker is up): {s}"
    return f"broker unreachable: {s}"


@app.get("/api/platform/admin/broker/tokens")
async def admin_list_broker_tokens(admin: User = Depends(require_admin)) -> dict[str, Any]:
    if not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin may manage broker tokens")
    try:
        return await app.state.broker.tokens()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


@app.post("/api/platform/admin/broker/tokens")
async def admin_create_broker_token(body: BrokerTokenBody,
                                    admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Mint a token. The response carries the PLAINTEXT ONCE -- the broker keeps only a sha256
    hash, so there is no second chance to read it and the UI says so before generating."""
    if not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin may manage broker tokens")
    try:
        return await app.state.broker.create_token(body.label.strip(), body.scope)
    except BrokerError as exc:
        # A 400 from the broker (bad label, duplicate, unknown scope) is the caller's fault, and
        # a 409 is the broker refusing a mint that would lock every client out: both are the
        # broker's own words for the admin, and neither may read as "the broker broke". The
        # words come from exc.detail, the broker's parsed message. Splitting str(exc) on ": "
        # used to cut a message in half at its own first colon.
        if exc.status in (400, 409):
            raise HTTPException(status_code=exc.status, detail=exc.detail or str(exc)) from exc
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


@app.delete("/api/platform/admin/broker/tokens/{token_id}")
async def admin_revoke_broker_token(token_id: str,
                                    admin: User = Depends(require_admin)) -> dict[str, Any]:
    if not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin may manage broker tokens")
    try:
        return await app.state.broker.revoke_token(token_id)
    except BrokerError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=f"no such token '{token_id}'") from exc
        # A 409 is the broker refusing a tokens.json write it cannot land, naming the file: its
        # own words for the admin, as at the mint route, not a 502 that reads as an outage.
        if exc.status == 409:
            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


async def _upstreams_view() -> list[dict[str, Any]]:
    """Every broker a rail slot may be pointed at, ``local`` first.

    Rows are the broker's own -- ``{"name", "url", "healthy"}``, never a token. A broker too old
    to know ``/v1/upstreams`` reports local only, so the panel still renders and delegation is
    simply unavailable rather than broken. A broker that is DOWN does not reach here on the GET
    path: ``roles()`` is fetched first and its BrokerError still surfaces as the 502 it always
    did.
    """
    try:
        ups = (await app.state.broker.upstreams()).get("upstreams", [])
    except BrokerError:
        ups = []
    return ups or [{"name": "local", "url": "", "healthy": True}]


async def _upstream_models(
    name: str,
    disabled: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """One upstream's picker choices. A box that is unreachable, unauthorized or merely slow
    yields an EMPTY list rather than an exception: one dark remote must not blank out the whole
    Rails tab, which would take every purely local slot's picker down with it. ``disabled`` is
    platform-wide policy, so it filters every box's inventory, not just this one's."""
    try:
        raw = await app.state.broker.models(None if name == "local" else name)
    except BrokerError:
        return []
    return model_options(raw.get("models", []), disabled)


# Broker blockers that the Claude switch itself changes. They must not disable the switch, or
# switching the last role off would leave nothing on the tab that could switch one back on.
_CLI_ROLE_SET_BLOCKERS = frozenset({"no_roles_configured", "cli_all_roles_off"})

# Why a vision role's Claude switch cannot be turned ON on this broker: its `cli` block does
# not say it can send images to the Claude CLI (`vision: true`, the broker's stream-json image
# path). A broker from before that path refuses every call that carries an image
# (vision_unsupported) and the local model answers it, while the rail's chip reads Claude for
# the owner. A block that does not say is read as "cannot", as a broker older than the switch
# is read as not ready (_cli_block). The one sentence is the route's 409 and the slot's
# `cli_unavailable`, so the tab says before the click what the route says after it.
CLI_NO_VISION = ("the broker does not say it can send images to Claude, so every image call "
                 "on this role stays local")

# Why a STREAMING role's Claude switch cannot be turned ON on this broker: its `cli` block does
# not say it streams to the Claude CLI (`streams: true`). A broker from before streamed CLI calls
# answers every /v1/chat/stream locally, named or not, while the rail's chip reads Claude for the
# owner. Which roles stream is the catalog's `stream` flag (rails_models.py STREAM_SLOT_ROLES).
# The vision rule's shape exactly: the route's 409 and the slot's `cli_unavailable` are this one
# sentence, and a block that does not say is read as "it does not".
CLI_NO_STREAM = ("the broker does not say it streams to Claude, so every streamed answer on this "
                 "role stays local")


def _cli_block(view: dict[str, Any]) -> dict[str, Any]:
    """The broker's Claude switch state from the Rails tab's one read, normalised.

    A broker from before the switch sends no ``cli`` key. That is reported as a blocker of its
    own rather than defaulted to "ready", so the tab shows every switch as unavailable instead of
    offering one whose PUT the old broker would answer with a 404.
    """
    raw = view.get("cli")
    if not isinstance(raw, dict):
        raw = {"ready": False, "blockers": ["broker_has_no_cli_switch"], "enabled_roles": [],
               "roles_source": "env", "roles_file_set": False, "roles_problem": ""}
    blockers = [b for b in (raw.get("blockers") or []) if isinstance(b, str)]
    role_models = raw.get("role_models")
    return {
        "ready": raw.get("ready") is True,
        "blockers": blockers,
        # What disables the switches: every blocker except the ones a switch fixes.
        "switch_blockers": [b for b in blockers if b not in _CLI_ROLE_SET_BLOCKERS],
        "enabled_roles": sorted(r for r in (raw.get("enabled_roles") or [])
                                if isinstance(r, str)),
        "roles_source": raw.get("roles_source") if raw.get("roles_source") in ("env", "file")
        else "env",
        "roles_file_set": raw.get("roles_file_set") is True,
        "roles_problem": raw.get("roles_problem") if isinstance(raw.get("roles_problem"), str)
        else "",
        # Each role's own Claude model, and the one a role with none gets (BROKER_CLI_MODEL,
        # "" for the CLI's own default). A broker older than the model choice sends neither.
        "role_models": {str(k): v for k, v in role_models.items()
                        if isinstance(k, str) and isinstance(v, str)}
        if isinstance(role_models, dict) else {},
        "default_model": raw.get("default_model") if isinstance(raw.get("default_model"), str)
        else "",
        # Whether the broker sends images to the CLI. Only a JSON true counts: a broker from
        # before the image path sends no key, and that is the answer "it cannot" (CLI_NO_VISION).
        "vision": raw.get("vision") is True,
        # Whether the broker sends streamed chat to the CLI, read the same way: only a JSON true,
        # and a broker from before streamed CLI calls sends no key (CLI_NO_STREAM).
        "streams": raw.get("streams") is True,
    }


def _cli_models(view: dict[str, Any]) -> list[str]:
    """The Claude models a role may be given (the broker's BROKER_CLI_MODELS), from the same
    `cli` block, in the broker's order. [] from a broker older than the model choice, which the
    tab reads as "nothing to choose"."""
    raw = view.get("cli")
    models = raw.get("models") if isinstance(raw, dict) else None
    return [m for m in models if isinstance(m, str)] if isinstance(models, list) else []


async def _rails_payload(disabled: set[str] = frozenset(),
                         admin: User | None = None) -> dict[str, Any]:
    """The Rails-tab payload: each installed rail's model slots (resolved model + description)
    plus the installed generative models to choose from, PER BROKER. Resolved via the broker.
    Disabled models are dropped from the pickers.

    ``models`` is keyed by upstream name because a slot's choices depend on where it runs: point
    one off-site and the dropdown has to offer THAT box's inventory, not this card's. Every
    reported upstream gets a key -- empty when it cannot be read -- so the frontend can always
    index by the slot's own ``upstream`` without a missing-key branch.

    The Claude switch rides the same read: ``cli`` (the broker's state, see _cli_block), and on
    every slot ``cli_on`` (its role is switched on), ``cli_wired`` (this rail passes who is
    asking, the catalog's static flag), ``cli_ready`` (the backend is ready apart from which
    roles are on), ``cli_model`` (the Claude model the broker's switch file gives its role,
    None for the default), ``cli_switchable`` (the slot has a switch at all, from its kind:
    build_rails_view) and ``cli_unavailable`` (why its switch cannot be turned ON on this
    broker, or None: CLI_NO_VISION on a vision role while the broker does not say it sends
    images to the CLI, else CLI_NO_STREAM on a streaming role while it does not say it streams
    to the CLI, the rules the switch route holds). ``cli_models`` is the broker's
    allowlist, the choices the tab offers after "Default". ``cli_editable`` is whether THIS
    admin may flip a switch or choose a model: super-admin only, and the frontend has no other
    way to know, because /api/platform/apps does not say.
    """
    broker = app.state.broker
    # ONE broker read for the whole tab. `inventory` makes the broker report every box's health
    # and installed models from the same probe that judged the delegated rows. Asking for them
    # separately (upstreams(), then models(box) per box) had the broker dial each remote's
    # /healthz and /v1/models a SECOND time on every load of this tab.
    view = await broker.roles(inventory=True)
    roles = view.get("roles", [])
    if isinstance(view.get("upstreams"), list) and isinstance(view.get("models"), dict):
        upstreams = view["upstreams"] or [{"name": "local", "url": "", "healthy": True}]
        names = [str(u["name"]) for u in upstreams if u.get("name")]
        inventories = view["models"]
        # A box the broker could not read arrives as [] (or is absent): empty choices for that
        # box, never an error, so one dark remote cannot blank the local pickers.
        models: dict[str, list[dict[str, Any]]] = {
            n: model_options(inventories.get(n) or [], disabled) for n in names}
    else:
        # A broker older than `inventory` ignores the parameter and answers roles alone, and the
        # gateway and the broker are deployed separately. Fall back to asking for each piece,
        # which is the double probe, but a correct tab.
        upstreams = await _upstreams_view()
        named = [u for u in upstreams if u.get("name")]
        names = [str(u["name"]) for u in named]
        # Only a box the broker reports healthy is worth a round-trip; the rest are pre-seeded
        # empty. Fetched concurrently so N remotes cost one slow box's latency and not the sum
        # -- and a remote is exactly the thing that is slow.
        live = [str(u["name"]) for u in named if u.get("healthy", True)]
        fetched = await asyncio.gather(*(_upstream_models(n, disabled) for n in live))
        models = {n: [] for n in names}
        models.update(dict(zip(live, fetched)))
    enabled = set(app.state.settings.enabled_apps)
    rails = build_rails_view(roles, enabled)
    # Where each slot RUNS, carried onto the slot from the broker's role row so the picker knows
    # which inventory in ``models`` to index. Defaulted to "local": this broker always sends the
    # key, but a slot rendered as unplaced would be a worse failure than assuming this box.
    up_by_role = {r.get("role"): (r.get("upstream") or "local") for r in roles}
    for rail in rails:
        for slot in rail["slots"]:
            up = up_by_role.get(slot["role"], "local")
            slot["upstream"] = up
            # STRIP the `upstream::` prefix off the pattern. `pattern` and `upstream` have to be
            # orthogonal -- one says which model, the other says which box -- because the panel
            # round-trips `pattern` straight back into the PUT. Left prefixed, choosing "local"
            # on a delegated slot sent model="offsite::x" with upstream="local", the gateway
            # skipped its prefixing step, set_role accepted it because offsite IS registered,
            # and the role stayed delegated while the panel reported it as local. Picking a
            # different box was worse still: "offsite2::offsite::x".
            pat = slot.get("pattern")
            if up != "local" and isinstance(pat, str) and pat.startswith(f"{up}::"):
                slot["pattern"] = pat[len(up) + 2:]
    cli = _cli_block(view)
    on = set(cli["enabled_roles"])
    for rail in rails:
        for slot in rail["slots"]:
            slot["cli_on"] = slot["role"] in on
            slot["cli_ready"] = not cli["switch_blockers"]
            slot["cli_model"] = cli["role_models"].get(slot["role"])
            # Per ROLE, as admin_set_rail_cli refuses it: every slot on a vision role.
            slot["cli_unavailable"] = (CLI_NO_VISION if slot["role"] in VISION_SLOT_ROLES
                                       and not cli["vision"] else None)
            # And every slot on a streaming role, the image reason first on a role that is both.
            if (slot["cli_unavailable"] is None and slot["role"] in STREAM_SLOT_ROLES
                    and not cli["streams"]):
                slot["cli_unavailable"] = CLI_NO_STREAM
    return {"rails": rails,
            "models": models,
            "upstreams": upstreams,
            "media": media_options(),
            "cli": cli,
            "cli_models": _cli_models(view),
            "cli_editable": admin is not None and admin.is_superadmin is True}


@app.get("/api/platform/admin/rails")
async def admin_rails(admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await _rails_payload(await _disabled_set(), admin)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


@app.put("/api/platform/admin/rails/{role}/cli")
async def admin_set_rail_cli(role: str, body: RailCliBody,
                             admin: User = Depends(require_admin)) -> dict[str, Any]:
    """The Rails tab's Claude switch: may an OWNER's own call on this role go to the Claude CLI.

    SUPER-ADMIN ONLY, like the broker tokens. It decides which rails' data may leave the box for
    the owner's Claude subscription, which is a different kind of authority from repointing a
    model. It changes WHICH ROLES only: who may use the CLI (BROKER_CLI_OWNERS) and whether it
    runs at all (BROKER_CLI_ENABLED) are broker environment settings no route reaches.

    Per role, because the broker routes by role: a role two rails share switches for both, which
    the tab says beside the switch. A text or a vision slot's role has the switch (CLI_SLOT_KINDS);
    an image slot's has none, being a media-worker backend and not a model. Switching ON needs a
    slot whose rail passes who is asking (`cli` in RAIL_MODEL_SLOTS): on an unwired role it would
    change nothing today, and would start sending data off the box the day that rail is wired,
    with nobody deciding it then. A vision role's ON also needs a broker that says it sends
    images to the CLI (`vision` in its `cli` block): before that, every call with an image
    falls back to the local model while the chip reads Claude, so the answer is a 409
    (CLI_NO_VISION), and a read that fails refuses too. A streaming role's ON (a slot whose rail
    streams its answers on the role: `stream` in RAIL_MODEL_SLOTS) likewise needs a broker that
    says it streams to the CLI (`streams`): before that, every streamed answer stays local while
    the chip reads Claude (CLI_NO_STREAM, the same 409 and the same refusal when the read
    fails). Switching OFF is always allowed.

    The same route chooses the Claude model the role's calls use (`model`: an id, or null for
    the default; left out, it stays). Which ids are allowed is the BROKER's list
    (BROKER_CLI_MODELS), so the broker judges the id and its 400 reaches the admin in its own
    words: a second copy of the list here would be a second answer to drift from the first.
    Choosing one needs a wired slot like switching ON does; clearing one is always allowed.
    A vision role's model is not held to the image check: a model sends nothing off the box
    by itself, the switch does.
    """
    if not admin.is_superadmin:
        raise HTTPException(status_code=403,
                            detail="only a super-admin may switch Claude on or off for a rail")
    if role not in RAIL_SLOT_ROLES:
        raise HTTPException(status_code=404, detail=f"unknown rail model slot '{role}'")
    if role not in CLI_SLOT_ROLES:
        raise HTTPException(status_code=400, detail=(
            f"@{role} has no text or vision slot: an image slot renders pictures through the "
            f"media worker, which no Claude model stands in for"))
    if body.enabled and role not in CLI_WIRED_ROLES:
        raise HTTPException(status_code=409, detail=(
            f"no rail passes who is asking on @{role}, so every call on it stays local and "
            f"switching Claude on would change nothing. Wire the rail first."))
    if body.enabled and role in VISION_SLOT_ROLES:
        # Read now, from the block the tab drew from (roles_inventory's `cli`): the broker
        # sends no `vision` on any lighter read. A read that fails refuses, because the guess
        # that lets the switch through is the one that would mislead.
        try:
            view = await app.state.broker.roles(inventory=True)
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=(
                f"@{role} was not switched on: the gateway could not read whether the broker "
                f"can send images to Claude ({_broker_detail(exc)})")) from exc
        if not _cli_block(view if isinstance(view, dict) else {})["vision"]:
            raise HTTPException(status_code=409, detail=(
                f"@{role} reads images, and {CLI_NO_VISION}. Update the broker first."))
    if body.enabled and role in STREAM_SLOT_ROLES:
        # The image check's shape: the same inventory read, and a read that fails refuses. A role
        # that both reads images and streams is asked twice, which no catalog role does today.
        try:
            seen = await app.state.broker.roles(inventory=True)
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=(
                f"@{role} was not switched on: the gateway could not read whether the broker streams "
                f"to Claude ({_broker_detail(exc)})")) from exc
        if not _cli_block(seen if isinstance(seen, dict) else {})["streams"]:
            raise HTTPException(status_code=409, detail=(
                f"@{role} streams its answers, and {CLI_NO_STREAM}. Update the broker first."))
    choose = "model" in body.model_fields_set
    if choose and body.model is not None and role not in CLI_WIRED_ROLES:
        raise HTTPException(status_code=409, detail=(
            f"no rail passes who is asking on @{role}, so every call on it stays local and a "
            f"Claude model there would change nothing. Wire the rail first."))
    try:
        reply = await app.state.broker.set_cli_role(
            role, body.enabled, **({"model": body.model} if choose else {}))
    except BrokerError as exc:
        # 400 (a role the broker does not know, or a model it does not allow) and 409 (no switch
        # file, or an invalid one) are the broker's own sentences for the admin; anything else is
        # the broker failing.
        if exc.status in (400, 409):
            raise HTTPException(status_code=exc.status, detail=exc.detail or str(exc)) from exc
        if exc.status == 404:
            raise HTTPException(status_code=502, detail=(
                "the broker has no Claude switch (PUT /v1/cli/roles); it is older than this "
                "gateway")) from exc
        if exc.status == 422 and choose:
            # This gateway sends only bodies the broker's own rule accepts, so a 422 is a broker
            # that requires `enabled` on every call: one from before the model choice.
            raise HTTPException(status_code=502, detail=(
                "the broker cannot choose a Claude model per role; it is older than this "
                "gateway")) from exc
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    if choose and not (isinstance(reply, dict) and isinstance(reply.get("cli"), dict)
                       and isinstance(reply["cli"].get("models"), list)):
        # A broker from before the model choice takes {"enabled", "model"}, applies the switch
        # and drops the model without a word; its reply is how to tell.
        raise HTTPException(status_code=502, detail=(
            "the broker applied the switch but not the model: it cannot choose a Claude model "
            "per role, being older than this gateway"))
    try:
        return await _rails_payload(await _disabled_set(), admin)
    except BrokerError as exc:
        # The switch is LIVE: the broker wrote it, and only the tab's re-read failed. A 502 here
        # told the admin the switch had failed while owner calls on the role already went to
        # Claude, and the tab kept the old checkbox. So answer with the switch's OWN reply, the
        # broker's cli block (no `rails`): the shell redraws the slots it has from it and says
        # the tab could not be re-read.
        return {"cli": _cli_block(reply if isinstance(reply, dict) else {}),
                "cli_models": _cli_models(reply if isinstance(reply, dict) else {}),
                "cli_editable": True,
                "reload_error": ("The switch was applied, but the Rails tab could not be "
                                 f"re-read: {_broker_detail(exc)}")}


# No /admin/upstreams or /admin/upstreams/{name}/models routes: only tests ever called them.
# The Rails tab reads the same upstream list and per-box inventories through _rails_payload.


# --- admin: model pool (every installed model + lifecycle) ------------------
async def _disabled_set() -> set[str]:
    """Admin-disabled model names. The BROKER owns this (disabled.json), so every rail that talks
    to the broker — not just the gateway Rails picker — sees the same set. Empty if broker down."""
    try:
        return set(await app.state.broker.disabled())
    except BrokerError:
        return set()


def _loaded_names(status: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for item in status.get("loaded", []) or []:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            n = item.get("name") or item.get("model")
            if n:
                out.add(n)
    return out


def _roles_using(roles: list[dict[str, Any]]) -> dict[str, list[str]]:
    """model name -> the roles that currently resolve to it (its 'in use' footprint).

    Counts BOTH what a role is configured to use and what is actually SERVING it. The two
    differ whenever the broker has substituted, and this footprint is what makes the Models
    tab refuse a destructive Delete -- so keying it on `resolved` alone would protect the
    model nobody is running and leave the substitute deletable out from under a live rail.

    A DELEGATED role is skipped. Its resolved/serving name is the model on the remote box, so a
    same-named local copy is not what it runs, and counting it would refuse a safe Delete here.
    """
    use: dict[str, list[str]] = {}
    for r in roles:
        if r.get("upstream") not in (None, "local"):
            continue
        role = r.get("role")
        for m in {r.get("resolved"), r.get("serving")}:
            if m and role not in use.setdefault(m, []):
                use[m].append(role)
    return {m: sorted(rs) for m, rs in use.items()}


def _rail_blocked_by(rid: str) -> str | None:
    """Which setting keeps a catalog rail off this box, or None when the box runs it.

    Two settings can, and each has its own fix: 'disabled_apps' (PLATFORM_DISABLED_APPS lists
    it: remove it there) and 'not_enabled' (PLATFORM_ENABLED_APPS does not: add it there). A
    rail named in PLATFORM_DISABLED_APPS is usually already in PLATFORM_ENABLED_APPS, whose
    default is every rail, so telling the admin to add it there sends them to edit a list it is
    already on, and it stays off. One rule for the Rail Manager's card (`blocked_by`) and the
    toggle's refusal, so the two cannot name different settings. A rail PLATFORM_DISABLED_APPS
    lists is named by it even if PLATFORM_ENABLED_APPS leaves it out too, as the toggle always
    did: the enabled list as written is not kept once the subtraction has run (config.py), so
    the gateway cannot tell that case apart."""
    settings = app.state.settings
    if rid in set(settings.enabled_apps):
        return None
    return "disabled_apps" if rid in set(settings.disabled_apps) else "not_enabled"


@app.get("/api/platform/admin/rail-manager")
def admin_rail_manager(admin: User = Depends(require_admin),
                        db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    """Every rail this build knows about, with the copy needed to decide whether to run it.

    Three states, all DERIVED rather than stored, so the answer cannot go stale:
      enabled      — installed here and switched on
      disabled     — installed, switched off by an admin (reversible, one row in rail_state)
      unavailable  — in the catalog but not in this deployment's enabled_apps

    'unavailable' is the honest name for the case the UI cannot fix. Turning such a rail on means
    editing PLATFORM_ENABLED_APPS or PLATFORM_DISABLED_APPS and restarting the gateway, because
    the static mount for /<id>/ is registered at import time — so the catalog reports it and says
    so rather than offering a button that would appear to work and then 404. Which of the two is
    `blocked_by` (_rail_blocked_by), None on every rail this box runs.
    """
    disabled = disabled_rail_ids(db)
    installed = set(app.state.settings.enabled_apps)
    dists = set(app.state.settings.resolved_app_dists())
    backends = set(app.state.backends)
    # How many users can currently reach each rail — the thing to check before switching one off.
    # People, not rows: a super-admin reaches every rail with no Entitlement row at all, and
    # counting rows alone reported 0 users for a rail its owner used daily. A super-admin who
    # also holds rows (the role migration grants them) is still one person.
    supers = set(db.execute(select(User.id).where(User.is_superadmin.is_(True))).scalars())
    reach: dict[str, set[int]] = {}
    for e in db.execute(select(Entitlement)).scalars().all():
        reach.setdefault(e.app_id, set()).add(e.user_id)
    grants = {rid: len(reach.get(rid, set()) | supers) for rid in APP_IDS}

    out = []
    for entry in APP_CATALOG:
        rid = entry["id"]
        if rid not in installed:
            state = "unavailable"
        elif rid in disabled:
            state = "disabled"
        else:
            state = "enabled"
        out.append({
            "id": rid,
            "label": entry.get("label"),
            "icon": entry.get("icon"),
            "description": entry.get("description"),
            "status": entry.get("status", "ready"),
            "state": state,
            "installed": rid in installed,
            # A rail with no built bundle renders blank rather than erroring, so surface it here:
            # resolved_app_dists() deliberately skips a missing dist instead of failing startup.
            "bundle_built": rid in dists,
            "has_backend": rid in backends,
            "entitled_users": grants.get(rid, 0),
            # Which setting to change for an unavailable rail, so the card names the right one.
            "blocked_by": _rail_blocked_by(rid),
        })
    return {"rails": out}


class RailToggleBody(BaseModel):
    rail_id: str
    enabled: bool


@app.post("/api/platform/admin/rail-manager/toggle")
def admin_rail_toggle(body: RailToggleBody, admin: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    """Switch a rail on or off for everyone. Reversible; nothing is deleted.

    Refuses a rail this deployment does not have — enabling it would put a tile in the launcher
    with no backend and no bundle behind it, which is worse than saying no.

    Deliberately NOT enforcement: an open session keeps working. The model pool draws the same
    line, and the alternative is 403-ing someone mid-task to apply an availability decision.
    """
    rid = body.rail_id
    if rid not in APP_IDS:
        raise HTTPException(status_code=404, detail=f"no such rail '{rid}'")
    blocked = _rail_blocked_by(rid)
    if body.enabled and blocked:
        # Two causes, two different fixes (_rail_blocked_by, the card's own rule).
        if blocked == "disabled_apps":
            cause = (f"'{rid}' is switched off for this box by PLATFORM_DISABLED_APPS. Remove it "
                     f"from that list and restart the gateway")
        else:
            cause = (f"'{rid}' is not installed in this deployment. Add it to "
                     f"PLATFORM_ENABLED_APPS and restart the gateway")
        raise HTTPException(
            status_code=409,
            detail=(f"{cause} — its /{rid}/ mount is "
                    f"registered at startup, so it cannot be switched on from here."))
    row = db.get(RailState, rid)
    if body.enabled:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(RailState(rail_id=rid, disabled_by=admin.username))
    db.commit()
    return {"rail_id": rid, "state": "enabled" if body.enabled else "disabled"}


@app.get("/api/platform/admin/models")
async def admin_models(admin: User = Depends(require_admin),
                       db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    """Every model installed on the box, annotated with In-Use (roles), Loaded (VRAM now) and
    Enabled (the broker's availability flag). One inventory for the whole workstation model pool.

    Each model's category + blurb resolves curated (hand-authored) → cached (broker-LLM-generated
    by the scheduled scan, flagged ``generated``) → fallback (class/vision placeholder)."""
    broker = app.state.broker
    try:
        models = (await broker.models()).get("models", [])
        # local_only: _roles_using skips delegated rows (their names are the remote's
        # models), so probing every remote box to fetch them only made this tab wait.
        roles = (await broker.roles(local_only=True)).get("roles", [])
        # /v1/ps, not /v1/status: only the resident model names are read here, and status()
        # also runs nvidia-smi and reads the queue, paying a process spawn per tab load.
        resident = await broker.ps()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    loaded = _loaded_names(resident)
    use = _roles_using(roles)
    gen = {g.name: g for g in db.execute(select(GeneratedModelDesc)).scalars().all()}
    out = []
    for m in models:
        name = m.get("name")
        if not name:
            continue
        cached = gen.get(name)
        desc = (model_catalog.curated(name)
                or (cached and {"category": cached.category, "blurb": cached.blurb})
                or model_catalog.fallback(m.get("class"), bool(m.get("vision"))))
        out.append({
            "name": name, "class": m.get("class"), "parameter_size": m.get("parameter_size"),
            "size": m.get("size"), "vision": bool(m.get("vision")),
            "loaded": name in loaded,
            "in_use": bool(use.get(name)), "roles": use.get(name, []),
            "enabled": not m.get("disabled"),   # broker's disabled.json flag
            "category": desc["category"], "blurb": desc["blurb"],
            "generated": name in gen and model_catalog.curated(name) is None,
        })
    out.sort(key=lambda x: (not x["in_use"], x["class"] or "", x["name"]))
    # The category display labels + order, so the tab can group without hard-coding them.
    return {"models": out, "categories": [
        {"id": cid, "label": lbl, "order": order}
        for cid, (lbl, order) in sorted(model_catalog.CATEGORIES.items(), key=lambda kv: kv[1][1])
    ]}


class ModelToggleBody(BaseModel):
    name: str
    enabled: bool


@app.post("/api/platform/admin/models/toggle")
async def admin_model_toggle(body: ModelToggleBody,
                             admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Enable/disable a model in the pool (reversible), persisted to the broker's disabled.json so
    every rail's pickers honour it. Disabled = hidden + unloaded (best-effort); a role already on
    it keeps working (availability control, not enforcement).

    The current list is read STRICTLY here, unlike the read views that use _disabled_set(). A
    failed read used to come back as an empty set, and the write below then replaced the
    broker's whole disabled.json with a list holding only the toggled model: one dropped GET
    silently re-enabled every other disabled model on the box."""
    try:
        disabled = set(await app.state.broker.disabled())
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
        with contextlib.suppress(BrokerError):
            await app.state.broker.unload(body.name)
    try:
        await app.state.broker.set_disabled(sorted(disabled))
    except BrokerError as exc:
        # A 409 is the broker refusing a disabled.json write it cannot land, naming the file:
        # relayed in its own words, not as a 502 that reads as the broker failing.
        if exc.status == 409:
            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    return {"name": body.name, "enabled": body.enabled}


@app.post("/api/platform/admin/models/delete")
async def admin_model_delete(body: ModelBody,
                             admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Permanently remove a model from the box (ollama rm). Refused while any rail role resolves
    to it, so a live dependency can't be yanked out. Irreversible — the UI double-confirms."""
    name = body.model.strip()
    try:
        # local_only, as in admin_models: the in-use check ignores delegated rows anyway.
        roles = (await app.state.broker.roles(local_only=True)).get("roles", [])
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    using = _roles_using(roles).get(name, [])
    if using:
        raise HTTPException(status_code=409,
                            detail=f"'{name}' is in use by: {', '.join('@' + r for r in using)}. "
                                   "Repoint those rails first (Admin → Rails).")
    ollama = app.state.settings.ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request("DELETE", f"{ollama}/api/delete",
                                        json={"model": name, "name": name})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ollama delete failed: {exc}") from exc
    disabled = await _disabled_set()   # a deleted model can't stay in the disabled set
    if name in disabled:
        with contextlib.suppress(BrokerError):
            await app.state.broker.set_disabled(sorted(disabled - {name}))
    return {"deleted": name}


@app.put("/api/platform/admin/rails/{role}")
async def admin_set_rail_model(role: str, body: RailModelBody,
                               admin: User = Depends(require_admin)) -> dict[str, Any]:
    # Only per-rail slot roles are editable here — this panel can't repoint a shared class
    # (e.g. @chat) out from under multiple rails.
    if role not in RAIL_SLOT_ROLES:
        raise HTTPException(status_code=404, detail=f"unknown rail model slot '{role}'")
    model = body.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="a model is required")
    # `model` names a MODEL; `upstream` names the box. A caller that puts the delegation
    # prefix in the model field is composing the stored value itself, which lets it set a
    # combination this endpoint's own checks would refuse — a local-looking write that is
    # actually delegated, or a doubled `a::b::model`. Belt and braces with the strip in
    # _rails_payload: that keeps the panel from ever sending one, this makes it non-bypassable.
    if "::" in model:
        raise HTTPException(
            status_code=400,
            detail="name the model alone and put the box in 'upstream'; "
                   f"'{model}' already carries a delegation prefix")
    upstream = (body.upstream or "local").strip() or "local"
    # An image slot may only be set to a known media backend (sdxl-turbo / flux-schnell), and
    # only ever on THIS box: a media backend is loaded by our own media worker, so delegating one
    # would name a thing the remote broker has no concept of and fail at generation time.
    if role in IMAGE_SLOT_ROLES:
        if upstream != "local":
            raise HTTPException(status_code=400,
                                detail="an image slot runs on this box's media worker and "
                                       "cannot be delegated to an upstream")
        # In any case, as the broker's image_backend() reads it, and saved as the backend spells
        # itself: `FLUX-Schnell` is flux-schnell.
        backend = image_backend(model)
        if backend is None:
            raise HTTPException(status_code=400, detail=f"'{model}' is not a valid image backend")
        model = backend
    if upstream != "local":
        # Checked here as well as in the broker so the panel gets a clean 400 naming the
        # registered choices instead of a relayed error, and so an unregistered name can never
        # reach roles.json -- saved there the broker resolves it LOCALLY while this panel goes
        # on claiming the slot is off-site.
        known = {str(u.get("name", "")) for u in await _upstreams_view()}
        if upstream not in known:
            raise HTTPException(status_code=400,
                                detail=f"unknown upstream '{upstream}'; "
                                       f"registered: {', '.join(sorted(known))}")
        model = f"{upstream}::{model}"
    try:
        await app.state.broker.set_role(role, model)
        # Filter disabled models from the returned picker too, so a disabled model can't flash
        # back into the dropdown right after an Apply (the GET path already filters). The admin
        # rides along so `cli_editable` stays what the GET said.
        return await _rails_payload(await _disabled_set(), admin)
    except BrokerError as exc:
        # A 409 is a state of the box with its reason (the broker refuses every repoint while
        # roles.json cannot be read, rather than rewrite it from nothing): relayed in the
        # broker's own words, as the Claude switch's route does, not as a 502 that reads as
        # the broker failing.
        if exc.status == 409:
            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc
        # The broker validates the role/pattern; surface a 400 for a client-fixable error.
        msg = str(exc)
        code = 400 if exc.status == 400 else 502
        raise HTTPException(status_code=code, detail=msg) from exc


# --- admin: central scheduler (the 'Schedule' tab) --------------------------


class ScheduleBody(BaseModel):
    recurrence: dict[str, Any]
    enabled: bool = True


@app.get("/api/platform/admin/schedules")
def admin_schedules(admin: User = Depends(require_admin),
                    db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    return {"rails": scheduler.list_view(db, set(app.state.settings.enabled_apps))}


@app.put("/api/platform/admin/schedules/{rail}/{task_id}")
def admin_set_schedule(rail: str, task_id: str, body: ScheduleBody,
                       admin: User = Depends(require_admin),
                       db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    try:
        scheduler.set_schedule(db, rail, task_id, body.recurrence, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rails": scheduler.list_view(db, set(app.state.settings.enabled_apps))}


@app.post("/api/platform/admin/schedules/{rail}/{task_id}/run")
async def admin_run_schedule(rail: str, task_id: str, admin: User = Depends(require_admin),
                             db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await scheduler.run_now(db, app.state.http, app.state.backends, rail, task_id,
                                       getattr(app.state, "broker", None))
    except scheduler.AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- reverse proxy to each app's independent backend ------------------------
# (the app_access_gate middleware has already authorized this app for the user)


# HEAD is relayed like the rest. Without it a HEAD here was a 405 from this route, or a 404 from
# the rail's static mount behind it, and never reached the rail.
@app.api_route(
    "/{app_name}/api/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(app_name: str, path: str, request: Request) -> Response:
    base = app.state.backends.get(app_name)
    if base is None:
        raise HTTPException(status_code=404, detail=f"unknown app '{app_name}'")
    if _has_dot_segment(path):
        raise HTTPException(status_code=400, detail="'.' and '..' path segments are not allowed")
    return await _forward(f"{base}/api/{path}", app_name, request)


def _has_dot_segment(path: str) -> bool:
    """Would this path climb out of the prefix the route put in front of it?

    Starlette hands a route its path already percent-DECODED, so ``/job-aid/api/..%2fdocs``
    arrives here as ``../docs``. Joined onto ``{base}/api/`` and normalised by httpx, that
    reached the rail's ``/docs`` and ``/openapi.json``, which the /api/ prefix exists to keep
    out of reach. A backslash counts as a separator too, for any backend that treats it as one.
    Only whole segments: ``x..y`` and ``file.tar.gz`` are ordinary names.
    """
    return any(seg in (".", "..") for seg in path.replace("\\", "/").split("/"))


# Cookies that are the PLATFORM's credentials, never a rail's: the session (its configured name)
# and Cloudflare Access's. Compared case-insensitively; nothing legitimate in a rail shares them.
# Kept from the rail both ways: stripped from the request (_rail_cookie_header) and refused in the
# rail's Set-Cookie lines (_copy_upstream_headers).
_PLATFORM_COOKIES = {"cf_authorization"}

# Request headers that carry a credential for the PLATFORM's front door, never a rail's, the
# header form of the cookies above: Cloudflare Access's JWT (Cf-Access-Jwt-Assertion, beside the
# CF_Authorization cookie), a service token (Cf-Access-Client-Id / -Secret), and any
# Authorization or Proxy-Authorization the browser or a script sent. No rail reads Authorization
# from a gateway-relayed request: each authenticates by the x-platform-* identity, and the bearer
# a rail SENDS (its broker token, openmaic-app's shim token) goes rail to service, never through
# here. openmaic's relay to the app it wraps strips the same set
# (rails/openmaic/backend/openmaic_app/api/proxy.py, _CREDENTIAL_HEADERS).
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cf-access-jwt-assertion"})
_CREDENTIAL_PREFIXES = ("cf-access-client-",)


def _platform_cookie_names() -> set[str]:
    """The platform's own cookie names, lowercased: Access's, the session's configured name, and
    its default one."""
    session = getattr(getattr(app.state, "settings", None), "session_cookie", "platform_session")
    return _PLATFORM_COOKIES | {"platform_session", str(session).lower()}


def _rail_cookie_header(request_headers: Any) -> str | None:
    """The request's Cookie header without the platform's own cookies, or None if none remain.

    Every Cookie line is read (getlist), so a client that sends two cannot slip the session past
    by putting it in the second. Pairs are kept byte for byte, in order.
    """
    lines = (request_headers.getlist("cookie") if hasattr(request_headers, "getlist")
             else [request_headers.get("cookie", "")])
    drop = _platform_cookie_names()
    kept = []
    for line in lines:
        for pair in (line or "").split(";"):
            name = pair.split("=", 1)[0].strip()
            if name and name.lower() not in drop:
                kept.append(pair.strip())
    return "; ".join(kept) or None


# Response headers a relay never copies: hop-by-hop ones, plus content-type, which the relay sets
# itself (media_type). Content-Length and Content-Encoding PASS: the body goes out as the rail's
# raw bytes (_RelayedResponse), so the rail's own length and encoding describe exactly what is sent.
# They were dropped while the relay sent httpx's decoded `.content`, whose length it recomputed.
_RELAY_DROP = (_HOP_BY_HOP - {"content-length"}) | {"content-type"}


def _copy_upstream_headers(response: Response, upstream_headers: Any) -> Response:
    """Copy a rail's response headers onto ``response``, one header per upstream line.

    NOT through a dict. httpx's ``Headers.items()`` joins repeated names into one
    comma-separated value, which is legal for most headers and corrupts Set-Cookie: two
    cookies came out as one ``Set-Cookie: a=1; Path=/, b=2; Path=/`` that browsers read as a
    single cookie, undoing a rail (openmaic) that relays several. ``multi_items()`` keeps each
    line, and ``append`` writes each one.

    A Set-Cookie naming one of the platform's own cookies (_platform_cookie_names) is refused:
    a rail that set ``platform_session`` could fix a browser's session to one it chose, or log
    the user out by clearing it, and ``CF_Authorization`` is Cloudflare Access's. The rail's
    own cookies pass.
    """
    items = (upstream_headers.multi_items() if hasattr(upstream_headers, "multi_items")
             else upstream_headers.items())
    platform = _platform_cookie_names()
    for k, v in items:
        if k.lower() == "set-cookie" and _set_cookie_name(v) in platform:
            continue
        if k.lower() not in _RELAY_DROP:
            response.headers.append(k, v)
    return response


def _set_cookie_name(line: str) -> str:
    """The cookie a Set-Cookie value names, lowercased: everything before the first `=` of its
    first attribute."""
    return line.split(";", 1)[0].split("=", 1)[0].strip().lower()


class _BodyTooLarge(Exception):
    """Raised from inside the relay's body stream once it passes the rail's ceiling.

    Not an httpx.HTTPError, so httpx lets it through unwrapped and it cannot be mistaken for an
    unreachable rail (that one is a 502)."""


def _request_ceiling(app_name: str) -> int:
    """The most body bytes the relay hands this rail in one request: the rail's own manifest
    figure (config.MAX_REQUEST_BYTES), else the platform default (PLATFORM_MAX_REQUEST_BYTES)."""
    declared = MAX_REQUEST_BYTES.get(app_name)
    if declared is not None:
        return declared
    settings = getattr(app.state, "settings", None)
    return int(getattr(settings, "max_request_bytes", DEFAULT_MAX_REQUEST_BYTES))


def _too_large(app_name: str, limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=(
        f"this request is larger than the {limit / (1024 * 1024):.0f} MB {app_name} accepts"))


def _declared_length(headers: Any) -> int | None:
    """The request's Content-Length, or None when it has none. The server has already refused a
    malformed one (h11 answers 400 before the app runs), so None there is only defensive."""
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


async def _capped_body(request: Request, limit: int) -> AsyncIterator[bytes]:
    """The request body as it arrives, never more than `limit` bytes of it.

    Counted here, not trusted from Content-Length: a chunked body declares nothing. The chunk
    that crosses the limit is the last one read, so a refusal costs at most limit + one chunk,
    and the gateway never holds more than the chunk in flight."""
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise _BodyTooLarge
        if chunk:
            yield chunk


async def _forward(url: str, app_name: str, request: Request) -> Response:
    """Relay one request to a rail backend, carrying the verified identity.

    Extracted from proxy() so the root-asset routes can reuse it rather than growing a second,
    subtly different relay. Everything that makes this correct lives here -- the anti-spoof
    header strip, the peers/rooms encoding, the request-body ceiling, the streamed response and
    the response-header forwarding -- and a copy would drift from all five.

    THE REQUEST BODY IS STREAMED, NEVER READ WHOLE. `await request.body()` held every proxied
    body in this process (the one every rail shares) before a byte was relayed, and nothing
    capped it: a rail's own limit bounds what that rail receives, not what the gateway holds.
    Now a declared length over the rail's ceiling is refused before the body is read, and any
    other body is relayed as it arrives and cut off with a 413 once it passes the ceiling.
    Nothing replays it: the client makes no retries and follows no redirects, so a body that
    can be read only once is read only once.

    SO IS THE RESPONSE (_RelayedResponse), event stream or not.

    What reaches the rail is the client's request as sent, less what is the platform's: its
    query string byte for byte, its own Accept-Encoding (identity when it named none), none of
    the platform's credentials (cookies, _CREDENTIAL_HEADERS), and the identity set here.
    """
    limit = _request_ceiling(app_name)
    declared = _declared_length(request.headers)
    if declared is not None and declared > limit:
        raise _too_large(app_name, limit)
    # Drop hop-by-hop headers AND any client-supplied x-platform-* (anti-spoof): identity is
    # set only by us, below, from the session the access gate already verified. And the front
    # door's credentials (_CREDENTIAL_HEADERS): Cloudflare Access's JWT reached every rail in
    # Cf-Access-Jwt-Assertion while its cookie twin was stripped.
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and not k.lower().startswith("x-platform-")
               and k.lower() != "cookie" and k.lower() not in _CREDENTIAL_HEADERS
               and not k.lower().startswith(_CREDENTIAL_PREFIXES)}
    # httpx adds its own Accept-Encoding (gzip, deflate) to a request that carries none, and the
    # rail's raw bytes go back under the rail's own Content-Encoding (_RelayedResponse), so a
    # client that named no encoding was sent gzip it never said it could read. Identity on its
    # behalf; a client that named encodings keeps its list.
    headers.setdefault("accept-encoding", "identity")
    # The query string as the client sent it, not request.query_params: Starlette's multidict
    # hands httpx one value per key, so `?tag=a&tag=b` reached the rail as `?tag=b`. httpx sends
    # a query already in the URL as it is (no `params`, so nothing re-encodes it).
    query = request.url.query
    target = f"{url}?{query}" if query else url
    # A body of known length goes out with that length (a rail's own Content-Length check still
    # works); a chunked one goes out chunked. A request with neither has no body, and is sent
    # with none: an empty stream would make httpx label a plain GET as chunked.
    body: bytes | AsyncIterator[bytes] = b""
    chunked = "transfer-encoding" in request.headers
    if declared:
        headers["content-length"] = str(declared)
        body = _capped_body(request, limit)
    elif chunked:
        body = _capped_body(request, limit)
    # The platform's own credentials stay HERE. Every rail authenticates by the x-platform-*
    # identity set below, and none reads the session cookie, yet each was handed the caller's
    # platform_session (a super-admin's, for the owner) and Cloudflare Access's
    # CF_Authorization on every request. The rail's own cookies still pass.
    rail_cookies = _rail_cookie_header(request.headers)
    if rail_cookies:
        headers["cookie"] = rail_cookies
    ident_user = getattr(request.state, "platform_user", None)
    if ident_user is not None:
        headers["x-platform-user"] = ident_user
        headers["x-platform-admin"] = "1" if getattr(request.state, "platform_is_admin", False) else "0"
        # Only sent when non-empty, so the header is simply absent for the overwhelming
        # majority of requests and a rail's "no peers" path stays the common one.
        # JSON, not comma-separated: usernames are String(64) with no charset validation, so a
        # comma is legal in one and would silently split it into two names that do not exist.
        peers = getattr(request.state, "platform_peers", None)
        if peers:
            headers["x-platform-peers"] = json.dumps(peers)
        rooms = getattr(request.state, "platform_rooms", None)
        if rooms:
            headers["x-platform-rooms"] = json.dumps(rooms)
    # Every response is relayed as the rail sends it (_RelayedResponse), never read whole: a
    # backup download or a job's output.zip used to sit in this process in full before the
    # client saw a byte. What still differs is the timeout, and that is decided before the
    # request goes out because the CLIENT says up front what it wants: an EventSource always
    # sends `Accept: text/event-stream`. Server-sent events get no read timeout, since a stage
    # quiet for longer than one killed iep-goals' live job log while the job carried on.
    # Ordinary traffic keeps the client's 600 s read timeout, or a hung backend would hang its
    # request in the gateway for ever.
    event_stream = "text/event-stream" in request.headers.get("accept", "").lower()
    # Which client (_RELAY_LIMITS): an upload goes out on the uploads' own, capped client, so a
    # flood of slow ones queues behind itself; everything else, event streams included, on the
    # uncapped one, so a page load never waits for a connection.
    upload = not event_stream and (chunked or (declared or 0) > _UPLOAD_FROM)
    client: httpx.AsyncClient = app.state.http_body if upload else app.state.http
    if event_stream:
        # identity, not the browser's gzip/br: a compressed event stream waits in the rail's
        # compressor until a block fills, so a live log would arrive in lumps.
        headers["accept-encoding"] = "identity"
        stream_req = client.build_request(
            request.method, target, content=body, headers=headers,
            timeout=httpx.Timeout(600.0, read=None),
        )
    else:
        stream_req = client.build_request(
            request.method, target, content=body, headers=headers,
        )
    try:
        upstream = await client.send(stream_req, stream=True)
    except httpx.PoolTimeout as exc:
        # Only the uploads' client has a pool to run out of. Not a 502: the rail is fine, and
        # nothing of this body has been read, so the client can simply send it again.
        raise HTTPException(
            status_code=503, headers={"Retry-After": str(int(_UPLOAD_POOL_WAIT))},
            detail=("the gateway is relaying as many uploads as it takes at once; "
                    "send this one again shortly")) from exc
    except _BodyTooLarge:
        raise _too_large(app_name, limit) from None
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{app_name} backend unreachable: {exc}") from exc
    # The rail's response headers go too, line by line -- notably Content-Disposition, which
    # carries a download's filename (without it, browsers name every download "download.zip").
    default_type = "text/event-stream" if event_stream else "application/octet-stream"
    return _copy_upstream_headers(_RelayedResponse(
        upstream, upstream.headers.get("content-type", default_type), request, app_name,
    ), upstream.headers)


class _RelayedResponse(StreamingResponse):
    """A rail's response, relayed as the rail sends it.

    The body is the rail's RAW bytes (`aiter_raw()`), never decoded, so the rail's own
    Content-Length and Content-Encoding still describe exactly what goes out, and the gateway
    holds the chunk in flight rather than the response: the http middlewares hand each message
    on through unbuffered streams, so a client that reads slowly slows the rail's read too.

    A stream can end early two ways, and each is handled here.
    - The rail breaks off (its connection drops, a read times out). The status line has gone,
      so this can no longer become a 502; instead the request is marked cut (_RELAY_CUT) and the
      error re-raised, and _CutShortGuard keeps the response from being finished, so the client
      sees a failed download rather than a complete-looking shorter one.
    - The client goes away. Starlette cancels the stream (uvicorn says so through receive()), or
      send() raises (an ASGI 2.4 server). Either way the rail's response is closed in `finally`,
      shielded, because the cancellation that ended the stream would cancel the close as well.
      The event-stream branch closed it in a BackgroundTask, which runs only after a body that
      finished, so on a 2.4 server every abandoned stream left its rail connection open.
    """

    def __init__(self, upstream: httpx.Response, media_type: str, request: Request,
                 app_name: str) -> None:
        self._upstream = upstream
        super().__init__(self._relay(request, app_name), status_code=upstream.status_code,
                         media_type=media_type)

    async def _relay(self, request: Request, app_name: str) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._upstream.aiter_raw():
                yield chunk
        except Exception as exc:
            setattr(request.state, _RELAY_CUT,
                    f"{app_name}'s response broke off mid-body, so the client's copy is cut "
                    f"short rather than finished: {exc!r}")
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._upstream.aclose()


# --- websocket reverse proxy to an app backend -----------------------------
# The HTTP proxy above can't carry a WS upgrade, and Starlette's http middleware
# (the entitlement gate) does NOT run for websocket scope — so this route
# authenticates the handshake itself, exactly like the gate, then bridges frames
# to the app's own /ws/* endpoint. Declared before the app mounts + SPA so it
# wins for /{app}/ws/*. Used by the workstation terminal.


# How often a live WebSocket re-asks "is this session still allowed here?". The handshake was
# the only check, so a revoked entitlement, a deleted account or an expired session kept an
# open terminal (workstation's SSH shell) for as long as the socket lived.
_WS_RECHECK_SECONDS = 30.0
# Close code for a socket ended by that re-check: the 4403 the handshake already uses.
_WS_REVOKED = 4403


async def _pump_ws(client: WebSocket, upstream: Any,
                   still_allowed: Callable[[], bool] | None = None,
                   every: float = _WS_RECHECK_SECONDS) -> None:
    revoked = False

    async def watch() -> None:
        nonlocal revoked
        while True:
            await asyncio.sleep(every)
            try:
                ok = still_allowed()
            except Exception:  # noqa: BLE001 - a check that cannot run is not a yes
                ok = False
            if not ok:
                revoked = True
                return

    async def c2u() -> None:
        try:
            while True:
                msg = await client.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])
        except Exception:  # noqa: BLE001
            pass

    async def u2c() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client.send_bytes(bytes(frame))
                else:
                    await client.send_text(frame)
        except Exception:  # noqa: BLE001
            pass

    tasks = {asyncio.create_task(c2u()), asyncio.create_task(u2c())}
    if still_allowed is not None:
        tasks.add(asyncio.create_task(watch()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await (client.close(code=_WS_REVOKED) if revoked else client.close())
        except Exception:  # noqa: BLE001
            pass


def _ws_still_allowed(token: str | None, uid: int, app_name: str) -> bool:
    """The WS handshake's own two checks, asked again while the socket lives: the same session
    token still names the same account, and that account may still reach this app. Revoking
    either (or the session expiring) now ends the socket instead of waiting for it to close."""
    with app.state.db.session_ctx() as db:
        again = user_for_token(db, token)
        return (again is not None and again.id == uid and app_name in
                entitled_app_ids(db, again, APP_IDS, all_access=again.is_superadmin))


def _ws_origin_ok(ws: WebSocket) -> bool:
    """Anti-CSWSH (P1.2): a browser sends Origin on the WS handshake; reject any that
    isn't the platform's own page. Empty allowlist => same-origin (Origin host must
    match the Host we were reached on). A missing Origin is a non-browser client, still
    gated by the session cookie + entitlement below, so it's allowed through."""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    allow = app.state.settings.allowed_ws_origins
    if allow:
        return origin in allow
    return urlsplit(origin).netloc == ws.headers.get("host", "")


def ws_identity_headers(username: str, is_admin: str, peers: list[str],
                        rooms: list[int] | None = None) -> dict[str, str]:
    """Trusted identity for an upstream WS handshake.

    Separate from the HTTP proxy's version because the two build headers differently -- HTTP
    filters a client dict, WS constructs a fresh one -- but they must agree on the CONTRACT,
    so the peers encoding lives in one place rather than being spelled twice. The header is
    omitted entirely when there are no peers, so a rail's common path is unchanged.
    """
    out = {"x-platform-user": username, "x-platform-admin": is_admin}
    if peers:
        out["x-platform-peers"] = json.dumps(peers)
    if rooms:
        out["x-platform-rooms"] = json.dumps(rooms)
    return out


@app.websocket("/{app_name}/ws/{path:path}")
async def ws_proxy(ws: WebSocket, app_name: str, path: str) -> None:
    if app_name not in APP_IDS:
        await ws.close(code=4404)
        return
    if not _ws_origin_ok(ws):
        await ws.close(code=4403)
        return
    token = ws.cookies.get(app.state.settings.session_cookie)
    with app.state.db.session_ctx() as db:
        user = user_for_token(db, token)
        if user is None:
            await ws.close(code=4401)
            return
        if app_name not in entitled_app_ids(db, user, APP_IDS, all_access=user.is_superadmin):
            await ws.close(code=4403)
            return
        username, is_admin = user.username, ("1" if user.is_admin else "0")
        uid = user.id
        # Inside the session, like the HTTP gate, and on the same rule (PEER_TILES, ROOM_TILES).
        peers = peer_usernames(db, user) if app_name in PEER_TILES else []
        rooms = room_ids(db, user) if app_name in ROOM_TILES else []
    base = app.state.backends.get(app_name)
    if base is None:
        await ws.close(code=4404)
        return
    if _has_dot_segment(path):      # the same escape as the HTTP proxy's, out of /ws/
        await ws.close(code=4400)
        return
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    query = ws.url.query
    upstream_url = f"{ws_base}/ws/{path}" + (f"?{query}" if query else "")
    await ws.accept()
    try:
        # additional_headers is the websockets>=13 asyncio-client API. Identity is
        # set only by us here, from the session we just verified (never client-supplied).
        async with ws_connect(
            upstream_url,
            additional_headers=ws_identity_headers(username, is_admin, peers, rooms),
            max_size=None,
            open_timeout=10,
        ) as upstream:
            await _pump_ws(ws, upstream, lambda: _ws_still_allowed(token, uid, app_name))
    except Exception:  # noqa: BLE001 — connect failure or mid-stream error
        try:
            await ws.close(code=1011)
        except Exception:  # noqa: BLE001
            pass


# --- serve the unified shell SPA (mounted last so /api + proxy win) ---------


def _mount_root_assets() -> None:
    """Serve each enabled rail's declared root-origin assets (config.ROOT_ASSETS).

    Registered BEFORE the SPA catch-all, which is the whole reason this is needed: that
    catch-all matches literally everything and returns index.html, so an unrouted
    /logos/openai.svg came back as HTML with status 200. The browser then shows a
    broken-image glyph while the network tab shows a success -- the same failure the
    catch-all's own `api/` guard was added for.

    The path is forwarded UNCHANGED. The gateway deliberately learns nothing about how a rail
    maps these onto whatever it fronts; openmaic's backend re-prefixes them for the Next.js app
    it proxies, and a future rail can do something else entirely.
    """
    def _make_handler(owner: str):
        """Bind the owning rail in a CLOSURE, not a default argument.

        `async def handler(request, _owner: str = app_id)` is the obvious way to capture a loop
        variable and it is wrong here: FastAPI reads the signature and treats a defaulted scalar
        as a QUERY PARAMETER, so `/logo-horizontal.png?_owner=recipe-book` would have re-pointed
        the request at another rail -- past the entitlement gate, which already ran on the path.
        """
        async def handler(request: Request) -> Response:
            base = app.state.backends.get(owner)
            if base is None:
                raise HTTPException(status_code=404, detail=f"unknown app '{owner}'")
            return await _forward(f"{base}{request.url.path}", owner, request)
        return handler

    settings = GatewaySettings()
    for prefix, app_id in settings.root_asset_routes().items():
        route = f"{prefix}{{path:path}}" if prefix.endswith("/") else prefix
        # GET/HEAD only: these are static assets, and a rail claiming the root should not
        # thereby acquire a writable surface outside its own namespace.
        app.add_api_route(route, _make_handler(app_id), methods=["GET", "HEAD"],
                          name=f"root-assets:{app_id}:{prefix}", include_in_schema=False)


def _mount_app_remotes() -> None:
    """Serve each enabled app's built federation remote at /<app>/ (same origin as
    the shell, so no CORS). The app_access_gate middleware still authorizes these
    static requests per user; the /<app>/api/* proxy route (declared earlier) wins
    for API calls since its middle segment must be the literal 'api'."""
    for app_id, dist in GatewaySettings().resolved_app_dists().items():
        app.mount(f"/{app_id}", StaticFiles(directory=str(dist), html=True), name=app_id)


def _mount_spa() -> None:
    dist = GatewaySettings().resolved_frontend_dist()
    if dist is None:
        return
    app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")
    index = dist / "index.html"

    # GET and HEAD: a HEAD for the shell (an uptime probe of /) was a 405.
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], name="spa")
    async def spa(full_path: str, request: Request) -> FileResponse:
        # This catch-all exists for CLIENT-SIDE ROUTING: an unknown /some/deep/link must return
        # the shell so the router can handle it. But it matches literally everything, so before
        # this guard a typo'd or removed API route answered 200 text/html — a caller checking only
        # the status code sees success and then fails on the body, which is how a renamed endpoint
        # can look healthy. An API path that reached here is genuinely not a route: say so.
        if full_path.startswith("api/"):
            # Unless it is a HEAD for a route that takes other methods: FastAPI adds no HEAD to a
            # GET route, so every HEAD on /api/platform/* lands here, and "no such endpoint" would
            # be false. Starlette's 405 naming the route's methods, as before this took HEAD.
            allowed = _methods_at(f"/{full_path}") if request.method == "HEAD" else set()
            if allowed:
                raise HTTPException(status_code=405, detail="Method Not Allowed",
                                    headers={"Allow": ", ".join(sorted(allowed))})
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{full_path}")
        return FileResponse(str(index))


def _methods_at(path: str) -> set[str]:
    """Every method some route other than the SPA catch-all serves at ``path``; empty if none."""
    scope = {"type": "http", "path": path, "root_path": "", "method": "HEAD"}
    out: set[str] = set()
    for route in app.router.routes:
        if (isinstance(route, APIRoute) and route.name != "spa"
                and route.matches(scope)[0] is not Match.NONE):
            out |= route.methods
    return out


_mount_root_assets()
_mount_app_remotes()
_mount_spa()
