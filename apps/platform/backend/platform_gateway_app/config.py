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


class GatewaySettings(PlatformSettings):
    app_name: str = "platform-gateway"

    host: str = "127.0.0.1"
    port: int = 8700

    # Built unified SPA (frontend/dist). Empty = auto-detect the sibling.
    frontend_dist: str = ""

    # Independent app backends the gateway reverse-proxies to (/<app>/api/* ->
    # that backend's /api/*). Apps register here as they come onto the platform.
    # edu-suite is the recommitted first citizen; its URL is env-overridable
    # (PLATFORM_APP_EDU_SUITE_URL) so the container can point at the host.
    app_edu_suite_url: str = "http://127.0.0.1:8800"
    # IEP Present Levels: a second instance of the edu-suite dashboard image (IEP_ONLY=1)
    # with its own library/DB/entitlement, isolating student PII from the content instance.
    app_iep_url: str = "http://127.0.0.1:8801"
    app_recipe_book_url: str = "http://127.0.0.1:8830"
    app_workstation_url: str = "http://127.0.0.1:8720"
    app_terminal_fun_url: str = "http://127.0.0.1:8730"
    app_ai_playground_url: str = "http://127.0.0.1:8850"
    app_co_worker_url: str = "http://127.0.0.1:8860"

    # Direct Ollama endpoint — used ONLY by the admin model-pool "Delete" action (ollama rm),
    # which the broker has no verb for. All inference still goes through the broker. Container
    # points this at the host via PLATFORM_OLLAMA_URL.
    ollama_url: str = "http://127.0.0.1:11434"

    # Apps that are integrated (proxied /api + served federated bundle). Others in the
    # catalog show on the rail as 'soon' but aren't reachable. Accepts a comma-separated env string
    # (PLATFORM_ENABLED_APPS=terminal-fun,recipe-book); NoDecode stops pydantic-settings from trying
    # to JSON-decode the env value first (which errors on a bare comma list), so the validator splits.
    enabled_apps: Annotated[tuple[str, ...], NoDecode] = ("edu-suite", "iep", "recipe-book", "workstation", "terminal-fun", "ai-playground")

    @field_validator("enabled_apps", mode="before")
    @classmethod
    def _parse_enabled_apps(cls, v):
        if isinstance(v, str): return tuple(x.strip() for x in v.split(",") if x.strip())
        return v

    # Built frontend remotes (module-federation), each served at /<app>/. Host-native
    # paths by default (the apps build alongside the GPU layer); env-overridable so the
    # container points at the mounted dist.
    edu_suite_dist: str = "rails/edu-suite/apps/dashboard/frontend/dist"
    iep_dist: str = "rails/edu-suite/apps/dashboard/frontend/dist-iep"
    recipe_book_dist: str = "rails/recipe-book/frontend/dist"
    workstation_dist: str = "rails/workstation/frontend/dist"
    terminal_fun_dist: str = "rails/terminal-fun/frontend/dist"
    ai_playground_dist: str = "rails/ai-playground/frontend/dist"
    co_worker_dist: str = "rails/co-worker/frontend/dist"

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
            "edu-suite": self.app_edu_suite_url.rstrip("/"),
            "iep": self.app_iep_url.rstrip("/"),
            "recipe-book": self.app_recipe_book_url.rstrip("/"),
            "workstation": self.app_workstation_url.rstrip("/"),
            "terminal-fun": self.app_terminal_fun_url.rstrip("/"),
            "ai-playground": self.app_ai_playground_url.rstrip("/"),
            "co-worker": self.app_co_worker_url.rstrip("/"),
        }
        return {name: urls[name] for name in self.enabled_apps if name in urls}

    def resolved_frontend_dist(self) -> Path | None:
        p = Path(self.frontend_dist) if self.frontend_dist else APP_ROOT / "frontend" / "dist"
        return p if (p / "index.html").exists() else None

    def resolved_app_dists(self) -> dict[str, Path]:
        """Enabled apps whose built federation remote is present on disk, keyed by app id.
        The gateway mounts each at /<app>/. A missing dist is skipped (the app just won't
        load its bundle) rather than crashing the gateway."""
        raw = {"edu-suite": self.edu_suite_dist, "iep": self.iep_dist,
               "recipe-book": self.recipe_book_dist,
               "workstation": self.workstation_dist, "terminal-fun": self.terminal_fun_dist,
               "ai-playground": self.ai_playground_dist,
               "co-worker": self.co_worker_dist}
        out: dict[str, Path] = {}
        for name in self.enabled_apps:
            p = Path(raw.get(name, ""))
            if raw.get(name) and (p / "assets" / "remoteEntry.js").exists():
                out[name] = p
        return out
