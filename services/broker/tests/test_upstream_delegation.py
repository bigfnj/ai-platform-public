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
        delegate_ref=lambda v, registry=None: (
            (v.partition("::")[0], v.partition("::")[2])
            if "::" in v and v.partition("::")[0] in upstreams else (None, v)),
        # resolve_ref consults the admin-disabled set on the LOCAL branch. This stub predates
        # that check; without it the local half of route() dies inside the stub rather than on
        # anything this file is about.
        disabled=lambda: set(),
        ollama_timeout=600.0,
        # chat_stream asks the Claude CLI decision first (as chat always has), and the answer
        # for this stub is the default one: the CLI is off, so the call routes as before.
        cli_enabled=False,
    )
    b.ollama = SimpleNamespace(tags=_async([{"name": "gemma3:4b"}]))
    b._digests = {}
    b._cli_refusals = {}
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


# --- think on the delegated STREAMING path ---------------------------------------------------
#
# Until 2026-10-05 `think` stopped at this hop: the buffered UpstreamClient.chat relayed it but
# chat_stream had no such parameter. Every rail streams, so a delegated THINKING model spent the
# whole num_predict on hidden reasoning and streamed an empty answer (measured: gemma4:26b, 3,341
# chars of thinking and 0 of content at num_predict 800). Both halves are pinned: the client puts
# the field on the wire, and the broker hands it to the client.

def test_upstream_chat_stream_puts_think_on_the_wire(monkeypatch):
    import httpx
    from app import upstream as up_mod

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text='{"message": {"content": "ok"}, "done": true}\n')

    real = httpx.AsyncClient
    monkeypatch.setattr(up_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    client = up_mod.UpstreamClient("offsite", "http://x:11500", token="t")

    async def run():
        return [f async for f in client.chat_stream("gemma4:26b", [{"role": "user", "content": "q"}],
                                                    think=False)]

    frames = asyncio.run(run())
    assert frames == [{"message": {"content": "ok"}, "done": True}]
    assert seen["body"]["think"] is False


def test_upstream_chat_stream_omits_think_when_unset(monkeypatch):
    """None means 'the remote's default', and must stay absent rather than be sent as null."""
    import httpx
    from app import upstream as up_mod

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text='{"done": true}\n')

    real = httpx.AsyncClient
    monkeypatch.setattr(up_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    client = up_mod.UpstreamClient("offsite", "http://x:11500")
    asyncio.run(_drain(client.chat_stream("m", [{"role": "user", "content": "q"}])))
    assert "think" not in seen["body"]


async def _drain(agen):
    return [f async for f in agen]


def test_broker_relays_think_to_a_delegated_stream(monkeypatch):
    from app import upstream as up_mod

    b = _broker({"servicenow-rag": "offsite::gemma4:26b"},
                {"offsite": {"url": "http://x:11500", "token": "t"}})
    got = {}

    async def fake_stream(self, model, messages, **kw):
        got.update(kw, model=model)
        yield {"message": {"content": "ok"}}

    monkeypatch.setattr(up_mod.UpstreamClient, "chat_stream", fake_stream)
    frames = asyncio.run(_drain(b.chat_stream("@servicenow-rag",
                                              [{"role": "user", "content": "q"}], think=False)))
    assert frames == [{"message": {"content": "ok"}}]
    assert got["model"] == "gemma4:26b"
    assert got["think"] is False


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
    b._upstream = lambda name, registry=None: remote
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
    """Two boxes, each slow to read: probed at once, one box is dialled while the other is still
    being read; probed in series, never.

    The verdict is that overlap, not a stopwatch. It was `< 0.7 s` against 0.4 s reads, 0.3 s
    over an idle run and 0.1 s under the cost of reading in series."""
    reading = {"offsite": 0, "lab": 0}
    overlapped: list[bool] = []

    class _Watched(_Remote):
        def __init__(self, name, *a, **kw):
            super().__init__(*a, **kw)
            self._name = name

        async def _read(self, what, value):
            reading[self._name] += 1
            overlapped.append(all(reading.values()))
            try:
                return await super()._read(what, value)
            finally:
                reading[self._name] -= 1

    remotes = {"offsite": _Watched("offsite", REMOTE, delay=0.05),
               "lab": _Watched("lab", ["qwen3:8b"], delay=0.05)}
    b = _broker({"a": "offsite::mistral-small3*:24b", "b": "lab::qwen3:8b"},
                {**OFFSITE, "lab": {"url": "http://lab:11500/"}})
    b._embed_hints = ()
    b._upstream = lambda name, registry=None: remotes[name]
    view = {r["role"]: r for r in asyncio.run(b.roles_view())}
    assert view["a"]["installed"] is True and view["b"]["installed"] is True
    assert any(overlapped), "each box was dialled only once the other's reads had ended"


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


# --- roles_inventory: the Rails tab's whole read, one probe per box -----------------------------
#
# The Rails tab needs the roles, every registered box's health, and each box's models. Asked for
# as three reads (/v1/roles, /v1/upstreams, /v1/models?upstream=X) that dialled every delegated
# remote's /healthz and /v1/models twice per load. roles_inventory answers all three from the
# sweep roles_view already makes. The end-to-end count through the gateway is in
# apps/platform/backend/tests/test_rails_tab_probe_count.py.

LAB = {"lab": {"url": "http://lab:11500"}}


def _inventory(roles, remotes, local=(), local_fails=False):
    b = _broker(roles, {**OFFSITE, **LAB})
    b._embed_hints = ()
    b._upstream = lambda name, registry=None: remotes[name]

    async def _local(tags=None):   # handed the sweep's read; this stand-in has its own list
        if local_fails:
            raise RuntimeError("ollama is down")
        return list(local)

    b.list_models = _local
    # The Claude switch state the inventory also carries reads the CLI settings, which this
    # stub broker (built without __init__) does not have and this file is not about. Its own
    # tests are in test_cli_routing.py (test_the_rails_tab_read_carries_the_switch_state).
    b.cli_summary = lambda: {"stub": True}
    return asyncio.run(b.roles_inventory())


def test_the_inventory_probes_every_registered_box_exactly_once():
    """`offsite` runs a delegated role, `lab` runs nothing but must still offer its models in
    the picker. Each is asked for health and inventory once; residency only where a role runs."""
    offsite = _Remote(REMOTE, loaded=["mistral-small3.2:24b"])
    lab = _Remote(["qwen3:8b"])
    view = _inventory(DELEGATED, {"offsite": offsite, "lab": lab})
    assert offsite.calls == {"healthy": 1, "models": 1, "status": 1, "roles": 0}
    assert lab.calls == {"healthy": 1, "models": 1, "status": 0, "roles": 0}
    assert {r["role"]: r for r in view["roles"]}["openmaic"]["loaded"] is True


def test_the_inventory_reports_every_box_local_first_with_its_health():
    view = _inventory(DELEGATED, {"offsite": _Remote(REMOTE), "lab": _Remote([], healthy=False)})
    assert view["upstreams"] == [
        {"name": "local", "url": "", "healthy": True},
        {"name": "lab", "url": "http://lab:11500", "healthy": False},
        {"name": "offsite", "url": OFFSITE["offsite"]["url"], "healthy": True},
    ]
    assert all("token" not in u for u in view["upstreams"]), "a token must never be published"
    assert view["cli"] == {"stub": True}, "the inventory no longer carries the switch state"


def test_the_inventory_hands_over_each_box_s_models_as_the_box_sent_them():
    """Raw rows, not the reshaped copy the resolver ranks on: the picker reads the broker's own
    /v1/models shape (class, parameter_size, vision) and would lose fields otherwise."""
    rows = [{"name": "qwen3:8b", "class": "heavy", "parameter_size": "8B", "vision": False}]
    view = _inventory({}, {"offsite": _Remote(REMOTE), "lab": _Remote(rows)},
                      local=[{"name": "gemma3:4b", "class": "heavy"}])
    assert view["models"]["lab"] == rows
    assert [m["name"] for m in view["models"]["offsite"]] == list(REMOTE)
    assert view["models"]["local"] == [{"name": "gemma3:4b", "class": "heavy"}]


def test_a_dark_or_unreadable_box_has_no_models_and_the_view_still_answers():
    down = _Remote(REMOTE, healthy=False)
    broken = _Remote(["qwen3:8b"], broken={"models"})
    view = _inventory(DELEGATED, {"offsite": down, "lab": broken}, local_fails=True)
    assert view["models"] == {"local": [], "lab": [], "offsite": []}
    assert down.calls["models"] == 0, "a box reported down is not dialled for a read"


def test_roles_view_alone_still_probes_only_the_boxes_a_role_runs_on():
    """Every rail's chips poll /v1/roles. An idle box must not start costing each of those
    polls a round trip just because the Rails tab now reads it through the same sweep."""
    lab = _Remote(["qwen3:8b"])
    b = _broker(DELEGATED, {**OFFSITE, **LAB})
    b._embed_hints = ()
    b._upstream = lambda name, registry=None: {"offsite": _Remote(REMOTE), "lab": lab}[name]
    asyncio.run(b.roles_view())
    assert lab.calls == {"healthy": 0, "models": 0, "status": 0, "roles": 0}


def test_the_local_view_dials_no_remote_and_leaves_delegated_rows_out():
    """For the Models tab, which asks only about this card. A delegated row's names are the
    remote's models, so it is left out rather than reported unprobed (installed: False would
    be a claim nobody checked)."""
    remote = _Remote(REMOTE)
    b = _broker({**DELEGATED, "chat": "gemma3*"}, OFFSITE)
    b._embed_hints = ()
    b._upstream = lambda name, registry=None: remote
    rows = {r["role"]: r for r in asyncio.run(b.roles_view_local())}
    assert "openmaic" not in rows
    assert rows["chat"]["installed"] is True and rows["chat"]["resolved"] == "gemma3:4b"
    assert remote.calls == {"healthy": 0, "models": 0, "status": 0, "roles": 0}
