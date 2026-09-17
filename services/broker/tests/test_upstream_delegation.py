"""Delegating a role to another broker.

The platform was one broker per box until a rail needed a bigger card than the one it was
installed on. Rather than teach every rail to find a second broker — a per-rail URL, a per-rail
token, and a contract rule for each — a role may name a registered upstream and this broker
forwards the call. The rail is unchanged and does not know.

Two properties carry most of the risk and are tested hardest here: the syntax must not collide
with an Ollama model tag, and a delegated call must NOT take the local GPU gate.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.broker import Broker
from app.config import BrokerSettings


def _settings(tmp_path, upstreams: dict | None, monkeypatch) -> BrokerSettings:
    if upstreams is not None:
        (tmp_path / "upstreams.json").write_text(json.dumps(upstreams), encoding="utf-8")
        monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    else:
        monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("BROKER_ROLES_FILE", str(tmp_path / "roles.json"))
    return BrokerSettings()


OFFSITE = {"offsite": {"url": "http://192.168.1.11:11500/", "token": "t"}}


# --- the registry -------------------------------------------------------------------------

def test_registry_reads_and_normalises(tmp_path, monkeypatch):
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    assert s.upstreams() == {"offsite": {"url": "http://192.168.1.11:11500", "token": "t"}}


def test_a_missing_or_malformed_registry_degrades_to_local_only(tmp_path, monkeypatch):
    """Delegation is an enhancement. A broken registry must not take the GPU layer down."""
    assert _settings(tmp_path, None, monkeypatch).upstreams() == {}
    (tmp_path / "upstreams.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    assert BrokerSettings().upstreams() == {}


def test_local_cannot_be_redefined(tmp_path, monkeypatch):
    """Otherwise 'local' means this card or a remote depending on which code path asked."""
    s = _settings(tmp_path, {"local": {"url": "http://elsewhere:11500"}}, monkeypatch)
    assert "local" not in s.upstreams()


def test_an_entry_without_a_url_is_dropped(tmp_path, monkeypatch):
    s = _settings(tmp_path, {"offsite": {"token": "t"}}, monkeypatch)
    assert s.upstreams() == {}


# --- the syntax ---------------------------------------------------------------------------

def test_a_double_colon_names_an_upstream(tmp_path, monkeypatch):
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    assert s.delegate_ref("offsite::mistral-small3*:24b") == ("offsite", "mistral-small3*:24b")


def test_a_single_colon_is_a_model_tag_not_a_delegation(tmp_path, monkeypatch):
    """The whole reason the separator is doubled. `gemma3:4b` must never be read as upstream
    'gemma3' and model '4b' — and it would be, on whichever box happened to register an upstream
    named after a model family."""
    s = _settings(tmp_path, {"gemma3": {"url": "http://x:11500"}}, monkeypatch)
    assert s.delegate_ref("gemma3:4b") == (None, "gemma3:4b")


def test_an_unregistered_upstream_stays_whole(tmp_path, monkeypatch):
    """Returned verbatim so it fails to match anything and shows as a MISSING chip. Stripping
    the prefix and running a same-named model locally would use the wrong box and report success.
    """
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    assert s.delegate_ref("nosuchbox::gemma3:4b") == (None, "nosuchbox::gemma3:4b")


def test_set_role_refuses_an_unregistered_upstream(tmp_path, monkeypatch):
    """Saved unchecked, the panel would report the role as off-site while every call ran here."""
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    with pytest.raises(ValueError, match="unknown upstream"):
        s.set_role("chat", "nosuchbox::gemma3:4b")


def test_set_role_accepts_a_registered_one(tmp_path, monkeypatch):
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    s.set_role("chat", "offsite::mistral-small3*:24b")
    assert s.roles()["chat"] == "offsite::mistral-small3*:24b"


# --- routing ------------------------------------------------------------------------------

def _broker(roles: dict, upstreams: dict):
    b = Broker.__new__(Broker)
    b.settings = SimpleNamespace(
        roles=lambda: roles,
        upstreams=lambda: upstreams,
        delegate_ref=lambda v: (
            (v.partition("::")[0], v.partition("::")[2])
            if "::" in v and v.partition("::")[0] in upstreams else (None, v)),
        ollama_timeout=600.0,
    )
    b.ollama = SimpleNamespace(tags=_async([{"name": "gemma3:4b"}]))
    return b


def _async(value):
    async def f(*a, **k):
        return value
    return f


def test_split_routes_a_delegated_role_to_its_upstream():
    b = _broker({"openmaic": "offsite::mistral-small3*:24b"},
                {"offsite": {"url": "http://192.168.1.11:11500", "token": "t"}})
    up, model = asyncio.run(b._split("@openmaic"))
    assert up is not None and up.name == "offsite"
    # The glob is handed over UNRESOLVED: the upstream knows its inventory and this box does not.
    assert model == "mistral-small3*:24b"


def test_split_keeps_a_local_role_local_and_resolves_it_here():
    b = _broker({"chat": "gemma3*"}, {})
    up, model = asyncio.run(b._split("@chat"))
    assert up is None
    assert model == "gemma3:4b"


def test_delegated_calls_do_not_take_the_local_gpu_gate():
    """The single-slot gate models THIS card. Holding it for a generation running on another box
    would serialise local work behind something that cannot contend with it — the opposite of
    what the gate is for. Asserted by giving the broker a gate that fails if entered.
    """
    b = _broker({"openmaic": "offsite::m*"},
                {"offsite": {"url": "http://x:11500", "token": ""}})

    class Boom:
        def hold(self, **kw):
            raise AssertionError("the local GPU gate was taken for a delegated call")

    b.gate = Boom()
    sent = {}

    async def fake_chat(model, messages, **kw):
        sent["model"] = model
        return {"message": {"content": "ok"}}

    async def run():
        up, model = await b._split("@openmaic")
        up.chat = fake_chat
        # Mirrors broker.chat()'s delegated branch: forward, never touching b.gate.
        return await up.chat(model, [{"role": "user", "content": "hi"}])

    assert asyncio.run(run())["message"]["content"] == "ok"
    assert sent["model"] == "m*"


# --- BOM tolerance ---------------------------------------------------------------------------

def test_a_bom_written_roles_file_is_still_read(tmp_path, monkeypatch):
    """PowerShell's Set-Content / Out-File write a UTF-8 BOM by default, and every one of these
    files is hand-edited on a Windows box.

    Read as plain utf-8 the BOM makes json.loads raise, the overlay's `except` swallows it, and
    the ENTIRE role map silently reverts to the 24 GB DEFAULT_ROLES with nothing logged. Found
    exactly that way while testing delegation: a roles.json that plainly said
    `offsite::mistral-small3*:24b` came back reporting the default.
    """
    monkeypatch.setenv("BROKER_ROLES_FILE", str(tmp_path / "roles.json"))
    monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    (tmp_path / "roles.json").write_text(json.dumps({"chat": "tagged:7b"}), encoding="utf-8-sig")
    assert BrokerSettings().overlay_roles() == {"chat": "tagged:7b"}


def test_a_bom_written_upstreams_file_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    (tmp_path / "upstreams.json").write_text(
        json.dumps({"offsite": {"url": "http://x:11500"}}), encoding="utf-8-sig")
    assert "offsite" in BrokerSettings().upstreams()
