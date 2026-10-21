"""A ceiling on one route's request body, enforced before anything reads the body.

FastAPI parses a multipart form before the handler runs and before any dependency is solved
(identity included), and Starlette spools the whole body to a temp file while it does. So
neither the route nor a dependency can bound an upload: by the time `upload_image` holds its
UploadFile, the bytes have all been received. This ASGI middleware sits in front of that.

  * A declared `Content-Length` over the limit is refused with a 413 before one byte is read.
  * Otherwise `receive` is wrapped, and the request is cut off with a 413 as soon as the bytes
    received pass the limit, whatever the client declared (chunked, or a false length).

The response is FastAPI's error shape, `{"detail": "..."}`, which the frontend shows as it
is. In the deployed stack the gateway streams the body here and holds it to the same figure
(rail.json `max_request_bytes`, docs/RAIL_CONTRACT.md "Request bodies"), so an oversized image
is normally refused there first; this still bounds whatever reaches the rail by another path.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class _TooLarge(Exception):
    """Raised from the wrapped receive() once the body passes the limit."""


def _declared_length(scope: dict) -> int | None:
    for name, value in scope.get("headers") or ():
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodyLimit:
    """Refuse `method path` with a 413 once its body passes `limit()` bytes.

    `limit` and `detail` are called per request, so the figure follows the setting it is
    derived from rather than a copy taken at import."""

    def __init__(self, app, *, method: str, path: str, limit: Callable[[], int],
                 detail: Callable[[], str]) -> None:
        self.app = app
        self.method = method
        self.path = path
        self.limit = limit
        self.detail = detail

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if (scope.get("type") != "http" or scope.get("method") != self.method
                or scope.get("path", "").rstrip("/") != self.path):
            await self.app(scope, receive, send)
            return
        limit = self.limit()
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await self._refuse(send)
            return

        received = 0
        over = False
        started = False

        async def bounded_receive() -> Message:
            nonlocal received, over
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    over = True
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if over:
                # Whatever the app makes of a body cut short (FastAPI turns it into a 400
                # "error parsing the body") is replaced by the 413 below.
                return
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, guarded_send)
        except Exception:
            if not over:
                raise
        if over and not started:
            await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        body = json.dumps({"detail": self.detail()}).encode("utf-8")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
