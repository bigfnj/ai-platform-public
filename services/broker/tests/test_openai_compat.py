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
