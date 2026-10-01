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
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from websockets.asyncio.client import connect as ws_connect
from sqlalchemy import select
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
from platform_gateway_app.config import GatewaySettings
from platform_gateway_app.rails_models import (
    IMAGE_SLOT_ROLES,
    RAIL_SLOT_ROLES,
    build_rails_view,
    is_valid_image_model,
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

# Per-IP failed-login timestamps for a simple brute-force throttle (single instance).
_login_fails: dict[str, list[float]] = {}
# Sweep lapsed buckets once the map gets this big (see the sweep in login()).
_LOGIN_FAILS_SWEEP_AT = 1024


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
    app.state.http = httpx.AsyncClient(timeout=600.0)
    db = Database(settings.db_url)
    db.create_all(Base.metadata)
    _ensure_schema(db)
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
    owner = seg if seg in APP_IDS else app.state.settings.root_asset_owner(request.url.path)
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
            request.state.platform_peers = peer_usernames(db, user)
            # Two shapes because the rails need different questions answered. edu-suite has
            # no child entity, so "whose work may I see" (peers) is the only sensible model
            # there; iep-goals hangs everything off a student, where "which rooms am I in" is
            # the better one -- a child belongs to a classroom, not to whoever typed first.
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

    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
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


@app.get("/api/platform/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "app": app.state.settings.app_name, "apps": list(app.state.backends)}


@app.get("/api/platform/status")
async def platform_status(user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        status = await app.state.broker.status()
        return {"broker_reachable": True, **status}
    except BrokerError as exc:
        return {"broker_reachable": False, "detail": str(exc), **_EMPTY_STATUS}


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


class TtsLightBody(BaseModel):
    text: str
    voice: str | None = None
    lang_code: str | None = None
    speed: float | None = None


class TranscribeBody(BaseModel):
    audio_b64: str
    suffix: str | None = None
    language: str | None = None


@app.post("/api/platform/tts_light")
async def platform_tts_light(body: TtsLightBody, user: User = Depends(require_user)) -> Any:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        return await app.state.broker.tts_light(
            text, voice=body.voice, lang_code=body.lang_code, speed=body.speed
        )
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
@app.post("/api/platform/load")
async def platform_load(body: ModelBody, admin: User = Depends(require_admin)) -> Any:
    try:
        # Default to a 30m auto-unload (not pinned) so a manually loaded model
        # doesn't camp VRAM forever — matches the tray's "Unload after" default.
        return await app.state.broker.load(body.model, keep_alive="30m")
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    if db.execute(select(User).where(User.username == uname)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"user '{uname}' already exists")
    if body.is_superadmin and not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin can grant super-admin")
    u = User(username=uname, password_hash=hash_password(body.password),
             is_admin=body.is_admin or body.is_superadmin, is_superadmin=body.is_superadmin)
    if not u.is_superadmin:  # a super-admin is all-access; explicit grants are moot
        _apply_grant(u, body.apps, _grantable(db, admin))
    db.add(u)
    db.commit()
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


class WorkspaceCreate(BaseModel):
    name: str
    members: list[str] = []          # usernames


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    members: list[str] | None = None


def _workspace_out(w: Workspace) -> dict[str, Any]:
    return {"id": w.id, "name": w.name,
            "members": sorted(m.user.username for m in w.members)}


def _manageable(db: OrmSession, actor: User) -> set[str]:
    """The usernames an actor may place in a workspace.

    The workspace mirror of _grantable(). Everything for a super-admin; for a plain admin,
    every user who is NOT a super-admin -- which is exactly "who they could already manage",
    since admin_update_user refuses a super-admin target to a non-super-admin actor.

    This exists because the four workspace routes had NO delegation rule while the user routes
    directly above them had three, and a workspace grant is STRONGER than an app grant: it
    decides whose DATA you see inside an app you can already reach. Without this rule a plain
    admin could create a workspace containing the super-admin and thereby read the
    super-admin's owner-scoped records in every rooms-aware rail, access the Users tab would
    refuse that admin outright.
    """
    rows = db.execute(select(User)).scalars().unique().all()
    return {u.username for u in rows if actor.is_superadmin or not u.is_superadmin}


def _apply_members(db: OrmSession, w: Workspace, submitted: list[str],
                   manageable: set[str]) -> None:
    """Apply a membership edit under the delegation rule.

    Two different answers for two different situations, and the asymmetry is deliberate:

      omission -> FREEZE. A member the actor cannot manage stays. Same as _apply_grant: an
        edit must not silently strip what the actor was never shown as removable.
      addition -> 403. Adding a name outside `manageable` is refused loudly, NOT dropped.
        test_unknown_username_is_rejected_not_silently_dropped already settled that question
        for this surface: a silent drop means the admin believes the aide can see the teacher
        and finds out otherwise from the teacher.
    """
    frozen = {m.user.username for m in w.members if m.user.username not in manageable}
    wanted = {u.strip() for u in submitted if u.strip()}
    refused = sorted(u for u in wanted - frozen if u not in manageable)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=f"not yours to add to a workspace: {refused}. A workspace decides whose "
                   f"records another user can read, so it is limited to the users you may "
                   f"already manage.")
    _set_members(db, w, sorted(wanted | frozen))


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
    # Deleting a room the super-admin is in is a destructive act on THEIR sharing, and the
    # same delegation rule that governs who may be added governs who may be removed wholesale.
    manageable = _manageable(db, admin)
    beyond = sorted(m.user.username for m in w.members if m.user.username not in manageable)
    if beyond:
        raise HTTPException(
            status_code=403,
            detail=f"this workspace contains user(s) you may not manage: {beyond}")
    db.delete(w)
    db.commit()
    return {"ok": True}


class RailModelBody(BaseModel):
    model: str  # a concrete installed model name, or a glob pattern to keep auto-resolution
    # Which registered broker runs it. "local" is this box; any other name is composed into the
    # stored role value as "<upstream>::<model>", the broker's delegation syntax. Defaulted, so
    # a frontend that predates delegation still posts {"model": ...} alone and means local.
    upstream: str = "local"


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
        # A 400 from the broker (bad label, duplicate, unknown scope) is the caller's fault and
        # must not read as "the broker broke".
        if "-> 400" in str(exc):
            raise HTTPException(status_code=400, detail=str(exc).split(": ", 2)[-1]) from exc
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


@app.delete("/api/platform/admin/broker/tokens/{token_id}")
async def admin_revoke_broker_token(token_id: str,
                                    admin: User = Depends(require_admin)) -> dict[str, Any]:
    if not admin.is_superadmin:
        raise HTTPException(status_code=403, detail="only a super-admin may manage broker tokens")
    try:
        return await app.state.broker.revoke_token(token_id)
    except BrokerError as exc:
        if "-> 404" in str(exc):
            raise HTTPException(status_code=404, detail=f"no such token '{token_id}'") from exc
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


async def _rails_payload(disabled: set[str] = frozenset()) -> dict[str, Any]:
    """The Rails-tab payload: each installed rail's model slots (resolved model + description)
    plus the installed generative models to choose from, PER BROKER. Resolved via the broker.
    Disabled models are dropped from the pickers.

    ``models`` is keyed by upstream name because a slot's choices depend on where it runs: point
    one off-site and the dropdown has to offer THAT box's inventory, not this card's. Every
    reported upstream gets a key -- empty when it cannot be read -- so the frontend can always
    index by the slot's own ``upstream`` without a missing-key branch.
    """
    broker = app.state.broker
    roles = (await broker.roles()).get("roles", [])
    upstreams = await _upstreams_view()
    named = [u for u in upstreams if u.get("name")]
    names = [str(u["name"]) for u in named]
    # Only a box the broker reports healthy is worth a round-trip; the rest are pre-seeded
    # empty. Fetched concurrently so N remotes cost one slow box's latency and not the sum --
    # and a remote is exactly the thing that is slow.
    live = [str(u["name"]) for u in named if u.get("healthy", True)]
    fetched = await asyncio.gather(*(_upstream_models(n, disabled) for n in live))
    models: dict[str, list[dict[str, Any]]] = {n: [] for n in names}
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
    return {"rails": rails,
            "models": models,
            "upstreams": upstreams,
            "media": media_options()}


@app.get("/api/platform/admin/rails")
async def admin_rails(admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await _rails_payload(await _disabled_set())
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc


@app.get("/api/platform/admin/upstreams")
async def admin_upstreams(admin: User = Depends(require_admin)) -> dict[str, Any]:
    """The brokers a rail slot may be delegated to, with live health. Deliberately never 502s:
    local is always a valid answer, and the panel has to render when no remote is registered."""
    return {"upstreams": await _upstreams_view()}


@app.get("/api/platform/admin/upstreams/{name}/models")
async def admin_upstream_models(name: str,
                                admin: User = Depends(require_admin)) -> dict[str, Any]:
    """One chosen upstream's inventory, so moving a slot off-site can repaint just that picker
    instead of re-reading every box. An unregistered name is a 404 rather than an empty list:
    silently offering nothing is indistinguishable from a box that is merely down."""
    known = {str(u.get("name", "")) for u in await _upstreams_view()}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"unknown upstream '{name}'; "
                                                    f"registered: {', '.join(sorted(known))}")
    return {"upstream": name, "models": await _upstream_models(name, await _disabled_set())}


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


@app.get("/api/platform/admin/rail-manager")
def admin_rail_manager(admin: User = Depends(require_admin),
                        db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    """Every rail this build knows about, with the copy needed to decide whether to run it.

    Three states, all DERIVED rather than stored, so the answer cannot go stale:
      enabled      — installed here and switched on
      disabled     — installed, switched off by an admin (reversible, one row in rail_state)
      unavailable  — in the catalog but not in this deployment's enabled_apps

    'unavailable' is the honest name for the case the UI cannot fix. Turning such a rail on means
    editing PLATFORM_ENABLED_APPS and restarting the gateway, because the static mount for
    /<id>/ is registered at import time — so the catalog reports it and says so rather than
    offering a button that would appear to work and then 404.
    """
    disabled = disabled_rail_ids(db)
    installed = set(app.state.settings.enabled_apps)
    dists = set(app.state.settings.resolved_app_dists())
    backends = set(app.state.backends)
    # How many users can currently reach each rail — the thing to check before switching one off.
    grants: dict[str, int] = {}
    for e in db.execute(select(Entitlement)).scalars().all():
        grants[e.app_id] = grants.get(e.app_id, 0) + 1

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
    if body.enabled and rid not in set(app.state.settings.enabled_apps):
        raise HTTPException(
            status_code=409,
            detail=(f"'{rid}' is not installed in this deployment. Add it to "
                    f"PLATFORM_ENABLED_APPS and restart the gateway — its /{rid}/ mount is "
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
        roles = (await broker.roles()).get("roles", [])
        status = await broker.status()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    loaded = _loaded_names(status)
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
            "modified_at": m.get("modified_at"),
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
    it keeps working (availability control, not enforcement)."""
    disabled = await _disabled_set()
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
        with contextlib.suppress(BrokerError):
            await app.state.broker.unload(body.name)
    try:
        await app.state.broker.set_disabled(sorted(disabled))
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc
    return {"name": body.name, "enabled": body.enabled}


@app.post("/api/platform/admin/models/delete")
async def admin_model_delete(body: ModelBody,
                             admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Permanently remove a model from the box (ollama rm). Refused while any rail role resolves
    to it, so a live dependency can't be yanked out. Irreversible — the UI double-confirms."""
    name = body.model.strip()
    try:
        roles = (await app.state.broker.roles()).get("roles", [])
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
        if not is_valid_image_model(model):
            raise HTTPException(status_code=400, detail=f"'{model}' is not a valid image backend")
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
        # back into the dropdown right after an Apply (the GET path already filters).
        return await _rails_payload(await _disabled_set())
    except BrokerError as exc:
        # The broker validates the role/pattern; surface a 400 for a client-fixable error.
        msg = str(exc)
        code = 400 if "-> 400" in msg else 502
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
    return await scheduler.run_now(db, app.state.http, app.state.backends, rail, task_id,
                                   getattr(app.state, "broker", None))


# --- reverse proxy to each app's independent backend ------------------------
# (the app_access_gate middleware has already authorized this app for the user)


@app.api_route(
    "/{app_name}/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(app_name: str, path: str, request: Request) -> Response:
    base = app.state.backends.get(app_name)
    if base is None:
        raise HTTPException(status_code=404, detail=f"unknown app '{app_name}'")
    return await _forward(f"{base}/api/{path}", app_name, request)


async def _forward(url: str, app_name: str, request: Request) -> Response:
    """Relay one request to a rail backend, carrying the verified identity.

    Extracted from proxy() so the root-asset routes can reuse it rather than growing a second,
    subtly different relay. Everything that makes this correct lives here -- the anti-spoof
    header strip, the peers/rooms encoding, the SSE passthrough and the response-header
    forwarding -- and a copy would drift from all four.
    """
    body = await request.body()
    # Drop hop-by-hop headers AND any client-supplied x-platform-* (anti-spoof): identity is
    # set only by us, below, from the session the access gate already verified.
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and not k.lower().startswith("x-platform-")}
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
    # Server-sent events must NOT be buffered. `http.request()` reads the whole upstream body
    # before responding, which for an SSE endpoint means the client sees nothing until the
    # generator finishes and then gets every event at once -- iep-goals' live job log rendered
    # empty for the whole run. Worse, a stage quiet for longer than the read timeout killed the
    # connection and lost the log while the job carried on.
    #
    # An EventSource always sends `Accept: text/event-stream`, so the CLIENT tells us up front.
    # That is what makes this safe to decide before the request goes out -- and it has to be
    # decided before, because the streaming branch also drops the read timeout, and doing that
    # to ordinary traffic would let a hung backend hang the gateway forever. Anything that does
    # not ask for SSE takes the original buffered path below, unchanged.
    if "text/event-stream" in request.headers.get("accept", "").lower():
        stream_req = app.state.http.build_request(
            request.method, url, params=request.query_params, content=body, headers=headers,
            timeout=httpx.Timeout(600.0, read=None),
        )
        try:
            upstream = await app.state.http.send(stream_req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502,
                                detail=f"{app_name} backend unreachable: {exc}") from exc
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={k: v for k, v in upstream.headers.items()
                     if k.lower() not in _HOP_BY_HOP
                     and k.lower() not in ("content-encoding", "content-type")},
            background=BackgroundTask(upstream.aclose),
        )
    try:
        upstream = await app.state.http.request(
            request.method, url, params=request.query_params, content=body, headers=headers
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{app_name} backend unreachable: {exc}") from exc
    # Forward the upstream response headers too — notably Content-Disposition, which
    # carries a download's filename (without this, browsers name every download
    # "download.zip"). Drop hop-by-hop + content-length/encoding (httpx already decoded
    # the body and Response recomputes length) and content-type (set via media_type).
    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in _HOP_BY_HOP and k.lower() not in ("content-encoding", "content-type")}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=resp_headers,
    )


# --- websocket reverse proxy to an app backend -----------------------------
# The HTTP proxy above can't carry a WS upgrade, and Starlette's http middleware
# (the entitlement gate) does NOT run for websocket scope — so this route
# authenticates the handshake itself, exactly like the gate, then bridges frames
# to the app's own /ws/* endpoint. Declared before the app mounts + SPA so it
# wins for /{app}/ws/*. Used by the workstation terminal.


async def _pump_ws(client: WebSocket, upstream: Any) -> None:
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

    a = asyncio.create_task(c2u())
    b = asyncio.create_task(u2c())
    try:
        await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        a.cancel()
        b.cancel()
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


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
        peers = peer_usernames(db, user)   # inside the session, like the HTTP gate
        rooms = room_ids(db, user)
    base = app.state.backends.get(app_name)
    if base is None:
        await ws.close(code=4404)
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
            await _pump_ws(ws, upstream)
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

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:  # noqa: ARG001 - client-side routing fallback
        # This catch-all exists for CLIENT-SIDE ROUTING: an unknown /some/deep/link must return
        # the shell so the router can handle it. But it matches literally everything, so before
        # this guard a typo'd or removed API route answered 200 text/html — a caller checking only
        # the status code sees success and then fails on the body, which is how a renamed endpoint
        # can look healthy. An API path that reached here is genuinely not a route: say so.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{full_path}")
        return FileResponse(str(index))


_mount_root_assets()
_mount_app_remotes()
_mount_spa()
