"""Store-level per-user ownership (audit E2): owner stamping + owner-scoped listing.
Imports only dashboard.store (no edu_media_core), so it runs without the media deps."""
from dashboard.store import Store


def _store(tmp_path):
    return Store(str(tmp_path / "jobs.db"))


def test_owner_stamped_and_readback(tmp_path):
    s = _store(tmp_path)
    s.create_job("x", "X", "wf", "/d/x", owner="alice")
    assert s.get_job("x")["owner"] == "alice"


def test_list_is_owner_scoped(tmp_path):
    s = _store(tmp_path)
    s.create_job("a1", "A", "wf", "/d/a1", owner="alice")
    s.create_job("b1", "B", "wf", "/d/b1", owner="bob")
    s.create_job("leg", "L", "wf", "/d/leg")  # legacy: owner NULL

    assert {j["id"] for j in s.list_jobs(restrict_owner="alice")} == {"a1"}
    assert {j["id"] for j in s.list_jobs(restrict_owner="bob")} == {"b1"}
    # No restriction (admin / internal sweep) sees everything, including the legacy NULL-owner job.
    assert {j["id"] for j in s.list_jobs()} == {"a1", "b1", "leg"}
    # A user never sees the legacy NULL-owner row.
    assert all(j["id"] != "leg" for j in s.list_jobs(restrict_owner="alice"))
