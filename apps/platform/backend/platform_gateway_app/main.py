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
import secrets
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse
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
    user_for_token,
    verify_password,
)
from platform_gateway_app import theme as theme_mod
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
    Base, Entitlement, Schedule, SessionRow, Setting, User, UserTheme,
)
from platform_gateway_app import scheduler

_HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}

# Per-IP failed-login timestamps for a simple brute-force throttle (single instance).
_login_fails: dict[str, list[float]] = {}


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
    segment is a known app id requires a logged-in user entitled to that app."""
    parts = request.url.path.split("/")
    seg = parts[1] if len(parts) > 1 else ""  # first path segment, e.g. "edu-suite"
    if seg in APP_IDS:
        token = request.cookies.get(app.state.settings.session_cookie)
        with app.state.db.session_ctx() as db:
            user = user_for_token(db, token)
            if user is None:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            if seg not in entitled_app_ids(db, user, APP_IDS, all_access=user.is_superadmin):
                return JSONResponse({"detail": f"not authorized for '{seg}'"}, status_code=403)
            # Stash the VERIFIED identity (read while the session is open) so the proxy can
            # forward it to the app backend as trusted headers. Backends scope their own data
            # by it — e.g. recipe-book scopes each recipe to its owning platform user.
            request.state.platform_user = user.username
            request.state.platform_is_admin = bool(user.is_admin)
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


def _set_session_cookie(response: Response, token: str) -> None:
    s = app.state.settings
    response.set_cookie(
        s.session_cookie, token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True, secure=s.cookie_secure, samesite=s.cookie_samesite, path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.post("/api/platform/login")
def login(body: LoginBody, request: Request, response: Response, db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    s = app.state.settings
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _login_fails.get(ip, []) if now - t < s.login_window_seconds]
    _login_fails[ip] = recent
    if len(recent) >= s.login_max_fails:
        raise HTTPException(status_code=429, detail="too many attempts; wait a few minutes")

    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        _login_fails[ip].append(now)
        raise HTTPException(status_code=401, detail="invalid username or password")

    _login_fails.pop(ip, None)
    sess = create_session(db, user, s.session_ttl_hours)
    _set_session_cookie(response, sess.token)
    return _me(user, db)


@app.post("/api/platform/logout")
def logout(request: Request, response: Response, db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    delete_session(db, request.cookies.get(app.state.settings.session_cookie))
    response.delete_cookie(app.state.settings.session_cookie, path="/")
    return {"ok": True}


@app.get("/api/platform/me")
def me(user: User = Depends(require_user), db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    return _me(user, db)


@app.get("/api/platform/apps")
def apps_for_user(user: User = Depends(require_user), db: OrmSession = Depends(get_db)) -> dict[str, Any]:
    allowed = entitled_app_ids(db, user, APP_IDS, all_access=user.is_superadmin)
    # Only surface rails actually installed here (enabled_apps), so a lean/subset install doesn't
    # show excluded rails that would 404. Roadmap 'soon' entries still show. Full deploy = no-op.
    enabled = set(app.state.settings.enabled_apps)
    apps = [a for a in APP_CATALOG
            if a["id"] in allowed and (a["id"] in enabled or a.get("status") == "soon")]
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


# GPU control (load / unload / cancel a job) is ADMIN-ONLY: any user may VIEW status + the
# job queue, but only an admin may evict a model or cancel someone's job.
@app.post("/api/platform/load")
async def platform_load(body: ModelBody, admin: User = Depends(require_admin)) -> Any:
    try:
        # Default to a 30m auto-unload (not pinned) so a manually loaded model
        # doesn't camp VRAM forever — a sensible default for a manually loaded model.
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
    enabled = set(app.state.settings.enabled_apps)
    catalog = [a for a in APP_CATALOG if a["id"] in enabled]
    return {"users": [_user_out(u) for u in users], "catalog": catalog,
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


class RailModelBody(BaseModel):
    model: str  # a concrete installed model name, or a glob pattern to keep auto-resolution


async def _rails_payload(disabled: set[str] = frozenset()) -> dict[str, Any]:
    """The Rails-tab payload: each installed rail's model slots (resolved model + description)
    plus the list of installed generative models to choose from. Resolved via the broker.
    Disabled models are dropped from the pickers."""
    broker = app.state.broker
    roles = (await broker.roles()).get("roles", [])
    models = (await broker.models()).get("models", [])
    enabled = set(app.state.settings.enabled_apps)
    return {"rails": build_rails_view(roles, enabled),
            "models": model_options(models, disabled),
            "media": media_options()}


@app.get("/api/platform/admin/rails")
async def admin_rails(admin: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await _rails_payload(await _disabled_set())
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=f"broker unreachable: {exc}") from exc


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
    """model name -> the roles that currently resolve to it (its 'in use' footprint)."""
    use: dict[str, list[str]] = {}
    for r in roles:
        m = r.get("resolved")
        if m:
            use.setdefault(m, []).append(r.get("role"))
    return {m: sorted(rs) for m, rs in use.items()}


@app.get("/api/platform/admin/models")
async def admin_models(admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Every model installed on the box, annotated with In-Use (roles), Loaded (VRAM now) and
    Enabled (the broker's availability flag). One inventory for the whole workstation model pool."""
    broker = app.state.broker
    try:
        models = (await broker.models()).get("models", [])
        roles = (await broker.roles()).get("roles", [])
        status = await broker.status()
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=f"broker unreachable: {exc}") from exc
    loaded = _loaded_names(status)
    use = _roles_using(roles)
    out = []
    for m in models:
        name = m.get("name")
        if not name:
            continue
        out.append({
            "name": name, "class": m.get("class"), "parameter_size": m.get("parameter_size"),
            "size": m.get("size"), "vision": bool(m.get("vision")),
            "modified_at": m.get("modified_at"),
            "loaded": name in loaded,
            "in_use": bool(use.get(name)), "roles": use.get(name, []),
            "enabled": not m.get("disabled"),   # broker's disabled.json flag
        })
    out.sort(key=lambda x: (not x["in_use"], x["class"] or "", x["name"]))
    return {"models": out}


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
        raise HTTPException(status_code=502, detail=f"broker unreachable: {exc}") from exc
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
        raise HTTPException(status_code=502, detail=f"broker unreachable: {exc}") from exc
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
    # An image slot may only be set to a known media backend (sdxl-turbo / flux-schnell).
    if role in IMAGE_SLOT_ROLES and not is_valid_image_model(model):
        raise HTTPException(status_code=400, detail=f"'{model}' is not a valid image backend")
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
    return await scheduler.run_now(db, app.state.http, app.state.backends, rail, task_id)


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
    url = f"{base}/api/{path}"
    body = await request.body()
    # Drop hop-by-hop headers AND any client-supplied x-platform-* (anti-spoof): identity is
    # set only by us, below, from the session the access gate already verified.
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and not k.lower().startswith("x-platform-")}
    ident_user = getattr(request.state, "platform_user", None)
    if ident_user is not None:
        headers["x-platform-user"] = ident_user
        headers["x-platform-admin"] = "1" if getattr(request.state, "platform_is_admin", False) else "0"
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
            additional_headers={"x-platform-user": username, "x-platform-admin": is_admin},
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
        return FileResponse(str(index))


_mount_app_remotes()
_mount_spa()
