"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API (never Ollama directly). The
broker owns the one-heavy-model VRAM policy and wildcard model resolution, so this
app just POSTs Ollama-shaped requests to it.

Buffered only, on purpose: the broker's ``/v1/chat`` returns a single JSON body
and the platform gateway proxy buffers upstream responses, so token streaming to
the browser is not achievable when hosted. The UI shows a working state, then
renders the full result. Base URL + default model are env-overridable so the same
code runs standalone (broker on localhost) and in the container
(``host.docker.internal``).

``embed`` (semantic search) and ``generate_icon`` (per-recipe SDXL clipart) are
added in P1 once the broker's embed / image endpoints are pinned down.
"""
from __future__ import annotations

import json
import os
import re

import httpx

BROKER_URL = os.environ.get("RECIPE_BOOK_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}  # broker control-plane token (if set)
# Size-scoped wildcard by default; the broker resolves it to the newest matching
# installed model. A plain name (e.g. "llama3.1:8b") also works and skips resolution.
DEFAULT_MODEL = os.environ.get("RECIPE_BOOK_LLM_MODEL", "llama3.1:8b")
# Embedding model for semantic search (the broker resolves it to an installed model).
EMBED_MODEL = os.environ.get("RECIPE_BOOK_EMBED_MODEL", "bge-m3")
# The recipe/cocktail assistant uses a stronger general model than the default:
# gemma3:27b has strong culinary knowledge + prose and is NOT a reasoning model
# (no <think> latency). Env-overridable; the broker resolves the size-scoped glob.
ASSISTANT_MODEL = os.environ.get("RECIPE_BOOK_ASSISTANT_MODEL", "gemma3*:27b")
# Multimodal model for recipe-import vision (reads photos / scanned pages). gemma3:27b
# is multimodal AND the assistant, so reusing it avoids a second heavy VRAM load.
VISION_MODEL = os.environ.get("RECIPE_BOOK_VISION_MODEL", "gemma3*:27b")
# Image backend for per-recipe clipart icons. "flux-schnell" (nf4 FLUX.1-schnell) has
# far stronger prompt adherence than the old "sdxl-turbo" for a single clean object;
# the broker's media worker resolves the name to the loaded pipeline.
ICON_MODEL = os.environ.get("RECIPE_BOOK_ICON_MODEL", "flux-schnell")

# Reasoning models (qwen3, deepseek-r1) wrap their chain-of-thought in <think>…</think>.
# We render the assistant content directly, so strip those traces before returning.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

# A cold heavy-model load can take a while; keep a generous default.
DEFAULT_TIMEOUT = float(os.environ.get("RECIPE_BOOK_BROKER_TIMEOUT", "600"))


def _strip_reasoning(text: str) -> str:
    if not text:
        return text
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # unclosed/opening trace left dangling
        text = text.split("</think>")[-1]
    return text.replace("<think>", "").strip()


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


def _post(path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    try:
        resp = httpx.post(BROKER_URL + path, json=payload, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker POST {path} -> {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker POST {path} unreachable: {exc}") from exc
    return resp.json()


def _get(path: str, timeout: float = 30.0) -> dict:
    try:
        resp = httpx.get(BROKER_URL + path, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker GET {path} -> {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker GET {path} unreachable: {exc}") from exc
    return resp.json()


def chat(
    model: str | None,
    messages: list[dict],
    *,
    options: dict | None = None,
    fmt: str | dict | None = None,
    keep_alive: str | int = "10m",
) -> str:
    """Buffered chat. Returns the assistant message content (str)."""
    payload: dict = {"model": model or DEFAULT_MODEL, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    resp = _post("/v1/chat", payload)
    content = (resp.get("message") or {}).get("content", "") or ""
    return _strip_reasoning(content)


def chat_json(
    model: str | None,
    messages: list[dict],
    *,
    options: dict | None = None,
    keep_alive: str | int = "10m",
) -> dict:
    """Buffered chat with ``format=json``; parses the content as JSON."""
    content = chat(model, messages, options=options, fmt="json", keep_alive=keep_alive)
    try:
        return json.loads(content or "{}")
    except json.JSONDecodeError:
        return {}


def models() -> dict:
    """Installed models, shaped for the UI picker."""
    try:
        resp = _get("/v1/models")
    except BrokerError as exc:
        return {"broker_up": False, "models": [], "error": str(exc)}
    out = []
    for m in resp.get("models", []):
        out.append({
            "name": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "params": m.get("parameter_size"),
            "class": m.get("class"),
        })
    return {"broker_up": True, "models": out}


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth)."""
    return _get("/v1/status")


def up() -> bool:
    try:
        s = _get("/v1/status")
        return bool(s.get("ollama_reachable"))
    except BrokerError:
        return False


def generate_images(prompts: list[str], *, negative_prompt: str = "", steps: int = 4,
                    size: int = 512, model: str | None = None,
                    timeout: float | None = None) -> list[str]:
    """Batch text->image via the broker's image worker (POST /v1/image). Returns a list
    of base64 PNG strings aligned with ``prompts`` (empty entries on partial failure).
    ONE call loads the model once for the whole batch — cheaper than per-image calls.
    ``model`` picks the backend (``sdxl-turbo`` | ``flux-schnell``); defaults to
    ``RECIPE_BOOK_ICON_MODEL`` (:data:`ICON_MODEL`)."""
    resp = _post("/v1/image",
                 {"prompts": prompts, "negative_prompt": negative_prompt,
                  "steps": steps, "size": size, "model": model or ICON_MODEL},
                 timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
    return resp.get("images") or []


def embed(text: str | list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed a string or list of strings via the broker (/v1/embed). Returns a list
    of vectors aligned with the input, tolerant of the couple of response shapes."""
    resp = _post("/v1/embed", {"model": model or EMBED_MODEL, "input": text})
    if isinstance(resp.get("embeddings"), list):
        return resp["embeddings"]
    if isinstance(resp.get("embedding"), list):
        return [resp["embedding"]]
    data = resp.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and "embedding" in data[0]:
        return [d["embedding"] for d in data]
    raise BrokerError("embed response had no vectors")
