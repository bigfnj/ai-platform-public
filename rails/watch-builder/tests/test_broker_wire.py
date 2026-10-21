"""What this rail actually puts on the wire to the broker's /v1/chat.

test_ai.py stubs `broker.chat` whole, which is right for the AI logic and blind to the request
body. That blindness is how the designer's 3000-token cap went nowhere: it was sent as a
top-level `max_tokens`, the OpenAI spelling, and the broker (Ollama's dialect: `options`,
`num_predict`) dropped it without a word. These pin the body itself.
"""
from __future__ import annotations

import json

import pytest

from watch_builder import broker

# The top-level fields the broker's ChatRequest reads (services/broker/app/schemas.py). The
# broker drops any other field, logging a warning since 2026-10-07; a rail cannot import the
# broker, so the list is restated here and a new field there has to be added here too.
BROKER_FIELDS = {"model", "messages", "options", "keep_alive", "format", "think", "requester"}


class _Resp:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Capture the request instead of sending it. No broker, no GPU."""
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Resp({"message": {"role": "assistant", "content": "ok"}})

    monkeypatch.setattr(broker.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_the_token_cap_and_temperature_travel_inside_options(sent):
    out = broker.chat([{"role": "user", "content": "q"}], model="@watch-builder-designer",
                      max_tokens=3000, temperature=0.6)
    assert out == "ok"
    assert sent["url"].endswith("/v1/chat")
    assert sent["body"]["options"] == {"num_predict": 3000, "temperature": 0.6}


def test_every_top_level_field_is_one_the_broker_reads(sent):
    """Anything outside this set is dropped by the broker, so sending it is a setting that
    silently does nothing."""
    broker.chat([{"role": "user", "content": "q"}], model="m", max_tokens=10)
    assert set(sent["body"]) <= BROKER_FIELDS, set(sent["body"]) - BROKER_FIELDS
