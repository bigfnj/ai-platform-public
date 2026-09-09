"""The `think` passthrough for reasoning models.

qwen3.6 defaults to thinking ON, and on a structured/JSON task it spends its whole output
budget on reasoning and returns EMPTY content (measured ~33% empty, ~8x latency on IEP
worksheet drafting). `think=false` fixes it. This asserts the parameter reaches Ollama's
payload only when set, so existing callers that omit it are byte-for-byte unchanged.
"""
import asyncio

import httpx

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
