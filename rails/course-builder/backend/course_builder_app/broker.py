"""Thin broker client for the Course Builder rail.

Sync functions (embed_sync, chat_sync) are called from ThreadPoolExecutor workers
running the indexing and building pipelines — those pipelines are themselves synchronous
(DuckDB, numpy), so httpx.post is correct here.

The RC022 facade (status, models, roles, up) is used by the API status/capabilities routes.
Unlike the openmaic broker this module keeps everything synchronous; the API routes call
it via asyncio.get_event_loop().run_in_executor when they need to avoid blocking the loop.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .config import settings

_BASE = settings.broker_url.rstrip("/")
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
_TIMEOUT = settings.broker_timeout


class BrokerError(RuntimeError):
    pass


def _get(path: str, timeout: float = 30.0) -> Any:
    try:
        resp = httpx.get(_BASE + path, headers=_AUTH, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker GET {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker unreachable on GET {path}: {exc}") from exc
    return resp.json()


def embed_sync(texts: list[str], model: str) -> list[list[float]]:
    try:
        resp = httpx.post(_BASE + "/v1/embed",
                          json={"model": model, "input": texts},
                          headers=_AUTH, timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise BrokerError(f"embed failed: {exc}") from exc
    data = resp.json()
    vectors = data.get("embeddings") or []
    if len(vectors) != len(texts):
        raise BrokerError(
            f"broker returned {len(vectors)} vectors for {len(texts)} inputs")
    return vectors


def chat_sync(messages: list[dict], model: str) -> str:
    try:
        resp = httpx.post(_BASE + "/v1/chat",
                          json={"model": model, "messages": messages, "keep_alive": "30m"},
                          headers=_AUTH, timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise BrokerError(f"chat failed: {exc}") from exc
    return (resp.json().get("message") or {}).get("content", "").strip()


# --- RC022 facade ---------------------------------------------------------------

def status() -> dict:
    resp = _get("/v1/status")
    return resp if isinstance(resp, dict) else {}


def models() -> list[dict]:
    resp = _get("/v1/models")
    if isinstance(resp, dict) and isinstance(resp.get("models"), list):
        return resp["models"]
    return resp if isinstance(resp, list) else []


def roles() -> list[dict]:
    resp = _get("/v1/roles")
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return resp["roles"]
    return resp if isinstance(resp, list) else []


def up() -> bool:
    try:
        return bool(_get("/healthz", timeout=5.0).get("status") == "ok")
    except (BrokerError, AttributeError):
        return False
