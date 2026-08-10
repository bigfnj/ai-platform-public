"""Platform API tier: a FastAPI JSON surface over the bouquet core.

The gateway proxies ``/bouquet/api/*`` here. The app runs as
``uvicorn --factory bouquet.api:create_api``.
"""

from bouquet.api.app import create_api

__all__ = ["create_api"]
