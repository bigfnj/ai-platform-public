"""FIX 2 — the three lookups that had no owner clause at all.

``get_queryset``, ``get_queries`` and ``get_run`` fetched by id and nothing else, while their
``list_*`` siblings right beside them were owner-scoped. No header trickery was needed: a
perfectly authenticated Alice could POST /api/bench/run with Bob's queryset id and the engine
would benchmark his labeled queries, then hand the query text back verbatim in
``results[].metrics.misses``; GET /api/bench/runs/{id} walked every tenant's run detail.

A foreign id now reads as absent — None, and 404 at the route — rather than 403, matching
rails/iep's ``_student_or_404``: a 403 confirms the id exists, which is half of what leaked.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from ai_playground import db
from ai_playground.bench import engine
from conftest import StubWS, make_corpus, make_queryset, ws_endpoint

SECRET = "a secret internal question"


# --- db.get_queryset / db.get_queries ------------------------------------------------------


def test_get_queryset_hides_another_owners_set(con):
    qsid = make_queryset(con, "bob", name="Bobs Set")
    assert db.get_queryset(con, qsid, "alice") is None
    assert db.get_queryset(con, qsid, "bob")["name"] == "Bobs Set"


def test_get_queryset_still_shows_a_shared_seed_set(con):
    qsid = make_queryset(con, None, name="Seed Set", kind="seed")
    assert db.get_queryset(con, qsid, "alice")["name"] == "Seed Set"


def test_get_queryset_any_owner_is_the_delete_paths_escape_hatch(con):
    """querysets.delete has to see a foreign set to refuse it (or, as admin, remove it)."""
    qsid = make_queryset(con, "bob", name="Bobs Set")
    assert db.get_queryset(con, qsid, "alice", any_owner=True)["owner"] == "bob"


def test_get_queries_hides_another_owners_queries(con):
    """Scoped through the parent set, which is the row that carries the owner — otherwise the
    labeled queries walk out even with get_queryset fixed."""
    qsid = make_queryset(con, "bob", name="Bobs Set")
    assert db.get_queries(con, qsid, "alice") == []
    assert [q["q"] for q in db.get_queries(con, qsid, "bob")] == [SECRET]


def test_get_queries_still_returns_a_shared_seed_sets_queries(con):
    qsid = make_queryset(con, None, name="Seed Set", kind="seed")
    assert len(db.get_queries(con, qsid, "alice")) == 1


# --- db.get_run ----------------------------------------------------------------------------


def test_get_run_hides_another_owners_run(con):
    rid = db.add_run(con, owner="bob", corpus_name="Bobs Docs", queryset="Bobs Set", k=4,
                     results=[{"id": "m", "metrics": {"misses": [{"q": SECRET}]}}])
    assert db.get_run(con, rid, "alice") is None
    assert db.get_run(con, rid, "bob")["corpus_name"] == "Bobs Docs"


def test_get_run_still_shows_a_shared_run(con):
    """list_runs shows null-owner runs to everyone; the detail view has to agree with it."""
    rid = db.add_run(con, owner=None, corpus_name="Shared", queryset="Seed Set", k=4, results=[])
    assert db.get_run(con, rid, "alice")["corpus_name"] == "Shared"


# --- the routes ----------------------------------------------------------------------------


def test_run_detail_route_404s_on_another_users_run(api, con):
    rid = db.add_run(con, owner="bob", corpus_name="Bobs Docs", queryset="Bobs Set", k=4,
                     results=[{"id": "m", "metrics": {"misses": [{"q": SECRET}]}}])
    resp = TestClient(api).get(f"/api/bench/runs/{rid}", headers={"X-Platform-User": "alice"})
    assert resp.status_code == 404
    assert SECRET not in resp.text


def test_run_detail_route_still_serves_your_own_run(api, con):
    rid = db.add_run(con, owner="alice", corpus_name="Alices Docs", queryset="Set", k=4,
                     results=[])
    resp = TestClient(api).get(f"/api/bench/runs/{rid}", headers={"X-Platform-User": "alice"})
    assert resp.status_code == 200 and resp.json()["corpus_name"] == "Alices Docs"


def test_bench_run_will_not_benchmark_another_users_queryset(api, con, monkeypatch):
    """The whole point of the leak: the engine ran over Bob's labeled queries and echoed them
    back. The engine must not be reached at all, so it is stubbed to say so."""
    calls: list[tuple] = []
    monkeypatch.setattr(engine, "run", lambda *a, **kw: calls.append(a) or [])

    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "bob", name="Bobs Set")
    resp = TestClient(api).post("/api/bench/run",
                                headers={"X-Platform-User": "alice"},
                                json={"corpus": cid, "queryset": qsid,
                                      "configs": [{"model": "m", "prompting": "none"}]})
    assert resp.status_code == 400
    assert calls == []
    assert SECRET not in resp.text


def test_bench_run_still_uses_your_own_queryset(api, con, monkeypatch):
    """Guards the over-correction: owner-scoping must not lock a user out of their own set."""
    seen: list[list[dict]] = []

    def fake_run(con2, chunks, queries, configs, k, *rest):
        seen.append(queries)
        return [{"id": "m", "metrics": {"R@1": 1.0}}]

    monkeypatch.setattr(engine, "run", fake_run)

    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "alice", name="Alices Set")
    resp = TestClient(api).post("/api/bench/run",
                                headers={"X-Platform-User": "alice"},
                                json={"corpus": cid, "queryset": qsid,
                                      "configs": [{"model": "m", "prompting": "none"}]})
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"id": "m", "metrics": {"R@1": 1.0}}]
    assert [q["q"] for q in seen[0]] == [SECRET]


def test_ws_bench_will_not_benchmark_another_users_queryset(api, con, monkeypatch):
    """The socket reads the same two lookups, so it leaked the same way."""
    calls: list[tuple] = []
    monkeypatch.setattr(engine, "run", lambda *a, **kw: calls.append(a) or [])

    cid = make_corpus(con, "alice", name="Alices Docs")
    qsid = make_queryset(con, "bob", name="Bobs Set")
    ws = StubWS(headers={"x-platform-user": "alice"},
                inbox=[{"corpus": cid, "queryset": qsid, "configs": [{"model": "m"}]}])
    asyncio.run(ws_endpoint(api, "/ws/bench")(ws))

    assert calls == []
    assert [f["type"] for f in ws.sent] == ["error"]
    assert SECRET not in str(ws.sent)


@pytest.mark.parametrize("fn", ["get_queryset", "get_queries", "get_run"])
def test_the_scoped_lookups_all_require_an_owner_argument(fn):
    """A caller that forgets the owner must fail at the call, not silently read everything."""
    assert "owner" in inspect.signature(getattr(db, fn)).parameters


# --- the DELETE routes, which said 403 where every read path says 404 ----------------------
# Found by the 2026-09-15 audit. The module docstring above already stated this invariant
# ("a 403 confirms the id exists, which is half of what leaked") and no test held the DELETE
# half to it, which is how the two routes drifted from it. A nonexistent id returns 200 on
# both, so a 403 was the single answer that told a foreign id apart from an absent one.


def test_deleting_another_owners_corpus_is_404_not_403(api, con):
    cid = make_corpus(con, "bob", name="Bobs Docs")
    resp = TestClient(api).delete(f"/api/rag/corpus/{cid}",
                                  headers={"X-Platform-User": "alice"})
    assert resp.status_code == 404
    # The refusal must not echo the owner or the name back either.
    assert "bob" not in resp.text.lower() and "Bobs Docs" not in resp.text


def test_deleting_another_owners_queryset_is_404_not_403(api, con):
    qsid = make_queryset(con, "bob", name="Bobs Set")
    resp = TestClient(api).delete(f"/api/bench/querysets/{qsid}",
                                  headers={"X-Platform-User": "alice"})
    assert resp.status_code == 404
    assert "bob" not in resp.text.lower() and "Bobs Set" not in resp.text


def test_a_foreign_id_is_indistinguishable_from_an_absent_one(api, con):
    """The property that makes the two tests above worth having: same status, same body.
    If an absent id ever stops returning the same thing as a foreign one, guessing ids
    becomes an inventory of other tenants' corpora again."""
    client = TestClient(api)
    cid = make_corpus(con, "bob", name="Bobs Docs")
    foreign = client.delete(f"/api/rag/corpus/{cid}", headers={"X-Platform-User": "alice"})
    absent = client.delete("/api/rag/corpus/99999", headers={"X-Platform-User": "alice"})
    assert (foreign.status_code, foreign.json()) == (absent.status_code, absent.json()), (
        f"foreign {foreign.status_code} {foreign.text} vs absent "
        f"{absent.status_code} {absent.text}")


def test_you_can_still_delete_your_own(api, con):
    """The guard is worth nothing if it refuses the owner too."""
    client = TestClient(api)
    cid = make_corpus(con, "alice", name="Alices Docs")
    assert client.delete(f"/api/rag/corpus/{cid}",
                         headers={"X-Platform-User": "alice"}).status_code == 200
    qsid = make_queryset(con, "alice", name="Alices Set")
    assert client.delete(f"/api/bench/querysets/{qsid}",
                         headers={"X-Platform-User": "alice"}).status_code == 200
