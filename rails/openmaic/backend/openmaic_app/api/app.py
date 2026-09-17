"""OpenMAIC rail — FastAPI backend.

Routes:
  GET  /api/healthz           liveness (+ whether the broker and the app container answer)
  GET  /api/capabilities      the model-chip payload the header polls every 6s
  ANY  /api/app/{path}        reverse proxy to the OpenMAIC application container
  ANY  /api/llm/v1/*          OpenAI-compatible shim over the broker (mounted sub-app)

This rail owns no course data of its own. It exists to give OpenMAIC — an upstream Next.js
application that cannot be a federated remote — a contract-shaped presence on the platform: one
catalog tile, one identity gate, one set of model chips, and one place where ``@role`` becomes a
real model.

Identity is required APP-WIDE rather than per route, so a route added later cannot be left
un-gated by omission. Docs go with it: FastAPI's /docs and /openapi.json bypass app-level
dependencies, so leaving them on would publish the route list to an un-gated caller.

The one deliberate exception is the /api/llm mount — see api/llm.py for why its caller presents
a service token instead of a platform identity, and why being a mount rather than a router is
load-bearing for that.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from starlette.concurrency import run_in_threadpool

from .. import broker, modelstate
from ..config import settings
from .identity import Identity, identity
from .llm import shim
from .proxy import app_reachable, close_client, router as proxy_router

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("openmaic")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    # The proxy holds one long-lived AsyncClient with its own connection pool. Without this the
    # sockets survive shutdown and a restart loop accumulates them.
    await close_client()


app = FastAPI(title="OpenMAIC", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(identity)], lifespan=_lifespan)

app.include_router(proxy_router)

# Mounted, not included: the shim answers a sibling container that has no platform identity, so
# it carries its own credential check. A mount also would not inherit the app-level dependency
# even if we wanted it to, which is exactly the trap worth being explicit about.
app.mount("/api/llm", shim)


@app.get("/api/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness. Note this route IS gated: a 401 from it still proves the process is up, which
    is all a container healthcheck needs."""
    up = await run_in_threadpool(broker.up)
    return {"ok": True, "app": settings.app_name,
            "broker": up, "openmaic_app": await app_reachable()}


@app.get("/api/capabilities")
async def capabilities(_ident: Identity = Depends(identity)) -> dict[str, Any]:
    """The header's chip payload, in the platform's standard envelope.

    When OPENMAIC_LLM_BASE_URL is set the reasoning slot is served by an external endpoint, not
    by the broker. Reporting broker residency for it then would be a lie of exactly the kind the
    four-state contract exists to prevent, so the slot is reported as an override instead.
    """
    override = settings.llm_base_url.strip()
    specs = [] if override else [("reasoning", "LLM", settings.llm_model)]
    specs.append(("embed", "Retrieval", settings.embed_model))

    # modelstate.resolve() makes three SYNCHRONOUS broker calls. Called straight from an async
    # route it parks the event loop for as long as they take, and this container runs one worker
    # by design — so a broker that black-holes would stall the reverse proxy and any in-flight
    # generation too, making the rail look dead when only the GPU layer is sick.
    state = await run_in_threadpool(modelstate.resolve, specs)
    if override:
        # 'cold' rather than 'loaded': the endpoint is configured and presumed reachable, but
        # this rail has no visibility into whether a model is resident on the far side.
        state["models"].insert(0, {
            "slot": "reasoning", "label": "LLM", "role": "external",
            "model": _host_of(override), "state": "cold",
        })
    state["app"] = {"reachable": await app_reachable(), "url": "/openmaic/api/app/"}
    return state


def _host_of(url: str) -> str:
    """Host:port of a configured endpoint, for display on a chip. Never the full URL — it can
    carry a key in the query string on some providers."""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0] or url
