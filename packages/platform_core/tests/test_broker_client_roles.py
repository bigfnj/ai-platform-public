"""BrokerClient.roles() on the wire: unchanged for every existing caller, `inventory` on request.

Every rail's chip polls /v1/roles through its own client, and the gateway through this one. The
`inventory` view costs the broker a probe of every registered box, so it must be sent only when
asked for: a stray query parameter here would make every roles() call pay for the Rails tab.

No network: an httpx.MockTransport records the request.
"""
import asyncio

import httpx

from platform_core.broker_client import BrokerClient


def _client(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"roles": []})

    return BrokerClient("http://broker.test", client=httpx.AsyncClient(
        base_url="http://broker.test", transport=httpx.MockTransport(handler)))


def test_roles_sends_no_query_by_default():
    seen: list = []
    asyncio.run(_client(seen).roles())
    assert seen[0].url.path == "/v1/roles"
    assert seen[0].url.query == b""


def test_roles_asks_for_the_inventory_only_when_told_to():
    seen: list = []
    asyncio.run(_client(seen).roles(inventory=True))
    assert seen[0].url.path == "/v1/roles"
    assert seen[0].url.params.get("inventory") == "true"


def test_roles_asks_for_local_only_when_told_to():
    seen: list = []
    asyncio.run(_client(seen).roles(local_only=True))
    assert dict(seen[0].url.params) == {"local_only": "true"}


def test_set_cli_role_puts_one_role_and_a_json_boolean():
    """One role per call, never the whole set, and a real JSON boolean: the broker's route
    takes StrictBool, so a client that sent "true" would be refused with a 422."""
    seen: list = []
    asyncio.run(_client(seen).set_cli_role("finance-fraud", True))
    req = seen[0]
    assert (req.method, req.url.path) == ("PUT", "/v1/cli/roles/finance-fraud")
    assert req.read() == b'{"enabled":true}'


def test_set_cli_role_sends_the_model_only_when_given_and_null_to_clear_it():
    """The broker leaves a role's Claude model alone when the body has no `model` key and
    clears it on null, so the client must tell "leave it" (no key) from "clear it" (None)."""
    seen: list = []
    client = _client(seen)
    asyncio.run(client.set_cli_role("edu", model="claude-haiku-5-5"))
    asyncio.run(client.set_cli_role("edu", model=None))
    asyncio.run(client.set_cli_role("edu", False, model="claude-opus-5-5"))
    assert [r.read() for r in seen] == [b'{"model":"claude-haiku-5-5"}', b'{"model":null}',
                                        b'{"enabled":false,"model":"claude-opus-5-5"}']
