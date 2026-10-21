"""The header chip and the broker must agree about who answers.

A rail's header chip says ``backend: "claude"`` when the call behind it will be served from the
owner's Claude subscription. The rail cannot import the broker -- rails are independent
deployables -- so the generated ``modelstate.py`` rebuilds ``Broker.cli_decision`` from what
``/v1/status["cli"]`` exposes, as ``cli_serves()``. Two copies of one predicate drift, and here
drift is not cosmetic: a chip that says "claude" for a call the broker sends to Ollama tells the
owner their subscription is doing work it is not, and the reverse hides that it is. This file
holds the two together.

It runs the TEMPLATE, not a rendered copy. ``tools/rail_templates/modelstate.py.tmpl`` is read
from disk and executed with a stub in place of the broker import, so a change to the template
is tested here before ``rail_template.py sync`` has written it anywhere -- and so
``mutate_cli_chip.py`` can mutate the one file every rail is generated from.

The broker side is a REAL ``Broker`` over REAL ``BrokerSettings``, for the reason
test_cli_routing gives: a stubbed ``cli_owner_set`` would keep passing after production stopped
reading the setting at all. The comparison is ``cli_serves(cli_state(), requester, ref)``
against ``cli_decision(ref, requester)[0]`` for every combination in the grid below, and
``test_the_grid_reaches_every_reason_the_broker_can_give`` proves the grid is not degenerate:
an axis that never produces a disagreement-capable case would pass every mutation silently.

Neutral account names only. The real owner accounts must not appear in a published file.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
import types
from pathlib import Path

import pytest

from app.broker import Broker
from app.broker import resolve_cli_exe as _real_resolve_cli_exe
from app.config import BrokerSettings

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "tools" / "rail_templates" / "modelstate.py.tmpl"
# Bytes first, so the witness below hashes exactly what the harness wrote. read_text would
# translate newlines, and a digest of a translated copy proves nothing about the file.
TEMPLATE_BYTES = TEMPLATE.read_bytes()
TEMPLATE_TEXT = TEMPLATE_BYTES.decode("utf-8")

# A harmless stand-in for the rail's broker facade import. Registered in sys.modules so the
# rendered `import ... as broker` line resolves exactly as it does in a rail; the resolve()
# tests below replace it per test with a fake that answers roles/models/status.
_STUB = "cli_chip_stub_broker"
_stub_mod = types.ModuleType(_STUB)


class _StubBrokerError(Exception):
    pass


_stub_mod.BrokerError = _StubBrokerError
sys.modules.setdefault(_STUB, _stub_mod)


def _load_template() -> types.ModuleType:
    mod = types.ModuleType("modelstate_template_under_test")
    mod.__file__ = str(TEMPLATE)
    src = TEMPLATE_TEXT.replace("{{BROKER_IMPORT}}", f"import {_STUB} as broker")
    exec(compile(src, str(TEMPLATE), "exec"), mod.__dict__)
    return mod


tmpl = _load_template()

OWNERS = "owner-one,owner-two"

# Every requester shape the broker treats differently. " owner-one " exercises the strip, and
# "Owner-One" the exact match: a case variant is another account, refused on both sides.
# "owner-two" is the case where NEITHER applies, so a normalisation bug that only bites a padded
# name cannot hide behind an already-clean one.
REQUESTERS = [None, "", "  ", "Owner-One", " owner-one ", "owner-two", "bob"]

# "xiep" is here on purpose. The broker refuses any ref without a leading "@", and that check is
# only OBSERVABLE when the rest of the ref, ref[1:], is itself an eligible role -- "iep"[1:] is
# "ep", refused for a different reason. Without a ref whose tail is eligible, deleting the "@"
# check from the template would agree with the broker on every case and survive.
# "@chat" is the switch-file axis's role (below), so that axis is observable.
REFS = ["@iep", "@IEP", "iep", "xiep", "qwen3.6*:27b", "@embed", "@chat"]


def _configs() -> list[dict]:
    out = []
    for enabled, exe, owners, roles in itertools.product(
            (True, False), (True, False), ("", OWNERS), ("", "iep", "@IEP")):
        out.append({
            "id": "-".join((
                "on" if enabled else "off",
                "exe" if exe else "noexe",
                "owners" if owners else "noowners",
                {"": "noroles", "iep": "iep", "@IEP": "AT-IEP"}[roles],
                "ok")),
            "enabled": enabled, "exe": exe, "owners": owners, "roles": roles, "blocker": None,
        })
    # The disk blockers only matter once everything else says yes, which is also the only
    # place the rail cannot see them for itself: it trusts `ready` for exactly these.
    for blocker in ("token_unreadable", "workdir_missing", "workdir_unset",
                    "settings_json_invalid"):
        out.append({"id": f"on-exe-owners-iep-{blocker}", "enabled": True, "exe": True,
                    "owners": OWNERS, "roles": "iep", "blocker": blocker})
    # The switch-file axis (BROKER_CLI_ROLES_FILE), on the fully configured base, where it is
    # the only thing left to decide. Every config above has NO file configured; these set one.
    # "absent" configures a path with no file behind it, the case where the env set still
    # applies, so the axis has a value where the file changes nothing as well as ones where it
    # changes everything. "@embed" is in REFS, which is what makes the file-only role in
    # "swap" observable.
    #
    # `chat`, not the `iep` the env axis above uses. A switch file that parses has every key
    # judged against roles(), and the public snapshot's DEFAULT_ROLES has no `iep` (it is
    # de-wired with its withheld rail), so there an @iep file switch is an unknown role: the
    # broker and the template both say no on every file config, they agree, and the axis tests
    # nothing (test_the_switch_file_axis_is_not_degenerate failed there). `chat` is a role both
    # trees keep. BROKER_CLI_ROLES names roles without judging them, so the env axis's `iep`
    # means the same in both.
    for name, env_roles, switches in (
            ("absent", "chat", None),
            ("on", "", {"chat": True}),                    # a role only the file switches on
            ("off", "chat", {"chat": False}),              # on in the env, off in the file
            ("swap", "chat", {"embed": True}),             # env-only chat, file-only embed
            ("invalid", "chat", "{not json"),              # fails closed
            # A key no role answers to is ignored and noted, with no blocker: the chip must
            # still read Claude for @chat, because the broker still routes it there.
            ("ignored", "", {"chat": True, "no-such-role": True}),
            # A switch written with its Claude model, on and off, and with a model
            # BROKER_CLI_MODELS does not allow (ignored with a note: the switch still routes).
            ("objon", "", {"chat": {"enabled": True, "model": "claude-haiku-5-5"}}),
            ("objoff", "chat", {"chat": {"enabled": False, "model": "claude-haiku-5-5"}}),
            ("objrefused", "", {"chat": {"enabled": True, "model": "claude-x-9"}}),
    ):
        out.append({"id": f"on-exe-owners-{env_roles or 'noroles'}-file{name}", "enabled": True,
                    "exe": True, "owners": OWNERS, "roles": env_roles, "blocker": None,
                    "switches": switches, "has_file": True})
    return out


CONFIGS = _configs()


def _clear_ambient_cli_env(mp: pytest.MonkeyPatch) -> None:
    """A BROKER_CLI_* variable in the developer's environment would silently change what the
    REAL settings read. Cleared by prefix rather than by list, so a setting added later cannot
    leak in from a shell that happens to have it."""
    for key in list(os.environ):
        if key.upper().startswith("BROKER_CLI_"):
            mp.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_cli_env(monkeypatch):
    _clear_ambient_cli_env(monkeypatch)


def _build(base: Path, mp: pytest.MonkeyPatch, cfg: dict) -> Broker:
    base.mkdir(parents=True, exist_ok=True)
    exe = base / "claude.exe"
    exe.write_bytes(b"")
    # Set on EVERY build, to the real resolver or to "not found". The grid builds many brokers
    # under one patch context, and a patch left over from the previous config would otherwise
    # decide this one. Read once, in __init__, so undoing it later changes nothing built here.
    mp.setattr("app.broker.resolve_cli_exe",
               _real_resolve_cli_exe if cfg["exe"] else (lambda _c: None))
    kw: dict = {
        "cli_enabled": cfg["enabled"], "cli_exe": str(exe), "cli_owners": cfg["owners"],
        "cli_roles": cfg["roles"],
        # Every disk-touching setting is passed explicitly, so a stray .env next to the run
        # cannot supply one.
        "cli_workdir": str(base), "cli_oauth_token": "", "cli_oauth_token_file": "",
    }
    if cfg["blocker"] == "token_unreadable":
        kw["cli_oauth_token_file"] = str(base / "no-such-token.txt")
    elif cfg["blocker"] == "workdir_missing":
        kw["cli_workdir"] = str(base / "gone")
    elif cfg["blocker"] == "workdir_unset":
        kw["cli_workdir"] = ""
    elif cfg["blocker"] == "settings_json_invalid":
        kw["cli_settings_json"] = "{not json"
    # Explicitly blank unless the config has a switch file, for the reason every disk-touching
    # setting above is explicit.
    kw["cli_roles_file"] = ""
    if cfg.get("has_file"):
        path = base / "cli-roles.json"
        switches = cfg["switches"]
        if isinstance(switches, dict):
            path.write_text(json.dumps(switches), encoding="utf-8")
        elif isinstance(switches, str):
            path.write_text(switches, encoding="utf-8")
        kw["cli_roles_file"] = str(path)
    return Broker(BrokerSettings(**kw))


def _disagreements(b: Broker) -> list[str]:
    cli = b.cli_state()
    bad = []
    for requester, ref in itertools.product(REQUESTERS, REFS):
        want, why = b.cli_decision(ref, requester)
        got = tmpl.cli_serves(cli, requester, ref)
        if got != want:
            bad.append(f"requester={requester!r} ref={ref!r}: broker says {want} ({why}), "
                       f"template says {got}")
    return bad


@pytest.fixture(scope="module")
def grid(tmp_path_factory) -> dict[str, Broker]:
    """One real Broker per config, built once for the module. Each Broker() costs ~0.5 s,
    nearly all of it httpx loading the CA bundle for an Ollama client this file never uses,
    and three tests read the same brokers."""
    base = tmp_path_factory.mktemp("cli-grid")
    with pytest.MonkeyPatch.context() as mp:
        _clear_ambient_cli_env(mp)
        return {cfg["id"]: _build(base / cfg["id"], mp, cfg) for cfg in CONFIGS}


# --- the equivalence ---------------------------------------------------------

@pytest.mark.parametrize("cfg", CONFIGS, ids=[c["id"] for c in CONFIGS])
def test_template_agrees_with_the_broker(grid, cfg):
    bad = _disagreements(grid[cfg["id"]])
    assert not bad, "the chip and the broker disagree:\n  " + "\n  ".join(bad)


def test_the_grid_reaches_every_reason_the_broker_can_give(grid):
    """The grid's own check. Every refusal reason `cli_decision` has, plus "ok", must come out
    of it at least once, and both answers must occur. An axis that never reaches a branch
    cannot catch a template that gets that branch wrong, and a passing equivalence test over
    such a grid is a statement about nothing."""
    reasons: set[str] = set()
    answers: set[bool] = set()
    for b in grid.values():
        for requester, ref in itertools.product(REQUESTERS, REFS):
            ok, why = b.cli_decision(ref, requester)
            reasons.add(why)
            answers.add(ok)
    assert answers == {True, False}
    assert reasons == {
        "ok", "disabled", "not_installed", "no_owners_configured", "no_requester",
        "not_owner", "not_a_role", "role_not_eligible", "token_unreadable",
        "workdir_missing", "workdir_unset", "settings_json_invalid", "cli_roles_file_invalid",
    }


def test_the_switch_file_axis_is_not_degenerate(grid):
    """The file axis must reach both of its sources and both directions of disagreement with
    the env, or a template (or a cli_state) that ignored the file would agree with the broker
    on every case. Checked against the brokers themselves, not against the config table, so a
    config that silently stopped writing its file would fail here."""
    def ok(cfg_id, ref):
        return grid[cfg_id].cli_decision(ref, "owner-two")[0]

    sources = {c["id"]: grid[c["id"]].settings.cli_effective().source
               for c in CONFIGS if c.get("has_file")}
    assert sources["on-exe-owners-chat-fileabsent"] == "env"
    assert set(sources.values()) == {"env", "file"}
    # The file adds a role the env lacks, and removes one the env has (with no file, the env's
    # chat is served: the "absent" config).
    assert ok("on-exe-owners-noroles-fileon", "@chat") is True
    assert ok("on-exe-owners-noroles-ok", "@chat") is False
    assert ok("on-exe-owners-chat-fileoff", "@chat") is False
    # The object form (a switch with its Claude model) routes like the bare one, both ways, and
    # a model the allowlist does not hold changes nothing about who is routed.
    assert ok("on-exe-owners-noroles-fileobjon", "@chat") is True
    assert ok("on-exe-owners-chat-fileobjoff", "@chat") is False
    assert ok("on-exe-owners-noroles-fileobjrefused", "@chat") is True
    assert ok("on-exe-owners-chat-fileabsent", "@chat") is True
    assert (ok("on-exe-owners-chat-fileswap", "@chat"),
            ok("on-exe-owners-chat-fileswap", "@embed")) == (False, True)
    assert ok("on-exe-owners-noroles-fileignored", "@chat") is True


def test_cli_decision_and_cli_state_count_nothing(grid):
    """Calling the predicate over the whole grid must leave every counter where it was. The
    refusal count is kept in `chat()`, not in `cli_decision`, and that is what makes the
    comparison above side-effect free; if the counting moved into the predicate, this test
    run would itself fill /v1/status with refusals nobody made.

    The fully configured broker, so "ok" is among the answers. It is shared with the tests
    above, which only strengthens this: whatever order they ran in, the counters must still
    read zero."""
    b = grid["on-exe-owners-iep-ok"]
    before = b.cli_state()
    for requester, ref in itertools.product(REQUESTERS, REFS):
        b.cli_decision(ref, requester)
        b.cli_state()
    after = b.cli_state()
    assert (after["calls"], after["refusals"], after["fallbacks"]) == (
        before["calls"], before["refusals"], before["fallbacks"]) == (
        0, {}, {"count": 0, "last": None})


def test_the_template_under_test_is_the_one_on_disk():
    """Witness for mutate_cli_chip.py. The harness passes the digest of the file it wrote;
    a mismatch means this run exec'd some other template and every verdict it produced is
    about code the mutation never touched. Without a harness it checks nothing and passes."""
    got = hashlib.sha256(TEMPLATE_BYTES).hexdigest()
    want = os.environ.get("CLI_CHIP_TEMPLATE_SHA256")
    if want:
        assert got == want, f"exec'd {TEMPLATE} with sha256 {got}, harness wrote {want}"


def test_the_broker_under_test_is_the_one_on_disk():
    """The same witness for the BROKER side, which mutate_cli_chip.py also mutates (the switch
    file's reader and cli_state). Hashes the files the imported modules were actually loaded
    from, and the files of Broker's methods broker.py ran (cli_state is in broker_cli.py), so a
    run that imported some other checkout's broker cannot pass it."""
    import app.broker
    import app.config
    files = [Path(app.broker.__file__), *map(Path, app.broker._METHOD_FILES),
             Path(app.config.__file__)]
    blob = b"".join(p.read_bytes() for p in files)
    got = hashlib.sha256(blob).hexdigest()
    want = os.environ.get("CLI_CHIP_BROKER_SHA256")
    if want:
        assert got == want, f"imported a broker with sha256 {got}, harness wrote {want}"


# --- cli_serves on what an older or broken broker sends ------------------------

READY = {"ready": True, "owners": ["owner-one"], "roles": ["iep"]}


@pytest.mark.parametrize("cli", [
    None,                                               # a broker from before the CLI backend
    "ready",                                            # not a dict at all
    {},                                                 # a dict with nothing in it
    {**READY, "ready": "true"},                         # truthy but not True
    {**READY, "ready": False},
    {**READY, "owners": "owner-one"},                   # a string, where a list belongs
    {**READY, "roles": None},
], ids=["absent", "string", "empty", "ready-string", "not-ready", "owners-str", "roles-none"])
def test_a_missing_or_malformed_cli_block_never_claims_the_cli(cli):
    """The rail runs against whatever broker is deployed. The answer for a shape it does not
    understand is "not served", the only answer that cannot misstate whose subscription pays."""
    assert tmpl.cli_serves(cli, "owner-one", "@iep") is False


def test_a_well_formed_block_is_served():
    """The positive control for the test above: the same block, intact, does say yes."""
    assert tmpl.cli_serves(READY, "owner-one", "@iep") is True


# --- resolve(): the per-slot opt-in, busy, and the envelope ----------------------

class _FakeBroker:
    """The facade surface modelstate reads: roles(), models(), status(), BrokerError."""

    BrokerError = _StubBrokerError

    def __init__(self, cli=None, down=False):
        self.cli = cli
        self.down = down

    def roles(self):
        if self.down:
            raise _StubBrokerError("connection refused")
        return [{"role": "iep", "resolved": "qwen3.6:27b", "upstream": "local"},
                {"role": "edu", "resolved": "mistral-small3.2:24b", "upstream": "local"}]

    def models(self):
        return [{"name": "qwen3.6:27b"}, {"name": "mistral-small3.2:24b"}]

    def status(self):
        out = {"loaded": [], "jobs": []}
        if self.cli is not None:
            out["cli"] = self.cli
        return out


def _cli(active=()):
    return {"ready": True, "owners": ["owner-one", "owner-two"], "roles": ["iep", "edu"],
            "active": list(active)}


SPECS = [("writer", "Writer", "@iep"), ("content", "Content", "@edu")]


def _resolve(monkeypatch, fake, **kw):
    monkeypatch.setattr(tmpl, "broker", fake)
    return {m["slot"]: m for m in tmpl.resolve(SPECS, **kw)["models"]}


def test_an_opted_in_slot_for_an_owner_is_claude(monkeypatch):
    out = _resolve(monkeypatch, _FakeBroker(_cli()), requester="owner-one", cli_slots=("writer",))
    assert out["writer"]["backend"] == tmpl.CLAUDE


def test_a_slot_not_in_cli_slots_is_never_claude(monkeypatch):
    """`@edu` is an eligible role and the viewer is an owner, so cli_serves() says yes. The
    slot still reads ollama, because the rail did not opt it in: the call behind it does not
    pass a requester, and the broker refuses every call that has none. A role-level rule
    would paint this chip claude for work that never leaves the GPU."""
    out = _resolve(monkeypatch, _FakeBroker(_cli()), requester="owner-one", cli_slots=("writer",))
    assert tmpl.cli_serves(_cli(), "owner-one", "@edu") is True
    assert out["content"]["backend"] == tmpl.OLLAMA


def test_a_bare_string_cli_slots_names_one_slot(monkeypatch):
    """`cli_slots=("writer")` is a str. It must mean the slot "writer", not its letters and
    not a substring test that would let "rite" through."""
    out = _resolve(monkeypatch, _FakeBroker(_cli()), requester="owner-one", cli_slots="writer")
    assert out["writer"]["backend"] == tmpl.CLAUDE
    out = _resolve(monkeypatch, _FakeBroker(_cli()), requester="owner-one", cli_slots="rite")
    assert out["writer"]["backend"] == tmpl.OLLAMA


def test_no_requester_and_no_slots_is_the_default_and_reads_ollama(monkeypatch):
    out = _resolve(monkeypatch, _FakeBroker(_cli()))
    assert {m["backend"] for m in out.values()} == {tmpl.OLLAMA}


def test_busy_is_only_for_the_viewers_own_call(monkeypatch):
    """`active` lists every owner's calls. Another owner's draft must not light this viewer's
    dot: it would say their own work had started when it had not."""
    theirs = {"role": "@iep", "rail": "a-rail", "since": 1.0, "requester": "owner-two"}
    out = _resolve(monkeypatch, _FakeBroker(_cli([theirs])), requester="owner-one",
                   cli_slots=("writer",))
    assert out["writer"]["backend"] == tmpl.CLAUDE
    assert out["writer"]["busy"] is False
    mine = {**theirs, "requester": "owner-one"}
    out = _resolve(monkeypatch, _FakeBroker(_cli([theirs, mine])), requester=" owner-one ",
                   cli_slots=("writer",))
    assert out["writer"]["busy"] is True


def test_busy_does_not_light_for_a_case_variant_account(monkeypatch):
    """The broker files a call under the requester exactly as it matched it, so `OWNER-ONE`
    in `active` is another account's call. Folding case here would show it as this one's."""
    theirs = {"role": "@iep", "rail": "a-rail", "since": 1.0, "requester": "OWNER-ONE"}
    out = _resolve(monkeypatch, _FakeBroker(_cli([theirs])), requester="owner-one",
                   cli_slots=("writer",))
    assert out["writer"]["backend"] == tmpl.CLAUDE
    assert out["writer"]["busy"] is False


def test_busy_matches_the_role_case_insensitively_and_only_that_role(monkeypatch):
    mine = {"role": "@IEP", "rail": None, "since": 1.0, "requester": "owner-one"}
    out = _resolve(monkeypatch, _FakeBroker(_cli([mine])), requester="owner-one",
                   cli_slots=("writer", "content"))
    assert out["writer"]["busy"] is True
    assert out["content"]["busy"] is False


def test_busy_needs_the_slot_to_be_claude(monkeypatch):
    """A call in flight for a slot the rail did not opt in says nothing about that chip."""
    mine = {"role": "@edu", "rail": None, "since": 1.0, "requester": "owner-one"}
    out = _resolve(monkeypatch, _FakeBroker(_cli([mine])), requester="owner-one",
                   cli_slots=("writer",))
    assert out["content"]["busy"] is False


def test_a_claude_slot_keeps_its_local_state(monkeypatch):
    """`state` is still local residency: the health of the fallback the broker uses when the
    CLI refuses a call. Claude answering must not paint it green."""
    out = _resolve(monkeypatch, _FakeBroker(_cli()), requester="owner-one", cli_slots=("writer",))
    assert out["writer"]["backend"] == tmpl.CLAUDE
    assert out["writer"]["state"] == tmpl.COLD


def test_an_older_broker_with_no_cli_block_reads_ollama(monkeypatch):
    out = _resolve(monkeypatch, _FakeBroker(None), requester="owner-one",
                   cli_slots=("writer", "content"))
    assert {(m["backend"], m["busy"]) for m in out.values()} == {(tmpl.OLLAMA, False)}


ENTRY_KEYS = {"slot", "label", "role", "model", "state", "upstream", "backend", "busy"}


@pytest.mark.parametrize("down", [False, True], ids=["ok", "unreachable"])
def test_every_entry_carries_every_key_in_both_branches(monkeypatch, down):
    out = _resolve(monkeypatch, _FakeBroker(_cli(), down=down), requester="owner-one",
                   cli_slots=("writer",))
    assert all(set(m) == ENTRY_KEYS for m in out.values())
    assert all(isinstance(m["busy"], bool) for m in out.values())
    if down:
        assert {(m["upstream"], m["backend"]) for m in out.values()} == {("local", tmpl.OLLAMA)}
