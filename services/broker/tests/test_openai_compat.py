"""The OpenAI-compatible surface at /openai/v1.

What these assert, and why each one is here rather than trusted:

  * the surface is mounted where clients are told to point, and does NOT shadow the
    platform dialect at /v1 that every rail speaks
  * `model` is rewritten from @role / glob to a concrete name before Ollama sees it,
    and NOTHING ELSE the caller sent is touched -- tools, vision parts and
    response_format have to survive the hop or the pass-through claim is false
  * a disabled model is refused, with an OpenAI-shaped 403 rather than a 502
  * errors carry `error.message`, because an OpenAI SDK reads that key and renders
    `None` when it is missing
  * the GPU gate is actually taken, including for streams

Ollama is a MockTransport throughout: this suite must not need a GPU, and asserting
on the payload Ollama would have received is stronger than asserting on a reply we
also wrote.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker import Broker, ModelDisabledError
from app.config import BrokerSettings


class _Recorder:
    """Stands in for Ollama. Records the request and returns a canned reply."""

    def __init__(self, disabled: tuple[str, ...] = (), tags: tuple[str, ...] = ()):
        self.seen: dict[str, object] = {}
        self._disabled = disabled
        self._tags = tags or ("qwen3.6:27b", "gemma4:26b", "bge-m3:latest")

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": n, "size": 1, "digest": n, "details": {"family": "test"}}
                for n in self._tags
            ]})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        # Eviction reads the resident set before every gated call. Nothing loaded, so
        # nothing to evict -- but the route has to exist or the call 404s inside the
        # gate and the failure surfaces as a missing `model` key three asserts later.
        if path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if path == "/api/generate":
            return httpx.Response(200, json={})
        body = json.loads(request.content or b"{}")
        self.seen["path"] = path
        self.seen["body"] = body
        if path == "/v1/chat/completions":
            if body.get("stream"):
                sse = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                       b'data: [DONE]\n\n')
                return httpx.Response(200, content=sse,
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={
                "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        if path == "/v1/embeddings":
            return httpx.Response(200, json={
                "object": "list", "model": body.get("model"),
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            })
        return httpx.Response(404, json={"error": "unexpected path"})


@pytest.fixture
def make_client(tmp_path):
    """Factory for a TestClient whose broker talks to a recorder instead of Ollama.

    Settings are driven through their REAL overlay files (BROKER_ROLES_FILE /
    BROKER_DISABLED_FILE / BROKER_TOKENS_FILE) rather than by patching the methods: BrokerSettings is a
    pydantic model that refuses attribute assignment, and more to the point, a test
    that stubs `disabled()` would still pass if the production path stopped reading
    disabled.json at all.
    """
    def _make(rec: _Recorder, disabled: tuple[str, ...] = ()):
        from app.main import app

        roles_file = tmp_path / "roles.json"
        roles_file.write_text(
            json.dumps({"chat": "qwen3.6:27b", "vision": "gemma4*:26b"}), encoding="utf-8")
        disabled_file = tmp_path / "disabled.json"
        disabled_file.write_text(json.dumps(list(disabled)), encoding="utf-8")

        # tokens_file is isolated for the same reason roles/disabled are, and the omission
        # BIT: without it BrokerSettings falls back to services/broker/tokens.json -- the
        # OPERATOR'S real store -- so creating one token on this box made fourteen tests 401.
        # A test must never read live credentials, and must never depend on their absence.
        tokens_file = tmp_path / "tokens.json"
        settings = BrokerSettings(
            roles_file=str(roles_file), disabled_file=str(disabled_file),
            tokens_file=str(tokens_file), auth_token="")
        broker = Broker(settings)
        broker.ollama._client = httpx.AsyncClient(
            base_url="http://ollama.test", transport=httpx.MockTransport(rec.handler))

        client = TestClient(app)
        app.state.settings = settings
        app.state.broker = broker
        return client

    return _make


# --- mounting -------------------------------------------------------------

def test_models_is_openai_shaped(make_client):
    rec = _Recorder()
    r = make_client(rec).get("/openai/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert {"id", "object", "created", "owned_by"} <= set(body["data"][0])


def test_platform_dialect_models_is_unchanged(make_client):
    """The whole reason for the /openai/v1 prefix. Rails read {"models": [...]} and a
    regression here breaks every rail's model chip at once."""
    rec = _Recorder()
    body = make_client(rec).get("/v1/models").json()
    assert "models" in body and "data" not in body


def test_disabled_model_is_absent_from_the_listing(make_client):
    rec = _Recorder()
    ids = [m["id"] for m in make_client(rec, disabled=("gemma4:26b",)).get("/openai/v1/models").json()["data"]]
    assert "gemma4:26b" not in ids
    assert "qwen3.6:27b" in ids


def test_an_untagged_name_in_the_disabled_set_hides_its_latest_install(make_client):
    """disabled.json is hand-editable: `bge-m3` there is the model Ollama lists as
    `bge-m3:latest`, which the chat route refuses, so the listing must not offer it."""
    rec = _Recorder()
    ids = [m["id"] for m in make_client(rec, disabled=("bge-m3",)).get(
        "/openai/v1/models").json()["data"]]
    assert "bge-m3:latest" not in ids
    assert "qwen3.6:27b" in ids


def test_an_untagged_name_is_refused_when_its_latest_install_is_disabled(make_client):
    """The audit's probe (2026-10-14). The Models tab disables list_models' name, `qwen3:latest`;
    a client asking for `qwen3` resolves to the bare name, and a plain `in` served it."""
    rec = _Recorder(tags=("qwen3:latest", "gemma4:26b"))
    r = make_client(rec, disabled=("qwen3:latest",)).post(
        "/openai/v1/chat/completions",
        json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "model_disabled"
    assert "path" not in rec.seen, "the disabled model was dispatched"


def test_resolve_for_external_raises_for_an_untagged_name_whose_latest_is_disabled(tmp_path):
    """The same probe one layer down, as the backlog states it."""
    import asyncio

    disabled = tmp_path / "disabled.json"
    disabled.write_text(json.dumps(["qwen3:latest"]), encoding="utf-8")
    b = Broker(BrokerSettings(roles_file=str(tmp_path / "roles.json"),
                              disabled_file=str(disabled),
                              tokens_file=str(tmp_path / "tokens.json"), auth_token=""))
    with pytest.raises(ModelDisabledError):
        asyncio.run(b.resolve_for_external("qwen3"))


# --- pass-through fidelity ------------------------------------------------

def test_role_is_resolved_before_ollama_sees_it(make_client):
    rec = _Recorder()
    r = make_client(rec).post("/openai/v1/chat/completions",
                          json={"model": "@chat", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert rec.seen["body"]["model"] == "qwen3.6:27b"


def test_glob_role_is_resolved(make_client):
    rec = _Recorder()
    make_client(rec).post("/openai/v1/chat/completions",
                      json={"model": "@vision", "messages": [{"role": "user", "content": "hi"}]})
    assert rec.seen["body"]["model"] == "gemma4:26b"


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streamed"])
def test_an_at_role_chat_is_resolved_once_per_request(make_client, monkeypatch, stream):
    """The route resolved every request twice: up front, to answer a refusal with a real status
    code, then again inside the broker call. Each resolve read roles.json twice (the delegation
    check, then resolve_ref) and disabled.json once: four and two reads a request. The route's
    resolution is handed to the broker call now, and is the one that runs."""
    rec = _Recorder()
    asked: list[str] = []
    handler = rec.handler

    async def counting(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return await handler(request)

    rec.handler = counting
    client = make_client(rec)
    reads: list[str] = []
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    r = client.post("/openai/v1/chat/completions", json={
        "model": "@chat", "messages": [{"role": "user", "content": "hi"}], "stream": stream})
    assert r.status_code == 200, r.text
    assert rec.seen["body"]["model"] == "qwen3.6:27b"
    counts = (reads.count("roles.json"), reads.count("disabled.json"), asked.count("/api/tags"))
    assert counts == (1, 1, 1), f"one request read (roles.json, disabled.json, tags) {counts}"


def test_caller_fields_survive_the_hop(make_client):
    """The pass-through claim in ollama.py is only true if unmapped fields arrive.
    A translating implementation would silently drop every one of these."""
    rec = _Recorder()
    sent = {
        "model": "qwen3.6:27b",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}],
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "temperature": 0.2, "top_p": 0.9, "max_tokens": 128, "seed": 7, "stop": ["END"],
    }
    make_client(rec).post("/openai/v1/chat/completions", json=sent)
    got = rec.seen["body"]
    for key, value in sent.items():
        if key == "model":
            continue
        assert got[key] == value, f"{key} did not survive the pass-through"


def test_embeddings_are_openai_shaped(make_client):
    rec = _Recorder()
    r = make_client(rec).post("/openai/v1/embeddings", json={"model": "bge-m3:latest", "input": "hi"})
    assert r.status_code == 200
    assert r.json()["data"][0]["object"] == "embedding"


def test_embed_role_resolves_to_its_own_embedder(make_client):
    rec = _Recorder()
    r = make_client(rec).post("/openai/v1/embeddings", json={"model": "@embed", "input": "hi"})
    assert r.status_code == 200, r.text
    assert rec.seen["body"]["model"] == "bge-m3:latest"


def test_a_missing_embedder_is_refused_not_substituted(make_client):
    """The audit's probe: bge-m3 absent, nomic installed. This route answered 200 with nomic
    vectors and no fallback header, while /v1/embed refused. Vectors from another model are
    not a degraded answer, they are a wrong one that poisons the caller's index."""
    rec = _Recorder(tags=("qwen3.6:27b", "nomic-embed-text:latest"))
    r = make_client(rec).post("/openai/v1/embeddings", json={"model": "@embed", "input": "hi"})
    assert r.status_code >= 400, r.text
    assert "path" not in rec.seen, f"an embedding request reached Ollama: {rec.seen}"
    assert "x-model-fallback" not in {k.lower() for k in r.headers}


def test_a_disabled_embedder_behind_a_role_is_refused_not_substituted(make_client):
    rec = _Recorder(tags=("qwen3.6:27b", "bge-m3:latest", "nomic-embed-text:latest"))
    r = make_client(rec, disabled=("bge-m3:latest",)).post(
        "/openai/v1/embeddings", json={"model": "@embed", "input": "hi"})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "model_disabled"
    assert "path" not in rec.seen


# --- policy ---------------------------------------------------------------

def test_disabled_model_is_refused_with_403_not_502(make_client):
    """A policy refusal must not read as a backend fault: a client should be able to
    tell 'pick another model' from 'the server is broken'."""
    rec = _Recorder()
    r = make_client(rec, disabled=("qwen3.6:27b",)).post(
        "/openai/v1/chat/completions",
        json={"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "model_disabled"


def test_disabling_the_target_is_honoured_by_the_role_pointing_at_it(make_client):
    """Checks the RESOLVED name. Otherwise Disable means 'disabled only for callers
    who spell the model out', and every @role keeps serving it.

    The OUTCOME changed deliberately when model fallback landed: an '@role' now substitutes
    rather than 403-ing, because "@chat" means "whatever the admin points chat at" and
    re-pointing it is inside that promise. The INTENT this test was written for survives and
    is what is asserted -- the disabled model is never DISPATCHED -- plus the announcement
    that makes the substitution visible rather than silent.
    """
    rec = _Recorder()
    r = make_client(rec, disabled=("qwen3.6:27b",)).post(
        "/openai/v1/chat/completions",
        json={"model": "@chat", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "qwen3.6:27b" not in json.dumps(rec.seen, default=str), "the disabled model was dispatched"
    assert r.headers.get("X-Model-Fallback-Reason") == "disabled"


def test_a_model_the_caller_spelled_out_is_still_refused_not_substituted(make_client):
    """The other half, and the reason the two are not one rule. A role is an indirection the
    admin owns; a concrete name is the caller's own choice, and answering it with a different
    model is the same lie as returning a wav labelled audio/mpeg."""
    rec = _Recorder()
    r = make_client(rec, disabled=("qwen3.6:27b",)).post(
        "/openai/v1/chat/completions",
        json={"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "model_disabled"


def test_errors_use_the_openai_envelope(make_client):
    """An OpenAI SDK reads error.message; FastAPI's default {"detail": ...} renders as
    None in the client and the user sees a blank failure."""
    rec = _Recorder()
    r = make_client(rec).post("/openai/v1/chat/completions", json={"messages": []})
    assert r.status_code == 400
    assert isinstance(r.json()["error"]["message"], str) and r.json()["error"]["message"]


# --- the gate -------------------------------------------------------------

def test_the_gpu_gate_is_taken(make_client):
    """The entire point of routing through the broker instead of straight at Ollama."""
    rec = _Recorder()
    client = make_client(rec)
    broker = client.app.state.broker
    seen_depth = {}

    original = broker._evict_other_heavy

    async def spy(keep=None):
        seen_depth["active"] = broker.gate.depth()["active"]
        return await original(keep=keep)

    broker._evict_other_heavy = spy  # type: ignore[method-assign]
    client.post("/openai/v1/chat/completions",
                json={"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "hi"}]})
    assert seen_depth["active"] == 1, "the gate was not held while dispatching"


def test_stream_relays_sse_and_holds_the_gate(make_client):
    rec = _Recorder()
    client = make_client(rec)
    broker = client.app.state.broker
    seen_depth = {}
    original = broker._evict_other_heavy

    async def spy(keep=None):
        seen_depth["active"] = broker.gate.depth()["active"]
        return await original(keep=keep)

    broker._evict_other_heavy = spy  # type: ignore[method-assign]
    with client.stream("POST", "/openai/v1/chat/completions",
                       json={"model": "qwen3.6:27b", "stream": True,
                             "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200
        text = "".join(chunk.decode() for chunk in r.iter_bytes())
    assert "data:" in text and "[DONE]" in text
    assert seen_depth["active"] == 1
    assert rec.seen["body"]["stream"] is True


def test_gate_is_released_after_the_call(make_client):
    rec = _Recorder()
    client = make_client(rec)
    client.post("/openai/v1/chat/completions",
                json={"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "hi"}]})
    assert client.app.state.broker.gate.depth() == {"active": 0, "waiting": 0}


class _Frames(_Recorder):
    """Ollama's stream in three frames, as a real one arrives, not one buffered body: a stream
    that is already over when the first write blocks would free the gate anyway."""

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return await super().handler(request)

        async def frames():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=frames(),
                              headers={"content-type": "text/event-stream"})


_STREAM_BODY = json.dumps({"model": "qwen3.6:27b", "stream": True,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()


def _chat_scope(spec: str) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": spec},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/openai/v1/chat/completions",
            "raw_path": b"/openai/v1/chat/completions", "query_string": b"",
            "root_path": "", "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 11500),
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(_STREAM_BODY)).encode())]}


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_a_stream_that_ends_in_send_gives_the_gate_back_before_the_request_ends(make_client,
                                                                                spec):
    """The stream holds the GPU gate until its last chunk. When it ends in Starlette's send()
    instead (2.3: the disconnect's cancellation landing while a write is blocked; 2.4: the
    write failing), `async for` leaves the body generator suspended, and with it the gate,
    so every heavy job queued behind a client that had already gone, until a collection
    closed it (the collector is off here). The response closes its body, and the body the
    broker's stream, before the request ends: the gate is free the moment the server is told
    the request is done."""
    import asyncio
    import gc

    client = make_client(_Frames())
    app, broker = client.app, client.app.state.broker
    body = _STREAM_BODY

    async def scenario():
        gone = asyncio.Event()
        writing = asyncio.Event()
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] != "http.response.body":
                return
            if spec == "2.4" and gone.is_set():
                raise ConnectionResetError("the client went away")
            writing.set()
            if spec == "2.3":
                await asyncio.Event().wait()        # blocked until the cancellation
            else:
                await gone.wait()                   # then the next write fails

        scope = _chat_scope(spec)

        async def served():
            try:
                await app(scope, receive, send)
            except Exception as exc:  # noqa: BLE001 -- uvicorn logs it and lets it go
                return type(exc).__name__
            return None

        task = asyncio.ensure_future(served())
        await asyncio.wait_for(writing.wait(), 10)
        held = broker.gate.depth()
        gone.set()
        raised = await asyncio.wait_for(task, 10)
        return held, broker.gate.depth(), raised

    collecting = gc.isenabled()
    gc.disable()
    try:
        held, after, raised = asyncio.run(scenario())
    finally:
        if collecting:
            gc.enable()
    assert held == {"active": 1, "waiting": 0}
    assert after == {"active": 0, "waiting": 0}, "the gate outlived the request"
    assert raised == (None if spec == "2.3" else "ClientDisconnect")


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_a_stream_whose_reader_stopped_gives_the_gate_back(make_client, monkeypatch, spec):
    """A client that keeps its connection open and stops reading: the write blocks, and that is
    all that happens (no disconnect, no failed write). The stream sat behind its send holding the
    GPU gate, so every heavy job queued behind a reader that had stopped, for as long as it kept
    the socket open. Each send is bounded now (app/streaming.py): past the bound the stream ends
    and the gate is free when the request does."""
    import asyncio
    import gc

    import app.streaming as streaming
    monkeypatch.setattr(streaming, "SEND_TIMEOUT_S", 0.3)
    client = make_client(_Frames())
    app, broker = client.app, client.app.state.broker

    async def scenario():
        writing = asyncio.Event()
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": _STREAM_BODY, "more_body": False}
            await asyncio.Event().wait()            # the caller never hangs up

        async def send(message):
            if message["type"] == "http.response.body":
                writing.set()
                await asyncio.Event().wait()        # and never reads

        async def served():
            try:
                await app(_chat_scope(spec), receive, send)
            except Exception as exc:  # noqa: BLE001 -- uvicorn logs it and lets it go
                return type(exc).__name__
            return None

        task = asyncio.ensure_future(served())
        await asyncio.wait_for(writing.wait(), 10)
        held = broker.gate.depth()
        try:
            raised = await asyncio.wait_for(task, 10)
        except asyncio.TimeoutError:
            return held, broker.gate.depth(), "the request never ended"
        return held, broker.gate.depth(), raised

    collecting = gc.isenabled()
    gc.disable()
    try:
        held, after, raised = asyncio.run(scenario())
    finally:
        if collecting:
            gc.enable()
    assert held == {"active": 1, "waiting": 0}
    assert raised is None, raised
    assert after == {"active": 0, "waiting": 0}, "the gate outlived the request"

# --- the two dialects must answer the same fault the same way ----------------------------------

def test_every_openai_route_answers_an_unknown_role_the_same_way(make_client):
    """A 400-vs-502 split across sibling routes is what the typed error arms set out to remove,
    and adding them to chat alone recreated it one route over: embeddings reaches
    resolve_for_external and images reaches _resolve_media, both of which raise UnknownRoleError,
    and both were still falling into a broad 502.

    Lives HERE rather than in test_role_fallback because make_client is what isolates the
    broker's settings -- and, load-bearing, it does NOT enter the TestClient context manager.
    Entering it runs the lifespan, which reassigns app.state.settings from the real environment
    and threw away the isolation, so a hand-rolled client 401'd instead of 400'ing.
    """
    rec = _Recorder()
    client = make_client(rec)
    cases = [
        ("/openai/v1/chat/completions",
         {"model": "@nosuchrole", "messages": [{"role": "user", "content": "hi"}]}),
        ("/openai/v1/embeddings", {"model": "@nosuchrole", "input": "hi"}),
        ("/openai/v1/images/generations", {"model": "@nosuchrole", "prompt": "a cat"}),
    ]
    for path, body in cases:
        r = client.post(path, json=body)
        assert r.status_code == 400, f"{path} answered {r.status_code}, not 400"
        assert r.json()["error"]["code"] == "unknown_role", path
        assert "nosuchrole" in json.dumps(r.json()), path
