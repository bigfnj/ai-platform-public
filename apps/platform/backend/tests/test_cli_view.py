"""Who sees the broker's CLI status block through GET /api/platform/status.

The route serves every logged-in user (the top-bar widget polls it every 5 s), and the block
names the owner accounts, the CLI's exe and workdir, any token problem, and who is running what.
Every test here guards a property that fails SILENTLY: spread the raw block and the widget still
works, nothing errors, and each user's browser quietly receives the owner list. So the
endpoint-level tests serialise the WHOLE response and search it for the names, rather than
checking that a particular key was removed: a leak through any key, present or future, fails.

Run tests/mutate_cli_view.py after changing cli_view.py or the status route.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from platform_core import BrokerError
from platform_gateway_app import main as m
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.cli_view import cli_view
from platform_gateway_app.models import User

OWNER, OTHER_OWNER, NOBODY = "owner-one", "owner-two", "bob"
EXE = "C:/Tools/claude/claude.exe"
WORKDIR = "D:/cli-workdir-marker"
TOKEN_PROBLEM = "token file unreadable: D:/secret-marker/token.txt"

CLI = {
    "backend": "claude",
    "enabled": True,
    "ready": True,
    "blockers": ["settings_json_invalid"],
    "exe": EXE,
    "owners": [OWNER, OTHER_OWNER],
    "roles": ["@iep", "@summary"],
    "workdir": WORKDIR,
    "token_problem": TOKEN_PROBLEM,
    "calls": 7,
    "refusals": {"not_owner": 2},
    "fallbacks": {"count": 1, "last": {"requested": "@iep", "reason": "timeout",
                                       "detail": "no reply"}},
    "active": [
        {"role": "@iep", "rail": "iep-goals", "since": 1000.0, "requester": OWNER},
        {"role": "@summary", "rail": None, "since": 1001.0, "requester": OTHER_OWNER},
        # No requester at all: belongs to nobody, so shown to nobody but an admin.
        {"role": "@iep", "rail": "iep-goals", "since": 1002.0},
    ],
    # One outcome per role, whoever called it last (services/broker/app/broker_cli.py). `requester`
    # is the broker's lowercased requester; an entry without it comes from a broker that
    # predates the field.
    "last_by_role": {"@iep": {"ok": True, "at": 999.0, "requester": OWNER},
                     "@summary": {"ok": False, "at": 998.0, "reason": "timeout",
                                  "requester": OTHER_OWNER},
                     "@chat": {"ok": False, "at": 997.0, "reason": "rate_limited"}},
}

# Strings that are an admin's to see and nobody else's.
OPERATOR_ONLY = [OTHER_OWNER, EXE, WORKDIR, TOKEN_PROBLEM, "settings_json_invalid",
                 "not_owner", '"owners"', '"exe"', '"workdir"', '"token_problem"',
                 '"blockers"', '"requester"', '"refusals"']


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# --- cli_view, the pure rule ------------------------------------------------------------------


def test_an_admin_gets_the_whole_block_unchanged():
    view = cli_view(CLI, "anyone-admin", True)
    assert view == CLI
    # A copy, not the broker's dict: the route must be free to hand it to a serializer, and a
    # caller mutating it must not reach the next viewer's answer.
    view["owners"].append("intruder")
    assert CLI["owners"] == [OWNER, OTHER_OWNER]


def test_an_owner_gets_the_reduced_shape():
    assert cli_view(CLI, OWNER, False) == {
        "backend": "claude",
        "ready": True,
        "for_you": True,
        "roles": ["@iep", "@summary"],
        "active": [{"role": "@iep", "rail": "iep-goals", "since": 1000.0}],
        "last_by_role": {"@iep": {"ok": True, "at": 999.0}},
    }


def test_an_owner_sees_only_their_own_last_outcomes():
    """last_by_role is keyed by role, so with two owners the newest outcome on a role is
    often the OTHER one's. Unfiltered, owner-one learned that owner-two's last @summary call fell
    back to the local model, and when."""
    mine = cli_view(CLI, OWNER, False)["last_by_role"]
    theirs = cli_view(CLI, OTHER_OWNER, False)["last_by_role"]
    assert mine == {"@iep": {"ok": True, "at": 999.0}}
    assert theirs == {"@summary": {"ok": False, "at": 998.0, "reason": "timeout"}}


def test_an_owners_interrupted_outcome_keeps_its_cause_and_nothing_else_new():
    """A CLI stream that stopped after its first token is recorded as `interrupted` with the
    failure's slug in `cause` (and the Claude model it ran). The widget names the cause, so the
    owner's view admits it; the model and anything else the broker adds stay out until admitted."""
    cli = dict(CLI, last_by_role={"@rag": {"ok": False, "at": 996.0, "reason": "interrupted",
                                          "cause": "stalled", "model": "claude-x",
                                          "chars": 120, "requester": OWNER}})
    assert cli_view(cli, OWNER, False)["last_by_role"] == {
        "@rag": {"ok": False, "at": 996.0, "reason": "interrupted", "cause": "stalled"}}


def test_requester_is_stripped_from_own_last_outcomes():
    view = cli_view(CLI, OWNER, False)
    assert view["last_by_role"], "the owner's own outcome should be there"
    assert all("requester" not in o for o in view["last_by_role"].values())
    assert OWNER not in _dump(view["last_by_role"])


def test_an_outcome_with_no_requester_is_shown_to_no_owner():
    """A broker from before the field sends outcomes with no requester. Those belong to nobody
    (the rule `active` follows), so no owner sees them; an admin still does."""
    for who in (OWNER, OTHER_OWNER):
        assert "@chat" not in cli_view(CLI, who, False)["last_by_role"]
    assert cli_view(CLI, "anyone-admin", True)["last_by_role"]["@chat"]["reason"] == "rate_limited"


@pytest.mark.parametrize("requester", ["  owner-one ", "owner-one\t"])
def test_last_outcome_matching_ignores_whitespace(requester):
    cli = copy.deepcopy(CLI)
    cli["last_by_role"]["@iep"]["requester"] = requester
    assert cli_view(cli, OWNER, False)["last_by_role"] == {"@iep": {"ok": True, "at": 999.0}}


@pytest.mark.parametrize("requester", ["OWNER-ONE", "Owner-One"])
def test_a_case_variant_outcome_is_not_the_owners(requester):
    """The broker's owner check is exact, so an outcome filed under another spelling is
    another account's, and showing it would hand that account's activity to this one."""
    cli = copy.deepcopy(CLI)
    cli["last_by_role"]["@iep"]["requester"] = requester
    assert cli_view(cli, OWNER, False)["last_by_role"] == {}


# --- a broker that keeps each requester's own latest outcome per role ------------------------
#
# services/broker `last_by_requester`: requester -> role -> outcome. Here owner-two called @iep
# AFTER owner-one, so `last_by_role` holds owner-two's failure and owner-one's own success
# survives only in the per-requester map. Filtering `last_by_role` alone, owner-one's badge
# simply vanished the moment anyone else used the role.

def _two_owners_one_role():
    cli = copy.deepcopy(CLI)
    theirs = {"ok": False, "at": 999.5, "reason": "timeout", "requester": OTHER_OWNER}
    cli["last_by_role"] = {"@iep": dict(theirs)}
    cli["last_by_requester"] = {
        OWNER: {"@iep": {"ok": True, "at": 990.0, "requester": OWNER}},
        OTHER_OWNER: {"@iep": dict(theirs)},
    }
    return cli


def test_an_owner_keeps_their_own_outcome_after_another_owner_calls_the_role():
    cli = _two_owners_one_role()
    assert cli_view(cli, OWNER, False)["last_by_role"] == {"@iep": {"ok": True, "at": 990.0}}
    assert cli_view(cli, OTHER_OWNER, False)["last_by_role"] == {
        "@iep": {"ok": False, "at": 999.5, "reason": "timeout"}}


def test_each_outcome_in_a_bucket_must_still_name_its_owner():
    """The bucket's key is not trusted alone: an outcome filed under owner-one that names
    owner-two is owner-two's, and shown to nobody else."""
    cli = _two_owners_one_role()
    cli["last_by_requester"][OWNER]["@iep"]["requester"] = OTHER_OWNER
    assert cli_view(cli, OWNER, False)["last_by_role"] == {}


def test_the_bucket_is_matched_the_way_the_broker_keys_it():
    cli = _two_owners_one_role()
    cli["last_by_requester"] = {" owner-one ": cli["last_by_requester"][OWNER]}
    assert cli_view(cli, OWNER, False)["last_by_role"] == {"@iep": {"ok": True, "at": 990.0}}


def test_a_case_variant_bucket_is_not_the_owners():
    cli = _two_owners_one_role()
    bucket = copy.deepcopy(cli["last_by_requester"][OWNER])
    bucket["@iep"]["requester"] = "Owner-One"
    cli["last_by_requester"] = {"Owner-One": bucket}
    assert cli_view(cli, OWNER, False)["last_by_role"] == {}


def test_an_owner_never_sees_the_per_requester_map_itself():
    view = cli_view(_two_owners_one_role(), OWNER, False)
    assert "last_by_requester" not in view
    assert OTHER_OWNER not in _dump(view)


def test_an_admin_sees_the_per_requester_map():
    assert cli_view(_two_owners_one_role(), "anyone-admin", True)["last_by_requester"][OWNER]


@pytest.mark.parametrize("bad", ["x", [1], {OWNER: "x"}, {OWNER: {"@iep": "x"}}, {3: {}}])
def test_a_malformed_per_requester_map_never_raises(bad):
    cli = _two_owners_one_role()
    cli["last_by_requester"] = bad
    assert cli_view(cli, OWNER, False)["last_by_role"] == {}


def test_an_owner_sees_only_their_own_active_entries():
    """Two owners: each sees their own running call and never the other's, nor the entry with
    no requester (which belongs to nobody)."""
    mine = cli_view(CLI, OWNER, False)["active"]
    theirs = cli_view(CLI, OTHER_OWNER, False)["active"]
    assert [e["since"] for e in mine] == [1000.0]
    assert [e["since"] for e in theirs] == [1001.0]


def test_requester_is_stripped_from_own_active_entries():
    view = cli_view(CLI, OWNER, False)
    assert view["active"], "the owner's own entry should be there"
    assert all("requester" not in e for e in view["active"])
    assert OWNER not in _dump(view), "the viewer's own account name came back in the view"


def test_a_non_owner_gets_nothing():
    """None, so the route OMITS the key. A `for_you: false` would still tell a non-owner the
    feature exists and that they are not in it."""
    assert cli_view(CLI, NOBODY, False) is None


@pytest.mark.parametrize("name", ["  owner-one ", "owner-one\t"])
def test_owner_matching_ignores_whitespace(name):
    view = cli_view(CLI, name, False)
    assert view is not None and view["for_you"] is True
    assert [e["since"] for e in view["active"]] == [1000.0]


@pytest.mark.parametrize("name", ["OWNER-ONE", "Owner-One", " Owner-one "])
def test_a_case_variant_viewer_is_not_the_owner(name):
    """The licence hole from the viewer's side. Usernames were unique case-sensitively, so
    `Owner-One` could be a second account; folding case here showed it the owner's CLI
    status as its own. The broker never served that account, so the view must not either."""
    assert cli_view(CLI, name, False) is None


def test_an_owner_list_entry_is_matched_as_written():
    cli = copy.deepcopy(CLI)
    cli["owners"] = ["  Owner-One ", OTHER_OWNER]
    cli["active"][0]["requester"] = "Owner-One"
    assert cli_view(cli, OWNER, False) is None
    view = cli_view(cli, "Owner-One", False)
    assert view is not None
    assert [e["since"] for e in view["active"]] == [1000.0]
    cli["active"][0]["requester"] = "OWNER-ONE"
    assert cli_view(cli, "Owner-One", False)["active"] == []


@pytest.mark.parametrize("name", [None, "", "   ", 42])
def test_a_blank_or_missing_username_gets_nothing(name):
    assert cli_view(CLI, name, False) is None


@pytest.mark.parametrize("who", [OWNER, NOBODY, "  OWNER-ONE "])
def test_operator_fields_never_reach_a_non_admin(who):
    text = _dump(cli_view(CLI, who, False))
    leaked = [s for s in OPERATOR_ONLY if s in text]
    assert leaked == [], f"{who!r} was shown {leaked}"


@pytest.mark.parametrize("flag", [1, "yes", "true", [True], None])
def test_only_a_literal_true_is_admin(flag):
    """Fails closed: a caller handing over something merely truthy gets the owner view, not
    the operator one."""
    view = cli_view(CLI, OWNER, flag)
    assert view is not None and "owners" not in view


@pytest.mark.parametrize("cli,name,expected", [
    (None, OWNER, None),
    ("claude", OWNER, None),
    ([CLI], OWNER, None),
    ({}, OWNER, None),
    ({"owners": None}, OWNER, None),
    ({"owners": [None, 3, ""]}, OWNER, None),
    # A bare string is iterable: as an owner "list" it must not make "o" an owner.
    ({"owners": "owner-one"}, "o", None),
    ({"owners": "owner-one"}, OWNER, None),
    ({"owners": {OWNER: 1}}, OWNER, None),
    ({"owners": [OWNER], "roles": "@iep", "active": "x", "last_by_role": [1],
      "backend": 5, "ready": "yes"},
     OWNER,
     {"backend": None, "ready": False, "for_you": True, "roles": [], "active": [],
      "last_by_role": {}}),
    ({"owners": [OWNER], "roles": ["@iep", 7],
      "active": [None, 1, "x", {"requester": 5}, {"requester": OWNER, "role": "@iep",
                                                   "pid": 4242}],
      "last_by_role": {"@iep": "ok", 3: {"ok": True, "requester": OWNER},
                       "@y": {"ok": True, "requester": 5}, "@z": {"ok": True, "requester": None},
                       "@x": {"ok": True, "detail": "D:/p", "requester": OWNER}}},
     OWNER,
     {"backend": None, "ready": False, "for_you": True, "roles": ["@iep"],
      "active": [{"role": "@iep"}], "last_by_role": {"@x": {"ok": True}}}),
])
def test_malformed_cli_never_raises(cli, name, expected):
    """A half-upgraded broker must degrade to "no cli key", never a 500 that takes the GPU meter
    down with it. The last case also pins the allowlist: an unknown field in an entry (`pid`,
    `detail`) is not passed on just because the broker started sending it."""
    assert cli_view(cli, name, False) == expected


def test_an_admin_with_a_malformed_block_gets_nothing_rather_than_a_crash():
    assert cli_view("claude", "anyone-admin", True) is None
    assert cli_view(None, "anyone-admin", True) is None


# --- the route, over HTTP, with a real session cookie ------------------------------------------

STATUS = {
    "ollama_reachable": True,
    "ollama_version": "0.12.0",
    "loaded": [{"name": "qwen2.5:3b", "class": "light"}],
    "heavy_loaded": [],
    "gpu": {"used_mb": 1000, "total_mb": 24000},
    "queue": {"active": 1, "waiting": 0},
    "fallbacks": {"count": 0, "last": None},
    "jobs": [{"seq": 1, "model": "qwen2.5:3b", "source": "iep-goals", "state": "active"}],
    "cli": CLI,
    "media": {"enabled": True, "active": None},
}


class _Broker:
    def __init__(self, status=None, fail: Exception | None = None):
        self._status, self.fail = status, fail

    async def status(self):
        if self.fail:
            raise self.fail
        # A fresh copy per call, as the real client hands back freshly decoded JSON.
        return copy.deepcopy(self._status)


@pytest.fixture()
def gateway(db, monkeypatch):
    """The real app and its real auth dependency, minus the lifespan (which would dial the
    broker and start the scheduler). Returns a function that logs a new user in."""
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    monkeypatch.setattr(m.app.state, "settings",
                        SimpleNamespace(session_cookie="platform_session",
                                        root_asset_owner=lambda _path: None),
                        raising=False)
    monkeypatch.setattr(m.app.state, "broker", _Broker(STATUS), raising=False)

    def login(name: str, admin: bool = False, superadmin: bool = False) -> TestClient:
        with db.session_ctx() as s:
            u = User(username=name, password_hash=hash_password("x"),
                     is_admin=admin or superadmin, is_superadmin=superadmin)
            s.add(u)
            s.commit()
            token = create_session(s, u, ttl_hours=1).token
        client = TestClient(m.app)
        client.cookies.set("platform_session", token)
        return client

    return login


def _get_status(client: TestClient) -> tuple[dict, str]:
    r = client.get("/api/platform/status")
    assert r.status_code == 200, r.text
    return r.json(), r.text


def test_a_non_admin_status_never_names_an_owner(gateway):
    """The leak this module exists for, checked on the bytes the browser gets. Searching the
    whole body, not one key, so a name smuggled out through any field fails."""
    body, text = _get_status(gateway(NOBODY))
    assert "cli" not in body
    for s in [OWNER, OTHER_OWNER, EXE, WORKDIR, TOKEN_PROBLEM]:
        assert s not in text, f"a non-admin's status carried {s!r}"


def test_an_owner_status_carries_only_their_view(gateway):
    body, text = _get_status(gateway(OWNER))
    assert body["cli"] == cli_view(CLI, OWNER, False)
    leaked = [s for s in OPERATOR_ONLY + [OWNER] if s in text]
    assert leaked == [], f"the owner's own status carried {leaked}"


def test_an_admin_status_carries_the_whole_block(gateway):
    """The other half: filtering must not starve the people who operate it. Without this, an
    over-eager filter that dropped the block for everyone would pass every test above."""
    body, text = _get_status(gateway("admin-one", admin=True))
    assert body["cli"] == CLI
    assert OWNER in text and OTHER_OWNER in text and WORKDIR in text


def test_a_superadmin_counts_as_an_admin(gateway):
    body, _ = _get_status(gateway("root-one", superadmin=True))
    assert body["cli"] == CLI


def test_an_admin_status_is_the_brokers_unchanged_and_in_order(gateway):
    body, _ = _get_status(gateway("admin-one", admin=True))
    assert body == {"broker_reachable": True, **STATUS}
    assert list(body) == ["broker_reachable", *STATUS]


def test_only_cli_differs_between_an_owner_and_a_non_owner(gateway):
    """Ownership changes the cli block and nothing else. The rest of a non-admin's status is
    status_view's allowlist (tests/test_status_view.py), the same for both, and in the broker's
    order (test_status_view.py also pins that cli keeps its place rather than moving last)."""
    nobody, _ = _get_status(gateway(NOBODY))
    owner, _ = _get_status(gateway(OWNER))
    assert "cli" not in nobody
    assert {k: v for k, v in owner.items() if k != "cli"} == nobody
    assert list(owner) == [k for k in ["broker_reachable", *STATUS] if k in owner]


def test_a_broker_without_a_cli_block_adds_none(gateway, monkeypatch):
    old = {k: v for k, v in STATUS.items() if k != "cli"}
    monkeypatch.setattr(m.app.state, "broker", _Broker(old), raising=False)
    body, _ = _get_status(gateway("admin-one", admin=True))
    assert body == {"broker_reachable": True, **old}


def test_an_unreachable_broker_still_answers_with_the_empty_status(gateway, monkeypatch):
    monkeypatch.setattr(m.app.state, "broker", _Broker(fail=BrokerError("down")), raising=False)
    body, _ = _get_status(gateway(NOBODY))
    assert body["broker_reachable"] is False and "cli" not in body


def test_anonymous_is_refused(gateway):
    assert TestClient(m.app).get("/api/platform/status").status_code == 401
