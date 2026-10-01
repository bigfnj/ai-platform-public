"""Shared fixtures for the ai-playground suite.

Hermetic by construction: a throwaway SQLite file, seed directories that do not exist (so
nothing is ever ingested), and ``no_outbound`` wired over every call that would leave the
process. That last one is not just hygiene — several tests here assert that an un-gated route
is refused, and a 401 that still spent the platform's NVIDIA key would not be a fix.

The rail is torch-free and stays that way: nothing below imports a model runtime.
"""
from __future__ import annotations

import pytest

from ai_playground import broker, config, corpora, db, nim
from ai_playground.api import app as appmod


@pytest.fixture
def no_outbound(monkeypatch):
    """Every way out of the process, wired to fail loudly: the broker (and so the GPU) and the
    NVIDIA client. A test that reaches one of these is not hermetic, so it should say so."""
    def refuse(*args, **kwargs):
        raise AssertionError("a hermetic test made an outbound call")

    monkeypatch.setattr(broker, "_post", refuse)
    monkeypatch.setattr(broker, "_get", refuse)
    # chat_stream does NOT go through _post: it builds its own httpx.AsyncClient, so this
    # fixture used to let it straight out of the process. Latent when the gap was found,
    # because the /ws/rag security tests stop at validation, but a future test calling it
    # would dial 127.0.0.1:11500 for real with a 600s timeout and a fixture named
    # `no_outbound` would be the reason nobody looked.
    monkeypatch.setattr(broker, "chat_stream", refuse)
    monkeypatch.setattr(nim, "AsyncOpenAI", refuse)
    return refuse


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the rail's whole data layer at a throwaway directory. The seed directories are
    named but never created, so ensure_seeds() has nothing to find and no reason to embed."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ai_playground.db"))
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(config, "SEED_CORPORA_DIR", tmp_path / "no-seed-corpora")
    monkeypatch.setattr(config, "SEED_QUERYSETS_DIR", tmp_path / "no-seed-querysets")
    # The DEPLOYED topology, which is the one that has to fail closed. Standalone dev is the
    # exception and is asserted explicitly where it matters.
    monkeypatch.setattr(config, "STANDALONE", False)
    # corpora._CACHE is keyed by corpus id, and ids restart at 1 in each fresh database.
    corpora._CACHE.clear()
    yield tmp_path
    corpora._CACHE.clear()


@pytest.fixture
def con(data_dir):
    """An initialised connection to the throwaway database."""
    c = db.connect()
    db.init_db(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def api(data_dir, no_outbound, monkeypatch):
    """A real create_api() app over the throwaway database.

    Seeding is stubbed rather than pointed somewhere empty: it runs on a daemon thread that
    would outlive this fixture's monkeypatching and then ingest the REAL seed corpora against
    the REAL broker.
    """
    monkeypatch.setattr(appmod, "_seed_in_background", lambda: None)
    return appmod.create_api()


class StubWS:
    """A WebSocket that records what a handler did to it.

    Enough surface for the two /ws handlers and nothing more: reading from or sending on a
    socket that was never accepted is the bug these tests are about, so the stub asserts rather
    than returning something plausible. Used instead of TestClient's handshake because that
    goes through the app-level identity dependency first, which would mask which of the two
    guards actually refused.
    """

    def __init__(self, headers: dict | None = None, inbox: list | None = None) -> None:
        self.headers = dict(headers or {})
        self.inbox = list(inbox or [])
        self.accepted = False
        self.close_code: int | None = None
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def receive_json(self) -> dict:
        assert self.accepted, "the handler read from a socket it never accepted"
        assert self.inbox, "the handler read more frames than the test supplied"
        return self.inbox.pop(0)

    async def send_json(self, data: dict) -> None:
        assert self.accepted, "the handler sent on a socket it never accepted"
        self.sent.append(data)


def ws_endpoint(app, path: str):
    """The raw coroutine behind an @app.websocket route."""
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no websocket route {path}")


def make_corpus(con, owner: str | None, *, name: str = "Docs", kind: str = "user") -> int:
    """A one-chunk corpus. The vector is stored but never used here — every test that reads a
    corpus stops at the ownership check or at the chunk text, neither of which needs the GPU."""
    cid = db.add_corpus(con, slug=name.lower().replace(" ", "-"), name=name, kind=kind,
                        owner=owner, embed_model="test")
    db.add_chunks(con, cid, [{"source": f"{name}.md", "text": "hello"}], [[1.0, 0.0]])
    return cid


def make_queryset(con, owner: str | None, *, name: str = "Set", kind: str = "user") -> int:
    return db.add_queryset(con, slug=name.lower().replace(" ", "-"), name=name, kind=kind,
                           owner=owner, queries=[{"q": "a secret internal question",
                                                  "targets": ["Docs.md"]}])
