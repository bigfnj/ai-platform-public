"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API, never Ollama directly — the broker
owns the one-heavy-model VRAM policy and wildcard resolution. Buffered only: /v1/chat
returns a single JSON body.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from terminal_fun_app.config import settings

# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}

# Reasoning models (qwen3, deepseek-r1) wrap chain-of-thought in <think>…</think>.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


def _strip_reasoning(text: str) -> str:
    if not text:
        return text
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.replace("<think>", "").strip()


def _base() -> str:
    return settings.broker_url.rstrip("/")


def chat(messages: list[dict], *, fmt: str | dict | None = None, model: str | None = None) -> str:
    """Buffered chat. Returns the assistant message content (str)."""
    payload: dict = {"model": model or settings.llm_model, "messages": messages, "keep_alive": "10m"}
    if fmt is not None:
        payload["format"] = fmt
    try:
        resp = httpx.post(_base() + "/v1/chat", json=payload, timeout=settings.broker_timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(f"broker /v1/chat -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker unreachable: {exc}") from exc
    content = (resp.json().get("message") or {}).get("content", "") or ""
    return _strip_reasoning(content)


def chat_json(messages: list[dict]) -> dict:
    """Buffered chat with format=json; parses the content as JSON ({} on failure)."""
    content = chat(messages, fmt="json")
    try:
        return json.loads(content or "{}")
    except json.JSONDecodeError:
        return {}


def up() -> bool:
    try:
        resp = httpx.get(_base() + "/v1/status", timeout=8.0, headers=_AUTH)
        resp.raise_for_status()
        return bool(resp.json().get("ollama_reachable"))
    except httpx.HTTPError:
        return False
