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
# Was `nvidia/nemotron-mini-4b-instruct`, which NVIDIA end-of-lifed on 2026-08-26T09:00:00Z and
# which this repo kept pointing at until 2026-09-11. The key still authenticated and /v1/models
# still returned 80 entries, so `available()` stayed True and the UI kept offering the toggle;
# generation then failed with `410 Gone ... has reached its end of life`. Sixteen days of a dead
# demo path, invisible because nothing calls `probe()` unless a human presses the button.
#
# Two things make a replacement safe, and the obvious check establishes NEITHER:
#   1. Being LISTED in /v1/models is not being ENTITLED to call it. `nemotron-nano-3-30b-a3b` is
#      listed for this account and returns 404 "Function ...: Not found for account ..." on a real
#      completion. So validate by completing, never by listing.
#   2. A reasoning model is not a drop-in. `nemotron-3.5-lightning-30b-a3b` works but spends its
#      budget in `reasoning_content` (107 completion tokens to say "OK"), which either leaks
#      reasoning into the demo's token stream or returns an empty answer at a small max_tokens.
# `mistralai/mistral-nemotron` is plain-instruct like the retired model (2 tokens for "OK", no
# reasoning_content) and is callable by this account, so the streaming path below is unchanged.
# Keep this id in step with deploy/docker-compose.yml and deploy/installer/docker-compose.installer.yml;
# tools/tests/test_nim_model_consistency.py fails if the three drift apart.
CHAT_MODEL = os.environ.get("AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "mistralai/mistral-nemotron")


def available() -> bool:
    return bool(API_KEY)


def info() -> dict:
    return {"available": available(), "endpoint": BASE_URL, "chat_model": CHAT_MODEL}


def _client() -> AsyncOpenAI:
    """A client per call, each of which owns an HTTPS connection pool — so every caller must
    close it. Both callers below do, with ``async with``; nothing here may drop one on the floor."""
    return AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60)


async def probe() -> None:
    """Cheap auth check: a 1-token completion. Raises on failure (bad/absent key)."""
    if not available():
        raise RuntimeError("no NVIDIA API key configured")
    async with _client() as client:
        await client.chat.completions.create(
            model=CHAT_MODEL, max_tokens=1,
            messages=[{"role": "user", "content": "ping"}])


async def chat_stream(messages: list[dict], *, max_tokens: int = 800) -> AsyncIterator[str]:
    """Stream assistant content deltas from NVIDIA's hosted endpoint.

    Nested ``async with``, mirroring broker.chat_stream, and for a sharper reason: an abandoned
    generator (the browser tab closed mid-answer) is closed at the ``yield``, and without the
    context managers neither the HTTPS response nor the client's pool was ever released — so
    NVIDIA kept generating, and BILLING, an answer with no reader left. The client is per-call,
    so leaking one leaks a connection pool with it.
    """
    async with _client() as client:
        stream = await client.chat.completions.create(
            model=CHAT_MODEL, messages=messages, stream=True,
            max_tokens=max_tokens, temperature=0.2)
        async with stream:
            async for chunk in stream:
                tok = chunk.choices[0].delta.content
                if tok:
                    yield tok
