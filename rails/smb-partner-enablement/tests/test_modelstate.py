"""The four-state model chips.

``modelstate.py`` is a generated file that must match byte for byte across every rail, so this
suite tests the contract rather than the implementation: the four state names, the resolution
ORDER, and the tag tolerance the file's own docstring calls non-optional. Those are the parts a
well-meaning simplification breaks — replacing ``_same()`` with ``==`` turns a resident
``bge-m3:latest`` into a red "not installed" chip on every rail at once.
"""
from __future__ import annotations

import pytest

from smb_partner import api, broker, config, modelstate

SPECS = api.MODEL_SLOTS


def resolve(fake, specs=None):
    return modelstate.resolve(specs or SPECS)


def state_of(out, slot):
    return next(m for m in out["models"] if m["slot"] == slot)


def test_the_rail_declares_the_two_slots_it_keeps_resident(fake_broker):
    """Answer model plus embedder, both live at once — the rail's whole premise. The slot ids
    match the gateway's RAIL_MODEL_SLOTS and rail.json."""
    assert [(s, ref) for s, _, ref in SPECS] == [("reasoning", config.RAG_MODEL),
                                                 ("retrieval", config.EMBED_MODEL)]


def test_a_resident_model_is_loaded(fake_broker):
    fake_broker.installed = ["qwen2.5:3b", "bge-m3:latest"]
    fake_broker.loaded = ["qwen2.5:3b", "bge-m3:latest"]
    out = resolve(fake_broker)
    assert out["broker"] == "ok"
    assert state_of(out, "reasoning")["state"] == modelstate.LOADED
    assert state_of(out, "retrieval")["state"] == modelstate.LOADED


def test_an_installed_but_unloaded_model_is_cold(fake_broker):
    fake_broker.loaded = []
    out = resolve(fake_broker)
    assert {m["state"] for m in out["models"]} == {modelstate.COLD}


def test_a_model_that_is_not_installed_is_missing(fake_broker):
    """Blue versus red is the one distinction an operator acts on differently: an
    ``ollama pull`` versus just asking a question."""
    fake_broker.installed = ["bge-m3:latest"]
    fake_broker.loaded = ["bge-m3:latest"]
    out = resolve(fake_broker)
    assert state_of(out, "reasoning")["state"] == modelstate.MISSING
    assert state_of(out, "retrieval")["state"] == modelstate.LOADED


def test_a_queued_job_on_a_cold_model_is_warming(fake_broker):
    fake_broker.loaded = []
    fake_broker.jobs = [{"model": "qwen2.5:3b", "state": "waiting"}]
    assert state_of(resolve(fake_broker), "reasoning")["state"] == modelstate.WARMING


@pytest.mark.parametrize("job_state", ["waiting", "active"])
def test_both_live_job_states_count_as_warming(fake_broker, job_state):
    fake_broker.loaded = []
    fake_broker.jobs = [{"model": "qwen2.5:3b", "state": job_state}]
    assert state_of(resolve(fake_broker), "reasoning")["state"] == modelstate.WARMING


def test_a_finished_job_does_not_leave_a_model_warming(fake_broker):
    fake_broker.loaded = []
    fake_broker.jobs = [{"model": "qwen2.5:3b", "state": "done"}]
    assert state_of(resolve(fake_broker), "reasoning")["state"] == modelstate.COLD


def test_loaded_beats_warming(fake_broker):
    """ORDER MATTERS: a resident model with a job in flight is loaded-and-busy, not warming.
    Warming means specifically "a job is waiting on a model that is not resident yet"."""
    fake_broker.loaded = ["qwen2.5:3b"]
    fake_broker.jobs = [{"model": "qwen2.5:3b", "state": "active"}]
    assert state_of(resolve(fake_broker), "reasoning")["state"] == modelstate.LOADED


def test_the_implicit_latest_tag_is_tolerated(fake_broker):
    """``@embed`` resolves to ``bge-m3`` while Ollama reports ``bge-m3:latest``. Comparing with
    ``==`` would call a resident embedder missing."""
    fake_broker.role_table = [{"role": "embed", "resolved": "bge-m3"}]
    fake_broker.installed = ["bge-m3:latest"]
    fake_broker.loaded = ["bge-m3:latest"]
    out = resolve(fake_broker, [("retrieval", "Retrieval", "@embed")])
    assert out["models"][0]["model"] == "bge-m3"
    assert out["models"][0]["state"] == modelstate.LOADED


def test_a_role_resolves_to_its_concrete_model(fake_broker):
    out = resolve(fake_broker)
    reasoning = state_of(out, "reasoning")
    assert reasoning["role"] == "@smb-partner-rag"
    assert reasoning["model"] == "qwen2.5:3b"


def test_an_unresolvable_role_is_reported_missing_rather_than_guessed(fake_broker):
    """Honest: the chip goes red and still names the role that could not be resolved."""
    fake_broker.role_table = []
    out = resolve(fake_broker, [("reasoning", "LLM", "@smb-partner-rag")])
    assert out["models"][0]["model"] == "@smb-partner-rag"
    assert out["models"][0]["state"] == modelstate.MISSING


def test_a_size_scoped_glob_picks_the_highest_installed_match(fake_broker):
    fake_broker.installed = ["qwen2.5:3b", "qwen2.5:7b", "bge-m3:latest"]
    fake_broker.loaded = ["qwen2.5:7b"]
    out = resolve(fake_broker, [("reasoning", "LLM", "qwen2.5:*")])
    assert out["models"][0]["model"] == "qwen2.5:7b"
    assert out["models"][0]["state"] == modelstate.LOADED


def test_an_unreachable_broker_renders_rather_than_raising(monkeypatch, fake_broker):
    """A header must render even when the GPU layer is down."""
    def down(*_a, **_kw):
        raise broker.BrokerError("connection refused")

    monkeypatch.setattr(broker, "roles", down)
    out = resolve(fake_broker)
    assert out["broker"] == "unreachable"
    assert {m["state"] for m in out["models"]} == {modelstate.MISSING}
    assert [m["slot"] for m in out["models"]] == ["reasoning", "retrieval"]


def test_the_envelope_shape_is_the_cross_rail_one(fake_broker):
    """``broker`` plus ``models``, each entry slot/label/role/model/state/upstream. The older
    spelling ("broker_reachable" plus a boolean "resident") was renamed rather than kept
    alongside — two spellings of the same status is how the drift started.

    ``upstream`` joined the envelope when roles became delegatable, and is ALWAYS present —
    "local" for a role that runs on this box — so a consumer reads it unconditionally rather
    than treating absence as local, which is how an old bundle and a new one would disagree
    without saying so. The assertion stays exact on purpose: this envelope is generated from
    one template into every rail, and a key present in some copies and not others is the drift
    it exists to prevent."""
    out = resolve(fake_broker)
    assert set(out) == {"broker", "models"}
    assert all(set(m) == {"slot", "label", "role", "model", "state", "upstream"}
               for m in out["models"])
    # Always a concrete value, never None or "" — the chip renders it.
    assert all(m["upstream"] for m in out["models"])
    assert all(m["state"] in {modelstate.MISSING, modelstate.COLD,
                              modelstate.WARMING, modelstate.LOADED} for m in out["models"])
    assert "broker_reachable" not in out
