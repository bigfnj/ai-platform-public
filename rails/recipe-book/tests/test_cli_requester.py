"""Whose Claude subscription may answer a recipe-book call: the rail-level guards.

The broker answers a buffered chat call from a platform owner's own Claude CLI subscription
when the call's `requester` names an owner and its @role is switched on there. It sees a name
and a role and nothing else, so this rail decides two things the broker cannot:

* WHICH CALLS may carry a name: only the assistant slot's own role and the vision slot's, the
  photo import (`broker._cli_requester`). `/api/assistant` takes any broker-defined @role from
  the client, so without that guard a request naming `@iep` would reach Claude under the
  owner's name on another rail's switch; the route also drops a client-picked vision role
  (`assistant._own_role_requester`), which is not its slot's.
* WHICH ROLE each named call is on. The facade keeps the name on BOTH slot roles, so a
  text call that drifted onto `@recipe-vision` would still carry the owner, and ride the
  vision slot's switch to Claude while the assistant chip reads Ollama. Every named
  server-side call is asserted as (role, name) (`calls`), not as a name alone.
* WHOSE NAME: the caller, unless an admin is acting as somebody else through `?owner=`, when
  the plan in the prompt is that other user's (`deps.cli_requester`). Background work (the
  icon-subject batch, the icon images) passes none.

The photo goes out as it always has, Ollama-style (`images` on the message). Whether the CLI
can read it is the broker's decision, and every CLI failure falls back to the local model.

Asserted on the body `broker._post` would send, so the routes, `deps.cli_requester`, the
facade guard and every call site are all in the path being tested. Offline: no broker, no
model, neutral account names.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from recipe_book import broker, config, db, extraction, icon_prompts, state
from recipe_book.api import deps
from recipe_book.api.routers import assistant as assistant_router
from recipe_book.api.routers import authoring as authoring_router
from recipe_book.api.routers import planner as planner_router

OWNER = "owner-one"
OTHER = "other-two"
MINE = {"X-Platform-User": OWNER, "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": OWNER, "X-Platform-Admin": "1"}

# One reply every JSON call site can read: a draft, a wine pairing, an empty plan.
REPLY = json.dumps({"title": "Soup", "meta": "", "ingredients": ["1 onion"],
                    "instructions": ["Cook."], "wine": "Pinot Noir (red)", "why": "w"})
# The facade's real transport, kept before any fixture swaps it, for the timeout test.
_REAL_POST = broker._post


@pytest.fixture()
def wire(monkeypatch):
    """Every /v1/chat body the facade builds, in order. Nothing leaves the process."""
    posted: list[dict] = []

    def fake_post(path, payload, timeout=None):
        assert path == "/v1/chat", path
        posted.append(payload)
        return {"message": {"content": REPLY}}

    monkeypatch.setattr(broker, "_post", fake_post)
    monkeypatch.setattr(broker, "ASSISTANT_MODEL", "@recipe")
    monkeypatch.setattr(broker, "VISION_MODEL", "@recipe-vision")
    monkeypatch.setattr(broker, "roles", lambda: [{"role": r, "resolved": "gemma4:26b"}
                                                  for r in ("recipe", "recipe-vision", "iep")])
    monkeypatch.setattr(broker, "picker_models",
                        lambda: {"broker_up": True, "models": [{"name": "gemma4:26b"}]})
    return posted


@pytest.fixture()
def client(tmp_path, monkeypatch, wire):
    """The three routers that make model calls, behind the app-level identity gate."""
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "PRIMARY_USER", "nobody-here")
    monkeypatch.setattr(state, "_catalog", None)   # reloaded from the empty temp DB
    con = db.connect()
    db.init_db(con)
    con.close()
    app = FastAPI(dependencies=[Depends(deps.identity)])
    for m in (assistant_router, authoring_router, planner_router):
        app.include_router(m.router)
    c = TestClient(app)
    c.posted = wire  # type: ignore[attr-defined]
    return c


def carried(posted: list[dict]) -> list:
    """The requester on each call, "ABSENT" when the field is not on the wire at all."""
    assert posted, "no call reached the facade, so nothing was asserted"
    return [p.get("requester", "ABSENT") for p in posted]


def calls(posted: list[dict]) -> list[tuple]:
    """(role, requester) per call. The name decides WHOSE subscription may answer; the role
    decides WHICH slot's switch the broker reads. Both of this rail's slot roles keep the
    name, so a text call on the vision role passes `carried` and fails here."""
    return list(zip([p["model"] for p in posted], carried(posted)))


# --- the facade: which calls may carry a name ---------------------------------------------


def test_the_assistant_role_carries_the_requester(wire):
    broker.chat("@recipe", [{"role": "user", "content": "q"}], requester=OWNER)
    assert carried(wire) == [OWNER]


def test_no_requester_means_none_on_the_wire(wire):
    broker.chat("@recipe", [{"role": "user", "content": "q"}])
    assert carried(wire) == ["ABSENT"]


def test_chat_json_puts_the_requester_on_the_wire(wire):
    broker.chat_json("@recipe", [{"role": "user", "content": "q"}], requester=OWNER)
    assert carried(wire) == [OWNER]


def test_the_vision_role_carries_the_requester(wire):
    broker.chat_json("@recipe-vision", [{"role": "user", "content": "q", "images": ["aW1n"]}],
                     requester=OWNER)
    assert carried(wire) == [OWNER]


def test_a_foreign_role_never_carries_the_name(wire):
    """The case the guard exists for: another rail's role, the generic vision class, the icon
    role, a concrete model."""
    for model in ("@iep", "@vision", "@recipe-icon", "gemma4:26b"):
        broker.chat(model, [{"role": "user", "content": "q"}], requester=OWNER)
    assert carried(wire) == ["ABSENT"] * 4


# --- the routes: the person waiting, on their own data ------------------------------------


def test_the_assistant_route_names_the_caller(client):
    r = client.post("/api/assistant", json={"mode": "ask", "prompt": "hi"}, headers=MINE)
    assert r.status_code == 200, r.text
    assert calls(client.posted) == [("@recipe", OWNER)]


def test_a_client_picked_foreign_role_stays_local(client):
    """`_resolve_model` accepts `@iep` because the broker defines it. The call goes out on it
    (that is the existing contract), but without the owner's name."""
    r = client.post("/api/assistant", json={"mode": "ask", "prompt": "hi", "model": "@iep"},
                    headers=MINE)
    assert r.status_code == 200, r.text
    assert client.posted[0]["model"] == "@iep"
    assert carried(client.posted) == ["ABSENT"]


def test_naming_the_rails_own_role_explicitly_still_names_the_caller(client):
    r = client.post("/api/assistant", json={"mode": "ask", "prompt": "hi", "model": "@recipe"},
                    headers=MINE)
    assert r.status_code == 200, r.text
    assert calls(client.posted) == [("@recipe", OWNER)]


def test_a_client_picked_vision_role_on_the_assistant_stays_local(client):
    """The facade keeps the name on the vision role for the photo import. A client picking that
    role for a text answer here would ride the vision slot's switch with the assistant's
    request, so the route drops the name: a client-chosen role never carries it."""
    r = client.post("/api/assistant", headers=MINE,
                    json={"mode": "ask", "prompt": "hi", "model": "@recipe-vision"})
    assert r.status_code == 200, r.text
    assert client.posted[0]["model"] == "@recipe-vision"
    assert carried(client.posted) == ["ABSENT"]


def test_every_authoring_call_names_the_caller(client, monkeypatch):
    """Draft, refine, URL import and a text-file import: the caller's own pasted text, each on
    the assistant's role. Only a photo goes to the vision role (below)."""
    monkeypatch.setattr(extraction, "extract_url",
                        lambda url: "1 onion, chopped. Cook it slowly in butter for a while.")
    rs = [
        client.post("/api/recipes/draft", json={"mode": "paste", "text": "1 onion. Cook."},
                    headers=MINE),
        client.post("/api/recipes/draft/refine",
                    json={"draft": {"title": "Soup"}, "message": "more salt"}, headers=MINE),
        client.post("/api/recipes/extract/url", json={"url": "http://example.invalid/r"},
                    headers=MINE),
        client.post("/api/recipes/extract/files", headers=MINE,
                    files=[("files", ("r.txt", b"1 onion, chopped. Cook it."))]),
    ]
    assert [r.status_code for r in rs] == [200] * 4, [r.text for r in rs]
    assert calls(client.posted) == [("@recipe", OWNER)] * 4


@pytest.fixture()
def photo(monkeypatch):
    """Any upload reads as one photo, so the import takes the vision path."""
    monkeypatch.setattr(extraction, "extract_file",
                        lambda name, data: {"text": "", "images": ["aW1n"]})


def _import_photo(client, headers, **params):
    r = client.post("/api/recipes/extract/files", headers=headers, params=params,
                    files=[("files", ("r.jpg", b"not really a jpeg"))])
    assert r.status_code == 200, r.text
    assert client.posted[-1]["model"] == "@recipe-vision"
    return client.posted[-1]


def test_a_photo_import_names_the_caller_on_the_vision_role(client, photo):
    """The caller's own photo, on the vision slot's own role. The image goes as it always has,
    Ollama-style on the message, so nothing about the wire changes for the broker."""
    sent = _import_photo(client, MINE)
    assert calls(client.posted) == [("@recipe-vision", OWNER)]
    assert sent["messages"][-1]["images"] == ["aW1n"]


def test_a_photo_import_while_viewing_as_another_user_stays_local(client, photo):
    """The same rule as every other call: an admin acting as somebody else names nobody."""
    _import_photo(client, ADMIN, owner=OTHER)
    assert carried(client.posted) == ["ABSENT"]


def test_another_users_photo_import_carries_their_name_never_the_owners(client, photo):
    """The rail names the person asking; the broker serves only BROKER_CLI_OWNERS from the CLI,
    so a non-owner's import runs locally there, and their chip says so (below)."""
    _import_photo(client, {"X-Platform-User": OTHER, "X-Platform-Admin": "0"})
    assert calls(client.posted) == [("@recipe-vision", OTHER)]


def test_the_photo_import_waits_the_rails_full_broker_timeout(client, photo, monkeypatch):
    """A CLI call that fails falls back to the local vision model inside the same request,
    so the rail must wait out BROKER_CLI_TIMEOUT plus that answer. It waits DEFAULT_TIMEOUT
    (RECIPE_BOOK_BROKER_TIMEOUT, 1320 s in compose), the setting tools/rail_smoke.py's
    CLI_RAIL_TIMEOUTS registers for this rail and checks against the broker's live
    cli.timeout_s. Asserted at the transport, so a shorter timeout on any path to it fails."""
    seen: list[float] = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": REPLY}}

    def fake_httpx_post(url, json=None, timeout=None, headers=None):
        seen.append(timeout)
        return _Resp()

    monkeypatch.setattr(broker, "_post", _REAL_POST)   # the wire fixture's fake, undone
    monkeypatch.setattr(broker.httpx, "post", fake_httpx_post)
    r = client.post("/api/recipes/extract/files", headers=MINE,
                    files=[("files", ("r.jpg", b"not really a jpeg"))])
    assert r.status_code == 200, r.text
    assert seen == [broker.DEFAULT_TIMEOUT]
    assert broker.DEFAULT_TIMEOUT == float(os.environ.get("RECIPE_BOOK_BROKER_TIMEOUT", "600"))


def test_the_planner_names_the_caller_on_their_own_plan(client):
    rs = [
        client.post("/api/planner/propose", json={"dates": ["2026-10-10"], "slots": ["dinner"]},
                    headers=MINE),
        client.post("/api/planner/propose/swap",
                    json={"date": "2026-10-10", "slot": "dinner", "ptype": "wine"}, headers=MINE),
        client.post("/api/planner/pair", json={"date": "2026-10-10", "ptype": "wine"},
                    headers=MINE),
    ]
    assert [r.status_code for r in rs] == [200] * 3, [r.text for r in rs]
    assert calls(client.posted) == [("@recipe", OWNER)] * 3


def test_a_meal_swap_names_the_caller_on_the_assistant_role(client, monkeypatch):
    """The swap that picks from the caller's own library: `planner_ai.swap`'s pick branch,
    which a meal or cocktail re-roll reaches only once its pool holds a recipe. The
    planner test above runs on an empty library, so its swaps reach the wine branch alone."""
    from recipe_book.catalog import Catalog, Recipe  # noqa: PLC0415
    monkeypatch.setattr(state, "_catalog", Catalog([Recipe(
        id="r1", title="Stew", category="Entrees", kind="meal", rel_path="Entrees/stew.md",
        ingredients=["1 onion"], instructions=["Cook."])]))
    r = client.post("/api/planner/propose/swap", headers=MINE,
                    json={"date": "2026-10-10", "slot": "dinner", "ptype": "meal"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["recipe_id"] == "r1", r.json()
    assert calls(client.posted) == [("@recipe", OWNER)]


def test_an_admin_acting_as_another_user_stays_local(client):
    """THE case for this rail. propose reads and pair writes OTHER's plan; the request names
    the admin. Every call in a view-as session carries no requester, whoever is an owner."""
    q = {"owner": OTHER}
    rs = [
        client.post("/api/planner/propose", params=q, headers=ADMIN,
                    json={"dates": ["2026-10-10"], "slots": ["dinner"]}),
        client.post("/api/planner/pair", params=q, headers=ADMIN,
                    json={"date": "2026-10-10", "ptype": "wine"}),
        client.post("/api/assistant", params=q, headers=ADMIN,
                    json={"mode": "ask", "prompt": "hi"}),
    ]
    assert [r.status_code for r in rs] == [200] * 3, [r.text for r in rs]
    assert carried(client.posted) == ["ABSENT"] * 3


def test_an_admin_viewing_as_themselves_is_their_own_work(client):
    r = client.post("/api/planner/propose", params={"owner": OWNER}, headers=ADMIN,
                    json={"dates": ["2026-10-10"], "slots": ["dinner"]})
    assert r.status_code == 200, r.text
    assert calls(client.posted) == [("@recipe", OWNER)]


def test_a_non_admins_owner_param_changes_nothing(client):
    """`owner_id` ignores `?owner=` for a non-admin, so the plan is still the caller's."""
    r = client.post("/api/planner/propose", params={"owner": OTHER}, headers=MINE,
                    json={"dates": ["2026-10-10"], "slots": ["dinner"]})
    assert r.status_code == 200, r.text
    assert calls(client.posted) == [("@recipe", OWNER)]


class _Ident:
    def __init__(self, user, is_admin):
        self.user, self.is_admin = user, is_admin


def test_names_are_compared_byte_exact():
    """A case variant of the admin's own name is another account."""
    assert deps.cli_requester(_Ident(OWNER, True), OWNER.upper()) is None
    assert deps.cli_requester(_Ident(OWNER, True), f" {OWNER} ") == OWNER
    assert deps.cli_requester(_Ident(None, True), None) is None


def test_the_icon_subject_batch_never_names_anyone(client):
    """Background: the weekly scheduler or an admin's repass, nobody waiting on it."""
    from recipe_book.catalog import Recipe  # noqa: PLC0415
    recs = [Recipe(id=f"r{i}", title=f"Dish {i}", category="Entrees", kind="meal",
                   rel_path=f"Entrees/d{i}.md") for i in range(3)]
    con = db.connect()
    try:
        icon_prompts.build(con, recipes=recs, force=True, batch=24)
    finally:
        con.close()
    assert carried(client.posted) == ["ABSENT"]


def test_the_icon_image_batch_never_names_anyone(monkeypatch):
    """The icon images go to the broker's image worker, never to a chat role, and carry no
    name: the batch runs for the weekly scheduler or an admin's repass with nobody waiting."""
    sent: list[tuple[str, dict]] = []

    def fake_post(path, payload, timeout=None):
        sent.append((path, payload))
        return {"images": [""]}

    monkeypatch.setattr(broker, "_post", fake_post)
    broker.generate_images(["a bowl of soup"])
    assert [p for p, _ in sent] == ["/v1/image"]
    assert "requester" not in sent[0][1]


# --- the chip: judged with the same identity the calls carry ------------------------------


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """The app the container runs, hermetic (test_auth.py's recipe), with a broker whose CLI
    serves OWNER on @recipe."""
    from recipe_book import ingest, seed, semantic  # noqa: PLC0415
    from recipe_book.api import create_api  # noqa: PLC0415

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(seed, "hydrate_if_empty", lambda: None)
    monkeypatch.setattr(seed, "hydrate_icons", lambda: False)
    monkeypatch.setattr(ingest, "ingest", lambda con: None)
    monkeypatch.setattr(semantic, "load", lambda: False)
    monkeypatch.setattr(state, "_catalog", None)
    monkeypatch.setattr(broker, "ASSISTANT_MODEL", "@recipe")
    monkeypatch.setattr(broker, "VISION_MODEL", "@recipe-vision")
    monkeypatch.setattr(broker, "EMBED_MODEL", "@embed")
    cli = {"ready": True, "owners": [OWNER], "roles": ["recipe", "recipe-vision", "embed"],
           "active": []}
    monkeypatch.setattr(broker, "roles", lambda: [
        {"role": "recipe", "resolved": "gemma4:26b"},
        {"role": "recipe-vision", "resolved": "gemma4:26b"},
        {"role": "embed", "resolved": "bge-m3:latest"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "gemma4:26b"},
                                                   {"name": "bge-m3:latest"}])
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [], "jobs": [], "cli": cli})
    c = TestClient(create_api())
    c.cli = cli  # type: ignore[attr-defined]
    return c


def _chips(api, headers, **params):
    r = api.get("/api/capabilities", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return {m["slot"]: m["backend"] for m in r.json()["models"]}


def test_an_owner_sees_the_assistant_and_vision_on_claude(api):
    """The embedder is never opted in, even with its role on the broker list."""
    assert _chips(api, MINE) == {"assistant": "claude", "vision": "claude", "embed": "ollama"}


def test_a_non_owner_sees_ollama(api):
    chips = _chips(api, {"X-Platform-User": OTHER})
    assert (chips["assistant"], chips["vision"]) == ("ollama", "ollama")


def test_an_admin_viewing_as_another_user_sees_ollama(api):
    """The frontend sends ?owner= on every request in view-as, the chip included."""
    chips = _chips(api, ADMIN, owner=OTHER)
    assert (chips["assistant"], chips["vision"]) == ("ollama", "ollama")
    chips = _chips(api, ADMIN)
    assert (chips["assistant"], chips["vision"]) == ("claude", "claude")


def test_a_role_off_the_allowlist_reads_ollama(api):
    """Each slot is judged on its own role: switching one on says nothing about the other."""
    api.cli["roles"] = ["recipe-vision"]
    chips = _chips(api, MINE)
    assert (chips["assistant"], chips["vision"]) == ("ollama", "claude")
    api.cli["roles"] = ["recipe"]
    chips = _chips(api, MINE)
    assert (chips["assistant"], chips["vision"]) == ("claude", "ollama")
