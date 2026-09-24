from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Broker — native on the Windows host; containers reach it via host.docker.internal.
    broker_url: str = "http://127.0.0.1:11500"
    broker_timeout: float = 300.0

    # Per-rail model roles. Defaulting to the ROLE keeps Admin → Rails authoritative.
    embed_role: str = "@embed"
    condense_role: str = "@openmaic"

    # DuckDB vector index. Kept outside the repo/OneDrive — it is large and churning.
    index_path: str = str(pathlib.Path.home() / ".course-builder-index.duckdb")

    # Optional path to the exam blueprint CSV (blueprint-domains.csv).
    # Point at ai-notes/research/blueprint-domains.csv or any CSV with
    # credential_code / domain_name / weight_percent / sub_skill columns.
    blueprint_csv: str = ""


settings = Settings()
