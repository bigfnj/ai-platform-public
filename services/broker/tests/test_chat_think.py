"""The `think` passthrough for reasoning models.

qwen3.6 defaults to thinking ON, and on a structured/JSON task it spends its whole output
budget on reasoning and returns EMPTY content (measured ~33% empty, ~8x latency on IEP
worksheet drafting). `think=false` fixes it. This asserts the parameter reaches Ollama's
payload only when set, so existing callers that omit it are byte-for-byte unchanged.

The STREAMING half is asserted end to end through `/v1/chat/stream`, not just against
`OllamaClient`: the parameter existing on the client proved nothing while the route and
`Broker.chat_stream` between them dropped it, which is exactly how it was inert for the
one rail (openmaic) that streams and asks for a format.
"""
import asyncio
import json as _json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker import Broker
from app.config import BrokerSettings
from app.ollama import OllamaClient


def _capture():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}})

    client = OllamaClient("http://ollama.test")
    client._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return client, seen


def _chat(**kw):
    client, seen = _capture()
    asyncio.run(client.chat("qwen3.6:27b", [{"role": "user", "content": "hi"}], **kw))
    return seen["payload"]


def test_think_false_is_forwarded():
    assert _chat(think=False)["think"] is False


def test_think_true_is_forwarded():
    assert _chat(think=True)["think"] is True


def test_think_omitted_leaves_no_key():
    """Existing callers must be unchanged — no stray think key defaulting the model."""
    assert "think" not in _chat()


def test_think_none_leaves_no_key():
    assert "think" not in _chat(think=None)


# --- the streaming path, through the route ---------------------------------

@pytest.fixture
def stream_payload(tmp_path):
    """POST /v1/chat/stream and hand back the payload Ollama actually received."""
    def _post(body: dict) -> dict:
        from app.main import app

        seen: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": []})
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [
                    {"name": "qwen3.6:27b", "size": 1, "digest": "d"}]})
            seen["payload"] = _json.loads(request.content or b"{}")
            return httpx.Response(200, content=b'{"done": true}\n',
                                  headers={"content-type": "application/x-ndjson"})

        settings = BrokerSettings(
            roles_file=str(tmp_path / "roles.json"),
            disabled_file=str(tmp_path / "disabled.json"),
            tokens_file=str(tmp_path / "tokens.json"), auth_token="")
        broker = Broker(settings)
        broker.ollama._client = httpx.AsyncClient(
            base_url="http://ollama.test", transport=httpx.MockTransport(handler))
        client = TestClient(app)
        app.state.settings = settings
        app.state.broker = broker
        resp = client.post("/v1/chat/stream", json=body)
        assert resp.status_code == 200, resp.text
        assert "error" not in resp.text, resp.text
        return seen["payload"]

    return _post


_BODY = {"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "hi"}]}


def test_stream_forwards_think_false_with_a_format(stream_payload):
    """The openmaic case: structured output plus thinking off. Dropping `think` here let a
    reasoning model's <think> preamble land in generated course text with no error."""
    payload = stream_payload({**_BODY, "format": "json", "think": False})
    assert payload["think"] is False
    assert payload["format"] == "json"
    assert payload["stream"] is True


def test_stream_forwards_think_true(stream_payload):
    assert stream_payload({**_BODY, "think": True})["think"] is True


def test_stream_omitting_think_leaves_no_key(stream_payload):
    """Unchanged for every caller that never set it -- no stray key defaulting the model."""
    assert "think" not in stream_payload(dict(_BODY))
