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
    host: str = "0.0.0.0"
    port: int = 8900

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
    llm_base_url: str = ""
    llm_api_key: str = ""

    # Sampling defaults for the shim. OpenMAIC sends its own values per request; these only
    # fill the gaps. keep_alive holds the model resident between the many small calls a single
    # course generation makes — without it each slide pays a fresh load.
    llm_keep_alive: str = "30m"


settings = Settings()
