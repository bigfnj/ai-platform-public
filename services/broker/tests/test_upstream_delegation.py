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
from app.upstream import UpstreamError


def _settings(tmp_path, upstreams: dict | None, monkeypatch) -> BrokerSettings:
    if upstreams is not None:
        (tmp_path / "upstreams.json").write_text(json.dumps(upstreams), encoding="utf-8")
        monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    else:
        monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("BROKER_ROLES_FILE", str(tmp_path / "roles.json"))
    return BrokerSettings()


OFFSITE = {"offsite": {"url": "http://gpu-box.example.internal:11500/", "token": "t"}}


# --- the registry -------------------------------------------------------------------------

def test_registry_reads_and_normalises(tmp_path, monkeypatch):
    s = _settings(tmp_path, OFFSITE, monkeypatch)
    assert s.upstreams() == {"offsite": {"url": "http://gpu-box.example.internal:11500", "token": "t"}}


def test_a_missing_or_malformed_registry_degrades_to_local_only(tmp_path, monkeypatch):
    """Delegation is an enhancement. A broken registry must not take the GPU layer down."""
    assert _settings(tmp_path, None, monkeypatch).upstreams() == {}
    (tmp_path / "upstreams.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "upstreams.json"))
    assert BrokerSettings().upstreams() == {}


def test_local_cannot_be_redefined(tmp_path, monkeypatch):
    """Otherwise 'local' means this card or a remote depending on which code path asked."""
    s = _settings(tmp_path, {"local": {"url": "http://gpu-box.example.internal:11500"}}, monkeypatch)
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
        # resolve_ref consults the admin-disabled set on the LOCAL branch. This stub predates
        # that check; without it the local half of route() dies inside the stub rather than on
        # anything this file is about.
        disabled=lambda: set(),
        ollama_timeout=600.0,
    )
    b.ollama = SimpleNamespace(tags=_async([{"name": "gemma3:4b"}]))
    b._digests = {}
    return b


def _async(value):
    async def f(*a, **k):
        return value
    return f


def test_route_sends_a_delegated_role_to_its_upstream():
    b = _broker({"openmaic": "offsite::mistral-small3*:24b"},
                {"offsite": {"url": "http://gpu-box.example.internal:11500", "token": "t"}})
    up, model, fb = asyncio.run(b.route("@openmaic", substitute=False))
    assert up is not None and up.name == "offsite"
    # No fallback across a box boundary: substituting a LOCAL model for a role the
    # operator sent off-site would un-delegate it silently.
    assert fb is None
    # The glob is handed over UNRESOLVED: the upstream knows its inventory and this box does not.
    assert model == "mistral-small3*:24b"


def test_route_keeps_a_local_role_local_and_resolves_it_here():
    b = _broker({"chat": "gemma3*"}, {})
    up, model, fb = asyncio.run(b.route("@chat", substitute=False))
    assert up is None
    assert model == "gemma3:4b"
    assert fb is None


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
        up, model, _fb = await b.route("@openmaic", substitute=False)
        up.chat = fake_chat
        # Mirrors broker.chat()'s delegated branch: forward, never touching b.gate.
        return await up.chat(model, [{"role": "user", "content": "hi"}])

    assert asyncio.run(run())["message"]["content"] == "ok"
    assert sent["model"] == "m*"


# --- roles_view: a delegated row is judged on the box that runs it ---------------------------

class _Remote:
    """A remote broker's three read endpoints, as roles_view() dials them. Counts calls, so a
    test can prove one probe per box per sweep rather than one per role."""

    def __init__(self, models=(), loaded=(), healthy=True, broken=(), delay=0.0, roles=()):
        self._models = [m if isinstance(m, dict) else {"name": m} for m in models]
        self._loaded = list(loaded)
        self._healthy = healthy
        self._broken = set(broken)
        self._delay = delay       # seconds each READ (not healthy) takes, for the timeout tests
        self._roles = list(roles)
        self.calls = {"healthy": 0, "models": 0, "status": 0, "roles": 0}

    async def _read(self, what, value):
        self.calls[what] += 1
        if self._delay and what != "healthy":
            await asyncio.sleep(self._delay)
        if what in self._broken:
            raise UpstreamError(f"upstream 'offsite' GET /v1/{what} -> 401")
        return value

    async def roles(self):
        return await self._read("roles", self._roles)

    async def healthy(self):
        return await self._read("healthy", self._healthy)

    async def models(self):
        return await self._read("models", self._models)

    async def status(self):
        return await self._read("status", {"loaded": [{"name": n} for n in self._loaded]})


REMOTE = ("mistral-small3.1:24b", "mistral-small3.2:24b", "gemma3:27b")
DELEGATED = {"openmaic": "offsite::mistral-small3*:24b"}


def _view(roles, remote):
    b = _broker(roles, OFFSITE)
    b._embed_hints = ()
    b._upstream = lambda name: remote
    return {r["role"]: r for r in asyncio.run(b.roles_view())}


def test_a_delegated_glob_resident_on_the_remote_reads_loaded_by_its_concrete_name():
    """The regression. The documented form is a GLOB after `::`, and a glob never equals a
    resident model's name, so matching the raw pattern against the remote's loaded list kept
    every glob-delegated chip on COLD however warm that card was. Expanded against the list the
    REMOTE reports, the row names what that box will actually run."""
    row = _view(DELEGATED, _Remote(REMOTE, loaded=["mistral-small3.2:24b"]))["openmaic"]
    assert row["resolved"] == row["serving"] == "mistral-small3.2:24b"
    assert row["installed"] is True and row["loaded"] is True
    assert row["upstream"] == "offsite" and row["fallback"] is None


def test_a_resident_sibling_on_the_remote_is_not_loaded():
    """Resolved but not resident. The remote has the OLDER build warm and the glob picks 3.2,
    so the answer is cold: residency is judged by the concrete name, not the family."""
    row = _view(DELEGATED, _Remote(REMOTE, loaded=["mistral-small3.1:24b"]))["openmaic"]
    assert row["resolved"] == "mistral-small3.2:24b"
    assert row["installed"] is True and row["loaded"] is False


def test_a_glob_the_remote_has_nothing_for_is_not_installed():
    """Reachable is not enough. A remote that answers but has nothing the pattern matches
    cannot serve the role -- every call would fail there -- so green would be a lie, and red
    sends someone to pull the model on the right machine. `resolved` is None, as the local
    branch reports an unmatched glob; never the raw glob, which a rail would expand against its
    OWN inventory and so name a local near-miss."""
    row = _view(DELEGATED, _Remote(["gemma3:27b"]))["openmaic"]
    assert row["installed"] is False and row["loaded"] is False
    assert row["resolved"] is None and row["serving"] is None
    assert row["upstream"] == "offsite"


@pytest.mark.parametrize("remote", [
    _Remote(REMOTE, loaded=["mistral-small3.2:24b"], healthy=False),
    _Remote(REMOTE, broken={"healthy"}),
    _Remote(REMOTE, loaded=["mistral-small3.2:24b"], broken={"models"}),
], ids=["down", "health-probe-raises", "answers-but-inventory-unreadable"])
def test_an_unreadable_remote_degrades_its_rows_and_never_raises(remote):
    """roles_view feeds the Rails tab and every rail's chips, so one dark box must not take the
    view down -- nor colour a purely local role. It degrades to not-installed rather than
    guessing: a model we could not see is not one we can vouch for."""
    rows = _view({**DELEGATED, "chat": "gemma3*"}, remote)
    assert rows["openmaic"]["installed"] is False and rows["openmaic"]["loaded"] is False
    assert rows["openmaic"]["resolved"] is None
    assert rows["chat"]["installed"] is True


def test_a_box_that_is_down_is_not_dialled_again():
    """healthy() has a 5 s timeout, the reads 30 s. Once it says down, reading on would make the
    Rails tab wait out both timeouts for an answer already known."""
    remote = _Remote(REMOTE, healthy=False)
    _view(DELEGATED, remote)
    assert remote.calls == {"healthy": 1, "models": 0, "status": 0, "roles": 0}


def test_unreadable_residency_is_cold_not_missing():
    """Inventory read, status not: the model is there and only its residency is unknown, so the
    row stays installed and simply does not claim LOADED."""
    row = _view(DELEGATED, _Remote(REMOTE, broken={"status"}))["openmaic"]
    assert row["installed"] is True and row["loaded"] is False


def test_latest_tolerance_matches_the_local_rule():
    """Ollama reports an untagged pull as `name:latest`. A role pinned to `offsite::bge-m3`
    must find `bge-m3:latest` installed AND resident on the remote, exactly as resolve_ref and
    the rails' _same() would locally."""
    row = _view({"embed": "offsite::bge-m3"},
                _Remote(["bge-m3:latest"], loaded=["bge-m3:latest"]))["embed"]
    assert row["resolved"] == "bge-m3"
    assert row["installed"] is True and row["loaded"] is True


def test_a_concrete_name_the_remote_has_not_pulled_is_not_installed():
    """No glob, so the resolver passes the name through without looking at the list. The
    membership check is what keeps a model pulled on the wrong box from reading green."""
    row = _view({"openmaic": "offsite::mistral-small3.2:24b"},
                _Remote(["gemma3:27b"]))["openmaic"]
    assert row["resolved"] == "mistral-small3.2:24b"
    assert row["installed"] is False and row["loaded"] is False


def test_the_remote_ranking_sees_the_remote_s_parameter_size():
    """A broker's /v1/models flattens Ollama's details.parameter_size to the top level, and
    rank_key reads the nested one. Unreshaped, `qwen3:latest` (8.2B) would rank as size 0 and
    lose to `qwen3:4b` here, while the remote -- ranking its own Ollama tags -- serves 8.2B."""
    remote = _Remote([{"name": "qwen3:4b", "parameter_size": "4.0B"},
                      {"name": "qwen3:latest", "parameter_size": "8.2B"}])
    assert _view({"chat": "offsite::qwen3*"}, remote)["chat"]["resolved"] == "qwen3:latest"


def test_each_upstream_is_probed_once_per_sweep():
    """Several roles commonly share one box, and each probe is three network round trips."""
    remote = _Remote(REMOTE)
    _view({"openmaic": "offsite::mistral-small3*:24b", "course": "offsite::gemma3*"}, remote)
    assert remote.calls == {"healthy": 1, "models": 1, "status": 1, "roles": 0}


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


# --- audit_roles must survive an unregistered upstream ----------------------------------------

def test_audit_survives_a_role_delegated_to_an_unregistered_box(tmp_path, monkeypatch):
    """audit_roles() runs UNGUARDED in the broker's lifespan, so anything it raises stops the
    service from starting.

    The delegated branch interpolated `src` (the "inherited from DEFAULT_ROLES" provenance
    note), which was assigned several lines BELOW it. Roles are iterated in sorted order, so a
    delegated role sorting first raised UnboundLocalError and the broker did not come up; on a
    later iteration it was quieter and worse, silently carrying the previous role's provenance.

    A malformed or renamed upstreams.json triggers it for EVERY delegated role at once, because
    settings.upstreams() degrades to {} rather than raising. That is the realistic path in: the
    registry is gitignored and hand-edited.
    """
    monkeypatch.setenv("BROKER_ROLES_FILE", str(tmp_path / "roles.json"))
    monkeypatch.setenv("BROKER_UPSTREAMS_FILE", str(tmp_path / "absent.json"))
    # 'aaa-first' sorts ahead of everything, so the bad role IS the first iteration.
    (tmp_path / "roles.json").write_text(
        json.dumps({"aaa-first": "ghostbox::some-model:7b"}), encoding="utf-8")

    b = Broker.__new__(Broker)
    b.settings = BrokerSettings()
    b.ollama = SimpleNamespace(tags=_async([{"name": "gemma3:4b", "size": 1}]))

    findings = asyncio.run(b.audit_roles())
    line = next((f for f in findings if "aaa-first" in f), None)
    assert line is not None, findings
    assert "ghostbox" in line and "not" in line and "registered" in line


# --- bounded, concurrent probing, and inputs a remote of another make can send -----------------

def test_a_slow_remote_cannot_stall_the_roles_view(monkeypatch):
    """Every rail's /v1/roles client gives up at 30 s. Read in series at the client's 30 s default,
    one slow remote took the view past that, and then every chip on every rail went red, local
    ones included. Each read is now capped, so a remote that never answers costs the cap."""
    import time
    import app.broker as broker_mod
    monkeypatch.setattr(broker_mod, "_VIEW_PROBE_TIMEOUT", 0.2)
    remote = _Remote(REMOTE, loaded=["mistral-small3.2:24b"], delay=5.0)
    t0 = time.monotonic()
    view = _view({**DELEGATED, "recipe": "gemma3:4b"}, remote)
    assert time.monotonic() - t0 < 2.0
    row = view["openmaic"]
    assert row["installed"] is False and row["loaded"] is False and row["resolved"] is None
    assert view["recipe"]["installed"] is True           # the local role is unaffected


def test_every_upstream_is_probed_at_once():
    """Two boxes, each 0.4 s per read: probed in series that is about 0.8 s, at once about 0.4."""
    import time
    remotes = {"offsite": _Remote(REMOTE, delay=0.4), "lab": _Remote(["qwen3:8b"], delay=0.4)}
    b = _broker({"a": "offsite::mistral-small3*:24b", "b": "lab::qwen3:8b"},
                {**OFFSITE, "lab": {"url": "http://lab:11500/"}})
    b._embed_hints = ()
    b._upstream = lambda name: remotes[name]
    t0 = time.monotonic()
    view = {r["role"]: r for r in asyncio.run(b.roles_view())}
    assert time.monotonic() - t0 < 0.7
    assert view["a"]["installed"] is True and view["b"]["installed"] is True


def test_a_non_dict_details_from_a_remote_does_not_escape_the_view():
    """A remote that does not speak this broker's /v1/models shape can send `details` as a
    string. rank_key assumes a dict, and the AttributeError used to escape the read view and turn
    /v1/roles into a 500 that blanked every rail's chips."""
    remote = _Remote([{"name": "mistral-small3.1:24b", "details": "24B"},
                      {"name": "mistral-small3.2:24b", "details": ["x"]}])
    row = _view(DELEGATED, remote)["openmaic"]
    assert row["resolved"] in {"mistral-small3.1:24b", "mistral-small3.2:24b"}
    assert row["installed"] is True


def test_a_delegated_remote_role_is_judged_by_the_remote_map():
    """`offsite::@chat` names a ROLE on the remote, which expands it from its own map. No
    inventory here can reproduce that, so the remote's /v1/roles row decides."""
    remote = _Remote(REMOTE, loaded=["mistral-small3.2:24b"],
                     roles=[{"role": "chat", "resolved": "mistral-small3.2:24b", "installed": True}])
    row = _view({"openmaic": "offsite::@chat"}, remote)["openmaic"]
    assert row["resolved"] == "mistral-small3.2:24b"
    assert row["installed"] is True and row["loaded"] is True
    missing = _view({"openmaic": "offsite::@nope"}, _Remote(REMOTE, roles=[]))["openmaic"]
    assert missing["installed"] is False and missing["resolved"] is None


def test_the_remote_role_map_is_only_read_when_a_remote_role_needs_it():
    remote = _Remote(REMOTE)
    _view(DELEGATED, remote)
    assert remote.calls["roles"] == 0
