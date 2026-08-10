"""Optional convenience: pull a broker (Ollama) embedding model directly from the host daemon.

The platform principle is that rails go through the broker, and the broker has no pull endpoint,
so this reaches the host Ollama at ``config.OLLAMA_URL`` (``host.docker.internal:11434``) only to
*add* a model an admin registered. Best-effort: if the daemon isn't reachable the API falls back
to telling the admin to run ``ollama pull <model>`` on the broker box. Never used at query time.
"""
from __future__ import annotations

import json
import urllib.request

from ai_playground import config


def pull(model: str) -> dict:
    """Blocking pull (stream=false). Raises RuntimeError with a readable reason on failure."""
    url = config.OLLAMA_URL + "/api/pull"
    body = json.dumps({"model": model, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            data = json.loads(resp.read() or b"{}")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"could not pull '{model}' via {config.OLLAMA_URL} "
            f"({type(exc).__name__}). Run `ollama pull {model}` on the broker box instead.") from exc
    status = data.get("status", "")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return {"ok": True, "model": model, "status": status or "success"}
