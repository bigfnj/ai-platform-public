"""Backend config (pydantic-settings). Self-contained — this rail does no model
work, so it does not depend on platform_core or the broker. The gateway sits in
front: it authenticates the WS handshake + entitlement and injects x-platform-user,
so this backend is never directly reachable by a browser. Those headers are honoured
only from the gateway's own address (`gateway_host` below, main.py's GatewayOnlyIdentity),
because a player's shell in this container can reach this backend too."""

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
    # How long /api/chat waits on the broker. It must outlast the broker's Claude CLI timeout
    # (BROKER_CLI_TIMEOUT: 180 by default, often raised, e.g. to 300) PLUS the local answer the
    # broker falls back to when the CLI fails or hangs; at 120 s an owner's hung CLI call
    # reached them as "unavailable" instead of as gemma's answer. 480 = a 300 s CLI timeout
    # plus 180 s for a cold local load and a reply, and it stays under the gateway's 600 s
    # read timeout on the proxied request, so this rail's own "unavailable" line is what a
    # viewer sees if even that is exceeded, not a gateway error.
    broker_timeout: float = 480.0

    # --- who may assert a platform identity ------------------------------------
    # X-Platform-* headers are honoured only from an address this name resolves to (main.py,
    # GatewayOnlyIdentity). "gateway" is the gateway's compose SERVICE name: this container
    # shares exactly one network with it, so Docker's DNS answers with the gateway's address on
    # that network. A dev box whose gateway runs on the host sets TERMINAL_FUN_GATEWAY_HOST=
    # localhost, and the rail then says it is running degraded (healthz, and the log).
    gateway_host: str = "gateway"
    # A request from an unknown peer re-resolves the name at most this often. Short enough that a
    # recreated gateway (which can come back on a new IP) is recognised within seconds; long
    # enough that a loop of forged requests costs one lookup per interval rather than one each.
    gateway_resolve_interval: float = 5.0


settings = Settings()
