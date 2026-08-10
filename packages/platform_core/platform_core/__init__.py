"""Shared core for the self-hosted AI platform.

Apps depend on this package for two things today:

- ``PlatformSettings``: a pydantic-settings base every app's config extends, so
  config comes from env (12-factor), never hardcoded hosts/ports/paths.
- ``BrokerClient``: the async client apps use to reach the GPU/Model Broker.
  Apps must never call Ollama (or any GPU backend) directly.
"""

from platform_core.broker_client import BrokerClient, BrokerError
from platform_core.config import PlatformSettings

__all__ = ["BrokerClient", "BrokerError", "PlatformSettings"]
