"""FastAPI application factory for the Course Builder rail."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .identity import Identity, identity
from .index_router import router as index_router
from .build_router import router as build_router
from .status_router import router as status_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Course Builder",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(identity)],
    )
    app.include_router(status_router)
    app.include_router(index_router)
    app.include_router(build_router)
    return app


app = create_app()
