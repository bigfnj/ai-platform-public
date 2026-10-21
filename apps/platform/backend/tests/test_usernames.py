"""Usernames are unique ignoring case, ASCII-only, and the startup check enforces both histories.

WHY. The broker gives one account a personal Claude CLI subscription by name. Usernames used to
be unique case-SENSITIVELY while the broker's owner check folded case, so an admin could create
`Owner-One` beside `owner-one` and the second account spent the owner's subscription. The broker
now matches exactly (services/broker/tests/test_cli_routing.py); this file holds the gateway's
half: one account per name-ignoring-case, at creation, at login and in the database itself, and
a startup that refuses to run on a database that already holds a colliding pair.

The ASCII rule is there because the name travels to every rail in the x-platform-user header, which
httpx encodes as ASCII, so an accented name logged in and then 500'd on every rail call.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from platform_gateway_app import main as m
from platform_gateway_app.auth import hash_password
from platform_gateway_app.models import SessionRow, User


def _add(db, *names: str) -> list[int]:
    ids = []
    with db.session_ctx() as s:
        for n in names:
            u = User(username=n, password_hash=hash_password("pw"), is_admin=True,
                     is_superadmin=True)
            s.add(u)
            s.commit()
            ids.append(u.id)
    return ids


def _wire(db, monkeypatch) -> TestClient:
    monkeypatch.setattr(m, "_login_fails", {})
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    monkeypatch.setattr(
        m.app.state, "settings",
        SimpleNamespace(login_window_seconds=300, login_max_fails=8, session_ttl_hours=1,
                        cookie_secure=False, session_cookie="platform_session",
                        cookie_samesite="lax", root_asset_owner=lambda _path: None),
        raising=False)
    return TestClient(m.app)


@pytest.fixture()
def client(db, monkeypatch):
    """The real app minus its lifespan, on a database the startup check has already passed."""
    m._refuse_case_colliding_usernames(db)
    return _wire(db, monkeypatch)


def _login(client: TestClient, name: str, pw: str = "pw"):
    return client.post("/api/platform/login", json={"username": name, "password": pw})


def _admin(db, client: TestClient) -> TestClient:
    _add(db, "admin-x")
    assert _login(client, "admin-x").status_code == 200
    return client


# --- login ------------------------------------------------------------------

def test_login_ignores_case_and_signs_in_the_stored_account(db, client):
    [bob] = _add(db, "bob")
    r = _login(client, "BoB")
    assert r.status_code == 200, r.text
    # The stored spelling, not the typed one: this is what every rail and the broker see.
    assert r.json()["username"] == "bob"
    with db.session_ctx() as s:
        assert [row.user_id for row in s.execute(select(SessionRow)).scalars()] == [bob]


def test_login_still_checks_the_password_of_the_matched_account(db, client):
    _add(db, "bob")
    assert _login(client, "BOB", pw="wrong").status_code == 401


def test_an_unknown_name_costs_the_same_argon2_as_a_known_one(db, client, monkeypatch):
    """An unknown name skipped Argon2 and was refused about 45 ms sooner than a known name with
    a wrong password (measured), so the timing said which usernames exist. Now an unknown name
    is verified once too, against a real Argon2id hash nobody's password matches (_DUMMY_HASH),
    and both refusals read the same."""
    _add(db, "bob")
    seen: list[str] = []
    real = m.verify_password

    def counted(password: str, hashed: str) -> bool:
        seen.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(m, "verify_password", counted)
    unknown = _login(client, "nobody-by-this-name", pw="guess")
    assert len(seen) == 1, f"verify_password ran {len(seen)} times for an unknown name"
    assert seen[0].startswith("$argon2id$") and seen[0] == m._DUMMY_HASH, (
        "the unknown name was checked against no real Argon2 hash, so it costs nothing")
    known = _login(client, "bob", pw="guess")
    assert len(seen) == 2
    assert unknown.status_code == known.status_code == 401
    assert unknown.json() == known.json()


def test_the_dummy_hash_signs_nobody_in(db, client):
    """Whatever password an unknown name is sent with, the dummy verifies none of them."""
    for pw in ("", "pw", m._DUMMY_HASH):
        assert _login(client, "nobody-by-this-name", pw=pw).status_code == 401


# --- creation ----------------------------------------------------------------

def test_a_case_variant_of_an_existing_name_is_refused(db, client):
    admin = _admin(db, client)
    assert admin.post("/api/platform/admin/users",
                      json={"username": "bob", "password": "x"}).status_code == 200
    r = admin.post("/api/platform/admin/users", json={"username": "Bob", "password": "x"})
    assert r.status_code == 409, r.text
    with db.session_ctx() as s:
        assert s.execute(select(User.username).where(User.username.in_(["bob", "Bob"]))
                         ).scalars().all() == ["bob"]


def test_the_route_refuses_a_case_variant_without_the_index_too(db, monkeypatch):
    """The route's own check, proven apart from the database backstop: on a database the
    startup check has not run on yet there is no folded index, so only the route can refuse."""
    admin = _admin(db, _wire(db, monkeypatch))
    _add(db, "bob")
    r = admin.post("/api/platform/admin/users", json={"username": "BOB", "password": "x"})
    assert r.status_code == 409, r.text


def test_the_exact_same_name_is_still_refused(db, client):
    admin = _admin(db, client)
    admin.post("/api/platform/admin/users", json={"username": "bob", "password": "x"})
    assert admin.post("/api/platform/admin/users",
                      json={"username": " bob ", "password": "x"}).status_code == 409


@pytest.mark.parametrize("name", ["caf\u00e9-user", "bob\u200b", "tab\there", "x" * 65, "\u0130-dotted"])
def test_a_name_a_header_cannot_carry_is_refused(db, client, name):
    admin = _admin(db, client)
    r = admin.post("/api/platform/admin/users", json={"username": name, "password": "x"})
    assert r.status_code == 400, r.text
    assert "ASCII" in r.json()["detail"]


@pytest.mark.parametrize("name", ["owner.two", "first last", "a@b-c_d", "x" * 64, "~!"])
def test_printable_ascii_names_are_accepted(db, client, name):
    admin = _admin(db, client)
    r = admin.post("/api/platform/admin/users", json={"username": name, "password": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == name


def test_a_create_that_races_past_the_check_gets_409_not_500(db, client, monkeypatch):
    """The folded unique index is the backstop. Simulate the race by making the pre-check see
    nothing, so only the database can refuse the second spelling."""
    admin = _admin(db, client)
    _add(db, "bob")
    real_execute = m.OrmSession.execute

    def blind(self, stmt, *a, **kw):
        res = real_execute(self, stmt, *a, **kw)
        if "lower(users.username)" in str(stmt):
            return SimpleNamespace(first=lambda: None)
        return res

    monkeypatch.setattr(m.OrmSession, "execute", blind)
    r = admin.post("/api/platform/admin/users", json={"username": "BOB", "password": "x"})
    assert r.status_code == 409, r.text


# --- the database and the startup check -------------------------------------

def test_the_database_itself_refuses_a_case_variant(db):
    m._refuse_case_colliding_usernames(db)
    _add(db, "bob")
    with pytest.raises(IntegrityError):
        _add(db, "BOB")


def test_startup_refuses_colliding_accounts_and_names_them_by_id_only(db):
    ids = _add(db, "bob", "user-d", "Bob", "BOB", "user-e", "User-E")
    with pytest.raises(RuntimeError) as err:
        m._refuse_case_colliding_usernames(db)
    msg = str(err.value)
    assert "refusing to start" in msg
    # Exactly the colliding groups, each in id order; user-d's id collides with nobody's.
    listed = re.search(r"\((ids [^)]*)\)", msg).group(1)
    assert listed == f"ids {ids[0]}, {ids[2]}, {ids[3]}; ids {ids[4]}, {ids[5]}"
    lowered = msg.lower()
    for name in ("bob", "user-d", "user-e"):
        assert name not in lowered, f"the startup error named an account: {name}"
    # Refused BEFORE the index, which could not be built on this data anyway.
    with db.engine.connect() as c:
        names = c.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).scalars().all()
    assert m._USERNAME_FOLDED_INDEX not in names


def test_startup_passes_and_is_repeatable_on_a_clean_database(db):
    _add(db, "bob", "user-d")
    m._refuse_case_colliding_usernames(db)
    m._refuse_case_colliding_usernames(db)
    with db.engine.connect() as c:
        names = c.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).scalars().all()
    assert m._USERNAME_FOLDED_INDEX in names


def test_the_real_lifespan_runs_the_check_before_anything_else_touches_users(
        tmp_path, monkeypatch):
    """Through the shipped lifespan, so dropping the call from it fails here and not only in a
    unit test of the helper. Every app.state attribute the lifespan sets is pre-set through
    monkeypatch, so whatever it leaves behind is undone after the test."""
    from platform_core.db import Database
    from platform_gateway_app.models import Base

    url = f"sqlite:///{tmp_path / 'gw.db'}"
    pre = Database(url)
    pre.create_all(Base.metadata)
    _add(pre, "bob", "Bob")
    pre.engine.dispose()
    monkeypatch.setenv("PLATFORM_DB_URL", url)
    for attr in ("settings", "backends", "broker", "http", "http_body", "db", "sched_task"):
        monkeypatch.setattr(m.app.state, attr, None, raising=False)
    seeded = []
    monkeypatch.setattr(m, "_seed_admin", lambda *a, **k: seeded.append(1))

    async def enter():
        async with m.lifespan(m.app):
            pass

    with pytest.raises(RuntimeError, match="refusing to start"):
        asyncio.run(enter())
    assert seeded == [], "the lifespan reached the admin seed before refusing"
