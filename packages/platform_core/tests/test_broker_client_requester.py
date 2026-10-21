"""BrokerClient.chat's `requester`: who a broker call is FOR.

The broker answers a call from the platform owner's own Claude subscription only when this
field names an owner. The client's job is to put the value on the request when it is given
and to leave the key OUT when it is not: an older broker's request model has no such field,
and "" or null is a name the broker would have to refuse rather than an absence it reads as
background work. Both halves are asserted, because neither is visible at runtime.

No network: the request method is stubbed, the seam test_broker_client_voice.py uses.
"""
import asyncio

import pytest

from platform_core.broker_client import BrokerClient

MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


@pytest.fixture
def client(monkeypatch):
    c = BrokerClient.__new__(BrokerClient)
    seen: dict = {}

    async def fake_request(method, path, **kwargs):
        seen.update(method=method, path=path, json=kwargs.get("json"))
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(c, "_request", fake_request, raising=False)
    c.seen = seen  # type: ignore[attr-defined]
    return c


def test_requester_is_sent_when_given(client):
    asyncio.run(client.chat("@iep", MSGS, requester="owner-one"))
    assert client.seen["path"] == "/v1/chat"
    assert client.seen["json"]["requester"] == "owner-one"


@pytest.mark.parametrize("absent", [None, ""])
def test_requester_is_omitted_when_absent(client, absent):
    asyncio.run(client.chat("@iep", MSGS, requester=absent))
    assert "requester" not in client.seen["json"]


def test_the_default_payload_is_unchanged(client):
    """Byte-identical to the payload before the field existed, for an older broker."""
    asyncio.run(client.chat("@iep", MSGS, options={"temperature": 0.2}, format="json",
                            think=False, keep_alive="30m"))
    assert client.seen["json"] == {"model": "@iep", "messages": MSGS,
                                   "options": {"temperature": 0.2}, "keep_alive": "30m",
                                   "format": "json", "think": False}
