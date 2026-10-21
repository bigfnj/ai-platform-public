"""A closed broker stream closes its inner stream BEFORE it gives the GPU gate back.

`Broker.chat_stream` (its Ollama and its delegated branch) and `Broker.openai_chat_stream` relayed
their inner generator with a plain `async for`. When the route closes the OUTER generator (a
client that hung up, a write that failed: ClosingStreamingResponse), GeneratorExit leaves that
loop and only the outer generator is closed. The inner one is merely dropped, and asyncio's
async-generator finalizer closes it a few loop turns later. By then the gate is free, so the next
job could take the card while Ollama was still generating for nobody (audit 2026-10-14: a partial
regression of the intent of b03a9567, which made the routes close the broker's stream promptly).

Each case opens the stream, takes one frame, awaits `aclose()` and asks AT ONCE whether the inner
stream's close has run, and whether the gate was still held when it ran. No Ollama, no remote: the
inner streams are fakes that record their own close.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import upstream as up_mod
from app.broker import Broker
from app.config import BrokerSettings

MESSAGES = [{"role": "user", "content": "hi"}]


class _Inner:
    """One inner stream: frames, then more to come that nobody reads. Records its close."""

    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.opened = False
        self.closed = False
        self.gate_at_close: int | None = None

    async def frames(self, first):
        self.opened = True
        try:
            yield first
            await asyncio.sleep(3600)       # still generating, for a reader who has gone
            yield first
        finally:
            self.closed = True
            self.gate_at_close = self.broker.gate.active


class _Ollama:
    def __init__(self, inner: _Inner) -> None:
        self.inner = inner

    async def ps(self):
        return []                           # nothing resident, so nothing to evict

    async def tags(self):
        return [{"name": "qwen3:8b"}]

    def chat_stream(self, model, messages, **_kw):
        return self.inner.frames({"model": model, "message": {"content": "hi"}, "done": False})

    def openai_chat_stream(self, body):
        return self.inner.frames(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')

    async def aclose(self):
        pass


def _broker(tmp_path, roles: dict | None = None, upstreams: dict | None = None) -> Broker:
    (tmp_path / "roles.json").write_text(json.dumps(roles or {}), encoding="utf-8")
    (tmp_path / "upstreams.json").write_text(json.dumps(upstreams or {}), encoding="utf-8")
    return Broker(BrokerSettings(
        roles_file=str(tmp_path / "roles.json"), disabled_file=str(tmp_path / "disabled.json"),
        tokens_file=str(tmp_path / "tokens.json"),
        upstreams_file=str(tmp_path / "upstreams.json"), auth_token="", cli_enabled=False))


@pytest.mark.parametrize("site", ["ollama", "openai"])
def test_a_closed_stream_closes_ollama_s_stream_before_it_frees_the_gate(tmp_path, site):
    b = _broker(tmp_path)
    inner = _Inner(b)
    b.ollama = _Ollama(inner)
    seen: dict = {}

    async def go():
        gen = (b.chat_stream("qwen3:8b", MESSAGES) if site == "ollama"
               else b.openai_chat_stream({"model": "qwen3:8b", "messages": MESSAGES}))
        await gen.__anext__()
        seen["held"] = b.gate.active
        await gen.aclose()
        # Nothing awaited between the close and these reads: what they see is what the next job
        # queued for the gate would see.
        seen["inner_closed"] = inner.closed
        seen["gate_after"] = b.gate.active

    asyncio.run(go())
    assert inner.opened and seen["held"] == 1
    assert seen["inner_closed"], (
        "the broker's stream had closed while Ollama's was still open, generating for nobody")
    assert inner.gate_at_close == 1, "the gate was given back before Ollama's stream closed"
    assert seen["gate_after"] == 0


def test_a_closed_delegated_stream_closes_the_remote_stream_with_it(tmp_path, monkeypatch):
    """No gate on this branch (the work runs on another card), but the same order matters: the
    remote's stream, and the pooled connection it holds, end with the broker's stream rather than
    whenever the finalizer gets to them."""
    b = _broker(tmp_path, roles={"chat": "offsite::qwen3:30b"},
                upstreams={"offsite": {"url": "http://gpu-box.example.internal:11500"}})
    inner = _Inner(b)

    def fake_stream(self, model, messages, **_kw):
        return inner.frames({"model": model, "message": {"content": "hi"}, "done": False})

    monkeypatch.setattr(up_mod.UpstreamClient, "chat_stream", fake_stream)
    seen: dict = {}

    async def go():
        gen = b.chat_stream("@chat", MESSAGES)
        first = await gen.__anext__()
        await gen.aclose()
        seen["inner_closed"] = inner.closed
        return first

    first = asyncio.run(go())
    assert first["model"] == "qwen3:30b"
    assert seen["inner_closed"], "the broker's stream had closed while the remote's was still open"
    assert inner.gate_at_close == 0, "a delegated stream took the local gate"
