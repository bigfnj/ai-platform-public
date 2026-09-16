"""Reverse proxy to the OpenMAIC application container.

The rail serves the real app under ``/openmaic/api/app/`` so the browser loads it SAME-ORIGIN
with the rest of the platform. That is the whole design, and it buys three things at once:

  * the gateway's session cookie and X-Platform-User gate stay in front of every request,
    including the app's own XHR and asset loads;
  * the iframe can keep ``allow-same-origin``, which it must — an opaque origin withholds the
    session cookie and every gated subresource comes back 401;
  * no second origin to configure, no CORS, and no ALLOWED_FRAME_ANCESTORS build argument,
    because OpenMAIC's default ``frame-ancestors 'self'`` is already satisfied.

The price is that OpenMAIC has to be built with ``basePath``/``assetPrefix`` set to this mount
point, or it emits absolute ``/_next/*`` URLs that escape the prefix and 404. That is applied by
a patch script against the upstream checkout rather than being fixed up here: rewriting HTML and
JS in flight is the kind of thing that works on the first page and fails on the third.

This is a ROUTE, not a mount, so the app-level ``Depends(identity)`` gate does apply to it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import settings
from .identity import Identity, identity

_log = logging.getLogger("openmaic.proxy")

router = APIRouter()

# Hop-by-hop headers are meaningful only for a single transport leg (RFC 9110 7.6.1). Forwarding
# them corrupts the next leg: a passed-through `transfer-encoding: chunked` alongside httpx's own
# framing is the one that bites, and it presents as a truncated response rather than an error.
#
# `content-encoding` is NOT in this set, and that is load-bearing. It is an end-to-end header, and
# the body is relayed with aiter_raw() — still compressed. Stripping it hands the browser gzip
# bytes it has been told are plain text, which renders as binary garbage rather than as an error.
# `content-length` IS stripped because StreamingResponse re-frames the body itself.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
})

# One client for the process. A per-request AsyncClient would open a fresh pool each time and
# leak sockets under the burst of parallel asset loads a Next.js page start produces.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.app_url.rstrip("/"),
            timeout=httpx.Timeout(settings.app_timeout, connect=10.0),
            follow_redirects=False,  # the browser must see a 3xx so its URL stays under the prefix
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def upstream_path(path: str) -> str:
    """The URL to ask the app for, given the tail the gateway handed us.

    The gateway strips `/openmaic` and this router is mounted at `/api/app`, so `path` is only
    the tail. Next.js built with a basePath serves its pages AT the prefix, so the prefix has to
    go back on before the request leaves — forwarding the bare tail gets a 404 from Next, which
    reads as "the app is broken" rather than "the proxy dropped the prefix".

    The root is the fiddly case and it is a redirect LOOP if you get it wrong. Next serves the
    root at the prefix with NO trailing slash and 308s `/prefix/` to `/prefix`; FastAPI's own
    `redirect_slashes` then 307s `/api/app` back to `/api/app/`, and its Location is an absolute
    URL with the gateway's `/openmaic` already stripped — so the browser bounces between two
    hosts' idea of the path and never loads anything.
    """
    prefix, tail = settings.public_prefix.rstrip("/"), path.lstrip("/")
    return prefix + "/" + tail if tail else prefix


async def app_reachable() -> bool:
    """Whether the OpenMAIC container answers. Never raises — this feeds a status chip."""
    try:
        resp = await get_client().get(upstream_path("api/health"), timeout=5.0)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


# Both spellings are registered so FastAPI never issues its own slash redirect: that redirect's
# Location is built from the path THIS app saw, which the gateway has already stripped `/openmaic`
# from, so following it leaves the rail entirely.
@router.api_route("/api/app", methods=_METHODS, include_in_schema=False)
@router.api_route("/api/app/{path:path}", methods=_METHODS)
async def proxy(request: Request, path: str = "",
                ident: Identity = Depends(identity)) -> Any:
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and k.lower() != "host"}
    # Pass the resolved identity on rather than the raw client headers. OpenMAIC does not read
    # these today; when it grows per-user state it should learn the caller from the platform,
    # not from whatever a sibling container chose to send.
    headers["x-platform-user"] = ident.user or ""
    headers["x-forwarded-prefix"] = "/openmaic/api/app"

    req = get_client().build_request(
        request.method,
        upstream_path(path),
        params=request.query_params,
        headers=headers,
        content=request.stream(),
    )
    try:
        resp = await get_client().send(req, stream=True)
    except httpx.HTTPError as exc:
        _log.warning("openmaic-app unreachable: %s", exc)
        raise HTTPException(status_code=502,
                            detail="the OpenMAIC application container is not reachable") from exc

    async def body():
        try:
            async for piece in resp.aiter_raw():
                yield piece
        finally:
            await resp.aclose()

    return StreamingResponse(
        body(),
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP},
        media_type=resp.headers.get("content-type"),
    )
