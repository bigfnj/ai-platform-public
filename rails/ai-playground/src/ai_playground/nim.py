"""External NVIDIA NIM client for the generation toggle.

When the demo flips to 'NVIDIA NIM', generation runs on NVIDIA's hosted, OpenAI-compatible
endpoint (build.nvidia.com) instead of the local broker. Retrieval stays local either way.
The key is injected from the gitignored deploy/.env as AI_PLAYGROUND_NVIDIA_API_KEY; absent
=> the toggle is unavailable and the UI greys it out.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from openai import APIStatusError, AsyncOpenAI

API_KEY = os.environ.get("AI_PLAYGROUND_NVIDIA_API_KEY", "")
BASE_URL = os.environ.get("AI_PLAYGROUND_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Retirement history. NVIDIA retires hosted models on a schedule this repo is not told about, so
# expect this to happen again; ModelUnavailable below makes the next one say so in plain words.
#
# 1st: `nvidia/nemotron-mini-4b-instruct`, end-of-lifed 2026-08-26T09:00:00Z, still pointed at
#   until 2026-09-11. The key still authenticated and /v1/models still returned 80 entries, so
#   `available()` stayed True and the UI kept offering the toggle; generation then failed with
#   `410 Gone ... has reached its end of life`. Sixteen days of a dead demo path, invisible
#   because nothing called `probe()` unless a human pressed the button.
# 2nd: `mistralai/mistral-nemotron`, end-of-lifed 2026-09-28T08:00:00Z, found 2026-10-07 by
#   `tools/rail_smoke.py --deep` (check `ai-playground.nim_model_callable`, HTTP 502 wrapping
#   the 410). Nine days, caught by the deep check this time rather than by a user.
#
# Two things make a replacement safe, and the obvious check establishes NEITHER:
#   1. Being LISTED in /v1/models is not being ENTITLED to call it. `nemotron-nano-3-30b-a3b` is
#      listed for this account and returns 404 "Function ...: Not found for account ..." on a real
#      completion. So validate by completing, never by listing. The 2nd replacement made the
#      point harder: of 20 listed chat models tried by a real completion, 13 answered that 404,
#      including all five Mistral instruct models tried and all six nvidia/ instruct ones.
#   2. A reasoning model is not a drop-in. `nemotron-3.5-lightning-30b-a3b` works but spends its
#      budget in `reasoning_content` (107 completion tokens to say "OK"), which either leaks
#      reasoning into the demo's token stream or returns an empty answer at a small max_tokens.
#      In the 2nd round `nemotron-3-super-120b-a12b`, `muse-glimmer-30b` and `glm-5.3-flash` all
#      answered 200 with content=None: 8 of 8 tokens spent reasoning, finish_reason "length".
# And a third, smaller one: a model that answers is not a model that answers in time.
#   `llama-3.2-90b-vision-instruct` is entitled and plain-instruct but took 98 s to say "OK",
#   past the 60 s client timeout below.
# `meta/llama-3.2-11b-vision-instruct` is plain-instruct (2 tokens for "OK", no
# reasoning_content; 0.4-0.8 s in the selection run, up to 5 s on a later probe) and callable by
# this account. It takes text-only chat messages, so the streaming path below is unchanged; no
# nvidia/ model qualified, which is why the NIM toggle now names a Meta model.
# Keep this id in step with deploy/docker-compose.yml, deploy/installer/docker-compose.installer.yml
# and the commented example in deploy/.env.example; tools/tests/test_nim_model_consistency.py
# fails if they drift apart.
CHAT_MODEL = os.environ.get("AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "meta/llama-3.2-11b-vision-instruct")

# What to do about it, in the words an operator needs. Each message leads with the model id and
# the cause because the demo shows only the first 160 characters of the error body.
_FIX = ("Set AI_PLAYGROUND_NVIDIA_CHAT_MODEL in deploy/.env to a plain-instruct model this key "
        "can call, proven by a real completion (see ai_playground/nim.py), then recreate the "
        "ai-playground container.")


class ModelUnavailable(RuntimeError):
    """The key authenticated and the configured model still cannot be called.

    Raised in place of openai's APIStatusError for the two answers a model swap is made of, so
    the toggle, the WebSocket error frame and the deep smoke check all say WHAT to change. Both
    used to surface as `Error code: 410 - {...}` / `Error code: 404 - {...}`, read as "the NVIDIA
    integration is broken" when the fix is one env var.
    """


def _model_unavailable(exc: APIStatusError) -> ModelUnavailable | None:
    """The plain-words version of a model-level refusal, or None for anything else."""
    body = exc.body if isinstance(exc.body, dict) else {}
    detail = str(body.get("detail") or "").strip()
    if exc.status_code == 410:
        # NVIDIA's own detail names the end-of-life date, which is worth keeping.
        said = f" NVIDIA said: {detail}" if detail else ""
        return ModelUnavailable(
            f"NVIDIA has retired the cloud model '{CHAT_MODEL}' (410 Gone). {_FIX}{said}")
    if exc.status_code == 404 and "not found for account" in detail.lower():
        # NVIDIA's detail is NOT echoed here: it carries the account id, and this message is
        # sent to the browser. A plain 404 (a typo, a wrong base URL) answers "404 page not
        # found" with no account in it, and is left as it was.
        return ModelUnavailable(
            f"This NVIDIA key is not entitled to the cloud model '{CHAT_MODEL}' (404 Not found "
            f"for account), although /v1/models may still list it. {_FIX}")
    return None


async def _complete(client: AsyncOpenAI, **kwargs):
    """`chat.completions.create` on CHAT_MODEL, with model-level refusals made legible. Both
    callers go through here; a third that called the client directly would bring back the bare
    `Error code: 410` for that path."""
    try:
        return await client.chat.completions.create(model=CHAT_MODEL, **kwargs)
    except APIStatusError as exc:
        plain = _model_unavailable(exc)
        if plain is None:
            raise
        raise plain from exc


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
        await _complete(client, max_tokens=1, messages=[{"role": "user", "content": "ping"}])


async def chat_stream(messages: list[dict], *, max_tokens: int = 800) -> AsyncIterator[str]:
    """Stream assistant content deltas from NVIDIA's hosted endpoint.

    Nested ``async with``, mirroring broker.chat_stream, and for a sharper reason: an abandoned
    generator (the browser tab closed mid-answer) is closed at the ``yield``, and without the
    context managers neither the HTTPS response nor the client's pool was ever released — so
    NVIDIA kept generating, and BILLING, an answer with no reader left. The client is per-call,
    so leaking one leaks a connection pool with it.
    """
    async with _client() as client:
        stream = await _complete(client, messages=messages, stream=True,
                                 max_tokens=max_tokens, temperature=0.2)
        async with stream:
            async for chunk in stream:
                tok = chunk.choices[0].delta.content
                if tok:
                    yield tok
