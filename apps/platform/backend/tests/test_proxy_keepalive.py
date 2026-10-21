"""The relay never reuses an upstream connection the rail is about to close.

Every rail backend runs uvicorn, which closes a connection idle for timeout_keep_alive (5 s by
default). Both relay clients kept idle connections for 5 s too (httpx's default), so a request
made on one idle about 5 s could go out just as the rail closed it: "Server disconnected without
sending a response", a 502 for a request the rail never read. And by the gateway's clock the
rail's timer fires early: it starts when the rail has written its answer, ours when the relay
has finished reading it. Now both pools drop a connection idle for _KEEPALIVE_EXPIRY (2 s), an
expiry rather than a retry, because a relayed POST may be a generation or a write and a resend
could run it twice. The broker's client to a delegated box made the same choice.

The rail below makes the window deterministic: a request that arrives on a connection it has
held idle for `idle_close` seconds or more is dropped unread, as a keep-alive timer that crossed
it would. The time scale is shrunk 10x, and the relay's expiry with it: the rail drops at 0.4 s
(its 5 s, taken early so the test does not lean on how promptly the event loop wakes), and the
third request comes 0.45 s after the second, which the old 5 s expiry (0.5 s here) still reused.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from platform_gateway_app import main as m
from platform_gateway_app.config import GatewaySettings

RAIL = "syn-keepalive-rail"     # synthetic: not a catalog id, so the access gate lets it through
UPLOAD = b"u" * (m._UPLOAD_FROM + 1)     # over _UPLOAD_FROM: goes out on the uploads' client


@pytest.fixture()
def idle_closing_rail():
    """A real HTTP/1.1 rail stand-in that keeps connections open, counts them, and drops a
    request that arrives after `idle_close` seconds of idleness on its connection."""
    stats = {"connections": 0, "dropped": 0, "idle_close": 0.4}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            stats["connections"] += 1
            self._idle_since = None

        def handle_one_request(self):
            line = self.rfile.readline(65537)
            if not line:
                self.close_connection = True
                return
            if (self._idle_since is not None
                    and time.monotonic() - self._idle_since >= stats["idle_close"]):
                stats["dropped"] += 1
                self.close_connection = True      # closed with no answer, the request unread
                return
            self.raw_requestline = line
            if not self.parse_request():
                return
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self._idle_since = time.monotonic()

        def log_message(self, *_args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", stats
    finally:
        srv.shutdown()
        srv.server_close()


def _scaled(limits: httpx.Limits) -> httpx.Limits:
    return httpx.Limits(max_connections=limits.max_connections,
                        max_keepalive_connections=limits.max_keepalive_connections,
                        keepalive_expiry=limits.keepalive_expiry / 10)


async def _call(method: str, body: bytes) -> int:
    path = f"/{RAIL}/api/x"
    headers = [(b"content-length", str(len(body)).encode())] if body else []
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 50000),
             "server": ("testserver", 80), "state": {}}
    sent: list[dict] = []
    asked = False

    async def receive() -> dict:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(msg: dict) -> None:
        sent.append(msg)

    await asyncio.wait_for(m.app(scope, receive, send), timeout=30)
    return next(x for x in sent if x["type"] == "http.response.start")["status"]


@pytest.mark.parametrize("method, body", [("GET", b""), ("POST", UPLOAD)],
                         ids=["the-relay-client", "the-uploads-client"])
async def test_a_request_after_an_idle_spell_is_not_sent_on_a_connection_the_rail_is_closing(
        idle_closing_rail, monkeypatch, method, body):
    url, stats = idle_closing_rail
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_APPS", raising=False)
    monkeypatch.setattr(m, "_RELAY_LIMITS", _scaled(m._RELAY_LIMITS))
    monkeypatch.setattr(m, "_UPLOAD_LIMITS", _scaled(m._UPLOAD_LIMITS))
    plain, uploads = m._relay_clients()
    monkeypatch.setattr(m.app.state, "backends", {RAIL: url}, raising=False)
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)
    monkeypatch.setattr(m.app.state, "http", plain, raising=False)
    monkeypatch.setattr(m.app.state, "http_body", uploads, raising=False)
    try:
        codes = [await _call(method, body), await _call(method, body)]
        await asyncio.sleep(0.45)
        codes.append(await _call(method, body))
    finally:
        await plain.aclose()
        await uploads.aclose()
    assert codes == [200, 200, 200], f"{codes}: a request went out on a connection being closed"
    assert stats["dropped"] == 0, "a request went out on a connection the rail was closing"
    assert stats["connections"] == 2, (
        f"{stats['connections']} connections for three requests: the pool should still reuse a "
        "warm one")


def test_both_pools_expire_idle_connections_well_inside_uvicorns_keep_alive():
    """The figure the scaled test above divides: well under the 5 s a rail keeps an idle
    connection, so a reused connection is never one the rail is about to close."""
    assert m._RELAY_LIMITS.keepalive_expiry <= 2.0
    assert m._UPLOAD_LIMITS.keepalive_expiry <= 2.0
