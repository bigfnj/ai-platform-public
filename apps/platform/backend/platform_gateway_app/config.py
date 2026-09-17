"""Gateway config — extends the shared PlatformSettings.

The app-backend URLs are the registry the proxy uses to route ``/<app>/api/*``.
Everything is env-overridable (PLATFORM_ prefix); defaults match the apps' dev
ports so the gateway runs from a clean checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import NoDecode

from platform_core import PlatformSettings

# platform_gateway_app/config.py -> platform_gateway_app -> backend -> <app root>
APP_ROOT = Path(__file__).resolve().parents[2]
# apps/platform -> apps -> the ai-platform repo root. Rail dists are derived from this
# rather than hard-coded absolutes, so relocating the tree cannot silently orphan them.
REPO_ROOT = APP_ROOT.parents[1]
RAILS = REPO_ROOT / "rails"

# Root-origin asset prefixes a rail owns, mirrored from each rail.json's `root_assets`.
#
# WHY A RAIL WOULD CLAIM THE ROOT AT ALL. A rail written for this platform lives entirely under
# /<id>/ and never needs this. A rail that WRAPS a third-party app does: openmaic fronts an
# upstream Next.js app whose source carries ~124 hand-written `<img src="/logos/...">` literals.
# Next's basePath only rewrites URLs Next itself generates (next/link, next/image, the metadata
# API) — a string literal in JSX is passed through untouched, so the browser resolves it against
# the ORIGIN root and it leaves the rail's namespace entirely.
#
# WHY IT IS MIRRORED HERE RATHER THAN READ. The gateway container cannot see the manifests:
# compose mounts only each rail's built dist, so /app has no rails/ tree. Same reason
# APP_CATALOG mirrors `description`. RC028 is what keeps the copy honest.
#
# WHY IT IS DECLARED AT ALL RATHER THAN HARD-CODED IN A PROXY RULE. The origin root is an
# exhaustible shared resource with a silent failure mode — two rails claiming /avatars/ would
# not error, one would just serve the other's images. That is the same class of bug as two rails
# claiming a vite port, which RC002 already guards, so it gets the same treatment: declared once,
# checked for collisions across every rail in the tree.
#
# A trailing slash means "this directory prefix"; anything else is an exact path.
ROOT_ASSETS: dict[str, tuple[str, ...]] = {
    "openmaic": ("/logos/", "/avatars/", "/vendor/", "/logo-horizontal.png",
                 "/openmaic-mark.png"),
}

# Paths the platform itself owns; a rail may never claim these. RC028 rejects them at check
# time, and `root_asset_routes()` drops them at runtime so a bad mirror cannot shadow the shell.
RESERVED_ROOT_PREFIXES = ("/api/", "/assets/", "/ws/")


class GatewaySettings(PlatformSettings):
    app_name: str = "platform-gateway"

    host: str = "127.0.0.1"
    port: int = 8700

    # Built unified SPA (frontend/dist). Empty = auto-detect the sibling.
    frontend_dist: str = ""

    # Independent app backends the gateway reverse-proxies to (/<app>/api/* ->
    # that backend's /api/*). Apps register here as they come onto the platform.
    # (PLATFORM_APP_EDU_SUITE_URL) so the container can point at the host.
    # with its own library/DB/entitlement, isolating student PII from the content instance.
    # IEP Goals: the standalone goals→worksheets rail. Runs ALONGSIDE Present Levels
    app_recipe_book_url: str = "http://127.0.0.1:8830"
    app_workstation_url: str = "http://127.0.0.1:8720"
    app_terminal_fun_url: str = "http://127.0.0.1:8730"
    app_ai_playground_url: str = "http://127.0.0.1:8850"
    app_co_worker_url: str = "http://127.0.0.1:8890"
    app_meeting_atlas_url: str = "http://127.0.0.1:8740"
    app_smb_partner_enablement_url: str = "http://127.0.0.1:8870"
    app_gemini_cx_url: str = "http://127.0.0.1:8880"
    app_openmaic_url: str = "http://127.0.0.1:8900"

    # Direct Ollama endpoint — used ONLY by the admin model-pool "Delete" action (ollama rm),
    # which the broker has no verb for. All inference still goes through the broker. Container
    # points this at the host via PLATFORM_OLLAMA_URL.
    ollama_url: str = "http://127.0.0.1:11434"

    # Apps that are integrated (proxied /api + served federated bundle). Others in the
    # catalog show on the rail as 'soon' but aren't reachable.
    #
    # NoDecode + the validator below because this is a COMPLEX field: pydantic-settings would
    # otherwise JSON-decode the env value inside EnvSettingsSource, before any validator runs, and
    # raise SettingsError on anything that isn't a JSON array. Every shipped override writes the
    # plain comma form instead — Dockerfile.gateway.bundled (`terminal-fun,recipe-book`), the
    # installer compose (`${PLATFORM_ENABLED_APPS:-terminal-fun}`) and env.lean.example (filled by
    # install.ps1's `$enabled -join ','`) — so the lean installer's gateway died at import, before
    # uvicorn could serve, and even a single bare value failed since it is not valid JSON either.
    # The full stack never hit it only because deploy/.env sets no such line. Accept both forms.
    enabled_apps: Annotated[tuple[str, ...], NoDecode] = ("recipe-book", "workstation", "terminal-fun", "ai-playground", "co-worker", "smb-partner-enablement", "gemini-cx", "meeting-atlas", "openmaic")

    @field_validator("enabled_apps", mode="before")
    @classmethod
    def _parse_enabled_apps(cls, v: object) -> object:
        """Accept a JSON array, a comma/whitespace-separated string, or an already-built sequence."""
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s.startswith("["):
            import json
            return tuple(json.loads(s))
        return tuple(p for p in (part.strip() for part in s.replace("\n", ",").split(",")) if p)

    # Built frontend remotes (module-federation), each served at /<app>/. Host-native
    # paths by default (the apps build alongside the GPU layer); env-overridable so the
    # container points at the mounted dist.
    # These five pointed at pre-monorepo standalone directories until 2026-08-19. All of those
    # paths are gone, and resolved_app_dists() SKIPS a rail whose dist is missing rather than
    # failing — so a host-native gateway silently served no bundle for five of eleven rails
    # (measured: 6 of 11 resolved). Containers never noticed, because compose overrides every
    # one of these with PLATFORM_*_DIST.
    recipe_book_dist: str = str(RAILS / "recipe-book" / "frontend" / "dist")
    workstation_dist: str = str(RAILS / "workstation" / "frontend" / "dist")
    terminal_fun_dist: str = str(RAILS / "terminal-fun" / "frontend" / "dist")
    ai_playground_dist: str = str(RAILS / "ai-playground" / "frontend" / "dist")
    co_worker_dist: str = str(RAILS / "co-worker" / "frontend" / "dist")
    meeting_atlas_dist: str = str(RAILS / "meeting-atlas" / "frontend" / "dist")
    smb_partner_enablement_dist: str = str(RAILS / "smb-partner-enablement" / "frontend" / "dist")
    gemini_cx_dist: str = str(RAILS / "gemini-cx" / "frontend" / "dist")
    openmaic_dist: str = str(RAILS / "openmaic" / "frontend" / "dist")

    # --- auth / multi-tenant (PLATFORM_ env prefix) -------------------------
    # SQLite on a mounted volume in the container; the seam is a SQLAlchemy URL so
    # it can point at Postgres later. PLATFORM_DB_URL.
    db_url: str = "sqlite:///./platform-gateway.db"
    session_cookie: str = "platform_session"
    session_ttl_hours: int = 168  # 7 days
    # Cookie hardening — cookie_secure MUST be true once served over HTTPS/public
    # (Phase 4). SameSite=lax is the safe same-origin default.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    # WS Origin allowlist (anti-CSWSH, P1.2). Empty => same-origin only: the browser's
    # Origin host must match the Host the gateway was reached on. Set explicit origins
    # (JSON list in PLATFORM_ALLOWED_WS_ORIGINS) to pin them instead.
    allowed_ws_origins: tuple[str, ...] = ()
    # First-run admin seed. If admin_password is empty, a strong random one is
    # generated and printed to the log once, so no weak default ever ships.
    admin_user: str = "admin"
    admin_password: str = ""
    # Login throttle per client IP: max failed attempts within the rolling window.
    login_max_fails: int = 8
    login_window_seconds: int = 300

    # Central scheduler: how often the fire loop checks for due tasks (seconds).
    scheduler_tick_seconds: int = 60

    def app_backends(self) -> dict[str, str]:
        urls = {
            "recipe-book": self.app_recipe_book_url.rstrip("/"),
            "workstation": self.app_workstation_url.rstrip("/"),
            "terminal-fun": self.app_terminal_fun_url.rstrip("/"),
            "ai-playground": self.app_ai_playground_url.rstrip("/"),
            "co-worker": self.app_co_worker_url.rstrip("/"),
            "smb-partner-enablement": self.app_smb_partner_enablement_url.rstrip("/"),
            "gemini-cx": self.app_gemini_cx_url.rstrip("/"),
            "meeting-atlas": self.app_meeting_atlas_url.rstrip("/"),
            "openmaic": self.app_openmaic_url.rstrip("/"),
        }
        return {name: urls[name] for name in self.enabled_apps if name in urls}

    def root_asset_routes(self) -> dict[str, str]:
        """Root-origin prefix -> owning app id, for ENABLED apps only.

        A disabled rail's prefixes are dropped rather than 404'd, so turning a rail off also
        releases its claim on the root instead of leaving a dead reservation behind.
        """
        out: dict[str, str] = {}
        for app_id in self.enabled_apps:
            for prefix in ROOT_ASSETS.get(app_id, ()):
                if not prefix.startswith("/"):
                    continue
                if any(prefix.startswith(r) for r in RESERVED_ROOT_PREFIXES):
                    continue
                # First declaration wins, deterministically by enabled order. RC028 makes a
                # collision a build-time failure; this is only so a bad mirror degrades to one
                # rail winning rather than to whichever route happened to register last.
                out.setdefault(prefix, app_id)
        return out

    def root_asset_owner(self, path: str) -> str | None:
        """The app id owning a root-origin request path, or None.

        Longest prefix first, so an exact file always beats a directory prefix that contains it.
        """
        routes = self.root_asset_routes()
        for prefix in sorted(routes, key=len, reverse=True):
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                return routes[prefix]
        return None

    def resolved_frontend_dist(self) -> Path | None:
        p = Path(self.frontend_dist) if self.frontend_dist else APP_ROOT / "frontend" / "dist"
        return p if (p / "index.html").exists() else None

    def resolved_app_dists(self) -> dict[str, Path]:
        """Enabled apps whose built federation remote is present on disk, keyed by app id.
        The gateway mounts each at /<app>/. A missing dist is skipped (the app just won't
        load its bundle) rather than crashing the gateway."""
        raw = {"recipe-book": self.recipe_book_dist, "workstation": self.workstation_dist, "terminal-fun": self.terminal_fun_dist,
               "ai-playground": self.ai_playground_dist, "co-worker": self.co_worker_dist,
               "smb-partner-enablement": self.smb_partner_enablement_dist,
               "gemini-cx": self.gemini_cx_dist,
               "meeting-atlas": self.meeting_atlas_dist,
               "openmaic": self.openmaic_dist}
        out: dict[str, Path] = {}
        for name in self.enabled_apps:
            p = Path(raw.get(name, ""))
            if raw.get(name) and (p / "assets" / "remoteEntry.js").exists():
                out[name] = p
        return out
