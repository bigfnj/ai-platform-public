"""Co-Worker rail config (pydantic-settings, env_prefix CO_WORKER_).

The gateway sits in front: it authenticates requests and injects x-platform-user,
so this backend is never directly reachable by a browser.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CO_WORKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "co-worker"
    host: str = "0.0.0.0"
    port: int = 8860

    # Where the co-work harvest process drops JSON files.
    # In the container this is the mount point; on the host it's wherever you
    # bind-mount or copy files. Set CO_WORKER_INBOX_DIR to override.
    inbox_dir: str = "/data/inbox"

    # Broker connection for executive brief synthesis.
    # CO_WORKER_BROKER_URL / CO_WORKER_BROKER_AUTH_TOKEN override at runtime.
    broker_url: str = "http://host.docker.internal:11500"
    broker_auth_token: str = ""

    # The synthesis model, as a broker reference. Defaults to this rail's own @role so that
    # Admin -> Rails is authoritative: repointing the role there changes what this rail uses,
    # with no restart (roles.json is hot-read). A concrete name or a glob also works — the
    # broker resolves all three — but pinning one here takes the rail OUT of Admin's control,
    # which is how terminal-fun and recipe-book ended up silently ignoring the panel.
    synthesis_model: str = "@co-worker-synthesis"

    # The user whose inbox is being harvested — used to filter self-authored items so the
    # model never generates "Reply to [yourself]" attention items.
    # Set CO_WORKER_USER_NAME (and optionally CO_WORKER_USER_EMAIL) in your .env file.
    user_name: str = ""
    user_email: str = ""

    # Synthesize-on-staleness: auto-trigger a synthesis pass when the brief is older than
    # the current inbox. Set CO_WORKER_AUTO_SYNTHESIZE=false to disable entirely.
    auto_synthesize: bool = True
    # Minimum seconds between auto-triggered attempts (not applied to manual clicks).
    auto_synthesize_min_interval_s: int = 900
    # Tolerance for clock skew / filesystem timestamp granularity on the Windows bind mount.
    auto_synthesize_mtime_epsilon_s: float = 2.0


settings = Settings()
