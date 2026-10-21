"""What a BrokerError carries, and what image() puts on the wire.

The gateway relays a broker refusal (a bad token label, a duplicate, the first-mint lockout) to
the admin's screen. It used to recover the broker's message by splitting ``str(exc)`` on ": ",
which cut any message containing that sequence in half; the test then faked the exception
string, so it agreed with the parser rather than with the broker. These tests go through the
REAL client against a MockTransport answering in FastAPI's own error shape.

No network.
"""
import asyncio
import json

import httpx
import pytest

from platform_core.broker_client import BrokerClient, BrokerError


def _client(handler):
    return BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler)))


def _refusing(status: int, body):
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)
    return handler


def test_a_fastapi_refusal_keeps_its_whole_detail_and_status():
    msg = "a token labelled 'studio: east' already exists"
    with pytest.raises(BrokerError) as err:
        asyncio.run(_client(_refusing(400, {"detail": msg})).create_token("studio: east", "full"))
    assert err.value.status == 400
    assert err.value.detail == msg
    # The message keeps its old shape, so a caller matching on it is unaffected.
    assert str(err.value).startswith("broker POST /v1/tokens -> 400: ")


def test_a_non_json_error_body_is_the_detail_as_text():
    with pytest.raises(BrokerError) as err:
        asyncio.run(_client(_refusing(502, "<html>bad gateway</html>")).tokens())
    assert err.value.status == 502 and err.value.detail == "<html>bad gateway</html>"


def test_a_structured_detail_falls_back_to_the_body_text():
    """FastAPI's 422 carries a LIST in detail; that is not a sentence to show anyone."""
    body = {"detail": [{"loc": ["body", "label"], "msg": "field required"}]}
    with pytest.raises(BrokerError) as err:
        asyncio.run(_client(_refusing(422, body)).tokens())
    assert err.value.status == 422
    assert json.loads(err.value.detail) == body   # the body as sent, not a list's repr


def test_a_200_that_is_not_json_is_a_broker_error_not_a_decode_error():
    with pytest.raises(BrokerError) as err:
        asyncio.run(_client(_refusing(200, "<html>captive portal</html>")).status())
    assert err.value.status == 200
    assert "not JSON" in str(err.value)


def test_an_unreachable_broker_has_no_status():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(BrokerError) as err:
        asyncio.run(_client(handler).status())
    assert err.value.status is None and err.value.detail is None


def _recording(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"images": []})
    return handler


def test_image_sends_no_model_unless_asked():
    seen: list = []
    asyncio.run(_client(_recording(seen)).image(["a cat"]))
    assert seen == [{"prompts": ["a cat"], "steps": 4, "size": 512}]


def test_image_can_name_a_backend():
    seen: list = []
    asyncio.run(_client(_recording(seen)).image(["a cat"], model="flux-schnell"))
    assert seen[0]["model"] == "flux-schnell"
