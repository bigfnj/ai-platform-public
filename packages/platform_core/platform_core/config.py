"""Base settings every platform app extends.

Strict 12-factor: no hardcoded host/port/path. Everything comes from the
environment (or a local .env during development). Apps subclass this and add
their own fields; the shared fields (how to reach the broker) live here so an
app finds the broker by config, not by a baked-in ``localhost``.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    """Shared config base for platform apps."""

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # How this app reaches the GPU/Model Broker. Apps talk to the broker, never
    # to Ollama/XTTS/SDXL directly.
    broker_url: str = "http://127.0.0.1:11500"

    # Human-facing name of the app, used in logs and the shared shell.
    app_name: str = "platform-app"
