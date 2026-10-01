"""V-16 — the STREAMING broker facade maps every httpx escape onto BrokerError.

`ws_rag` catches `Exception`, so this rail never showed the V-16 symptom — but that made the
facade's contract untested rather than sound, and it is the same facade. This file drives the real
body against an `httpx` stand-in, so the typed error is a property of the facade and not of the
breadth of one caller's handler. It also closes a hole in `conftest.no_outbound`, which wires
`_post`/`_get` to refuse and leaves `chat_stream` — which builds its own `httpx.AsyncClient` —
able to dial the real broker.

The stand-in transport genuinely streams, and that is not incidental. `httpx.MockTransport` hands
back a response whose content is already set, so `exc.response.text` works there and the
`httpx.ResponseNotRead` these tests exist to catch never fires — a buffered fake would pass
against a mapping that copies `_post`'s message verbatim, which is the one way to get this fix
wrong. Measured before it was written: against a streaming transport `.text` raises
ResponseNotRead, a StreamError/RuntimeError that `except httpx.HTTPError` does not catch.

House style: coroutines are driven with `asyncio.run` rather than pulling in an async plugin.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from ai_playground import broker

#: The shape of a real broker 401 body, so the response under test is not an empty one.
BODY = b'{"detail":"unauthorized: broker token rotated"}'


class _Chunks(httpx.AsyncByteStream):
    """An un-consumed async byte stream, so the response streams rather than arrives buffered."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class _Transport(httpx.AsyncBaseTransport):
    """Either raises on connect, or answers with a streaming response of the given status."""

    def __init__(self, status: int, chunks: list[bytes], raises: Exception | None) -> None:
        self.status, self.chunks, self.raises = status, chunks, raises

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.raises is not None:
            raise self.raises
        return httpx.Response(self.status, stream=_Chunks(self.chunks),
                              headers={"content-type": "application/x-ndjson"})


class _FakeHttpx:
    """Only the surface `chat_stream` touches: `AsyncClient`, plus the exception types its
    `except` clauses name. Set on the broker module rather than patched into httpx itself, so
    nothing else in the process gets a rewired client for the length of a test."""

    HTTPError = httpx.HTTPError
    HTTPStatusError = httpx.HTTPStatusError

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport

    def AsyncClient(self, **kwargs):  # noqa: N802 - mirrors the httpx class it stands in for
        return httpx.AsyncClient(transport=self.transport, **kwargs)


class _ShimHttpx:
    """The no-network `httpx` the rail suites install to make a forgotten mock loud. Its mirror
    `HTTPStatusError` carries no `.response` at all, so the mapping has to cope with an absent
    attribute rather than raise an AttributeError out of its own handler."""

    class HTTPError(Exception):
        pass

    class HTTPStatusError(HTTPError):
        pass

    def AsyncClient(self, **_kwargs):  # noqa: N802 - mirrors the httpx class it stands in for
        raise self.HTTPStatusError("broker said 401")


@pytest.fixture()
def stream(monkeypatch):
    """Install a transport; get back a callable that drains the real `chat_stream` through it."""
    def install(*, status: int = 200, chunks: tuple[bytes, ...] = (),
                raises: Exception | None = None):
        monkeypatch.setattr(broker, "httpx", _FakeHttpx(_Transport(status, list(chunks), raises)))

        async def drain() -> list[str]:
            return [tok async for tok in broker.chat_stream(
                None, [{"role": "user", "content": "hi"}])]

        return lambda: asyncio.run(drain())

    return install


def test_the_happy_path_still_yields_content_deltas(stream):
    """The guards are worth nothing if they changed what a good stream does."""
    drain = stream(chunks=(b'{"message":{"content":"The corpus "}}\n',
                           b'{"message":{"content":"says so [1]."}}\n',
                           b'{"done":true}\n'))
    assert drain() == ["The corpus ", "says so [1]."]


def test_a_mid_answer_401_is_a_broker_error_naming_the_status(stream):
    """Escape 1 — `raise_for_status()`, the one that took a whole conversation down.

    `pytest.raises(BrokerError)` IS the ResponseNotRead assertion: had the mapping copied
    `_post`'s `exc.response.text`, this would fail with a ResponseNotRead instead of passing.
    The `__cause__` check pins that down — the chain has to end at the HTTPStatusError, not at
    something raised while trying to describe it.
    """
    drain = stream(status=401, chunks=(BODY,))
    with pytest.raises(broker.BrokerError) as caught:
        drain()
    # Equality, not `"401" in ...`: an HTTPStatusError IS an httpx.HTTPError, so the second
    # clause alone would also catch this and report it as "unreachable" — wrong, and a substring
    # check would have passed anyway because that message quotes the code too. This assertion is
    # what makes the clause ORDER, and not merely their presence, something a test can fail on.
    assert str(caught.value) == "broker POST /v1/chat/stream -> 401"
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)


def test_reaching_for_the_streamed_body_is_the_trap_the_mapping_sidesteps():
    """Locks the REASON for reporting only the status code, executably, because the comment that
    records it cannot fail.

    Both ways of describing a streamed response from a handler OUTSIDE the stream context raise
    a `StreamError` — a `RuntimeError` that `except httpx.HTTPError` does not catch and
    `except BrokerError` does not catch either, since `BrokerError` is its sibling rather than
    its parent. So a later tidy-up that "restores parity with `_post`" reintroduces exactly the
    unmapped escape V-16 was about, and this test is what stops it silently.
    """
    captured: list[httpx.HTTPStatusError] = []

    async def probe() -> None:
        async with httpx.AsyncClient(transport=_Transport(401, [BODY], None)) as client:
            try:
                async with client.stream("POST", "http://broker/v1/chat/stream", json={}) as resp:
                    resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                captured.append(exc)

    asyncio.run(probe())
    exc = captured[0]
    assert exc.response.status_code == 401, "the code is always available; that is why it is used"

    with pytest.raises(httpx.ResponseNotRead) as not_read:
        exc.response.text                                     # what `_post` does
    assert not isinstance(not_read.value, httpx.HTTPError)
    assert not isinstance(not_read.value, broker.BrokerError)

    with pytest.raises(httpx.StreamClosed) as closed:
        asyncio.run(exc.response.aread())                     # the documented alternative
    assert not isinstance(closed.value, httpx.HTTPError)


def test_a_connect_failure_opening_the_stream_is_a_broker_error(stream):
    """Escape 2 — `client.stream()` itself, before there is any response to check."""
    drain = stream(raises=httpx.ConnectError("connection refused"))
    with pytest.raises(broker.BrokerError, match="unreachable"):
        drain()


def test_a_read_timeout_mid_stream_is_a_broker_error(stream):
    """Escape 2 again, by exception TYPE rather than by position.

    The docstring here used to claim "a stream that stalls after the first delta ... the
    mapping working around a live yield". It does not: the transport raises on its first
    statement, so no response exists and nothing has been yielded. It is the connect-failure
    test with a different exception class, which is still worth having (a ReadTimeout and a
    ConnectError take the same clause only because both subclass httpx.HTTPError) but is not
    what it said.

    The property it claimed DOES hold, measured separately against a real server that sent
    one frame then stalled past a 1s timeout: BrokerError, __cause__ a ReadTimeout, and the
    consumer keeps the delta it already had. Expressing that through this fake needs a
    transport that can stall mid-body, which httpx.AsyncByteStream cannot do."""
    drain = stream(raises=httpx.ReadTimeout("timed out"))
    with pytest.raises(broker.BrokerError, match="unreachable"):
        drain()


def test_a_truncated_ndjson_frame_is_a_broker_error(stream):
    """Escape 3 — a partial final line, so `json.loads` is handed half a frame.

    NOT a severed hop, despite what this said before. A genuinely severed chunked stream
    raises httpx.RemoteProtocolError, which IS an httpx.HTTPError, so the clause above takes
    that case and reports it as unreachable. This clause is reached by a graceful end of
    stream whose last line is incomplete, which is exactly what the fake below produces:
    an AsyncByteStream has no way to express an incomplete body."""
    drain = stream(chunks=(b'{"message":{"content":"The corpus "}}\n', b'{"message":{"cont'))
    with pytest.raises(broker.BrokerError, match="malformed frame"):
        drain()


def test_a_status_error_with_no_response_attribute_is_still_mapped(monkeypatch):
    """Robustness over politeness: the mapping reads `.response`/`.status_code` defensively, so a
    stand-in exception type that carries neither still comes out as a BrokerError. Without this
    the fix would fail loudest exactly where the suites run."""
    monkeypatch.setattr(broker, "httpx", _ShimHttpx())

    async def drain() -> None:
        async for _ in broker.chat_stream(None, [{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(broker.BrokerError, match="broker said 401"):
        asyncio.run(drain())


def test_an_in_band_error_frame_is_reported_verbatim(stream):
    """The broker's own NDJSON error frame already raised BrokerError, and none of the new
    clauses may re-wrap it — which is why not one of them is `except Exception`."""
    drain = stream(chunks=(b'{"error":"model nemotron-3-nano:4b is not installed"}\n',))
    with pytest.raises(broker.BrokerError, match=r"^model nemotron-3-nano:4b is not installed$"):
        drain()


# --- found by the 2026-09-15 audit of this very commit ------------------------------

@pytest.mark.parametrize("bad", [b"[1, 2]\n", b"5\n", b"null\n", b'"boom"\n'])
def test_a_frame_that_parses_but_is_not_an_object_is_a_broker_error(stream, bad):
    """Escape 4, which the first version of this fix missed entirely.

    `json.loads` SUCCEEDS on a bare list, number, string or null, so `json.JSONDecodeError`
    never fires; then `frame.get("error")` raises `AttributeError`, which none of the three
    clauses catch. Measured consequences before the guard: a bare HTTP 500 out of
    smb-partner's POST /api/scenario/generate, which is the documented fallback for clients
    that cannot hold a socket, and the raw Python string "'list' object has no attribute
    'get'" rendered into the answer panel on the WebSocket paths.

    Parametrized over all four non-object JSON types on purpose: a guard written as
    `isinstance(frame, list)` would pass the first case and fail the other three.
    """
    drain = stream(chunks=(b'{"message":{"content":"partial "}}\n', bad))
    with pytest.raises(broker.BrokerError) as caught:
        drain()
    assert "non-object frame" in str(caught.value)
    # Not an AttributeError wearing a BrokerError's name: the guard must raise directly.
    assert not isinstance(caught.value.__cause__, AttributeError)


def test_a_failure_whose_str_is_empty_still_names_something(stream):
    """`str(httpx.ReadTimeout())` is the EMPTY STRING, and a read timeout mid-answer is the
    likeliest failure of all on a 600s streaming call. So the frame the user saw read
    "broker POST /v1/chat/stream unreachable: " and stopped at the colon.

    Asserted on the rendered message rather than on the exception type, because the defect
    was entirely in the rendering."""
    drain = stream(raises=httpx.ReadTimeout(""))
    with pytest.raises(broker.BrokerError) as caught:
        drain()
    msg = str(caught.value)
    assert not msg.rstrip().endswith(":"), f"error message says nothing after the colon: {msg!r}"
    assert "ReadTimeout" in msg
