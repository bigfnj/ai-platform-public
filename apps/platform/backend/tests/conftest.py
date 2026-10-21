"""Shared fixtures for the gateway tests: a throwaway file-backed SQLite DB with the auth +
schedule tables created, a session factory, and a synthetic rail added to the model-slot
catalog for one test. No network, no broker, no FastAPI app."""
import pytest

from platform_core.db import Database
from platform_gateway_app import rails_models
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


@pytest.fixture()
def add_rail(monkeypatch):
    """add_rail(rail_id, slots): a synthetic rail in RAIL_MODEL_SLOTS for one test, so a rule is
    pinned whatever the real catalog holds today (which slots are wired moves with the rails).

    Every role set is recomputed with rails_models.role_sets, the module's own rules, and the
    new roles are added IN PLACE: main.py imported those sets by name, so a fresh set object
    would never reach the routes. Both are undone after the test."""
    added: list[tuple[set[str], set[str]]] = []

    def add(rail_id: str, slots: list[dict]) -> None:
        monkeypatch.setitem(rails_models.RAIL_MODEL_SLOTS, rail_id, slots)
        for name, roles in rails_models.role_sets(rails_models.RAIL_MODEL_SLOTS).items():
            live = getattr(rails_models, name)
            new = roles - live
            live |= new
            added.append((live, new))

    yield add
    for live, new in added:
        live -= new


# No test outside a live class reaches the network: tools/tests/offline_guard.py, which every
# suite's conftest takes. tools/tests is appended, so nothing there shadows a module of this suite.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.append(str(next(p for p in Path(__file__).resolve().parents
                         if (p / "tools" / "tests" / "offline_guard.py").is_file())
                    / "tools" / "tests"))
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: E402,F401
