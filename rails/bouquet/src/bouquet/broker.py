"""Thin client to the platform GPU/Model Broker.

All model work goes through the broker's HTTP API (never Ollama directly). The
broker owns the one-heavy-model VRAM policy, ``@role`` resolution, and wildcard
model resolution, so this app just POSTs Ollama-shaped chat requests to it.

Buffered only, on purpose: the broker's ``/v1/chat`` returns a single JSON body
and the platform gateway proxy buffers upstream responses, so streaming to the
browser is not achievable when hosted. The UI shows a working state, then renders
the full result.

Vision: Ollama's ``/api/chat`` reads a per-message ``images`` list of base64 PNG/
JPEG, which the broker passes through to a vision-capable model (``@vision`` →
gemma3:27b). Identifying the flowers is a chat call with the photo attached.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from bouquet import config

# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = os.environ.get("BROKER_AUTH_TOKEN", "").strip()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable."""


# Reasoning models (qwen3, deepseek-r1) wrap their chain-of-thought in <think>…</think>.
# We render the assistant content directly, so strip those traces if a role is repointed
# at a reasoner. @chat/@vision don't emit them, so this is a no-op in the default setup.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    if not text:
        return text
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # unclosed/opening trace left dangling
        text = text.split("</think>")[-1]
    return text.replace("<think>", "").strip()


def _post(path: str, payload: dict, timeout: float | None = None) -> dict:
    try:
        resp = httpx.post(config.BROKER_URL + path, json=payload, headers=_AUTH,
                          timeout=timeout if timeout is not None else config.BROKER_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker POST {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker POST {path} unreachable: {exc}") from exc
    return resp.json()


def _get(path: str, timeout: float = 30.0) -> dict:
    try:
        resp = httpx.get(config.BROKER_URL + path, timeout=timeout, headers=_AUTH)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BrokerError(
            f"broker GET {path} -> {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BrokerError(f"broker GET {path} unreachable: {exc}") from exc
    return resp.json()


def chat(
    model: str,
    messages: list[dict],
    *,
    options: dict | None = None,
    fmt: str | dict | None = None,
    keep_alive: str | int = "10m",
) -> str:
    """Buffered chat. Returns the assistant message content (str).

    ``messages`` may carry an ``images`` list (base64) on a message for vision.
    ``fmt`` is Ollama's ``format`` — ``"json"`` or a JSON Schema dict for
    structured output.
    """
    payload: dict = {"model": model, "messages": messages, "keep_alive": keep_alive}
    if options is not None:
        payload["options"] = options
    if fmt is not None:
        payload["format"] = fmt
    resp = _post("/v1/chat", payload)
    content = (resp.get("message") or {}).get("content", "") or ""
    return _strip_reasoning(content)


def _parse_json_loose(text: str) -> dict:
    """Parse a JSON object out of model output, tolerating a ```json fence or
    surrounding prose (some models wrap the object). Returns {} on failure."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip a Markdown code fence, or grab the outermost {...}.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}
    return {}


def chat_json(
    model: str,
    messages: list[dict],
    *,
    options: dict | None = None,
    keep_alive: str | int = "10m",
) -> dict:
    """Chat in Ollama's loose JSON mode (``format="json"``), robustly parsed.

    Deliberately NOT strict JSON-Schema (``format=<schema>``): gemma3 (@vision)
    returns EMPTY content when a schema is combined with an image, but produces
    correct JSON under loose ``"json"`` mode. The desired shape is described in the
    prompt instead. Returns the parsed object, or {} if unparseable."""
    content = chat(model, messages, options=options, fmt="json", keep_alive=keep_alive)
    return _parse_json_loose(content)


def embed_image(image_b64: str) -> list[float]:
    """One image's SigLIP embedding vector via the broker (CPU-side, ungated — never
    evicts the resident vision model). Used for retrieval-grounding."""
    resp = _post("/v1/embed_image", {"images": [image_b64]})
    embs = resp.get("embeddings") or []
    if not embs:
        raise BrokerError("embed_image returned no embedding")
    return embs[0]


def status() -> dict:
    """Broker/GPU status passthrough (loaded models, VRAM, queue depth)."""
    return _get("/v1/status")


def up() -> bool:
    try:
        return bool(_get("/v1/status").get("ollama_reachable"))
    except BrokerError:
        return False
