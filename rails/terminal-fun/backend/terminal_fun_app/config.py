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
    # This rail's broker ROLE, so Admin -> Rails stays authoritative: repointing
    # @terminal-fun there changes what this rail uses with no restart (roles.json is
    # hot-read). Override with TERMINAL_FUN_LLM_MODEL, which also accepts a concrete name
    # or a size-scoped glob (gemma4*:12b) — modelstate resolves all three.
    #
    # This default was the concrete pin "gemma3:12b". Compose overrides it with @terminal-fun,
    # so the container obeyed the panel, but standalone dev silently ignored it — the same
    # pinned-default bug that was reported closed after only the compose side was fixed.
    # Conformance RC013 checks the in-code default for exactly that reason.
    llm_model: str = "@terminal-fun"
    broker_timeout: float = 120.0


settings = Settings()
