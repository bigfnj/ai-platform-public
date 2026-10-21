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
import threading
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import settings
from .identity import Identity, identity

_log = logging.getLogger("openmaic.proxy")

router = APIRouter()

# --- who is using the app: the LLM shim's single-user check ------------------------------------
#
# The shim's caller is the app container, which carries no platform identity, so a generation
# call cannot say who is waiting on it (api/llm.py). What this rail CAN see is who is using the
# app: every request the browser makes in the frame comes through `proxy` below, behind the
# gateway's identity gate. `sole_user` answers whether exactly one account did that recently,
# which is the only case in which the shim may name the configured OPENMAIC_CLI_REQUESTER
# (api/llm.py `_shim_requester`). In memory and per process: a restart forgets everyone, which
# reads as "nobody seen", so the name stays off until that account's next request in the frame.
# Never pruned: its keys are gateway accounts, which the gateway's own user table bounds.
#
# It reads the X-Platform-User this rail is handed, and that header reaches a route only from
# the gateway's own address (api/gateway_only.py): from openmaic-app or any other container on
# the compose network it is dropped before the identity gate, which then answers 401, so no
# peer can put a name in here. The variable is still for a single-user box: the ledger knows
# who is using the app, not who started a given generation.

_seen: dict[str, float] = {}
_seen_lock = threading.Lock()
_clock = time.monotonic


def note_user(user: str | None) -> None:
    """Record that `user` made a request through the proxy just now. No user (standalone dev)
    is nobody, and is not recorded."""
    who = (user or "").strip()
    if not who:
        return
    with _seen_lock:
        _seen[who] = _clock()


def sole_user(name: str | None, within: float) -> bool:
    """True only when `name` made a proxied request within the last `within` seconds AND no
    other user did. Anything else, including nobody at all, is False."""
    who = (name or "").strip()
    if not who or within <= 0:
        return False
    now = _clock()
    with _seen_lock:
        recent = {user for user, at in _seen.items() if now - at <= within}
    return recent == {who}

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

# THE PLATFORM'S CREDENTIALS STOP HERE. openmaic-app is third-party code (THU-MAIC/OpenMAIC),
# and the gateway hands this rail every browser header it received. Relayed as they were, the
# app was given the caller's `platform_session` cookie (the owner's is a super-admin session),
# the gateway's own x-platform-admin / x-platform-peers trust signals, and any Authorization
# the browser attached to this origin; and its Set-Cookie came back to the browser unread, so it
# could even REPLACE the platform session with one of its choosing. Found by the 2026-10-08
# audit with a recording upstream; tests/test_proxy_credentials.py pins every one of these.
#
# Names, not values, and compared lowercased: a cookie name is case-sensitive to the gateway,
# but nothing legitimate in the wrapped app is called PLATFORM_SESSION either, and refusing the
# case variants costs nothing. CF_Authorization and the cf-access-* headers are Cloudflare
# Access's own credentials for this origin when the platform is published through it: the same
# class of thing as the session cookie, so they go too.
_PLATFORM_COOKIES = frozenset({"platform_session", "cf_authorization"})
_CREDENTIAL_HEADERS = frozenset({
    "authorization", "cookie", "cf-access-jwt-assertion",
    "cf-access-client-id", "cf-access-client-secret",
})
# If the wrapped app ever authenticates its own API calls with an Authorization header, they
# will 401 inside the frame after this change. That is the right failure: a header on THIS
# origin belongs to the platform's front door, and the app can carry its own credential in a
# cookie of its own name, which is relayed.


def _cookie_name(pair: str) -> str:
    return pair.split("=", 1)[0].strip().lower()


def app_cookies(values: list[str]) -> str:
    """The Cookie header the app may see: every cookie EXCEPT the platform's. `values` is every
    Cookie header the request carried (an HTTP/2 client may split them), joined back into one."""
    pairs = [p.strip() for v in values for p in v.split(";") if p.strip()]
    return "; ".join(p for p in pairs if _cookie_name(p) not in _PLATFORM_COOKIES)


def upstream_headers(request: Request, user: str | None) -> dict[str, str]:
    """The request headers openmaic-app is given: the browser's, minus hop-by-hop, minus every
    platform credential and trust signal, plus the identity this rail resolved itself."""
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
               and k.lower() not in _CREDENTIAL_HEADERS
               # Every x-platform-* the gateway set, not just the two named in the audit: the
               # set grows (peers, rooms), and an allowlist of one is the only form that cannot
               # be outgrown by the next one added.
               and not k.lower().startswith("x-platform-")}
    cookies = app_cookies(request.headers.getlist("cookie"))
    if cookies:
        headers["cookie"] = cookies
    # Pass the resolved identity on rather than the raw client headers. OpenMAIC does not read
    # these today; when it grows per-user state it should learn the caller from the platform,
    # not from whatever a sibling container chose to send.
    headers["x-platform-user"] = user or ""
    headers["x-forwarded-prefix"] = "/openmaic/api/app"
    return headers

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
    # Who is using the app, for the LLM shim's single-user check (sole_user, above). Every
    # request in the frame counts, the root assets included (root_asset calls this).
    note_user(ident.user)
    headers = upstream_headers(request, ident.user)

    req = get_client().build_request(
        request.method,
        upstream_path(path),
        # The RAW query string, not request.query_params. Starlette's multidict deduplicates on
        # .items(), which is what httpx consumes — so `?tag=math&tag=physics` would reach the app
        # as `?tag=physics`, losing every repeat of a repeated key.
        params=request.url.query or None,
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

    return _relay(resp, body())


def mount_root_assets(fastapi_app) -> None:
    """Serve the root-origin assets the gateway forwards here (rail.json `root_assets`).

    The gateway relays those paths UNCHANGED and tells us nothing about which prefix matched, so
    this is a catch-all that maps a DECLARED root path onto the app's basePath and 404s anything
    else.

    Checking the declaration here rather than trusting the gateway is what keeps standalone
    honest. In production the gateway forwards only what this rail declared, so a blanket proxy
    would behave identically — but standalone has no gateway in front, and a blanket proxy there
    forwards the rail's own /openapi.json and /docs to the app it fronts instead of 404ing. A
    route that behaves differently depending on what is in front of it is not one you can test.

    Registered after every other route, so it cannot shadow /api/capabilities or /api/healthz.
    """

    @fastapi_app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def root_asset(path: str, request: Request,
                         ident: Identity = Depends(identity)) -> Any:
        if not settings.is_root_asset(path):
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{path}")
        return await proxy(request, path=path, ident=ident)


def _relay(resp: httpx.Response, stream: AsyncIterator[bytes]) -> StreamingResponse:
    """Wrap an upstream response, preserving REPEATED headers.

    raw_headers from multi_items(), NOT a dict from .items(). httpx joins repeated keys into one
    comma-separated value, and Set-Cookie is the one header where that is destructive: two
    cookies become `sid=..; Expires=Wed, 21 Oct..., csrf=..`, the browser parses one of them, and
    the comma inside Expires corrupts even that. Next.js sets session and CSRF cookies together
    as a matter of course, so this is the normal path, not an edge case.
    """
    out = StreamingResponse(
        stream,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )
    out.raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in resp.headers.multi_items()
        if k.lower() not in _HOP_BY_HOP and not _sets_a_platform_cookie(k, v)
    ]
    return out


def _sets_a_platform_cookie(name: str, value: str) -> bool:
    """True for an upstream Set-Cookie naming one of the platform's cookies. The app is served
    on the platform's own origin, so a `platform_session` it set would REPLACE the caller's
    session in the browser (log them out, or log them in as whoever the app chose). Refused,
    and said so in the log, because an app that tries is worth an operator's attention."""
    if name.lower() != "set-cookie" or _cookie_name(value) not in _PLATFORM_COOKIES:
        return False
    _log.warning("openmaic-app tried to set the platform cookie %r; refused",
                 _cookie_name(value))
    return True
