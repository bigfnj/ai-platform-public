"""A read-aloud whose client goes away ends the gateway's broker request.

web/src/read_aloud.ts aborts a read's request on Stop, on a new read and on an unmount, so the
browser lets go of /api/platform/tts_light at once. The route behind it was a plain await of the
broker: Starlette cancels nothing for a plain route when its client leaves, so the gateway held
its request to the broker open until the audio came back for nobody, and the broker, its client
still there, synthesised all of it. Now the broker call runs in a task the route cancels once the
connection says the client has gone (main._unless_client_leaves), which closes the broker's
connection: the broker stops the job when that happens (services/broker tests/test_tts_light.py).

The broker here is a real HTTP server on loopback, reached through the gateway's real
BrokerClient, and it watches its own socket: a request whose connection closes before it answers
is recorded as dropped. So what is asserted is what a broker would see, not what a fake client
object was told. The gateway is the real ASGI app, both http middlewares and the body ceiling
included, driven by a client that leaves the way uvicorn reports it (receive() says disconnect).
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from platform_core import BrokerClient
from platform_gateway_app import main as m
from platform_gateway_app.config import GatewaySettings

AUDIO = {"audio_b64": "UklGRg==", "sample_rate": 24000}
#: How long the stub broker takes to answer a read: far beyond every bound below, so a gateway
#: that waits for the answer fails on time instead of passing late.
SYNTH_S = 8.0


@pytest.fixture()
def broker():
    """A stub broker. /v1/tts_light reads the body and then answers after `synth_s` (SYNTH_S),
    unless the connection closes first, which it records as `dropped` (with the time it saw it)."""
    seen = {"asked": threading.Event(), "dropped": threading.Event(), "dropped_at": None,
            "answered": 0, "bodies": [], "synth_s": SYNTH_S}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            seen["bodies"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            seen["asked"].set()
            # The request is in. Wait for the answer's time while watching the socket: a read
            # of b"" is the gateway closing its end.
            self.connection.settimeout(0.05)
            until = time.monotonic() + seen["synth_s"]
            while time.monotonic() < until:
                try:
                    if self.connection.recv(1, socket.MSG_PEEK) == b"":
                        seen["dropped_at"] = time.monotonic()
                        seen["dropped"].set()
                        self.close_connection = True
                        return
                except (TimeoutError, socket.timeout):
                    continue
                except OSError:
                    seen["dropped_at"] = time.monotonic()
                    seen["dropped"].set()
                    self.close_connection = True
                    return
            self.connection.settimeout(None)
            body = json.dumps(AUDIO).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            seen["answered"] += 1

        def log_message(self, *_args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture()
async def gateway(monkeypatch, broker):
    """The real app, its broker client pointed at the stub, a signed-in user taken as given."""
    url, seen = broker
    client = BrokerClient(url)
    monkeypatch.setattr(m.app.state, "broker", client, raising=False)
    monkeypatch.setattr(m.app.state, "settings", GatewaySettings(), raising=False)
    monkeypatch.setitem(m.app.dependency_overrides, m.require_user,
                        lambda: SimpleNamespace(username="reader", is_admin=False))
    yield seen
    await client.aclose()


async def _read_aloud(gone: asyncio.Event, sent: list[dict]) -> None:
    """POST /api/platform/tts_light through the real app. receive() answers http.disconnect once
    `gone` is set, as uvicorn does when the connection closes; every message sent is kept."""
    body = json.dumps({"text": "Read this aloud, please."}).encode()
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(msg: dict) -> None:
        sent.append(msg)

    path = "/api/platform/tts_light"
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())],
             "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}
    await m.app(scope, receive, send)


async def _until(event: threading.Event, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not event.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return event.is_set()


async def test_a_read_whose_client_leaves_ends_the_brokers_request(gateway):
    """The old route awaited the broker to its answer: the stub saw no drop, and the call ended
    only when the audio came back, SYNTH_S later, for a client long gone."""
    seen = gateway
    gone = asyncio.Event()
    sent: list[dict] = []
    call = asyncio.ensure_future(_read_aloud(gone, sent))
    assert await _until(seen["asked"], 5), "the read never reached the broker"
    assert not call.done(), f"the read ended before its client left: {sent}"
    left_at = time.monotonic()
    gone.set()
    try:
        await asyncio.wait_for(call, 3)
    except asyncio.TimeoutError:
        pytest.fail("the gateway was still waiting on the broker 3 s after its client left")
    finally:
        if not call.done():
            call.cancel()
    assert await _until(seen["dropped"], 3), "the broker never saw the gateway let go of the read"
    assert seen["dropped_at"] - left_at < 3
    assert seen["answered"] == 0
    assert sent and sent[0]["status"] == 499, sent[:1]


async def test_a_read_whose_client_stays_gets_the_brokers_audio(gateway):
    """The witness: the race must not cost a client who waits its answer."""
    seen = gateway
    seen["synth_s"] = 0.3
    sent: list[dict] = []
    await asyncio.wait_for(_read_aloud(asyncio.Event(), sent), 15)
    assert sent[0]["status"] == 200, sent[:2]
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    assert json.loads(body) == AUDIO
    assert not seen["dropped"].is_set() and seen["answered"] == 1
    assert seen["bodies"] == [{"text": "Read this aloud, please."}]
