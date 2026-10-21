"""A /v1/chat body field the broker does not know is still ignored, and is now SAID to be.

watch-builder sent `max_tokens: 3000` and `temperature` at the top level of its /v1/chat body,
the OpenAI place for them. The broker's dialect is Ollama's, where both live under `options`
(`num_predict`, `temperature`), and pydantic's default for an unknown field is to drop it. So
the cap was discarded on every call, the rail got 200s and plausible answers, and nothing
anywhere recorded that its setting had never reached the model.

Refusing such a body (422) would be the stricter fix and the wrong one: every rail that sends a
harmless extra today would start failing. So the field is still dropped, and the broker logs a
warning naming it, which turns the silent drop into one a person can find.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker import Broker
from app.config import BrokerSettings

MODEL = "qwen3.6:27b"
BODY = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def post(tmp_path, monkeypatch):
    """POST a body to a real broker app whose Ollama is a MockTransport; returns
    (response, the payload Ollama received)."""
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES"):
        monkeypatch.delenv(key, raising=False)

    def _post(path: str, body: dict):
        from app.main import app

        seen: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": []})
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [
                    {"name": MODEL, "size": 1, "digest": "d"}]})
            seen["payload"] = json.loads(request.content or b"{}")
            if path.endswith("/stream"):
                return httpx.Response(200, content=b'{"done": true}\n',
                                      headers={"content-type": "application/x-ndjson"})
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"},
                                             "done": True})

        settings = BrokerSettings(
            roles_file=str(tmp_path / "roles.json"),
            disabled_file=str(tmp_path / "disabled.json"),
            tokens_file=str(tmp_path / "tokens.json"), auth_token="")
        broker = Broker(settings)
        broker.ollama._client = httpx.AsyncClient(
            base_url="http://ollama.test", transport=httpx.MockTransport(handler))
        app.state.settings = settings
        app.state.broker = broker
        resp = TestClient(app).post(path, json=body)
        return resp, seen.get("payload")

    return _post


def _warnings(capsys) -> list[str]:
    return [ln for ln in capsys.readouterr().err.splitlines() if "unknown field" in ln]


def test_unknown_top_level_fields_are_still_accepted_not_refused(post):
    """The compatibility half. A 422 here would break every rail sending a harmless extra."""
    resp, payload = post("/v1/chat", {**BODY, "max_tokens": 3000, "temperature": 0.4})
    assert resp.status_code == 200, resp.text
    # Still dropped: they are not Ollama request fields, and guessing a mapping for them would
    # be the broker inventing settings the caller never stated in its own dialect.
    assert "max_tokens" not in payload and "temperature" not in payload
    assert "options" not in payload


def test_each_dropped_field_is_named_in_a_warning(post, capsys):
    """The watch-builder case, exactly as it was sent."""
    resp, _ = post("/v1/chat", {**BODY, "max_tokens": 3000, "temperature": 0.4,
                                "stream": False})
    assert resp.status_code == 200
    [line] = _warnings(capsys)
    for name in ("max_tokens", "temperature", "stream"):
        assert name in line
    assert MODEL in line, "the warning must say which caller's role to go and fix"
    assert "options" in line, "and where the setting belongs instead"


def test_the_warning_names_fields_not_values(post, capsys):
    """A value can be prompt text or a credential; the log line is for an operator."""
    post("/v1/chat", {**BODY, "system_secret": "sk-do-not-log-me"})
    [line] = _warnings(capsys)
    assert "system_secret" in line
    assert "sk-do-not-log-me" not in line


def test_a_body_of_known_fields_logs_nothing(post, capsys):
    """Otherwise the warning is noise on every call and stops being read."""
    resp, payload = post("/v1/chat", {**BODY, "options": {"num_predict": 3000},
                                      "keep_alive": "5m", "format": "json", "think": False,
                                      "requester": "owner-one"})
    assert resp.status_code == 200
    assert payload["options"] == {"num_predict": 3000}
    assert _warnings(capsys) == []


def test_the_streaming_route_warns_too(post, capsys):
    resp, _ = post("/v1/chat/stream", {**BODY, "max_tokens": 50})
    assert resp.status_code == 200
    [line] = _warnings(capsys)
    assert "max_tokens" in line and "/v1/chat/stream" in line
