"""FIX 5 — the NVIDIA NIM client and its stream are closed on every path.

``_client()`` builds a fresh AsyncOpenAI, and a fresh HTTPS connection pool with it, on every
call. Neither ``probe()`` nor ``chat_stream()`` closed one. The stream was worse than untidy:
with no ``try/finally`` around the ``yield``, a browser tab closed mid-answer abandoned the
generator with the response to NVIDIA still open — still generating, still BILLED, with nobody
left to read it. broker.chat_stream had the nested ``async with`` right all along.

Everything here runs against a fake client: no key, no network, no NVIDIA.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_playground import nim


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeStream:
    """Stands in for openai's AsyncStream: async-iterable and an async context manager."""

    def __init__(self, chunks: list) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class FakeClient:
    """Stands in for AsyncOpenAI. Records whether anyone closed it."""

    def __init__(self, stream: FakeStream | None = None) -> None:
        self.stream = stream
        self.closed = False
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def close(self) -> None:
        self.closed = True

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream if kwargs.get("stream") else SimpleNamespace(choices=[])


@pytest.fixture
def client(monkeypatch):
    """nim wired to a fake client. The key is faked too, so available() says yes."""
    def install(stream: FakeStream | None = None) -> FakeClient:
        fake = FakeClient(stream)
        monkeypatch.setattr(nim, "API_KEY", "test-key")
        monkeypatch.setattr(nim, "AsyncOpenAI", lambda **kwargs: fake)
        return fake

    return install


def test_probe_closes_its_client(client):
    fake = client()
    asyncio.run(nim.probe())
    assert fake.calls and fake.calls[0]["max_tokens"] == 1
    assert fake.closed, "probe() built a client, and a connection pool, and abandoned it"


def test_probe_without_a_key_never_builds_a_client(monkeypatch):
    monkeypatch.setattr(nim, "API_KEY", "")
    monkeypatch.setattr(nim, "AsyncOpenAI",
                        lambda **kwargs: pytest.fail("built a client with no key"))
    with pytest.raises(RuntimeError):
        asyncio.run(nim.probe())


def test_chat_stream_yields_tokens_and_closes_both(client):
    fake = client(FakeStream([_chunk("Hel"), _chunk(""), _chunk("lo")]))

    async def drain():
        return [tok async for tok in nim.chat_stream([{"role": "user", "content": "hi"}])]

    assert asyncio.run(drain()) == ["Hel", "lo"]
    assert fake.stream.closed and fake.closed


def test_an_abandoned_stream_is_closed(client):
    """The browser tab closed after one token. Closing the generator has to tear down the
    response to NVIDIA and the client's pool — otherwise generation continues, and bills."""
    fake = client(FakeStream([_chunk("a"), _chunk("b"), _chunk("c")]))

    async def take_one():
        gen = nim.chat_stream([{"role": "user", "content": "hi"}])
        async for tok in gen:
            assert tok == "a"
            break
        await gen.aclose()      # what the event loop does to an abandoned async generator

    asyncio.run(take_one())

    assert fake.stream.closed, "the NVIDIA response was left open, still generating"
    assert fake.closed, "the per-call client, and its connection pool, was left open"


def test_a_failing_stream_still_closes_the_client(client):
    """A mid-stream error is the other way out of that generator."""
    class Exploding(FakeStream):
        async def __aiter__(self):
            yield _chunk("a")
            raise RuntimeError("upstream died")

    fake = client(Exploding([]))

    async def drain():
        async for _ in nim.chat_stream([{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(drain())
    assert fake.stream.closed and fake.closed
