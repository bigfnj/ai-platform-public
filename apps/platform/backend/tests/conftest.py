"""Shared fixtures for the gateway tests: a throwaway file-backed SQLite DB with the auth +
schedule tables created, and a session factory. No network, no broker, no FastAPI app."""
import pytest

from platform_core.db import Database
from platform_gateway_app.models import Base


@pytest.fixture()
def db(tmp_path):
    """A fresh Database on a temp SQLite file (file-backed so multiple sessions share state)."""
    database = Database(f"sqlite:///{tmp_path / 'gw.db'}")
    database.create_all(Base.metadata)
    return database


@pytest.fixture()
def session(db):
    s = db.session()
    try:
        yield s
    finally:
        s.close()
