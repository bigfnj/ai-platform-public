"""A settings file the broker cannot parse reads as its documented empty value. It never raises.

The hand-edited JSON files the broker hot-reads (tokens.json, disabled.json, upstreams.json,
voice_engines.json) caught only `OSError` and `JSONDecodeError`. A file that is not UTF-8 (what
Windows PowerShell 5.1's `>` writes: UTF-16 with a BOM) raises `UnicodeDecodeError`, and one
nested deeper than the parser's stack raises `RecursionError`; neither is a JSONDecodeError, so
both escaped. roles.json got this fix earlier (test_cli_routing); these four had not.

What each one cost, verified by the 2026-10-14 audit:

  * tokens.json: `require_token` read it before the /healthz exemption, so EVERY request was a
    500, the liveness probe included.
  * upstreams.json: with a role delegated, `audit_roles` reads it in the lifespan, so the broker
    did not start.
  * disabled.json: `resolve_ref` reads it for every `@role`, so every role chat failed.

Also here: the OpenAI surface's body parser (a 500 for a body that is not UTF-8 or is nested too
deeply; the caller's mistake, so a 400), the voice registry (read without utf-8-sig, so a BOM was
a 503), and require_token's order (healthz first, the token store last).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import voice
from app.broker import Broker
from app.config import BrokerSettings
from app.main import require_token

#: How deep a file has to nest before json.loads raises RecursionError on this interpreter.
DEEP = "[" * 100_000


def _utf16(value) -> bytes:
    """`value` as JSON, written the way Windows PowerShell 5.1's `>` writes text: UTF-16 + BOM."""
    return json.dumps(value).encode("utf-16")


# --- the four readers ----------------------------------------------------------------------

#: reader -> (the setting naming its file, a well-formed value for it, what an unreadable file
#: must read as). The well-formed value is what the UTF-16 file holds: valid JSON, wrong encoding.
READERS = {
    "tokens": ("tokens_file",
               [{"id": "a", "label": "a", "scope": "full", "hash": "h", "prefix": "bt_a"}], []),
    "disabled": ("disabled_file", ["qwen3:8b"], set()),
    "upstreams": ("upstreams_file", {"offsite": {"url": "http://gpu.test:11500"}}, {}),
    # No engines root, so the configured table is empty and the overlay is all there is.
    "voice_engines": ("voice_engines_file",
                      {"piper": {"python": "py", "entry": "e.py", "cwd": "."}}, {}),
}


def _write(path, kind: str, good) -> None:
    if kind == "utf-16":
        path.write_bytes(_utf16(good))
    else:
        path.write_text(DEEP, encoding="utf-8")


@pytest.mark.parametrize("kind", ["utf-16", "deep"])
@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_unparseable_settings_file_reads_as_its_empty_value(tmp_path, reader, kind):
    field, good, empty = READERS[reader]
    path = tmp_path / f"{reader}.json"
    _write(path, kind, good)
    settings = BrokerSettings(**{field: str(path)}, voice_engines_dir="")
    assert getattr(settings, reader)() == empty


@pytest.mark.parametrize("reader", sorted(READERS))
def test_the_same_file_saved_as_utf8_with_a_bom_still_reads(tmp_path, reader):
    """The control: the fix is about files that cannot be parsed, not a stricter reader. A BOM
    (PowerShell's Set-Content and Out-File write one) was always accepted and still is."""
    field, good, _empty = READERS[reader]
    path = tmp_path / f"{reader}.json"
    path.write_text(json.dumps(good), encoding="utf-8-sig")
    got = getattr(BrokerSettings(**{field: str(path)}, voice_engines_dir=""), reader)()
    assert got and got != READERS[reader][2]


def test_a_utf16_token_store_still_counts_as_configured_and_fails_closed(tmp_path):
    """Unparseable is not "no store": enforcement keys on the file EXISTING (test_tokens_store),
    so a store nobody can read means nobody authenticates, never everybody."""
    path = tmp_path / "tokens.json"
    path.write_bytes(_utf16(READERS["tokens"][1]))
    s = BrokerSettings(tokens_file=str(path))
    assert s.tokens() == [] and s.tokens_configured() is True


# --- what each one used to break ------------------------------------------------------------

class _Ollama:
    base_url = "http://ollama.test"

    async def version(self):
        return "0.0-test"


@pytest.mark.parametrize("kind", ["utf-16", "deep"])
def test_an_unparseable_token_store_leaves_healthz_up_and_every_other_route_closed(
        tmp_path, monkeypatch, kind):
    """Before, both answers were a 500: require_token parsed tokens.json before it looked at
    the path, and the parse raised."""
    from app.main import app
    path = tmp_path / "tokens.json"
    _write(path, kind, READERS["tokens"][1])
    settings = BrokerSettings(tokens_file=str(path), auth_token="",
                              roles_file=str(tmp_path / "roles.json"),
                              disabled_file=str(tmp_path / "disabled.json"))
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "broker", SimpleNamespace(ollama=_Ollama()), raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    client = TestClient(app, raise_server_exceptions=False)
    health = client.get("/healthz")
    assert health.status_code == 200, health.text
    closed = client.get("/v1/disabled", headers={"authorization": "Bearer bt_anything"})
    assert closed.status_code == 401, closed.text


@pytest.mark.parametrize("kind", ["utf-16", "deep"])
def test_a_delegated_role_with_an_unparseable_registry_does_not_stop_the_audit(
        tmp_path, monkeypatch, kind):
    """audit_roles runs in the lifespan, unguarded. With a role delegated it reads
    upstreams.json, and a raise there was a broker that did not start. Now the registry reads as
    empty and the audit says the box is not registered."""
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"chat": "offsite::qwen3:8b"}), encoding="utf-8")
    ups = tmp_path / "upstreams.json"
    _write(ups, kind, READERS["upstreams"][1])
    b = Broker.__new__(Broker)
    b.settings = BrokerSettings(roles_file=str(roles), upstreams_file=str(ups),
                                disabled_file=str(tmp_path / "disabled.json"))

    async def tags():
        return [{"name": "qwen3:8b", "size": 1}]

    async def no_card():
        return None

    b.ollama = SimpleNamespace(tags=tags)
    monkeypatch.setattr("app.broker.gpu.vram", no_card)
    out = asyncio.run(b.audit_roles())
    assert any("@chat" in line and "not registered" in line for line in out), out


@pytest.mark.parametrize("kind", ["utf-16", "deep"])
def test_an_unparseable_disabled_file_does_not_fail_a_role_resolve(tmp_path, kind):
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"chat": "qwen3:8b"}), encoding="utf-8")
    dis = tmp_path / "disabled.json"
    _write(dis, kind, READERS["disabled"][1])
    b = Broker.__new__(Broker)
    b.settings = BrokerSettings(roles_file=str(roles), disabled_file=str(dis))
    b._digests = {}
    tags = [{"name": "qwen3:8b", "size": 1, "digest": "d"}]
    name, fb = asyncio.run(b.resolve_ref("@chat", substitute=True, tags=tags))
    assert (name, fb) == ("qwen3:8b", None)


# --- require_token's order -----------------------------------------------------------------

def _gate_request(path: str, headers: dict, *, token: str = "secret"):
    """A request whose token store must NOT be read: tokens() raises if it is."""
    def tokens():
        raise AssertionError("require_token read the token store")
    return SimpleNamespace(
        url=SimpleNamespace(path=path), headers=headers, method="GET", state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(
            settings=SimpleNamespace(auth_token=token, tokens=tokens,
                                     tokens_configured=lambda: True),
            token_seen={})))


def test_healthz_reads_no_token_store():
    require_token(_gate_request("/healthz", {}))


def test_the_shared_secret_is_accepted_without_reading_the_token_store():
    """Every rail sends the shared secret. Its calls used to parse tokens.json first anyway."""
    req = _gate_request("/v1/status", {"authorization": "Bearer secret"})
    require_token(req)
    assert req.state.broker_scope == "full"


def test_a_named_token_still_reads_the_store():
    """The other side: only the shared secret skips the read. A named token is looked up."""
    req = _gate_request("/v1/status", {"authorization": "Bearer bt_named"})
    with pytest.raises(AssertionError, match="read the token store"):
        require_token(req)


def test_no_credential_is_refused_before_the_store_is_read():
    with pytest.raises(HTTPException) as e:
        require_token(_gate_request("/v1/status", {}))
    assert e.value.status_code == 401


# --- the OpenAI surface's body parser --------------------------------------------------------

@pytest.mark.parametrize("body, says", [
    (b'{"model": "@chat", "messages": "\xff"}', "not UTF-8"),
    (b"[" * 100_000, "nested too deeply"),
    (b"{not json", "invalid JSON body"),
], ids=["not-utf8", "deep", "not-json"])
@pytest.mark.parametrize("route", ["/openai/v1/chat/completions", "/openai/v1/embeddings",
                                   "/openai/v1/audio/speech", "/openai/v1/images/generations"])
def test_an_unparseable_openai_body_is_an_openai_shaped_400(tmp_path, monkeypatch, route, body,
                                                             says):
    from app.main import app
    monkeypatch.setattr(app.state, "settings", BrokerSettings(
        tokens_file=str(tmp_path / "tokens.json"), auth_token=""), raising=False)
    monkeypatch.setattr(app.state, "broker", SimpleNamespace(), raising=False)
    r = TestClient(app, raise_server_exceptions=False).post(
        route, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert says in err["message"] and err["type"] == "invalid_request_error"


# --- the voice registry ---------------------------------------------------------------------

def test_a_voice_registry_with_a_bom_loads(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"voices": [{"voice_id": "v1", "engine": "piper"}]}),
                   encoding="utf-8-sig")
    assert voice.load_registry(reg)["voices"][0]["voice_id"] == "v1"


def test_a_voice_registry_nested_too_deeply_is_a_registry_error(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(DEEP, encoding="utf-8")
    with pytest.raises(voice.VoiceRegistryError, match="unreadable"):
        voice.load_registry(reg)
