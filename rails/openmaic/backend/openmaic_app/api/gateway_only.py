"""Platform identity counts only when it arrives from the gateway's address.

Every rail believes X-Platform-User / X-Platform-Admin when they are present. That holds because
the gateway strips any client copy and sets its own, so a header that arrives was set either by
the gateway or by something else that can open a socket to the rail. For this rail "something
else" includes openmaic-app, the third-party Next.js application it wraps (THU-MAIC/OpenMAIC),
which shares the compose network and calls this rail's LLM shim, and every other container on
that network. Until this module any of them could name anybody:

  * to the reverse proxy, and so to the shim's sole-user ledger (api/proxy.py `note_user`), which
    decides whether the shim names OPENMAIC_CLI_REQUESTER on the app's generation calls: a peer
    could keep the owner's name on them, or keep it off;
  * to the shim itself, whose credential check (api/llm.py `llm_caller`) took any X-Platform-User
    as a platform identity, so a peer needed no service token at all to spend broker time.

GatewayOnlyIdentity drops every x-platform-* header a request carries unless the request comes
from one of the gateway's addresses. Dropped, not refused, and that is the one way this differs
from terminal-fun's copy (rails/terminal-fun/backend/terminal_fun_app/main.py), which answers 403:
there nothing but the gateway ever has a reason to call, while here openmaic-app is a legitimate
non-gateway caller of the shim. So a non-gateway request is judged exactly as if it had carried no
identity at all. Every gated route answers it 401, before any handler runs (the ledger is never
fed); the shim's service-token check still decides, which is how openmaic-app keeps reaching it.
A request carrying NO such header passes untouched and costs no lookup.

Middleware rather than a dependency, because it has to cover every route, the /api/llm mount and
the root-asset catch-all, including routes nobody has written yet. Here rather than in identity.py,
because identity.py is generated and byte-identical across rails. Copied, not imported: a rail does
not import another rail or the platform.
"""
from __future__ import annotations

import functools
import ipaddress
import json
import logging
import socket
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import settings
from .identity import standalone

_log = logging.getLogger("openmaic.identity")

_PLATFORM_HEADER = b"x-platform-"
_FORWARDED_FOR = b"x-forwarded-for"


def _canonical_ip(host: object) -> str | None:
    """`host` as one canonical address string, or None when it is not an address at all.

    IPv4-mapped IPv6 (`::ffff:172.18.0.22`, what a dual-stack listener reports for an IPv4 peer)
    folds to the IPv4 form, so the gateway is the same string whichever way a socket was opened;
    IPv6 is compressed, a scope id and brackets are dropped. A host NAME is not an address and is
    never matched: the peer of a TCP socket is always an address, and a name can only have come
    from somebody's header.
    """
    if not isinstance(host, str):
        return None
    raw = host.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    raw = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Raises OSError (socket.gaierror) when it does not."""
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


@functools.lru_cache(maxsize=1)
def _own_addresses() -> frozenset[str]:
    """This container's own addresses, from its hostname (Docker maps it to the container's
    address in /etc/hosts). Empty when the hostname does not resolve."""
    try:
        return frozenset(a for a in map(_canonical_ip, _resolve(socket.gethostname())) if a)
    except OSError:
        return frozenset()


def _is_local(addr: str) -> bool:
    return ipaddress.ip_address(addr).is_loopback or addr in _own_addresses()


class GatewayPeers:
    """The gateway's addresses, as this container resolves `settings.gateway_host`.

    Cached, because the answer changes only when the gateway is recreated, and re-resolved on a
    MISS (a peer not in the cache) at most once per `gateway_resolve_interval`. A miss is the
    gateway back with a new IP after a redeploy, or somebody else (openmaic-app on every shim
    call that carries a platform header, which it has no reason to send): the first is recognised
    within one interval, the second costs one lookup per interval however often it happens. A
    lookup REPLACES the set, so an address the gateway has given up stops counting at the next
    one. A lookup that fails keeps the last answer: a DNS hiccup must not cut the gateway off, and
    an address it held a moment ago is no more dangerous than it was then.

    Loopback and this container's own addresses are strangers like any other, because they are
    not in the set, unless the gateway's name resolves to one of them. That is a dev box with the
    gateway on localhost: it works, and it says it is degraded (mode(), /api/healthz, and a
    warning on every lookup), because there anything that reaches this rail over loopback can
    assert an identity.

    The lookup is synchronous on purpose. Docker's DNS answers a known name in well under a
    millisecond, at most once per interval; an async lookup would let the requests that arrive
    while it runs (a page start fires dozens of asset loads at once) see the stale set and lose
    their identity after every gateway redeploy.
    """

    def __init__(self, host: Callable[[], str], interval: Callable[[], float], *,
                 resolve: Callable[[str], Iterable[str]] = _resolve,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._host = host
        self._interval = interval
        self.resolve = resolve
        self._clock = clock
        self.addresses: frozenset[str] = frozenset()
        self.local: tuple[str, ...] = ()
        self._resolved_at: float | None = None

    def allows(self, peer: object) -> bool:
        ip = _canonical_ip(peer)
        if ip is None:
            return False
        if ip in self.addresses:
            return True
        now = self._clock()
        if self._resolved_at is not None and now - self._resolved_at < self._interval():
            return False
        self.refresh(now)
        return ip in self.addresses

    def refresh(self, now: float) -> None:
        host = self._host()
        self._resolved_at = now
        try:
            found = frozenset(a for a in map(_canonical_ip, self.resolve(host)) if a)
        except OSError as exc:
            _log.warning(f"openmaic: cannot resolve the gateway host {host!r} ({exc}); "
                         f"identity headers are accepted only from the last answer, "
                         f"{sorted(self.addresses) or 'nobody'}")
            return
        self.addresses = found
        self.local = tuple(sorted(a for a in found if _is_local(a)))
        if self.local:
            _log.warning(f"openmaic: DEGRADED identity check: the gateway host {host!r} "
                         f"resolves to {', '.join(self.local)}, a loopback or own address of this "
                         f"container, so anything that reaches this rail there can assert a "
                         f"platform identity. Right for a dev box with the gateway on localhost; "
                         f"never in a deployment.")

    def mode(self) -> str:
        """What /api/healthz reports: "on", "degraded", "unresolved" or "off (PLATFORM_STANDALONE)".

        It looks the gateway up first when nothing has yet, so the answer never depends on
        whether an identity-bearing request happened to arrive before it. "unresolved" means no
        lookup has ever answered, so nobody's identity is believed."""
        if standalone():
            return "off (PLATFORM_STANDALONE)"
        if self._resolved_at is None:
            self.refresh(self._clock())
        if not self.addresses:
            return "unresolved"
        return "degraded" if self.local else "on"


def _forwarded_for(headers: Iterable[tuple[bytes, bytes]]) -> set[str]:
    """Every address this request's X-Forwarded-For headers name, canonical.

    For one check. uvicorn rewrites scope["client"] from X-Forwarded-For when the socket's peer is
    on its trusted list, and that list is 127.0.0.1 by default: a caller on loopback sending
    `X-Forwarded-For: <the gateway>` would BE the gateway by the time this middleware looked. The
    image runs uvicorn with --no-proxy-headers (deploy/Dockerfile), which is the fix. This keeps
    the guard honest if that flag is ever lost: a rewritten client always comes out of this
    header, and the gateway's own address never legitimately appears in it (the gateway forwards
    what caddy wrote there, which is the BROWSER's side of the connection).
    """
    out: set[str] = set()
    for name, value in headers:
        if name.lower() != _FORWARDED_FOR:
            continue
        for item in value.decode("latin-1").split(","):
            item = item.strip()
            if item.startswith("[") and "]" in item:
                item = item[1:item.index("]")]
            elif item.count(":") == 1:
                item = item.split(":", 1)[0]
            ip = _canonical_ip(item)
            if ip:
                out.add(ip)
    return out


class _DropLog:
    """At most one `identity_dropped` line a second, carrying a count of the ones it held back. A
    peer can send forged headers in a loop, and a container log is a file on the host's disk."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None
        self._held = 0

    def __call__(self, **fields: Any) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < 1.0:
            self._held += 1
            return
        held, self._held, self._last = self._held, 0, now
        _log.warning(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                 "event": "identity_dropped", "suppressed": held, **fields}))


_dropped = _DropLog()


class GatewayOnlyIdentity:
    """ASGI middleware: x-platform-* headers reach the app only from the gateway's address."""

    def __init__(self, app: ASGIApp, peers: GatewayPeers) -> None:
        self.app = app
        self.peers = peers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Read at call time, like identity.standalone() everywhere else: standalone dev has no
        # gateway in front, and keeps exactly the behaviour it had before this check existed.
        if scope["type"] not in ("http", "websocket") or standalone():
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or ()
        if not any(name.lower().startswith(_PLATFORM_HEADER) for name, _ in headers):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else None
        if not self.peers.allows(peer):
            reason = "not the gateway"
        elif _canonical_ip(peer) in _forwarded_for(headers):
            reason = "the peer address came from X-Forwarded-For"
        else:
            await self.app(scope, receive, send)
            return
        _dropped(peer=peer or "-", kind=scope["type"], path=scope.get("path", ""), reason=reason)
        # A copy of the scope, never an edit of the caller's: the request goes on as one that
        # carried no platform identity, which every route and the shim already know how to judge.
        scope = dict(scope)
        scope["headers"] = [(name, value) for name, value in headers
                            if not name.lower().startswith(_PLATFORM_HEADER)]
        await self.app(scope, receive, send)


gateway_peers = GatewayPeers(lambda: settings.gateway_host,
                             lambda: settings.gateway_resolve_interval)
