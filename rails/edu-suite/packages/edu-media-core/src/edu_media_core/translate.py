"""Shared Ollama translation: JSON-mode chat + content-hash caching.

Both slide-audio and cvc-worksheets transcreate English source content into
child-friendly Mexican Spanish with qwen2.5 via Ollama, caching results by a
content hash. This module owns that mechanic. Each caller supplies its own
system prompt, user message, cache path, per-call options, and expected output
keys, so the domain-specific parts stay in the apps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import ollama

from . import cache

DEFAULT_MODEL = "qwen2.5:32b-instruct-q3_K_M"

# Back-compat re-exports: the cache mechanics moved to edu_media_core.cache (one shared,
# content-addressed store). These keep older call sites working.
content_hash = cache.content_hash


def load_cache(path: str | Path) -> dict:
    return cache.load(path)


def save_cache(path: str | Path, data: dict) -> None:
    cache.save(path, data)


def clear_cache(path: str | Path | None = None) -> None:
    """Delete the (shared, unless overridden) cache file so the next run re-queries."""
    cache.clear(path)


def cache_has(path: str | Path, key: str) -> bool:
    return key in cache.load(path)


def chat_json(system_prompt: str, user_message: str, *,
              model: str = DEFAULT_MODEL, options: dict | None = None,
              host: str | None = None) -> dict:
    """Run one JSON-mode chat turn against Ollama and return the parsed dict."""
    host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host)
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        format="json",
        options=options or {},
    )
    return json.loads(response["message"]["content"])


def translate_cached(*, cache_path: str | Path | None = None, cache_key: str | None = None,
                     system_prompt: str, user_message: str,
                     model: str = DEFAULT_MODEL, options: dict | None = None,
                     required_keys: tuple[str, ...] = (),
                     host: str | None = None) -> dict:
    """Return a cached translation if present, else query Ollama and cache it.

    Defaults to the shared suite cache (``edu_media_core.cache``) with a content-addressed
    key derived from ``(model, system_prompt, user_message)`` — so identical requests are
    reused across apps and restarts. Pass ``cache_path``/``cache_key`` to override (e.g. tests).
    Raises ValueError if any of ``required_keys`` is missing from the output.
    """
    path = cache_path or cache.translations_path()
    key = cache_key or cache.make_key(model, system_prompt, user_message)
    store = cache.load(path)
    if key in store:
        return store[key]

    result = chat_json(system_prompt, user_message,
                       model=model, options=options, host=host)
    missing = [k for k in required_keys if k not in result]
    if missing:
        raise ValueError(f"Translation output missing {missing}: {result}")

    store[key] = result
    cache.save(path, store)
    return result


def is_cached(system_prompt: str, user_message: str, *,
              model: str = DEFAULT_MODEL, cache_path: str | Path | None = None) -> bool:
    """True if this exact request is already in the (shared) cache."""
    path = cache_path or cache.translations_path()
    return cache.make_key(model, system_prompt, user_message) in cache.load(path)
