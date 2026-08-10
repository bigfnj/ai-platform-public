"""Shared SQLAlchemy helper for platform apps that need a database.

recipe-book is the first DB-backed app; this is that helper graduated into the
core so later apps get one consistent way to open a database. SQLite today, but
the seam is a standard SQLAlchemy URL, so an app can point at Postgres via config
without code changes.

Kept OUT of ``platform_core.__init__`` and behind the ``[db]`` optional extra, so
apps that don't use a database (e.g. the console) never import SQLAlchemy. Use it
with ``from platform_core.db import Database``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """Owns one engine + session factory for a platform app."""

    def __init__(self, url: str):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, future=True, connect_args=connect_args)
        self._factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def create_all(self, metadata: MetaData) -> None:
        """Create any missing tables for the given declarative metadata."""
        metadata.create_all(self.engine)

    def session(self) -> Session:
        """A new session the caller is responsible for closing."""
        return self._factory()

    @contextmanager
    def session_ctx(self) -> Iterator[Session]:
        """A session as a context manager (auto-closed)."""
        session = self._factory()
        try:
            yield session
        finally:
            session.close()

    def dependency(self) -> Iterator[Session]:
        """FastAPI dependency: a request-scoped session (yields then closes)."""
        session = self._factory()
        try:
            yield session
        finally:
            session.close()
