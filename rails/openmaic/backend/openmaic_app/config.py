"""Backend config (pydantic-settings).

This rail is a WRAPPER. The application it fronts — OpenMAIC, an upstream Next.js app — runs in
its own container and is reached over the compose network at `app_url`. Everything here is
either about finding that container or about talking to the broker on its behalf.

Note what is deliberately NOT here: the broker auth token. It is one platform-wide shared secret
read as the unprefixed ``BROKER_AUTH_TOKEN`` in broker.py, not a field on this class — a
pydantic field named ``broker_auth_token`` under ``env_prefix="OPENMAIC_"`` would resolve to
OPENMAIC_BROKER_AUTH_TOKEN, which nothing in deploy/ ever sets, and the rail would send no
Authorization header at all the day the broker starts enforcing one.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENMAIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "openmaic"

    # The OpenMAIC Next.js container on the compose network. The rail reverse-proxies it under
    # /openmaic/api/app/ so the iframe is same-origin and the gateway's identity gate stays in
    # front of it; see api/proxy.py for why same-origin is not merely tidier.
    app_url: str = "http://openmaic-app:3000"
    app_timeout: float = 300.0

    # The PUBLIC path the app is served at, and the basePath its image was built with. The two
    # must be the same string: Next.js with a basePath serves its pages AT that prefix, so the
    # proxy has to forward the full public path rather than the tail the gateway hands it. Get
    # this wrong and every request 404s from Next itself, which looks like a missing app rather
    # than a mismatched prefix.
    public_prefix: str = "/openmaic/api/app"

    # The origin-ROOT paths this rail serves, mirroring rail.json's `root_assets`. The gateway
    # only forwards these, so a catch-all that proxied everything would behave identically in
    # production — but NOT standalone, where there is no gateway deciding what arrives, and the
    # rail would forward its own /openapi.json and /docs to the app it fronts instead of 404ing.
    # Comma-separated so it stays a single env override. Trailing slash = directory prefix.
    root_assets: str = "/logos/,/avatars/,/logo-horizontal.png,/openmaic-mark.png"

    def root_asset_prefixes(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.root_assets.split(",") if p.strip())

    def is_root_asset(self, path: str) -> bool:
        """Whether a root-origin request path is one this rail declared."""
        p = "/" + path.lstrip("/")
        return any(p == pre or (pre.endswith("/") and p.startswith(pre))
                   for pre in self.root_asset_prefixes())

    # The broker is native on the Windows host; from a container reach it via
    # host.docker.internal (compose supplies the extra_hosts entry that makes that resolve).
    broker_url: str = "http://127.0.0.1:11500"
    broker_timeout: float = 300.0

    # Per-rail @roles, expanded by the broker from roles.json. Defaulting to the ROLE rather
    # than a model name is what keeps Admin -> Rails authoritative in standalone dev too: a
    # concrete pin here would silently ignore whatever an operator picks in that panel.
    llm_model: str = "@openmaic"
    embed_model: str = "@embed"

    # Escape hatch: an external OpenAI-compatible endpoint (a larger-VRAM host on the LAN, or a
    # hosted provider) to use INSTEAD of the local broker shim. When set, OpenMAIC is pointed
    # straight at it and the reasoning chip reports the override rather than broker residency,
    # because the broker is not serving that slot and claiming otherwise would be a lie.
    # Consumed by COMPOSE, which hands it to openmaic-app as OLLAMA_BASE_URL, and read here
    # only so the chip can say the slot is served elsewhere. Setting it without also setting
    # OPENMAIC_LLM_API_KEY sends an unauthenticated request to the far side.
    llm_base_url: str = ""

    # Sampling defaults for the shim. OpenMAIC sends its own values per request; these only
    # fill the gaps. keep_alive holds the model resident between the many small calls a single
    # course generation makes — without it each slide pays a fresh load.
    llm_keep_alive: str = "30m"


settings = Settings()
