"""External NVIDIA NIM client for the generation toggle.

When the demo flips to 'NVIDIA NIM', generation runs on NVIDIA's hosted, OpenAI-compatible
endpoint (build.nvidia.com) instead of the local broker. Retrieval stays local either way.
The key is injected from the gitignored deploy/.env as AI_PLAYGROUND_NVIDIA_API_KEY; absent
=> the toggle is unavailable and the UI greys it out.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from openai import AsyncOpenAI

API_KEY = os.environ.get("AI_PLAYGROUND_NVIDIA_API_KEY", "")
BASE_URL = os.environ.get("AI_PLAYGROUND_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
CHAT_MODEL = os.environ.get("AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "nvidia/nemotron-mini-4b-instruct")


def available() -> bool:
    return bool(API_KEY)


def info() -> dict:
    return {"available": available(), "endpoint": BASE_URL, "chat_model": CHAT_MODEL}


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60)


async def probe() -> None:
    """Cheap auth check: a 1-token completion. Raises on failure (bad/absent key)."""
    if not available():
        raise RuntimeError("no NVIDIA API key configured")
    await _client().chat.completions.create(
        model=CHAT_MODEL, max_tokens=1,
        messages=[{"role": "user", "content": "ping"}])


async def chat_stream(messages: list[dict], *, max_tokens: int = 800) -> AsyncIterator[str]:
    stream = await _client().chat.completions.create(
        model=CHAT_MODEL, messages=messages, stream=True,
        max_tokens=max_tokens, temperature=0.2)
    async for chunk in stream:
        tok = chunk.choices[0].delta.content
        if tok:
            yield tok
