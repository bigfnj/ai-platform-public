"""Uvicorn entry point.

`watch_builder.api.app:app` is the ASGI application; this module exists so the container can
run `python -m watch_builder.main` and get the configured host and port without repeating
them in the Dockerfile, where they would drift from config.py.
"""
from __future__ import annotations

import uvicorn

from watch_builder.config import settings


def main() -> None:
    uvicorn.run("watch_builder.api.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
