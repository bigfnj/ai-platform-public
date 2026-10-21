"""A streamed response that closes its own body, however the stream ends.

Starlette's StreamingResponse iterates its body with `async for`, and `async for` does not close
an async generator it stops iterating. When the iteration ends inside the generator (it finishes,
or a cancellation lands at one of its own awaits) that does not matter. When it ends OUTSIDE, in
Starlette's `await send(...)`, the generator is left suspended at its `yield`, still holding what
it holds: a Claude CLI process tree and its slot (/v1/chat/stream), the GPU gate and an Ollama
connection (both stream routes). Two ways that happens:

* ASGI 2.3: Starlette listens for the disconnect and cancels the stream, and the cancellation
  lands in send() if a send is what the stream is waiting on (a server whose send stays blocked
  past the disconnect; a middleware that checkpoints in send).
* ASGI 2.4: Starlette listens for nothing, and the server raises OSError from a send to a
  connection that has gone (Starlette re-raises it as ClientDisconnect).

Measured 2026-10-14 with a send that blocks until cancelled: the orphaned generator was held by
a reference cycle, so nothing closed it until a garbage collection ran, and the collection's own
close was wrong as well (every nested generator gets its own aclose() at once, so the inner ones
failed as "already running" and the broker logged the RuntimeError as a CLI failure). uvicorn
0.54 does not take that path today: its send returns at once on a connection that has gone, and
anyio does not cancel a task whose wake-up is already due, so the cancellation lands inside the
generator instead. That ordering belongs to two other projects, so the routes do not rely on it.

A third way a stream sits in send(), and the one nothing ended: a client that keeps its socket
open and stops reading. Every deadline the stream has (the CLI's first-token and idle bounds, the
broker's hang-up tick) runs only while it waits for its next frame, never while it is suspended at
a `yield` behind a send, and a server's send has no timeout of its own, so such a client held its
CLI slot (two of them used up BROKER_CLI_CONCURRENCY=2) or the GPU gate for as long as it liked.
Each send is bounded now (SEND_TIMEOUT_S), and a send that outlasts it ends the stream.

Such a stream ends with its response unfinished, on purpose, and uvicorn closes the connection,
which is what a client that resumes reading should see: an answer cut off, not a short one that
ended cleanly. Finishing it is not possible anyway: uvicorn's send waits for the client to drain
the transport before it writes a byte, the closing chunk included. What uvicorn also does is log
"ASGI callable returned without completing response." at ERROR, which reads as a crash beside the
broker's own WARNING line, so that one record is dropped for a stream the bound closed and for no
other (_BoundClosedFilter).
"""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import Any, AsyncGenerator

import anyio
from fastapi.responses import StreamingResponse

#: The longest ONE send of a streamed response may stay blocked before the stream is closed, in
#: seconds. Read at call time, so a test can shorten it. A send blocks only once the server's
#: write buffer is full: the kernel's socket buffer and then the transport's own (asyncio's
#: default high-water mark is 64 KiB) are holding what the client has not read, and it resumes
#: once the client has read the transport down to its low-water mark (16 KiB), 48 KiB later. So
#: a minute cuts off only a client reading slower than about 0.8 KiB/s while that much is
#: waiting for it, and an answer that fits in the buffers (nearly all of them) is never held at
#: all. A slow reader is never cut for being slow overall: the bound is per send and starts
#: again at the next one. The same minute as the CLI's own idle bound (`_STREAM_IDLE_S`): a
#: minute of nothing from either end is a stream that stopped.
SEND_TIMEOUT_S = 60.0


class SendStalled(Exception):
    """A send of a streamed response outlasted SEND_TIMEOUT_S. Not an OSError (as TimeoutError
    is): under ASGI 2.4 Starlette turns an OSError from send() into ClientDisconnect, and a
    client that stopped reading has not disconnected."""


#: What uvicorn logs at ERROR, on its "uvicorn.error" logger, when an app returns with its
#: response unfinished (h11 and httptools alike; uvicorn 0.54).
_UNFINISHED = "ASGI callable returned without completing response."
#: Set in the request's own task once the send bound has closed its stream. uvicorn awaits the
#: app in that task and logs from it as soon as the app returns, and every layer between (the
#: broker's middleware is pure ASGI) awaits the next in the same task, so the mark is seen there
#: and in no other request's task.
_CLOSED_BY_BOUND: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "broker_stream_closed_by_send_bound", default=False)


class _BoundClosedFilter(logging.Filter):
    """Drops uvicorn's "returned without completing response" ERROR for a stream the send
    bound closed, whose own WARNING line says what happened. Every other record passes,
    including that one for any other response left unfinished."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (_CLOSED_BY_BOUND.get() and record.msg == _UNFINISHED)


# On the logger itself, at import: uvicorn's dictConfig replaces that logger's handlers but keeps
# its filters, whichever happens first (the CLI configures logging before it imports the app).
logging.getLogger("uvicorn.error").addFilter(_BoundClosedFilter())


class ClosingStreamingResponse(StreamingResponse):
    """A StreamingResponse over an async generator, which it closes on every exit.

    The close runs in a shielded anyio scope: a cancellation that ended the stream (a server
    shutting down, an enclosing scope's) would otherwise cancel the close at its first await,
    and a body's cleanup is mostly awaits. An exhausted generator's close does nothing, so a
    stream that finished costs nothing extra. The request ends only once the close has, so a
    route's body is closed, not merely abandoned, by the time the server is told it is done.

    Every send is bounded at SEND_TIMEOUT_S. One that outlasts it is cancelled and the stream
    ends there (SendStalled, one WARNING line, not raised to the server): the body is closed as
    on any other exit, so the CLI's tree is killed and its slot, or the GPU gate, comes back. The
    response is left incomplete, which the server answers by closing the connection, and
    uvicorn's ERROR record for that is dropped (_BoundClosedFilter)."""

    def __init__(self, content: AsyncGenerator[bytes, None], **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._body = content

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        limit = SEND_TIMEOUT_S

        async def bounded(message: Any) -> None:
            with anyio.move_on_after(limit) as waited:
                await send(message)
            if waited.cancelled_caught:
                raise SendStalled(f"a send waited {limit:g}s for the client to read")

        try:
            await super().__call__(scope, receive, bounded)
        except SendStalled as stalled:
            # In this task, the one uvicorn logs from once this returns: see _CLOSED_BY_BOUND.
            _CLOSED_BY_BOUND.set(True)
            print(f"[broker] WARNING: {scope.get('path', 'stream')}: {stalled}; the stream was "
                  f"closed", file=sys.stderr, flush=True)
        finally:
            with anyio.CancelScope(shield=True):
                await self._body.aclose()
