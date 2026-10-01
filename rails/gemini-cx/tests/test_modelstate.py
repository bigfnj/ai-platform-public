"""The four-state model chips, and the two rules that are always the first to be "simplified".

modelstate.py is generated from tools/rail_template.py and every rail's copy must match byte
for byte, so these tests are not about this rail's opinion — they are about the cross-rail
visual language the chips speak. A rail that computes "warming" differently is a lie told in
a colour the user has learned to trust.

The two rules with a documented history of being broken:

* **``loaded`` is checked before ``warming``.** A resident model with a job in flight is
  loaded-and-busy, not warming. Orange means one thing only: a job is waiting on a model that
  is not resident yet.
* **Tag tolerance is not optional.** Ollama reports an untagged pull as ``:latest``, so
  ``@embed`` resolves to ``bge-m3`` while the loaded list says ``bge-m3:latest``. Comparing
  with ``==`` turns a resident embedder red.

The broker is stubbed everywhere; nothing here opens a socket.
"""
from __future__ import annotations

import pytest

from gemini_cx import api, broker, config, modelstate

SLOTS = [("reasoning", "LLM", "@gemini-cx-rag"), ("retrieval", "Retrieval", "@embed")]


@pytest.fixture()
def gpu(monkeypatch):
    """Stand up a fake broker: which roles resolve where, what is installed, what is hot."""
    def setup(*, roles=None, installed=(), loaded=(), jobs=()):
        monkeypatch.setattr(broker, "roles", lambda: [
            {"role": r, "resolved": m} for r, m in (roles or {}).items()])
        monkeypatch.setattr(broker, "models", lambda: [{"name": n} for n in installed])
        monkeypatch.setattr(broker, "status", lambda: {
            "loaded": [{"name": n} for n in loaded], "jobs": list(jobs)})
    return setup


def states(specs=SLOTS):
    return {m["slot"]: m["state"] for m in modelstate.resolve(specs)["models"]}


# --- the four states --------------------------------------------------------------------------

def test_a_model_that_is_not_installed_is_missing(gpu):
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"}, installed=["bge-m3"])
    assert states() == {"reasoning": "missing", "retrieval": "cold"}


def test_installed_but_not_resident_is_cold(gpu):
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"},
        installed=["qwen3:4b", "bge-m3"])
    assert states() == {"reasoning": "cold", "retrieval": "cold"}


def test_resident_is_loaded(gpu):
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"},
        installed=["qwen3:4b", "bge-m3"], loaded=["qwen3:4b", "bge-m3"])
    assert states() == {"reasoning": "loaded", "retrieval": "loaded"}


@pytest.mark.parametrize("job_state", ["waiting", "active"])
def test_a_queued_job_on_a_cold_model_is_warming(gpu, job_state):
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"},
        installed=["qwen3:4b", "bge-m3"], loaded=["bge-m3"],
        jobs=[{"model": "qwen3:4b", "state": job_state}])
    assert states() == {"reasoning": "warming", "retrieval": "loaded"}


def test_a_finished_job_does_not_leave_a_model_warming(gpu):
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"},
        installed=["qwen3:4b", "bge-m3"],
        jobs=[{"model": "qwen3:4b", "state": "done"}])
    assert states()["reasoning"] == "cold"


def test_loaded_beats_warming(gpu):
    """ORDER MATTERS: a resident model answering a question is loaded-and-busy. Reporting it
    as warming would make the chip flicker orange for the whole of every answer."""
    gpu(roles={"gemini-cx-rag": "qwen3:4b", "embed": "bge-m3"},
        installed=["qwen3:4b", "bge-m3"], loaded=["qwen3:4b"],
        jobs=[{"model": "qwen3:4b", "state": "active"}])
    assert states()["reasoning"] == "loaded"


# --- tag tolerance ----------------------------------------------------------------------------

def test_an_untagged_role_matches_an_implicit_latest(gpu):
    """@embed resolves to bge-m3; Ollama reports bge-m3:latest. Same model."""
    gpu(roles={"embed": "bge-m3"}, installed=["bge-m3:latest"], loaded=["bge-m3:latest"])
    assert states([("retrieval", "Retrieval", "@embed")]) == {"retrieval": "loaded"}


def test_a_latest_tagged_role_matches_an_untagged_install(gpu):
    gpu(roles={"embed": "bge-m3:latest"}, installed=["bge-m3"])
    assert states([("retrieval", "Retrieval", "@embed")]) == {"retrieval": "cold"}


def test_different_sizes_of_the_same_family_are_not_the_same_model(gpu):
    """Tag tolerance must not turn into name tolerance: qwen3:4b is not qwen3:8b."""
    gpu(roles={"gemini-cx-rag": "qwen3:4b"}, installed=["qwen3:8b"])
    assert states([("reasoning", "LLM", "@gemini-cx-rag")]) == {"reasoning": "missing"}


def test_a_latest_suffixed_tag_does_not_make_two_sizes_the_same(gpu):
    """The regression the narrow tolerance exists for.

    The old test was ``"latest" in (a + b)`` -- the word anywhere in the two names
    CONCATENATED -- and then compared only the part before the colon. So an installed
    ``qwen3:8b-latest`` satisfied a role pinned to ``qwen3:4b``: a resident 8b turning a 4b
    chip green, which is exactly what the four-state contract exists to prevent. The tag is
    stripped only when it IS ``:latest``, never when it merely contains the word.
    """
    gpu(roles={"gemini-cx-rag": "qwen3:4b"}, installed=["qwen3:8b-latest"])
    assert states([("reasoning", "LLM", "@gemini-cx-rag")]) == {"reasoning": "missing"}


# --- reference resolution ----------------------------------------------------------------------

def test_the_concrete_model_behind_a_role_is_reported_alongside_it(gpu):
    """The chip shows the role; the tooltip shows what the admin panel actually pointed it at."""
    gpu(roles={"gemini-cx-rag": "qwen3:4b"}, installed=["qwen3:4b"])
    slot = modelstate.resolve([("reasoning", "LLM", "@gemini-cx-rag")])["models"][0]
    assert slot["role"] == "@gemini-cx-rag"
    assert slot["model"] == "qwen3:4b"
    assert slot["label"] == "LLM"


def test_an_unresolvable_role_reads_as_missing_which_is_honest(gpu):
    gpu(roles={}, installed=["qwen3:4b"])
    slot = modelstate.resolve([("reasoning", "LLM", "@gemini-cx-rag")])["models"][0]
    assert slot["model"] == "@gemini-cx-rag"
    assert slot["state"] == "missing"


def test_a_size_scoped_glob_picks_the_highest_install(gpu):
    gpu(roles={"gemini-cx-rag": "qwen3:*b"}, installed=["qwen3:4b", "qwen3:8b"])
    slot = modelstate.resolve([("reasoning", "LLM", "@gemini-cx-rag")])["models"][0]
    assert slot["model"] == "qwen3:8b"
    assert slot["state"] == "cold"


# --- the broker being down ------------------------------------------------------------------------

def test_an_unreachable_broker_renders_rather_than_raising(monkeypatch):
    """A header must draw even when the GPU layer is down, so resolve() never raises."""
    def down(*_a, **_kw):
        raise broker.BrokerError("connection refused")

    for name in ("roles", "models", "status"):
        monkeypatch.setattr(broker, name, down)
    got = modelstate.resolve(SLOTS)
    assert got["broker"] == "unreachable"
    assert [m["state"] for m in got["models"]] == ["missing", "missing"]
    assert [m["model"] for m in got["models"]] == ["@gemini-cx-rag", "@embed"]


def test_a_reachable_broker_says_so(gpu):
    gpu(roles={"embed": "bge-m3"}, installed=["bge-m3"])
    assert modelstate.resolve(SLOTS)["broker"] == "ok"


# --- what this rail actually wires up ----------------------------------------------------------------

def test_the_rail_declares_exactly_the_two_slots_its_manifest_does():
    """rail.json declares reasoning + retrieval, and the admin panel keys off the slot ids."""
    assert [(slot, label) for slot, label, _ in api.MODEL_SLOTS] == [
        ("reasoning", "LLM"), ("retrieval", "Retrieval")]


def test_both_slots_point_at_roles_not_pinned_models():
    """RC013: a pinned default silently ignores Admin -> Rails, and nothing reports it."""
    assert [ref for _, _, ref in api.MODEL_SLOTS] == [config.RAG_MODEL, config.EMBED_MODEL]
    assert all(ref.startswith("@") for _, _, ref in api.MODEL_SLOTS)
