"""Client for a REMOTE broker this one delegates a role to.

The platform's model layer is one broker per box, and that was the only shape until a rail needed
a card bigger than the one it was installed on. Rather than teach every rail to talk to a second
broker — which would mean a per-rail broker URL, a per-rail token, and a contract rule for both —
a role may name an upstream and THIS broker forwards the call.

The rail is unaffected and does not know: it asks its own broker for ``@openmaic`` exactly as
before, and the answer happens to have been produced somewhere else.

What this deliberately does NOT do:

* **Take the local GPU gate.** A delegated call never touches this card. Holding the single-slot
  gate for it would serialise local work behind a remote generation that cannot possibly conflict
  with it, which is the opposite of what the gate is for. ``broker.py`` is where that decision is
  made; this module just moves bytes.
* **Retry or fall back to local.** A delegated role that cannot reach its upstream is an error, not
  a reason to quietly run a same-named model on the wrong box and report success.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class UpstreamError(RuntimeError):
    """The upstream broker refused the call or could not be reached."""


class UpstreamClient:
    """One remote broker, addressed by its ``/v1/*`` control plane."""

    def __init__(self, name: str, url: str, token: str = "", timeout: float = 600.0) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.timeout = timeout
        # Same two header spellings the local broker accepts, so a remote running either
        # generation of require_token() authenticates.
        self._headers = ({"Authorization": f"Bearer {token}", "X-Broker-Token": token}
                         if token else {})

    def _fail(self, what: str, exc: Exception) -> UpstreamError:
        return UpstreamError(f"upstream '{self.name}' {what}: {exc}")

    async def _get(self, path: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(self.url + path, headers=self._headers, params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            raise self._fail(f"GET {path} -> {exc.response.status_code}", exc) from exc
        except httpx.HTTPError as exc:
            raise self._fail(f"GET {path} unreachable", exc) from exc

    async def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as c:
                r = await c.post(self.url + path, json=payload, headers=self._headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            raise self._fail(f"POST {path} -> {exc.response.status_code}", exc) from exc
        except httpx.HTTPError as exc:
            raise self._fail(f"POST {path} unreachable", exc) from exc

    # --- read ------------------------------------------------------------------------------

    async def healthy(self) -> bool:
        """Never raises. /healthz is exempt from the token gate on every broker, so this answers
        even when our token is wrong — which is the distinction the admin panel needs: 'the box is
        down' and 'the box refuses us' are different problems with different fixes."""
        try:
            return bool((await self._get("/healthz", timeout=5.0)).get("status") == "ok")
        except (UpstreamError, AttributeError):
            return False

    async def models(self) -> list[dict[str, Any]]:
        resp = await self._get("/v1/models")
        return resp.get("models", []) if isinstance(resp, dict) else (resp or [])

    async def roles(self) -> list[dict[str, Any]]:
        resp = await self._get("/v1/roles")
        return resp.get("roles", []) if isinstance(resp, dict) else (resp or [])

    async def status(self) -> dict[str, Any]:
        resp = await self._get("/v1/status")
        return resp if isinstance(resp, dict) else {}

    # --- inference -------------------------------------------------------------------------

    async def chat(self, model: str, messages: list[dict[str, str]], *,
                   options: dict | None = None, keep_alive: Any = None,
                   format: Any = None, think: bool | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        for k, v in (("options", options), ("keep_alive", keep_alive),
                     ("format", format), ("think", think)):
            if v is not None:
                payload[k] = v
        return await self._post("/v1/chat", payload)

    async def chat_stream(self, model: str, messages: list[dict[str, str]], *,
                          options: dict | None = None, keep_alive: Any = None,
                          format: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Relay the upstream's NDJSON frames, one decoded dict per line.

        Frames are passed through UNCHANGED, including a terminal ``{"error": ...}``. The upstream
        reports a mid-stream failure that way with HTTP 200 long since sent, and rewriting it here
        would hide from the caller that the answer is truncated.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages}
        for k, v in (("options", options), ("keep_alive", keep_alive), ("format", format)):
            if v is not None:
                payload[k] = v
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", self.url + "/v1/chat/stream",
                                    json=payload, headers=self._headers) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "replace")
                        raise UpstreamError(
                            f"upstream '{self.name}' stream -> {r.status_code}: {body}")
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise self._fail("stream unreachable", exc) from exc

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        return await self._post("/v1/embed", {"model": model, "input": text})

    async def load(self, model: str, keep_alive: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model}
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        return await self._post("/v1/load", payload)

    async def unload(self, model: str) -> dict[str, Any]:
        return await self._post("/v1/unload", {"model": model})
