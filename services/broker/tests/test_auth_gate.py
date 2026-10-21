"""The token check and the body ceiling run before a route reads a byte of the body.

require_token is a FastAPI dependency, and FastAPI reads and parses a route's body before it
solves one: with BROKER_AUTH_TOKEN set, an unauthenticated 200 MB POST to /v1/chat was read whole
into memory and answered 422, not 401, and a multipart upload to /openai/v1/audio/transcriptions
was spooled to temp before its 401. Nothing bounded a body at all: uvicorn sets no limit.

`_TokenAndBodyGate` now sits in front of routing. With a credential configured it makes
require_token's own decision (`_authorise`) first, so a missing or wrong token is a 401 and an
inference token on a full-scope route a 403 before any body is read. Every route then has a
ceiling: a declared length over it is a 413 before a byte is read, and a body that declares none
is counted and cut off one chunk past it. An open broker (nothing configured) is only bounded.

Through the REAL ASGI app, with a client that hands the body over one chunk at a time and counts
what was taken. The gateway's test_platform_body_ceiling.py is the model.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from app import main as m
from app import openai_compat
from app.config import BrokerSettings
from app.schemas import TRANSCRIBE_MAX_B64, TTS_LIGHT_MAX_CHARS

CHUNK = 64 * 1024
MIB = 1024 * 1024
SHARED = "shared-secret"


class _Client:
    """Hands the app its body a chunk at a time, on demand, and counts what was taken."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks) or [b""]
        self._i = 0
        self.calls = 0
        self.read = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        if self._i < len(self._chunks):
            chunk = self._chunks[self._i]
            self._i += 1
            self.calls += 1
            self.read += len(chunk)
            return {"type": "http.request", "body": chunk,
                    "more_body": self._i < len(self._chunks)}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    @property
    def status(self) -> int:
        return next(x["status"] for x in self.sent if x["type"] == "http.response.start")

    @property
    def body(self) -> bytes:
        return b"".join(x.get("body", b"") for x in self.sent if x["type"] == "http.response.body")

    def json(self):
        return json.loads(self.body)


def _chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def _junk(total: int) -> list[bytes]:
    """`total` bytes of a body, as chunks that are all ONE bytes object: 200 MB costs 64 KB."""
    one = b"x" * CHUNK
    return [one] * (total // CHUNK)


def _scope(method: str, path: str, headers: list[tuple[str, str | bytes]]) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v if isinstance(v, bytes) else v.encode("latin-1"))
                        for k, v in headers],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}


def _named(label: str, scope: str, plain: str) -> dict:
    return {"id": label, "label": label, "scope": scope, "prefix": plain[:11],
            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(), "created": "now"}


class _Broker:
    """What the routes these tests reach call. Records that a route ran, and with how much."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.ran: list[str] = []
        self.ollama = SimpleNamespace(version=self._version, base_url="http://ollama.test")

    async def _version(self):
        return "0.0-test"

    async def chat(self, model, messages, **kw):
        self.ran.append(f"chat:{sum(len(x.get('content', '')) for x in messages)}")
        return {"message": {"role": "assistant", "content": "hi"}, "done": True}

    async def transcribe(self, audio_b64, suffix=None, language=None, client_gone=None):
        self.ran.append(f"transcribe:{len(audio_b64)}")
        return {"text": "hello"}

    async def roles_view(self):
        return []


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    """The real app on settings whose files live in tmp_path. `configure(...)` picks the
    credentials: the shared secret, named tokens (a store), both, or neither (open)."""
    state = {}

    def configure(*, shared: str = "", named: list[dict] | None = None):
        tokens = tmp_path / "tokens.json"
        if named is not None:
            tokens.write_text(json.dumps(named), encoding="utf-8")
        settings = BrokerSettings(tokens_file=str(tokens), auth_token=shared,
                                  roles_file=str(tmp_path / "roles.json"),
                                  disabled_file=str(tmp_path / "disabled.json"),
                                  upstreams_file=str(tmp_path / "upstreams.json"))
        broker = _Broker(settings)
        monkeypatch.setattr(m.app.state, "settings", settings, raising=False)
        monkeypatch.setattr(m.app.state, "broker", broker, raising=False)
        monkeypatch.setattr(m.app.state, "token_seen", {}, raising=False)
        state["broker"] = broker
        return broker

    async def call(method: str, path: str, chunks: list[bytes],
                   headers: list[tuple[str, str | bytes]]) -> _Client:
        client = _Client(chunks)
        await asyncio.wait_for(m.app(_scope(method, path, headers), client.receive,
                                     client.send), timeout=30)
        return client

    call.configure = configure          # type: ignore[attr-defined]
    call.state = state                  # type: ignore[attr-defined]
    return call


JSON = ("content-type", "application/json")


def _length(n: int) -> tuple[str, str]:
    return ("content-length", str(n))


CHUNKED = ("transfer-encoding", "chunked")


#: The bytes of a /v1/chat body around its one message's content.
_CHAT_CORE = len(json.dumps({"model": "@chat", "messages": [{"role": "user", "content": ""}]}))


def _chat(size: int) -> bytes:
    """A valid /v1/chat body of exactly `size` bytes (the message content is padding)."""
    pad = size - _CHAT_CORE
    assert pad >= 0
    return json.dumps({"model": "@chat",
                       "messages": [{"role": "user", "content": "a" * pad}]}).encode()


# --- a credential is checked before the body is read --------------------------------------------

@pytest.mark.parametrize("credential", [
    [], [("authorization", "Bearer wrong")], [("x-broker-token", "wrong")],
], ids=["none", "wrong-bearer", "wrong-x-broker-token"])
@pytest.mark.parametrize("framing", ["declared", "chunked"])
async def test_an_unauthenticated_200_mb_chat_is_a_401_before_a_byte_is_read(
        gate, credential, framing):
    gate.configure(shared=SHARED)
    size = 200 * MIB
    frame = _length(size) if framing == "declared" else CHUNKED
    client = await gate("POST", "/v1/chat", _junk(size), [JSON, frame, *credential])
    assert client.status == 401, client.body[:200]
    assert client.json() == {"detail": "invalid or missing broker token"}
    assert client.calls <= 1 and client.read <= CHUNK, (
        f"the broker took {client.calls} chunks ({client.read} bytes) before refusing")
    assert gate.state["broker"].ran == []


async def test_an_unauthenticated_upload_to_transcriptions_is_a_401_before_it_is_spooled(gate):
    gate.configure(named=[_named("studio", "inference", "bt_studio")])
    boundary = "x" * 16
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"a.webm\"\r\nContent-Type: audio/webm\r\n\r\n").encode()
    chunks = [head, *_junk(10 * MIB)]
    client = await gate("POST", "/openai/v1/audio/transcriptions", chunks,
                        [("content-type", f"multipart/form-data; boundary={boundary}"),
                         _length(len(head) + 10 * MIB)])
    assert client.status == 401
    assert client.calls <= 1


async def test_an_inference_token_on_a_full_scope_route_is_a_403_before_a_byte_is_read(gate):
    gate.configure(shared=SHARED, named=[_named("studio", "inference", "bt_studio")])
    client = await gate("PUT", "/v1/roles/chat", _junk(4 * MIB),
                        [JSON, _length(4 * MIB), ("authorization", "Bearer bt_studio")])
    assert client.status == 403, client.body[:200]
    assert "inference" in client.json()["detail"] and client.calls == 0


@pytest.mark.parametrize("header", [b"Bearer \xe9t\xe9", b"Bearer \xff"])
async def test_a_bearer_outside_ascii_is_a_401_not_a_500(gate, header):
    """compare_digest raises TypeError on a str with any character past ASCII, and a server
    decodes a header as Latin-1, so such a token used to be a 500 from the shared-secret
    compare. It is compared as bytes now: one more wrong token."""
    gate.configure(shared=SHARED, named=[])
    client = await gate("GET", "/v1/disabled", [b""], [("authorization", header)])
    assert client.status == 401, client.body[:200]


async def test_a_bearer_outside_ascii_is_a_401_from_require_token_too():
    """The dependency makes the same compare for every route the gate let through."""
    from fastapi import HTTPException
    req = SimpleNamespace(
        url=SimpleNamespace(path="/v1/status"), method="GET", state=SimpleNamespace(),
        headers={"authorization": "Bearer \xe9t\xe9"},
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(
            auth_token=SHARED, tokens=lambda: [], tokens_configured=lambda: True),
            token_seen={})))
    with pytest.raises(HTTPException) as exc:
        m.require_token(req)
    assert exc.value.status_code == 401


# --- what still gets through ---------------------------------------------------------------------

@pytest.mark.parametrize("credential", [("authorization", f"Bearer {SHARED}"),
                                        ("x-broker-token", SHARED),
                                        ("authorization", "Bearer bt_studio")],
                         ids=["shared", "shared-x-broker-token", "named-inference"])
@pytest.mark.parametrize("framing", ["declared", "chunked"])
async def test_an_authenticated_chat_with_a_large_body_reaches_the_route(gate, credential,
                                                                       framing):
    """A chat carrying photos is several MB: under its route's ceiling it is read and served."""
    broker = gate.configure(shared=SHARED, named=[_named("studio", "inference", "bt_studio")])
    body = _chat(6 * MIB)
    frame = _length(len(body)) if framing == "declared" else CHUNKED
    client = await gate("POST", "/v1/chat", _chunks(body), [JSON, frame, credential])
    assert client.status == 200, client.body[:300]
    assert broker.ran == [f"chat:{6 * MIB - _CHAT_CORE}"]


async def test_healthz_needs_no_credential(gate):
    gate.configure(shared=SHARED)
    client = await gate("GET", "/healthz", [b""], [])
    assert client.status == 200, client.body


async def test_the_api_description_is_gated_too(gate):
    """FastAPI registers /docs and /openapi.json itself, outside the app-wide dependency, so
    they answered anyone. The gate covers every path but /healthz."""
    gate.configure(shared=SHARED)
    for path in ("/openapi.json", "/docs"):
        assert (await gate("GET", path, [b""], [])).status == 401
    ok = await gate("GET", "/openapi.json", [b""], [("authorization", f"Bearer {SHARED}")])
    assert ok.status == 200 and b"/v1/chat" in ok.body


async def test_an_open_broker_lets_every_caller_through_and_only_bounds_the_body(gate):
    """Nothing configured is the documented open broker: the gate decides nothing but size."""
    gate.configure()
    assert (await gate("GET", "/v1/disabled", [b""], [])).status == 200
    assert (await gate("GET", "/openapi.json", [b""], [])).status == 200
    body = _chat(1 * MIB)
    served = await gate("POST", "/v1/chat", _chunks(body), [JSON, _length(len(body))])
    assert served.status == 200, served.body[:300]
    over = await gate("PUT", "/v1/disabled", _junk(2 * MIB), [JSON, _length(2 * MIB)])
    assert over.status == 413 and over.calls == 0


# --- the body ceiling -----------------------------------------------------------------------------

def _names(size: int) -> bytes:
    """A valid PUT /v1/disabled body of exactly `size` bytes (JSON allows any whitespace)."""
    core = json.dumps({"names": ["qwen3:8b"]}).encode()
    return core[:-1] + b" " * (size - len(core)) + b"}"


@pytest.mark.parametrize("framing", ["declared", "chunked"])
async def test_a_body_exactly_at_the_ceiling_is_served_and_one_byte_more_is_not(gate, framing):
    gate.configure(shared=SHARED)
    auth = ("authorization", f"Bearer {SHARED}")

    def hdrs(n: int):
        return [JSON, _length(n) if framing == "declared" else CHUNKED, auth]

    at = await gate("PUT", "/v1/disabled", _chunks(_names(m._BODY_MAX)), hdrs(m._BODY_MAX))
    assert at.status == 200, at.body[:300]
    over = await gate("PUT", "/v1/disabled", _chunks(_names(m._BODY_MAX + 1)),
                      hdrs(m._BODY_MAX + 1))
    assert over.status == 413, over.body[:300]
    assert "1.0 MiB" in over.json()["detail"]


async def test_a_declared_body_over_the_ceiling_is_a_413_before_a_byte_is_read(gate):
    gate.configure(shared=SHARED)
    client = await gate("POST", "/v1/chat", _junk(40 * MIB),
                        [JSON, _length(40 * MIB), ("authorization", f"Bearer {SHARED}")])
    assert client.status == 413 and client.calls == 0
    assert "32.0 MiB" in client.json()["detail"]


async def test_a_chunked_body_over_the_ceiling_is_cut_off_one_chunk_later(gate):
    broker = gate.configure(shared=SHARED)
    chunks = _junk(48 * MIB)
    client = await gate("POST", "/v1/chat", chunks,
                        [JSON, CHUNKED, ("authorization", f"Bearer {SHARED}")])
    assert client.status == 413, client.body[:200]
    assert client.read <= m._BODY_INFERENCE + CHUNK, f"read {client.read} bytes"
    assert client.calls < len(chunks) and broker.ran == []


async def test_a_cut_off_body_to_a_route_that_raises_on_it_is_a_413_not_a_500(gate):
    """The OpenAI routes read their own body (request.body()), and a body cut off at the ceiling
    reaches them as a client that left: ClientDisconnect, raised out of the route. The gate
    answers the 413 rather than letting that become a 500."""
    gate.configure(shared=SHARED)
    client = await gate("POST", "/openai/v1/chat/completions", _junk(40 * MIB),
                        [JSON, CHUNKED, ("authorization", f"Bearer {SHARED}")])
    assert client.status == 413, client.body[:200]
    assert client.read <= m._BODY_INFERENCE + CHUNK


async def test_a_413_on_the_openai_surface_is_openai_shaped(gate):
    gate.configure(shared=SHARED)
    client = await gate("POST", "/openai/v1/chat/completions", _junk(40 * MIB),
                        [JSON, _length(40 * MIB), ("authorization", f"Bearer {SHARED}")])
    assert client.status == 413
    err = client.json()["error"]
    assert err["code"] == "request_too_large" and "32.0 MiB" in err["message"]


async def test_transcribe_takes_its_largest_audio_and_is_cut_off_past_its_room(gate):
    broker = gate.configure(shared=SHARED)
    auth = ("authorization", f"Bearer {SHARED}")
    body = json.dumps({"audio_b64": "A" * TRANSCRIBE_MAX_B64, "suffix": ".webm",
                       "language": "es"}).encode()
    ok = await gate("POST", "/v1/transcribe", _chunks(body), [JSON, _length(len(body)), auth])
    assert ok.status == 200, ok.body[:300]
    assert broker.ran == [f"transcribe:{TRANSCRIBE_MAX_B64}"]
    room = m._BODY_ROOM["/v1/transcribe"]
    over = await gate("POST", "/v1/transcribe", _junk(room + CHUNK), [JSON, _length(room + 1),
                                                                      auth])
    assert over.status == 413 and over.calls == 0


# --- every route that reads a body has a ceiling that fits it -----------------------------------

#: Routes the default ceiling (_BODY_MAX, 1 MiB) is enough for, and why. A route that reads a
#: body and is in neither this set nor _BODY_ROOM fails the test below: decide which it is.
DEFAULT_IS_ENOUGH = {
    "PUT /v1/roles/{role}": "a model pattern of at most 128 characters",
    "PUT /v1/cli/roles/{role}": "a switch and a model id of at most 128 characters",
    "POST /v1/tokens": "a label of at most 64 characters and a scope",
    "PUT /v1/disabled": "model names of at most 128 characters each",
    "POST /v1/load": "a model name and a keep_alive",
    "POST /v1/unload": "a model name",
    "POST /v1/cancel": "a queue number",
    "POST /v1/image": "prompts, one SDXL or FLUX generation each",
    "POST /v1/tts_light": f"at most {TTS_LIGHT_MAX_CHARS} characters",
    "POST /v1/voice/synthesize": f"at most {TTS_LIGHT_MAX_CHARS} characters and a voice id",
    "POST /openai/v1/audio/speech": f"at most {TTS_LIGHT_MAX_CHARS} characters",
    "POST /openai/v1/images/generations": "one prompt, n of at most 4",
}


def _body_routes() -> list[str]:
    """"METHOD path" for every route that may read a body: each one FastAPI parses a body for,
    and every POST or PUT (the OpenAI surface reads its own with request.body())."""
    from fastapi.routing import APIRoute
    out = []
    # The OpenAI router's own list: FastAPI 0.14x keeps an included router as one entry of
    # app.routes. Its routes carry the /openai/v1 prefix in their path already.
    for r in [*m.app.routes, *openai_compat.router.routes]:
        if not isinstance(r, APIRoute):
            continue
        for method in sorted(r.methods):
            if r.body_field is not None or method in ("POST", "PUT", "PATCH"):
                out.append(f"{method} {r.path}")
    return out


def test_every_route_that_reads_a_body_has_a_ceiling_chosen_for_it():
    routes = _body_routes()
    assert len(routes) >= 20, routes
    unplaced = [r for r in routes
                if r.split(" ", 1)[1] not in m._BODY_ROOM and r not in DEFAULT_IS_ENOUGH]
    assert unplaced == [], f"routes with no ceiling chosen for them: {unplaced}"
    stale = [r for r in DEFAULT_IS_ENOUGH if r not in routes]
    assert stale == [], f"DEFAULT_IS_ENOUGH names routes that do not exist: {stale}"
    assert not [p for p in m._BODY_ROOM if not any(r.endswith(f" {p}") for r in routes)]


def test_each_room_holds_the_largest_body_its_route_takes():
    """Arithmetic, not a guess: each figure against the bound the route's own validator holds."""
    # Transcribe: the largest base64 TranscribeRequest accepts, with its other fields.
    b64 = len(json.dumps({"audio_b64": "A" * TRANSCRIBE_MAX_B64, "suffix": ".webm",
                          "language": "es"}))
    assert b64 <= m._BODY_ROOM["/v1/transcribe"]
    # The multipart route: the largest file it accepts, its part header and the form fields.
    framing = 4096
    assert openai_compat._MAX_AUDIO_BYTES + framing <= m._BODY_ROOM[
        "/openai/v1/audio/transcriptions"]
    # A tts or tts_batch at its 500-item cap of 8,000-character texts, every character escaped
    # (json.dumps writes a non-ASCII character as \\uXXXX, six bytes).
    item = len(json.dumps({"lang": "es", "text": "ñ" * 8000}))
    assert len(json.dumps({"items": []})) + 500 * (item + 2) <= m._BODY_ROOM["/v1/tts_batch"]
    assert m._BODY_ROOM["/v1/tts"] == m._BODY_ROOM["/v1/tts_batch"]
    # The 8,000-character routes on the default ceiling, escaped the same way.
    text = len(json.dumps({"text": "ñ" * TTS_LIGHT_MAX_CHARS, "voice": "ef_dora",
                           "lang_code": "e", "speed": 1.0}))
    assert text * 2 <= m._BODY_MAX
