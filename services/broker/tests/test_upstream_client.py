"""The REAL `UpstreamClient`, driven over HTTP rather than swapped for a stub.

`test_upstream_delegation.py` replaces `Broker._upstream` with a stand-in, which is right for
what it tests (what the broker does with a remote's answers) and leaves the client itself
covered only by reading it: how it unwraps a remote broker's envelopes, what it does with a
body of the wrong shape, which timeout each call carries, and which failures it turns into
`UpstreamError` rather than letting an httpx exception escape a read view. Each of those has a
caller that depends on it: roles_view reads `models()` and `status()` without re-checking their
shape, and the Rails tab reads `healthy()` as a plain boolean.

Two transports, on purpose. `httpx.MockTransport` sits beneath the client's own
`httpx.AsyncClient`, so everything above the socket is the production code path, and it can
answer with any shape. It cannot make time pass, so the timeouts are ALSO proven against a real
local socket that accepts a connection and never answers.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from app import upstream as up_mod
from app.upstream import UpstreamClient, UpstreamError

URL = "http://gpu-box.example.internal:11500"


class _Box:
    """A remote broker at the transport level. Records every request and answers from a table
    of ``(method, path) -> (status, json body)`` or a callable taking the request."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        if callable(answer):
            return answer(request)
        status, body = answer
        return httpx.Response(status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"})


@pytest.fixture()
def remote(monkeypatch):
    """Install a `_Box` beneath every AsyncClient the client creates; returns an installer.
    The real class is captured ONCE: a second install in the same test would otherwise wrap
    the first stand-in and pass `transport` twice."""
    real = httpx.AsyncClient

    def install(routes: dict) -> _Box:
        box = _Box(routes)
        monkeypatch.setattr(up_mod.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(box), **kw))
        return box
    return install


def _run(coro):
    return asyncio.run(coro)


def _bounded(coro, seconds: float = 10.0):
    """For the real-socket tests: a client that lost its timeout must FAIL the test, not hang
    the suite waiting on a box that will never answer."""
    return asyncio.run(asyncio.wait_for(coro, timeout=seconds))


# --- unwrapping: the envelope a broker sends, and the shapes a remote of another make sends ---

ROWS = [{"name": "mistral-small3.2:24b"}]


@pytest.mark.parametrize("method,path,key", [("models", "/v1/models", "models"),
                                             ("roles", "/v1/roles", "roles")])
@pytest.mark.parametrize("body,expect", [
    ("ENVELOPE", ROWS),            # this broker's own shape: {"<key>": [...]}
    (ROWS, ROWS),                  # a bare list, as an older or foreign broker answers
    ({"detail": "x"}, []),         # an object without the key: nothing, not the object
    (None, []),                    # JSON null: nothing, not None
], ids=["envelope", "bare-list", "object-without-key", "null"])
def test_a_list_read_unwraps_the_envelope_and_tolerates_other_shapes(
        remote, method, path, key, body, expect):
    """roles_view iterates whatever these return. Handing it the envelope dict instead would
    iterate its KEYS, and handing it None would raise out of a read view."""
    remote({("GET", path): (200, {key: ROWS} if body == "ENVELOPE" else body)})
    assert _run(getattr(UpstreamClient("offsite", URL), method)()) == expect


def test_status_passes_an_object_and_replaces_anything_else(remote):
    """roles_view reads `status_r.get("loaded")` on the result. A list must arrive as {}."""
    remote({("GET", "/v1/status"): (200, {"loaded": [{"name": "m"}]})})
    assert _run(UpstreamClient("offsite", URL).status()) == {"loaded": [{"name": "m"}]}
    remote({("GET", "/v1/status"): (200, [{"name": "m"}])})
    assert _run(UpstreamClient("offsite", URL).status()) == {}


# --- healthy(): a boolean, never an exception -------------------------------------------------

@pytest.mark.parametrize("answer,expect", [
    ((200, {"status": "ok", "ollama_reachable": True}), True),
    ((200, {"status": "degraded"}), False),
    ((503, {"status": "degraded", "ollama_reachable": False}), False),
    ((200, ["ok"]), False),
], ids=["ok", "degraded-200", "degraded-503", "not-an-object"])
def test_healthy_is_a_plain_boolean_for_every_answer(remote, answer, expect):
    """The broker's own /healthz answers 503 when its Ollama is down, which raise_for_status
    turns into an error; a foreign service on that port can answer with a list. Either way the
    Rails tab and roles_view need True or False, not a traceback."""
    remote({("GET", "/healthz"): answer})
    assert _run(UpstreamClient("offsite", URL).healthy()) is expect


def test_healthy_is_false_when_nothing_answers(remote):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)
    remote({("GET", "/healthz"): refuse})
    assert _run(UpstreamClient("offsite", URL).healthy()) is False


# --- failures are UpstreamError, naming the box ----------------------------------------------

def test_a_refused_read_names_the_box_the_path_and_the_status(remote):
    """"The box refuses us" needs a different fix from "the box is down": the status code is
    the difference, so it has to survive into the message."""
    remote({("GET", "/v1/models"): (401, {"detail": "invalid or missing broker token"})})
    with pytest.raises(UpstreamError) as exc:
        _run(UpstreamClient("offsite", URL).models())
    msg = str(exc.value)
    assert "offsite" in msg and "GET /v1/models" in msg and "401" in msg
    assert "unreachable" not in msg, "a box that refuses us was reported as one that is down"


def test_an_unreachable_read_is_an_upstream_error_not_an_httpx_one(remote):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)
    remote({("GET", "/v1/models"): refuse})
    with pytest.raises(UpstreamError) as exc:
        _run(UpstreamClient("offsite", URL).models())
    assert "unreachable" in str(exc.value)


def test_a_refused_chat_is_an_upstream_error_naming_the_status(remote):
    remote({("POST", "/v1/chat"): (409, {"detail": "no substitute"})})
    with pytest.raises(UpstreamError) as exc:
        _run(UpstreamClient("offsite", URL).chat("m", [{"role": "user", "content": "q"}]))
    assert "POST /v1/chat" in str(exc.value) and "409" in str(exc.value)


# --- the wire ------------------------------------------------------------------------------

def test_a_trailing_slash_in_the_registry_does_not_double_the_path(remote):
    """upstreams.json is hand-edited, and `http://box:11500/` is how a URL is usually pasted."""
    box = remote({("GET", "/v1/models"): (200, {"models": []})})
    _run(UpstreamClient("offsite", URL + "/").models())
    assert str(box.requests[0].url) == URL + "/v1/models"


def test_a_token_is_sent_in_both_header_spellings(remote):
    """The remote may run either generation of require_token(): one reads the bearer form,
    the older one X-Broker-Token."""
    box = remote({("GET", "/v1/models"): (200, {"models": []})})
    _run(UpstreamClient("offsite", URL, token="s3cret").models())
    headers = box.requests[0].headers
    assert headers["authorization"] == "Bearer s3cret"
    assert headers["x-broker-token"] == "s3cret"


def test_no_token_sends_no_auth_header_at_all(remote):
    """An empty `Bearer ` is a malformed credential, not an absent one."""
    box = remote({("GET", "/v1/models"): (200, {"models": []})})
    _run(UpstreamClient("offsite", URL).models())
    headers = box.requests[0].headers
    assert "authorization" not in headers and "x-broker-token" not in headers


def test_chat_sends_only_the_fields_that_were_set_and_returns_the_answer(remote):
    """None means "the remote's default". Sent as null, `think: null` or `format: null` would
    be a value the remote has to interpret instead of an absence."""
    box = remote({("POST", "/v1/chat"): (200, {"message": {"content": "hi"}, "done": True})})
    client = UpstreamClient("offsite", URL)
    out = _run(client.chat("m", [{"role": "user", "content": "q"}]))
    assert out == {"message": {"content": "hi"}, "done": True}
    assert json.loads(box.requests[0].content) == {
        "model": "m", "messages": [{"role": "user", "content": "q"}]}
    _run(client.chat("m", [{"role": "user", "content": "q"}], options={"num_predict": 9},
                     format="json", think=False))
    sent = json.loads(box.requests[1].content)
    assert sent["options"] == {"num_predict": 9}
    assert sent["format"] == "json" and sent["think"] is False
    assert "keep_alive" not in sent


# --- timeouts: the values each call carries -------------------------------------------------

def _read_timeout(box: _Box, i: int = 0):
    return box.requests[i].extensions["timeout"]["read"]


def test_the_health_probe_carries_the_short_timeout(remote):
    """roles_view budgets a sweep as healthz (5 s) plus one bounded read, and the Rails tab
    waits on it. A health probe on the 30 s read timeout would make a dark box cost 30 s."""
    box = remote({("GET", "/healthz"): (200, {"status": "ok"})})
    _run(UpstreamClient("offsite", URL).healthy())
    assert _read_timeout(box) == 5.0


@pytest.mark.parametrize("method", ["models", "roles", "status"])
def test_every_read_carries_the_read_timeout(remote, method):
    box = remote({("GET", f"/v1/{method}"): (200, {})})
    _run(getattr(UpstreamClient("offsite", URL), method)())
    assert _read_timeout(box) == 30.0


def test_a_chat_carries_the_clients_own_generation_timeout(remote):
    """Generation is slow, and the configured timeout is the one the operator sized for it."""
    box = remote({("POST", "/v1/chat"): (200, {"message": {"content": "x"}})})
    _run(UpstreamClient("offsite", URL).chat("m", [{"role": "user", "content": "q"}]))
    _run(UpstreamClient("offsite", URL, timeout=42.0).chat("m", [{"role": "user", "content": "q"}]))
    assert _read_timeout(box, 0) == 600.0
    assert _read_timeout(box, 1) == 42.0


# --- timeouts: actually enforced, against a socket that never answers -----------------------

@pytest.fixture()
def silent_box():
    """A real listening socket that never accepts. The kernel completes the handshake into the
    backlog, the request is written, and no byte ever comes back: a box that is up and hung."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    try:
        yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    finally:
        srv.close()


def test_a_hung_box_fails_the_health_probe_at_its_timeout(silent_box, monkeypatch):
    monkeypatch.setattr(up_mod, "_HEALTH_TIMEOUT", 0.3)
    t0 = time.monotonic()
    assert _bounded(UpstreamClient("offsite", silent_box).healthy()) is False
    assert time.monotonic() - t0 < 5


def test_a_hung_box_fails_a_read_at_its_timeout(silent_box, monkeypatch):
    monkeypatch.setattr(up_mod, "_READ_TIMEOUT", 0.3)
    t0 = time.monotonic()
    with pytest.raises(UpstreamError) as exc:
        _bounded(UpstreamClient("offsite", silent_box).models())
    assert time.monotonic() - t0 < 5
    assert "unreachable" in str(exc.value)


def test_a_hung_box_fails_a_chat_at_the_generation_timeout(silent_box):
    t0 = time.monotonic()
    with pytest.raises(UpstreamError):
        _bounded(UpstreamClient("offsite", silent_box, timeout=0.3).chat(
            "m", [{"role": "user", "content": "q"}]))
    assert time.monotonic() - t0 < 5


# --- the broker's /v1/models?upstream=X: a read VIEW, so bounded like the others ------------

def _broker_with(url: str):
    from app.broker import Broker
    b = Broker.__new__(Broker)
    b.settings = SimpleNamespace(upstreams=lambda: {"dark": {"url": url, "token": ""}})
    b._upstream = lambda name, registry=None: UpstreamClient(name, url)
    return b


def test_a_remote_inventory_read_is_bounded_by_the_view_timeout_not_the_read_timeout(
        silent_box, monkeypatch):
    """The admin picker's per-box list. It used to wait out the client's 30 s read timeout on a
    box that accepts and never answers; the gateway gives up sooner than that. Here the read
    timeout is set far above the test's own bound, so only the view bound can end the call."""
    import app.broker as broker_mod
    monkeypatch.setattr(up_mod, "_READ_TIMEOUT", 60.0)
    monkeypatch.setattr(broker_mod, "_VIEW_PROBE_TIMEOUT", 0.3)
    t0 = time.monotonic()
    with pytest.raises(UpstreamError) as exc:
        _bounded(_broker_with(silent_box).models_view("dark"), seconds=10.0)
    assert time.monotonic() - t0 < 5
    assert "dark" in str(exc.value)


def test_the_route_answers_502_naming_the_box_not_a_500(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import BrokerSettings
    from app.main import app

    async def _dark(upstream=None):
        raise UpstreamError("upstream 'dark' did not list its models within 8 s")

    monkeypatch.setattr(app.state, "settings", BrokerSettings(
        tokens_file=str(tmp_path / "tokens.json"), auth_token=""), raising=False)
    monkeypatch.setattr(app.state, "broker", SimpleNamespace(models_view=_dark), raising=False)
    resp = TestClient(app).get("/v1/models", params={"upstream": "dark"})
    assert resp.status_code == 502
    assert "dark" in resp.json()["detail"]


# --- the broker's /v1/upstreams: every box probed at once, each under its own bound ------------
#
# Admin > Rails reads it on every load, and it probed the registered boxes one after another, so
# N dark boxes cost N health timeouts back to back before the tab could draw.

def _upstreams_broker(boxes: dict[str, str], client):
    """A broker whose registry is `boxes` (name -> url) and whose `_upstream(name)` is `client`."""
    from app.broker import Broker
    b = Broker.__new__(Broker)
    b.settings = SimpleNamespace(
        upstreams=lambda: {n: {"url": u, "token": "t"} for n, u in boxes.items()})
    b._upstream = client
    return b


def test_the_upstreams_view_probes_two_hung_boxes_at_once(silent_box, monkeypatch):
    """Two boxes that accept and never answer, through the real client: each probe ends at the
    health timeout, and the second box is dialled before the first one's probe has ended.

    The verdict is the order of the probes, not a stopwatch. `took < 1.0` against a 0.6 s
    timeout sat 0.4 s above an idle run and 0.2 s under the cost of probing in turn, so a slow
    probe on a loaded host read as two in turn."""
    monkeypatch.setattr(up_mod, "_HEALTH_TIMEOUT", 0.6)
    shared = UpstreamClient("box", silent_box, "t")
    real_healthy = shared.healthy
    log: list[str] = []

    async def healthy():
        log.append("start")
        try:
            return await real_healthy()
        finally:
            log.append("end")

    monkeypatch.setattr(shared, "healthy", healthy)
    b = _upstreams_broker({"offsite": silent_box, "lab": silent_box}, lambda name: shared)
    view = _bounded(b.upstreams_view())
    assert view == [{"name": "local", "url": "", "healthy": True},
                    {"name": "lab", "url": silent_box, "healthy": False},
                    {"name": "offsite", "url": silent_box, "healthy": False}]
    assert log == ["start", "start", "end", "end"], (
        f"{log}: the second box was dialled only once the first one's probe had ended, in turn")


class _NeverAnswers:
    """A box whose health probe never returns, whatever the client's own timeouts say. With a
    `log`, each probe writes "start" when it is dialled and "cut" when the caller's bound
    cancels it, so a test reads the order of the probes instead of timing them."""

    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []

    async def healthy(self):
        self.log.append("start")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.log.append("cut")
            raise
        return True


def test_a_box_that_never_answers_costs_the_upstreams_view_one_bound(monkeypatch):
    """httpx's timeout bounds each socket operation, not the call, so a box that trickles its
    answer can outlast it. The view bounds each probe itself, and a probe that ran out is a box
    that is down, never a raise."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_VIEW_PROBE_TIMEOUT", 0.5)
    log: list[str] = []
    b = _upstreams_broker({"offsite": URL, "lab": "http://lab.example.internal:11500"},
                          lambda name: _NeverAnswers(log))
    view = _bounded(b.upstreams_view(), seconds=5.0)
    assert [(u["name"], u["healthy"]) for u in view] == [
        ("local", True), ("lab", False), ("offsite", False)]
    assert all("token" not in u for u in view), "a token must never be published"
    # Both dialled before either bound ran out, and each ended by its bound: one bound in all.
    assert log == ["start", "start", "cut", "cut"], (
        f"{log}: the second silent box was dialled only once the first one's bound had run out")


# --- one HTTP client per box, reused by every sweep -------------------------------------------
#
# Every UpstreamClient call used to open its own httpx client, and the broker built a new
# UpstreamClient for every call as well. Building an httpx client is not free: it loads an SSL
# context and the certifi bundle, measured at ~0.45-0.5 s on the live box, so /v1/upstreams took
# 0.8 s against a remote whose /healthz answers in 0.06 s, and a roles sweep paid it three times per
# delegated box (healthz, models, status). Now the broker holds one client per box, keyed by the
# box's url and token so an edited upstreams.json still takes effect on the next call, and per
# running event loop, because a client's pooled connections belong to the loop that opened them.

REMOTE = {("GET", "/healthz"): (200, {"status": "ok"}),
          ("GET", "/v1/models"): (200, {"models": [{"name": "qwen3:30b",
                                                    "parameter_size": "30B"}]}),
          ("GET", "/v1/status"): (200, {"loaded": []})}


class _LocalOllama:
    """This box's Ollama, with nothing installed: only the delegated row is under test."""

    async def tags(self):
        return []

    async def aclose(self):
        pass


def _registry(tmp_path, url: str, token: str = "") -> None:
    (tmp_path / "upstreams.json").write_text(
        json.dumps({"offsite": {"url": url, "token": token}}), encoding="utf-8")


def _delegating_broker(tmp_path, url: str, token: str = ""):
    """A real Broker on real settings files, with @chat delegated to `offsite`. Built BEFORE a
    test installs its client counter, so the broker's own Ollama client is not counted."""
    from app.broker import Broker
    from app.config import BrokerSettings
    (tmp_path / "roles.json").write_text(json.dumps({"chat": "offsite::qwen3*:30b"}),
                                         encoding="utf-8")
    _registry(tmp_path, url, token)
    b = Broker(BrokerSettings(
        roles_file=str(tmp_path / "roles.json"), disabled_file=str(tmp_path / "disabled.json"),
        tokens_file=str(tmp_path / "tokens.json"),
        upstreams_file=str(tmp_path / "upstreams.json"), auth_token=""))
    b.ollama = _LocalOllama()
    return b


def _chat_row(rows):
    return next(r for r in rows if r["role"] == "chat")


@pytest.fixture()
def counted(monkeypatch):
    """Like `remote`, and also keeps every httpx client the upstream module builds."""
    real = httpx.AsyncClient

    def install(routes: dict):
        box = _Box(routes)
        made: list[httpx.AsyncClient] = []

        def build(**kw):
            client = real(transport=httpx.MockTransport(box), **kw)
            made.append(client)
            return client
        monkeypatch.setattr(up_mod.httpx, "AsyncClient", build)
        return box, made
    return install


def test_a_box_gets_one_http_client_however_many_sweeps_read_it(tmp_path, counted):
    b = _delegating_broker(tmp_path, URL)
    box, made = counted(REMOTE)

    async def sweeps():
        return [await b.roles_view() for _ in range(2)]

    first, second = asyncio.run(sweeps())
    assert _chat_row(first)["installed"] and _chat_row(second)["installed"]
    assert len(box.requests) == 6          # healthz, models and status, per sweep
    assert len(made) == 1, f"{len(made)} HTTP clients for one box over two sweeps"


def test_an_edited_registry_reaches_the_new_address_and_token_on_the_next_call(tmp_path, counted):
    """upstreams.json is hot-read. A client kept per box must not keep dialling the address it
    was built with, or sending the token it was built with, after the file changes."""
    b = _delegating_broker(tmp_path, URL, token="one")
    box, made = counted(REMOTE)
    moved = "http://gpu-box-2.example.internal:11500"

    async def run():
        await b.roles_view()
        _registry(tmp_path, moved, token="one")
        await b.roles_view()
        _registry(tmp_path, moved, token="two")
        await b.roles_view()
        await b.roles_view()                    # unchanged since the last: the same client
        await b.aclose()

    asyncio.run(run())
    sent = [(r.url.host, r.headers.get("x-broker-token")) for r in box.requests]
    assert sent[0:3] == [("gpu-box.example.internal", "one")] * 3
    assert sent[3:6] == [("gpu-box-2.example.internal", "one")] * 3
    assert sent[6:12] == [("gpu-box-2.example.internal", "two")] * 6
    assert len(made) == 3, f"{len(made)} clients for three distinct (url, token) pairs"
    # Each replaced client is closed with the broker, not left holding its connections.
    assert all(c.is_closed for c in made), [c.is_closed for c in made]


def test_closing_the_broker_closes_its_box_clients(tmp_path, counted):
    b = _delegating_broker(tmp_path, URL)
    _box, made = counted(REMOTE)

    async def run():
        await b.roles_view()
        assert made and not any(c.is_closed for c in made)
        await b.aclose()

    asyncio.run(run())
    assert made and all(c.is_closed for c in made)


@pytest.fixture()
def live_box():
    """A real HTTP/1.1 server that keeps its connections open, so a client's pooled connection
    outlives the request that opened it. MockTransport has no connections, so it cannot show
    what goes wrong when a client is carried into another event loop: this can."""
    bodies = {path: body for (_method, path), (_status, body) in REMOTE.items()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - the stdlib's name
            body = json.dumps(bodies.get(self.path, {})).encode()
            self.send_response(200 if self.path in bodies else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_client_is_never_carried_into_another_event_loop(tmp_path, live_box):
    """Each asyncio.run is a new loop, as is each request a TestClient makes outside a `with`.
    A client reused from a finished loop fails on its pooled connection ("Event loop is
    closed"), and the read view reports the box as down."""
    b = _delegating_broker(tmp_path, live_box)
    first = asyncio.run(b.roles_view())
    second = asyncio.run(b.roles_view())
    assert _chat_row(first)["installed"] is True
    assert _chat_row(second)["installed"] is True, "the second loop could not read the box"


# --- the roles sweep's own health probe: bounded like the upstreams view's ----------------------
#
# The sweep bounded every read of a box at _VIEW_PROBE_TIMEOUT, but not the /healthz that gates
# those reads. httpx's timeout is per socket operation, so a box that accepts and trickles its
# health answer held /v1/roles (every rail's chips) and the Rails tab's inventory past it.

def test_a_box_whose_health_never_answers_costs_the_roles_sweep_one_bound(tmp_path, monkeypatch):
    """The verdict is what the probe saw: dialled once and cut by the sweep's own bound, inside
    a 5 s ceiling that only an unbounded probe (a 60 s one here) can reach. It was `took <
    0.85` against the 0.5 s bound, 0.35 s of headroom that a loaded host could use up."""
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_VIEW_PROBE_TIMEOUT", 0.5)
    b = _delegating_broker(tmp_path, URL)
    log: list[str] = []
    b._upstream = lambda name, registry=None: _NeverAnswers(log)

    row = _chat_row(_bounded(b.roles_view(), seconds=5.0))
    assert row["installed"] is False and row["loaded"] is False and row["resolved"] is None
    assert log == ["start", "cut"], f"{log}: the silent box's health was not cut by the bound"

    log.clear()
    view = _bounded(b.roles_inventory(), seconds=5.0)
    assert view["upstreams"] == [{"name": "local", "url": "", "healthy": True},
                                 {"name": "offsite", "url": URL, "healthy": False}]
    assert view["models"]["offsite"] == []
    assert log == ["start", "cut"], (
        f"{log}: the inventory's one probe of the silent box was not cut by the bound")


# --- a body that is not JSON (audit 2026-10-14) --------------------------------------------------
#
# A registered address that answers 200 with a page (a login portal, a proxy's error page, the
# wrong service on the port) made `r.json()` raise JSONDecodeError. That is a ValueError, which
# /v1/models?upstream=X answered 404 "not found", and healthy() raised despite "never raises".

def _page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html><body>Sign in</body></html>",
                          headers={"content-type": "text/html"})


@pytest.mark.parametrize("method", ["models", "roles", "status"])
def test_a_read_answered_with_a_page_is_an_upstream_error_naming_the_box(remote, method):
    remote({("GET", f"/v1/{method}"): _page})
    with pytest.raises(UpstreamError) as exc:
        _run(getattr(UpstreamClient("offsite", URL), method)())
    msg = str(exc.value)
    assert "offsite" in msg and f"GET /v1/{method}" in msg and "not JSON" in msg, msg


def test_a_chat_answered_with_a_page_is_an_upstream_error(remote):
    remote({("POST", "/v1/chat"): _page})
    with pytest.raises(UpstreamError) as exc:
        _run(UpstreamClient("offsite", URL).chat("m", [{"role": "user", "content": "q"}]))
    assert "POST /v1/chat" in str(exc.value) and "not JSON" in str(exc.value)


def test_healthy_is_false_for_a_page(remote):
    remote({("GET", "/healthz"): _page})
    assert _run(UpstreamClient("offsite", URL).healthy()) is False


def test_the_inventory_route_answers_502_for_a_box_that_answers_with_a_page(tmp_path, remote):
    """The backlog's check, through the route: 502 naming the box, not 404 "not found"."""
    from fastapi.testclient import TestClient

    from app.main import app

    b = _delegating_broker(tmp_path, URL)
    remote({("GET", "/v1/models"): _page})
    app.state.settings = b.settings
    app.state.broker = b
    resp = TestClient(app).get("/v1/models", params={"upstream": "offsite"})
    assert resp.status_code == 502, resp.text
    assert "offsite" in resp.json()["detail"]


# --- keep-alive: a pooled connection is not reused near the remote's own idle timeout ----------
#
# A remote broker runs uvicorn, which closes a connection idle for timeout_keep_alive (5 s by
# default). httpx's pool also kept idle connections for 5 s, so a call made on one idle about 5 s
# could be sent just as the remote closed it: "Server disconnected without sending a response",
# a 502 for a request the remote never read. The server below makes that window deterministic:
# a request that arrives on a connection idle for `idle_close` seconds or more is dropped unread,
# as a keep-alive timeout that crossed it would. The time scale is shrunk 10x for the test, and
# the client's expiry with it.

@pytest.fixture()
def idle_closing_box():
    """A real HTTP/1.1 broker stand-in that keeps connections open, counts them, and drops a
    request that arrives after `idle_close` seconds of idleness on its connection."""
    stats = {"connections": 0, "dropped": 0, "idle_close": 0.5}

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
            getattr(self, "do_" + self.command)()
            self.wfile.flush()
            self._idle_since = time.monotonic()

        def do_POST(self):  # noqa: N802 - the stdlib's name
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"message": {"content": "ok"}, "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", stats
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_call_after_an_idle_spell_is_not_sent_on_a_connection_the_remote_is_closing(
        idle_closing_box, monkeypatch):
    """Scaled 10x: the remote drops at 0.5 s idle (uvicorn's 5 s), the client's pool expires its
    idle connections at the shipped ratio (2 s against 5 s). The second call follows the first at
    once and reuses its connection; the third comes 0.6 s later, past the remote's limit, and must
    go out on a fresh connection rather than the one the remote drops."""
    url, stats = idle_closing_box
    monkeypatch.setattr(up_mod, "_KEEPALIVE_EXPIRY",
                        getattr(up_mod, "_KEEPALIVE_EXPIRY", 5.0) / 10, raising=False)
    client = UpstreamClient("offsite", url)
    msgs = [{"role": "user", "content": "q"}]

    async def run():
        out = [await client.chat("m", msgs), await client.chat("m", msgs)]
        await asyncio.sleep(0.6)
        out.append(await client.chat("m", msgs))
        await client.aclose()
        return out

    answers = _bounded(run())
    assert [a["message"]["content"] for a in answers] == ["ok"] * 3
    assert stats["dropped"] == 0, "a call went out on a connection the remote was closing"
    assert stats["connections"] == 2, (
        f"{stats['connections']} connections for three calls: the pool should reuse a warm one")


def test_the_pool_expires_idle_connections_well_inside_uvicorn_s_keep_alive():
    """The constant the scaled test above multiplies: well under the 5 s a remote broker keeps an
    idle connection, so a reused connection is never one the remote is about to close."""
    assert up_mod._KEEPALIVE_EXPIRY <= 2.0


# --- a client replaced by an upstreams.json edit is closed once nothing uses it -------------------
#
# The broker swaps a box's client when its url or token changes, and kept the old one open until
# the broker itself closed: every edit left a pool of connections open for the life of the process.
# It cannot be closed AT the swap, because a delegated stream may still be reading through it.

def test_a_replaced_client_is_closed_when_its_last_stream_ends_not_at_shutdown(tmp_path, counted):
    b = _delegating_broker(tmp_path, URL, token="one")
    seen: dict = {}

    async def run():
        release = asyncio.Event()

        async def lines():
            yield b'{"message": {"content": "a"}, "done": false}\n'
            await release.wait()
            yield b'{"done": true}\n'

        _box, made = counted({**REMOTE, ("POST", "/v1/chat/stream"):
                              lambda request: httpx.Response(200, content=lines())})
        streaming = b._upstream("offsite")
        gen = streaming.chat_stream("qwen3:30b", [{"role": "user", "content": "q"}])
        frames = [await gen.__anext__()]
        _registry(tmp_path, URL, token="two")
        fresh = b._upstream("offsite")               # the edit retires the streaming client
        await asyncio.sleep(0)
        seen["open_mid_stream"] = not made[0].is_closed
        release.set()
        frames += [f async for f in gen]
        await asyncio.sleep(0)
        seen["closed_after_stream"] = made[0].is_closed
        await fresh.models()                          # in use once, idle now
        _registry(tmp_path, URL, token="three")
        assert b._upstream("offsite") is not fresh
        await asyncio.sleep(0)
        seen["idle_closed_at_swap"] = made[1].is_closed
        seen["frames"] = frames
        await b.aclose()

    asyncio.run(run())
    assert seen["frames"] == [{"message": {"content": "a"}, "done": False}, {"done": True}]
    assert seen["open_mid_stream"], "the edit closed a client a stream was still reading through"
    assert seen["closed_after_stream"], "the replaced client stayed open after its stream ended"
    assert seen["idle_closed_at_swap"], "an idle replaced client stayed open until shutdown"


def test_closing_the_broker_closes_a_replaced_client_a_stream_is_still_reading(tmp_path, counted):
    """The broker keeps a busy replaced client, so its own close still reaches it at shutdown."""
    b = _delegating_broker(tmp_path, URL, token="one")

    async def run():
        release = asyncio.Event()

        async def lines():
            yield b'{"message": {"content": "a"}, "done": false}\n'
            await release.wait()
            yield b'{"done": true}\n'

        _box, made = counted({**REMOTE, ("POST", "/v1/chat/stream"):
                              lambda request: httpx.Response(200, content=lines())})
        gen = b._upstream("offsite").chat_stream("qwen3:30b", [{"role": "user", "content": "q"}])
        await gen.__anext__()
        _registry(tmp_path, URL, token="two")
        b._upstream("offsite")
        await b.aclose()
        closed = made[0].is_closed       # before the stream is finished or asyncio.run tidies up
        release.set()
        with contextlib.suppress(Exception):
            await gen.aclose()
        return closed

    assert asyncio.run(run()), "shutdown left a replaced client open under its stream"


def test_a_replaced_client_that_is_used_again_does_not_stay_open(tmp_path, counted):
    """A caller can still hold a client the edit retired: route() handed it out just before, or a
    roles sweep's probe holds it between its health read and the reads after. Each such use opens
    a pool of its own, closed again when the use ends, and never reuses one already closed."""
    b = _delegating_broker(tmp_path, URL, token="one")
    _box, made = counted(REMOTE)

    async def run():
        held = b._upstream("offsite")
        _registry(tmp_path, URL, token="two")
        b._upstream("offsite")
        await asyncio.sleep(0)
        answers = [await held.models()]
        await asyncio.sleep(0)
        answers.append(await held.models())
        await asyncio.sleep(0)
        closed = [c.is_closed for c in made]          # before the broker's own close
        await b.aclose()
        return answers, closed

    answers, closed = asyncio.run(run())
    assert answers == [[{"name": "qwen3:30b", "parameter_size": "30B"}]] * 2
    assert closed == [True, True], "a retired client's reopened pool stayed open"
