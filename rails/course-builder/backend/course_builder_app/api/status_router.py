"""Health, capabilities, and blueprint/role discovery routes."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends

from .. import broker, corpus as _corpus, builder as _builder
from ..config import settings
from .identity import Identity, identity

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=1)


def _in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_pool, fn, *args)


@router.get("/api/healthz")
async def healthz(_: Identity = Depends(identity)):
    broker_ok = await _in_thread(broker.up)
    stats = await _in_thread(_corpus.index_stats, settings.index_path)
    return {
        "broker": "ok" if broker_ok else "unreachable",
        "index": stats,
    }


@router.get("/api/capabilities")
async def capabilities(_: Identity = Depends(identity)):
    """Header chip payload. Polled every 6 s by the frontend."""
    broker_ok = await _in_thread(broker.up)
    stats = await _in_thread(_corpus.index_stats, settings.index_path)

    # Build model chip list from broker roles table.
    role_map: dict[str, dict] = {}
    try:
        for r in await _in_thread(broker.roles):
            role_map[r.get("role", "")] = r
    except broker.BrokerError:
        pass

    loaded_names: set[str] = set()
    try:
        st = await _in_thread(broker.status)
        for m in (st.get("models") or []):
            if m.get("loaded"):
                loaded_names.add(m.get("name", ""))
    except broker.BrokerError:
        pass

    def chip(slot: str, label: str, role: str) -> dict:
        r = role_map.get(role, {})
        model = r.get("model", role)
        upstream = r.get("upstream", "local")
        if not broker_ok:
            state = "missing"
        elif not model:
            state = "missing"
        elif upstream == "offsite":
            state = "loaded"  # offsite always reported as available
        elif model in loaded_names:
            state = "loaded"
        else:
            state = "cold"
        return {"slot": slot, "label": label, "model": model or role,
                "state": state, "role": role}

    return {
        "broker": "ok" if broker_ok else "unreachable",
        "models": [
            chip("embed", "Index", settings.embed_role),
            chip("condense", "Condense", settings.condense_role),
        ],
        "index": stats,
    }


@router.get("/api/blueprints")
async def blueprints(_: Identity = Depends(identity)):
    if not settings.blueprint_csv:
        return []
    return await _in_thread(_builder.load_blueprints, settings.blueprint_csv)


@router.get("/api/roles")
async def list_roles(_: Identity = Depends(identity)):
    try:
        return await _in_thread(broker.roles)
    except broker.BrokerError as exc:
        return {"error": str(exc)}
