"""Backend config (pydantic-settings). Self-contained — this rail does no model
work, so it does not depend on platform_core or the broker. The gateway sits in
front: it authenticates the WS handshake + entitlement and injects x-platform-user,
so this backend is never directly reachable by a browser."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TERMINAL_FUN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "terminal-fun"
    host: str = "0.0.0.0"
    port: int = 8730

    term_type: str = "xterm-256color"

    # Static web toys (graphical, non-PTY toys served as a sandboxed iframe). Baked into
    # the image under /opt/fun/webtoys/<id>/index.html; served at /api/webtoys/. Overridable
    # for local dev (TERMINAL_FUN_WEBTOYS_DIR) — main.py falls back to the repo rootfs copy.
    webtoys_dir: str = "/opt/fun/webtoys"

    # Absolute cap on any single session (seconds). Always enforced.
    max_secs: int = 2 * 3600
    # Per-user concurrent-session cap (a fun rail shouldn't let one person open 50
    # aquariums). Best-effort, single-process.
    max_sessions_per_user: int = 4

    # --- save/resume (NetHack + Crawl) -----------------------------------------
    # Per-owner game saves persist here (a mounted volume in prod). NetHack's shared
    # system save dir is where the Debian package writes; we namespace by player name.
    data_dir: str = "/data"
    nethack_save_dir: str = "/var/games/nethack/save"

    # --- AI assistant (via the platform broker) --------------------------------
    # The broker is native on the host; from the container reach it via host.docker.internal.
    broker_url: str = "http://127.0.0.1:11500"
    # The per-rail @role, expanded by the broker via roles.json — strong at the structured
    # JSON tuning + how-to answers. Defaulting to the ROLE rather than a model name keeps
    # Admin -> Rails authoritative in standalone dev too; the old "gemma3:12b" pin was
    # retired in the 2026-08-04 gemma3 -> gemma4 consolidation. Repoint the role, or set
    # TERMINAL_FUN_LLM_MODEL for a one-off override.
    llm_model: str = "@terminal-fun"
    broker_timeout: float = 120.0


settings = Settings()
