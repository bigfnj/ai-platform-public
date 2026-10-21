"""Watch Builder rail config (pydantic-settings, env_prefix WATCH_BUILDER_).

The gateway sits in front: it authenticates requests and injects x-platform-user, so this
backend is never directly reachable by a browser.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WATCH_BUILDER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "watch-builder"
    host: str = "0.0.0.0"
    port: int = 8920

    # Where saved design documents live, one JSON file per design, owner-scoped.
    data_dir: str = "/data/designs"

    # Scratch space for build trees. Deliberately NOT under data_dir: a build tree is
    # disposable and large (a seven-platform build writes ~7 MB of object files), and mixing
    # it with the documents people care about makes "safe to delete" a judgement call.
    work_dir: str = "/tmp/watch-builder"

    # The Pebble SDK root inside the image. `pebble` resolves the active SDK under here; the
    # value is the XDG data dir the tool actually uses, which is NOT ~/.pebble-sdk. Pointing a
    # volume at the wrong one makes the tool re-download and then fail on a toolchain it
    # cannot execute, and the error names arm-none-eabi-gcc rather than the mount.
    sdk_root: str = "/root/.local/share/pebble-sdk"

    # A warm seven-platform build measures ~4 s. This guards the pathological case (a cold
    # container unpacking the SDK, or a design whose generated C sends the compiler into a
    # corner), not the normal one, so it is generous rather than tight.
    build_timeout: int = 180

    # A single emulator boot plus screenshot measures ~6 s, and sdk.render() runs platforms
    # serially, so a seven-platform render is the slowest thing this rail does. POST /api/build
    # takes an optional `platforms` list to narrow it; the frontend sends none, so it renders
    # every platform the design targets (emery alone by default). This bounds EACH SDK call in a
    # render (the install, the screenshot, the raw screenshot), not the render as a whole.
    render_timeout: int = 120

    # Broker connection for the two AI features. WATCH_BUILDER_BROKER_URL overrides.
    broker_url: str = "http://host.docker.internal:11500"

    # The broker's control-plane token, read from the UNPREFIXED BROKER_AUTH_TOKEN because it
    # is one platform-wide shared secret rather than a per-rail setting. The prefixed spelling
    # is listed first so a rail-specific override still wins, and it has to be listed at all
    # because validation_alias bypasses env_prefix. Enforced by RC005 and RC014.
    broker_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WATCH_BUILDER_BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN"
        ),
    )

    # The same token as a FILE PATH, and the form that wins when both are set. Keeps the
    # shared secret out of `docker inspect`, crash dumps and /proc/<pid>/environ.
    broker_auth_token_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WATCH_BUILDER_BROKER_AUTH_TOKEN_FILE", "BROKER_AUTH_TOKEN_FILE"
        ),
    )

    @model_validator(mode="after")
    def _resolve_broker_token_file(self) -> "Settings":
        """Collapse the two spellings into `broker_auth_token` so every caller reads one field.

        A configured-but-unreadable file resolves to "" rather than falling back to the value
        form. The fallback would leave a deployment that believes it has moved off the
        environment still reading the secret from it, and would hide a typo'd path behind a
        rail that looks healthy. "" sends no header and earns the broker's visible 401.
        """
        path = (self.broker_auth_token_file or "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    self.broker_auth_token = fh.read().strip()
            except OSError:
                self.broker_auth_token = ""
        return self

    # The design model, as a broker reference. Defaults to this rail's own @role so that
    # Admin -> Rails stays authoritative: repointing the role there changes what this rail
    # uses with no restart, because roles.json is hot-read. Pinning a concrete name here would
    # take the rail out of Admin's control, which is how four dead model names survived the
    # 2026-08-04 consolidation.
    designer_model: str = "@watch-builder-designer"

    # Token budget for one design document. A full document with a dozen elements is well
    # under 2k tokens; the ceiling exists so a model that starts narrating cannot hold the
    # GPU indefinitely.
    designer_max_tokens: int = 3000

    # How many variants the explorer asks for in one pass. Each one is its own generation on
    # the designer model (plus a repair call when a reply is rejected) and nothing is built or
    # rendered, so this is the knob that decides how long "explore" holds the GPU, or, for a
    # viewer the broker answers from Claude, how many calls it spends on their subscription.
    # Nine fills a 3x3 grid, which is as many as anyone compares at once.
    variation_count: int = 9

    # Hard ceiling on what a caller may request, independent of the default above. A request
    # for 200 variants is at least 200 generations; refusing is kinder than queueing.
    variation_max: int = 16


settings = Settings()
