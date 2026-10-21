"""The Models-tab toggle writes the broker's WHOLE disabled list, so it must start from the real one.

It used to read that list through _disabled_set(), which turns any broker error into an empty
set (right for a read view, wrong here). One failed GET /v1/disabled and the write that followed
replaced disabled.json with a list holding only the toggled model, silently re-enabling every
other disabled model on the box. Now a failed read is a 502 and nothing is written.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from platform_core import BrokerClient, BrokerError
from platform_gateway_app import main as m


class _Broker:
    def __init__(self, disabled=None, fail_read=False):
        self._disabled = list(disabled or [])
        self.fail_read = fail_read
        self.written: list[list[str]] = []
        self.unloaded: list[str] = []

    async def disabled(self):
        if self.fail_read:
            raise BrokerError("broker GET /v1/disabled unreachable: timed out")
        return list(self._disabled)

    async def set_disabled(self, names):
        self.written.append(list(names))
        return {"disabled": names}

    async def unload(self, name):
        self.unloaded.append(name)
        return {}


def _toggle(monkeypatch, broker, name, enabled):
    monkeypatch.setattr(m.app.state, "broker", broker, raising=False)
    return asyncio.run(m.admin_model_toggle(m.ModelToggleBody(name=name, enabled=enabled),
                                            admin=None))


def test_a_failed_read_writes_nothing_and_is_a_502(monkeypatch):
    b = _Broker(disabled=["a:1", "b:2"], fail_read=True)
    with pytest.raises(HTTPException) as exc:
        _toggle(monkeypatch, b, "c:3", False)
    assert exc.value.status_code == 502
    assert b.written == [], "a list built from a failed read was written back"
    assert b.unloaded == []


def test_disabling_adds_to_the_existing_list(monkeypatch):
    b = _Broker(disabled=["a:1", "b:2"])
    _toggle(monkeypatch, b, "c:3", False)
    assert b.written == [["a:1", "b:2", "c:3"]]
    assert b.unloaded == ["c:3"]


def test_enabling_removes_only_that_model(monkeypatch):
    b = _Broker(disabled=["a:1", "b:2"])
    _toggle(monkeypatch, b, "a:1", True)
    assert b.written == [["b:2"]]


def test_a_write_the_broker_refuses_is_a_409_in_its_words(monkeypatch):
    """The broker answers a disabled.json it cannot land a write in with a 409 naming the file.
    Through the REAL BrokerClient, so the words are what the broker sends, not what a fake
    wrote; relayed as a 502 'broker unreachable', the admin went looking for an outage."""
    said = "refused: disabled.json cannot be read, so this write would replace it whole"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path == "/v1/disabled":
            return httpx.Response(409, json={"detail": said})
        if request.url.path == "/v1/disabled":
            return httpx.Response(200, json={"disabled": ["a:1"]})
        return httpx.Response(200, json={})          # the best-effort unload

    client = BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler)))
    with pytest.raises(HTTPException) as exc:
        _toggle(monkeypatch, client, "c:3", False)
    assert exc.value.status_code == 409 and exc.value.detail == said
