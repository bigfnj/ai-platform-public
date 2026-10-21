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

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

#: The health probe's ceiling. Short on purpose: roles_view budgets a sweep as one of these
#: plus one bounded read, and a box that is off has to cost that, not the read timeout.
_HEALTH_TIMEOUT = 5.0
#: A control-plane read (/v1/models, /v1/roles, /v1/status). Generation calls use the client's
#: own `timeout` instead, sized for a model that is still loading.
#: Both are read when a call is made, not bound as defaults, so they can be changed in one
#: place and shortened by a test.
_READ_TIMEOUT = 30.0
#: Seconds a pooled connection may sit idle before the pool drops it rather than reuse it. Well
#: inside the 5 s a remote broker keeps an idle connection open (uvicorn's default
#: timeout_keep_alive, which the broker runs with). httpx's own default is ALSO 5 s, so a call
#: made on a connection idle for about 5 s could be sent just as the remote closed it: "Server
#: disconnected without sending a response", a 502 for a request the remote never read.
#: A shorter expiry rather than a retry: a delegated POST is a generation, and the client cannot
#: tell a connection the remote closed unread from one that died mid-answer, so a resend could
#: run the generation twice. Read when a client is built, so a test can scale it.
_KEEPALIVE_EXPIRY = 2.0
#: The closes of replaced clients still running (UpstreamClient.retire), held so that a pending
#: close is not collected before it has finished.
_CLOSING: set[asyncio.Task[None]] = set()


class UpstreamError(RuntimeError):
    """The upstream broker refused the call or could not be reached."""


class UpstreamClient:
    """One remote broker, addressed by its ``/v1/*`` control plane.

    Holds ONE httpx client for every call it makes, opened on first use and closed by
    ``aclose()``. It used to open a client per request, and opening one is not free: it builds
    an SSL context and loads the certifi bundle, ~0.45-0.5 s measured on the live box, so
    /v1/upstreams took 0.8 s against a remote whose /healthz answered in 0.06 s, and a roles
    sweep paid it three times per delegated box. The broker keeps one of these per box (see
    ``Broker._upstream``), so the cost is paid once per box, not once per call.

    Each call still carries its own timeout, as before: the client's pool is shared, the
    deadlines are not.
    """

    def __init__(self, name: str, url: str, token: str = "", timeout: float = 600.0) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._token = token
        # Same two header spellings the local broker accepts, so a remote running either
        # generation of require_token() authenticates.
        self._headers = ({"Authorization": f"Bearer {token}", "X-Broker-Token": token}
                         if token else {})
        self._client: httpx.AsyncClient | None = None
        # Calls using this client right now, streams included, and whether the broker has
        # replaced it (retire). Together they say when its connections may be closed.
        self._uses = 0
        self._retired = False

    def serves(self, url: str, token: str) -> bool:
        """Whether this client is for the box at `url` with `token`: the broker's test for
        whether an edited upstreams.json still matches the client it holds."""
        return self.url == url.rstrip("/") and self._token == token

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, limits=httpx.Limits(
                max_connections=100, max_keepalive_connections=20,    # httpx's own defaults
                keepalive_expiry=_KEEPALIVE_EXPIRY))
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    @property
    def busy(self) -> bool:
        """Whether a call, a stream included, is using this client right now."""
        return self._uses > 0

    def retire(self) -> None:
        """The broker hands this client to no new call: upstreams.json changed under it.

        Its connections are closed once no call is using it: now if none is, else when the last
        one ends, because a delegated stream may still be reading through it. Closed only with
        the broker, as before, every edit left a pool open for the life of the process. A caller
        that still holds it may use it again; that use reopens a pool, closed again as it ends.
        """
        self._retired = True
        if not self._uses:
            self._close_soon()

    def _release(self) -> None:
        self._uses -= 1
        if self._retired and not self._uses:
            self._close_soon()

    def _close_soon(self) -> None:
        # A task, not an await: retire() runs inside the broker's synchronous client lookup, and
        # _release() in a finally that may be running under a cancellation.
        client, self._client = self._client, None
        if client is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(client.aclose())
        except RuntimeError:        # no loop left to close it on (a generator finalized late)
            return
        _CLOSING.add(task)
        task.add_done_callback(_CLOSING.discard)

    def _fail(self, what: str, exc: Exception) -> UpstreamError:
        return UpstreamError(f"upstream '{self.name}' {what}: {exc}")

    def _json(self, r: httpx.Response, what: str) -> Any:
        """The answer's body as JSON, or an UpstreamError naming the box.

        A registered address that answers 200 with a page (a sign-in portal, a proxy's error
        page, another service on the port) made `r.json()` raise JSONDecodeError, a ValueError:
        /v1/models?upstream=X answered it 404 "not found", and healthy() let it out despite
        promising never to raise. UnicodeDecodeError is a ValueError too; RecursionError is a
        body nested deeper than the parser's stack.
        """
        try:
            return r.json()
        except (ValueError, RecursionError) as exc:
            kind = r.headers.get("content-type") or "no content type"
            raise self._fail(f"{what} answered {r.status_code} with a body that is not JSON "
                             f"({kind})", exc) from exc

    async def _get(self, path: str, params: dict | None = None,
                   timeout: float | None = None) -> Any:
        self._uses += 1
        try:
            r = await self._http().get(self.url + path, headers=self._headers, params=params,
                                       timeout=_READ_TIMEOUT if timeout is None else timeout)
            r.raise_for_status()
            return self._json(r, f"GET {path}")
        except httpx.HTTPStatusError as exc:
            raise self._fail(f"GET {path} -> {exc.response.status_code}", exc) from exc
        except httpx.HTTPError as exc:
            raise self._fail(f"GET {path} unreachable", exc) from exc
        finally:
            self._release()

    async def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        self._uses += 1
        try:
            r = await self._http().post(self.url + path, json=payload, headers=self._headers,
                                        timeout=timeout or self.timeout)
            r.raise_for_status()
            return self._json(r, f"POST {path}")
        except httpx.HTTPStatusError as exc:
            raise self._fail(f"POST {path} -> {exc.response.status_code}", exc) from exc
        except httpx.HTTPError as exc:
            raise self._fail(f"POST {path} unreachable", exc) from exc
        finally:
            self._release()

    # --- read ------------------------------------------------------------------------------

    async def healthy(self) -> bool:
        """Never raises. /healthz is exempt from the token gate on every broker, so this answers
        even when our token is wrong — which is the distinction the admin panel needs: 'the box is
        down' and 'the box refuses us' are different problems with different fixes."""
        try:
            return bool((await self._get("/healthz", timeout=_HEALTH_TIMEOUT)).get("status")
                        == "ok")
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
                          format: Any = None,
                          think: bool | None = None) -> AsyncIterator[dict[str, Any]]:
        """Relay the upstream's NDJSON frames, one decoded dict per line.

        Frames are passed through UNCHANGED, including a terminal ``{"error": ...}``. The upstream
        reports a mid-stream failure that way with HTTP 200 long since sent, and rewriting it here
        would hide from the caller that the answer is truncated.

        ``think`` is forwarded like the buffered ``chat`` forwards it. Without it, a delegated
        THINKING model (gemma4:26b, qwen3.6:27b) spent the caller's whole num_predict on its hidden
        reasoning and streamed an EMPTY answer — measured 2026-10-05: 3,341 and 3,716 characters of
        thinking, zero of content, at num_predict 800. The remote broker's /v1/chat/stream already
        honours the field; this hop was the only one dropping it.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages}
        for k, v in (("options", options), ("keep_alive", keep_alive), ("format", format),
                     ("think", think)):
            if v is not None:
                payload[k] = v
        # A use until the stream ends or is closed, so a retired client stays open under it.
        self._uses += 1
        try:
            async with self._http().stream("POST", self.url + "/v1/chat/stream",
                                           json=payload, headers=self._headers,
                                           timeout=self.timeout) as r:
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
        finally:
            self._release()

    async def embed(self, model: str, text: str | list[str]) -> dict[str, Any]:
        return await self._post("/v1/embed", {"model": model, "input": text})

    async def load(self, model: str, keep_alive: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model}
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        return await self._post("/v1/load", payload)

    async def unload(self, model: str) -> dict[str, Any]:
        return await self._post("/v1/unload", {"model": model})
