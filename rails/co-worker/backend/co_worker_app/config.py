"""Co-Worker rail config (pydantic-settings, env_prefix CO_WORKER_).

The gateway sits in front: it authenticates requests and injects x-platform-user,
so this backend is never directly reachable by a browser.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
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

    # The harvest task prompts, bind-mounted read-only at /data/prompts by compose
    # (CO_WORKER_PROMPTS_MOUNT). Wired ahead of the API that will serve them — see 6a27eab.
    # Declared here so the injected CO_WORKER_PROMPTS_DIR is a real setting rather than
    # something extra="ignore" silently discards: an env var the container sets and the
    # config drops looks identical to one that works, which is the worse of the two failures.
    prompts_dir: str = "/data/prompts"

    # Broker connection for executive brief synthesis. CO_WORKER_BROKER_URL overrides.
    broker_url: str = "http://host.docker.internal:11500"

    # The broker's control-plane token. Read from the UNPREFIXED BROKER_AUTH_TOKEN, because
    # that is one platform-wide shared secret rather than a per-rail setting: compose hands
    # every rail the same `BROKER_AUTH_TOKEN: ${BROKER_AUTH_TOKEN:-}`, and every other rail
    # reads it as os.environ["BROKER_AUTH_TOKEN"].
    #
    # This field used to rely on env_prefix, so it resolved to CO_WORKER_BROKER_AUTH_TOKEN and
    # nothing else. That made the rail work under ONE of the two compose files and not the
    # other: deploy/docker-compose.yml passes the unprefixed name (as it does for all nine
    # services), while deploy/installer/docker-compose.installer.yml had been bent to spell it
    # CO_WORKER_BROKER_AUTH_TOKEN specifically to satisfy this field. So the same rail got a
    # token on the installer path and silently got none on the main path — and the token is
    # empty in the current deployment, so neither case complained. The moment one is configured,
    # the main-compose path 401s on the chips and every synthesis pass with nothing to explain
    # why. Accepting both spellings is what makes the rail correct under either file.
    #
    # validation_alias BYPASSES env_prefix, so the prefixed spelling has to be listed
    # explicitly. It is listed FIRST so a rail-specific override still wins over the
    # platform-wide token. Enforced by tools/rail_conformance.py RC005.
    broker_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CO_WORKER_BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN"
        ),
    )

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
