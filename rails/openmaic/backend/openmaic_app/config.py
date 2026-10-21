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
    # Seconds the reverse proxy waits on the app. An app request that generates waits on the
    # shim, and the shim on the broker: a call named on the owner can wait out the Claude CLI
    # (BROKER_CLI_TIMEOUT, 300 s on the owner's box) and then a local answer. At 300 s this was
    # exactly the CLI timeout, so the proxy cut the request the moment the broker fell back.
    # 1320 is the platform's broker ceiling, the figure broker_timeout below is given by
    # compose; compose passes OPENMAIC_APP_TIMEOUT, defaulting to the same.
    app_timeout: float = 1320.0

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
    root_assets: str = "/logos/,/avatars/,/vendor/,/logo-horizontal.png,/openmaic-mark.png"

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
    # Long enough for a call the broker sends to the Claude CLI (cli_requester below) to fail
    # and fall back to the local model inside the same request: BROKER_CLI_TIMEOUT plus a local
    # answer. Compose passes OPENMAIC_BROKER_TIMEOUT=1320, the platform's broker ceiling. The
    # shim's caller is the app container, never the gateway, so its 600 s read timeout does not
    # bound this.
    broker_timeout: float = 600.0

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

    # SINGLE-USER BOXES ONLY: every generation the app makes is attributed to this account.
    # The platform USERNAME (the login name) the shim names as `requester` on its broker calls,
    # so the broker may answer them from that person's own Claude CLI subscription when the
    # @openmaic role is switched on there. The app calls the shim server-to-server with the
    # service token and no platform identity, so nothing on a call says who is waiting on it;
    # this is the box owner saying "it is always me". It is honoured only while this account is
    # the ONLY user the rail's proxy has seen within cli_lease_s (api/proxy.py `sole_user`):
    # another user in the app, or this one gone quiet, and the shim names nobody, so the calls
    # stay on the local model whatever the switch says. Blank (the default): it names nobody,
    # ever (tests/test_no_requester.py).
    cli_requester: str = ""
    # How recently the proxy must have seen cli_requester, and how long any other user's visit
    # keeps the name off. One course generation runs well inside it.
    cli_lease_s: float = 1800.0

    # --- whose platform identity is believed ------------------------------------------------
    # X-Platform-* headers count only from an address this name resolves to (api/gateway_only.py);
    # from anyone else they are dropped before any route sees them. "gateway" is the gateway's
    # compose SERVICE name, and this container shares the compose default network with it, so
    # Docker's DNS answers with the gateway's address there. A dev box whose gateway runs on the
    # host sets OPENMAIC_GATEWAY_HOST=localhost, and the rail then says it is running degraded
    # (/api/healthz `peer_check`, and the log).
    gateway_host: str = "gateway"
    # A request from an unknown peer re-resolves the name at most this often. Short enough that a
    # recreated gateway (which can come back on a new IP) is recognised within seconds; long
    # enough that a loop of forged requests costs one lookup per interval rather than one each.
    gateway_resolve_interval: float = 5.0


settings = Settings()
