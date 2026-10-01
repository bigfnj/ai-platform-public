"""Runtime settings, and the grounding contract that lives in them.

Two different kinds of thing are checked here, both of which fail quietly.

**The system prompt is the product.** This rail's whole claim is that it refuses figures it
cannot cite and reports GA, Preview, coming soon and announced-only as four different answers
rather than collapsing them into "available". None of that is code — it is four sentences in
``config.SYSTEM_PROMPT``, one careless edit away from a rail that sounds identical and invents
prices. There is no other guard on it.

**The env overrides are the deployment.** The same module runs standalone (broker on
localhost, data under ./data) and in the container (mutable state on a mounted volume, seed
baked read-only at /srv/seed). Every one of those is an ``os.environ.get`` read at import, so
a renamed variable does not raise — it silently keeps the dev default, and the container
writes its index somewhere that is not the volume.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from gemini_cx import config, voice

RAIL_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((RAIL_ROOT / "rail.json").read_text(encoding="utf-8"))


# --- the grounding contract ------------------------------------------------------------------

def test_the_prompt_admits_the_corpus_can_come_up_short():
    """Rule 1. The alternative is a rail that fills a gap with general knowledge about Google,
    which is indistinguishable from a grounded answer to the person reading it."""
    prompt = config.SYSTEM_PROMPT
    assert "Answer ONLY from the provided context" in prompt
    assert "Never fill a gap with general knowledge" in prompt


def test_the_prompt_forbids_inventing_a_figure():
    """Rule 2, and the reason this rail exists at all: Google has published almost no GECX
    pricing, and an invented per-seat number is a lost deal."""
    prompt = config.SYSTEM_PROMPT
    assert "Never invent a price, percentage, latency figure, quota, or language list" in prompt
    assert "unpublished" in prompt


@pytest.mark.parametrize("status", ["GA", "Preview", "coming", "announced-only"])
def test_the_prompt_names_all_four_status_levels(status):
    assert status in config.SYSTEM_PROMPT


def test_the_prompt_forbids_collapsing_status_into_available():
    """Rule 3, quoted almost verbatim from the corpus authoring contract. GECX's own
    marketing collapses these; the rail must not."""
    assert "must not be collapsed into" in config.SYSTEM_PROMPT
    assert "'available'" in config.SYSTEM_PROMPT


def test_the_prompt_names_the_three_confusable_pairs():
    """Rule 4. Retrieval hands the model one chunk without its neighbours, so it fails toward
    the most lexically similar answer unless it is told which question it is answering."""
    prompt = config.SYSTEM_PROMPT
    assert "40+ text languages vs 10" in prompt
    assert "Gemini Enterprise vs Gemini Enterprise for CX" in prompt
    assert "handoff rules" in prompt


def test_the_prompt_asks_for_inline_citations():
    """The UI renders a numbered source list beside the answer; [1]/[2] is what ties them."""
    assert "Cite sources inline as [1]" in config.SYSTEM_PROMPT


# --- models -------------------------------------------------------------------------------------

def test_the_two_models_are_roles_not_pinned_names():
    """RC013: a pinned in-code default silently ignores Admin -> Rails, and a compose-only
    fix leaves standalone dev pinned — which is how that bug survived its last cleanup."""
    assert config.RAG_MODEL.startswith("@")
    assert config.EMBED_MODEL.startswith("@")


def test_the_roles_are_the_ones_the_manifest_declares():
    declared = {s["env"]: "@" + s["role"] for s in MANIFEST["model_slots"]}
    assert config.RAG_MODEL == declared["GEMINI_CX_RAG_MODEL"]
    assert config.EMBED_MODEL == declared["GEMINI_CX_EMBED_MODEL"]


def test_the_embedder_shares_the_generic_retrieval_role():
    """@embed is the platform-wide embedder. Pinning a private one here would hold a second
    embedder resident for no benefit, on a card that is already the constraint."""
    assert config.EMBED_MODEL == "@embed"


# --- ports, sizes and the seam defaults --------------------------------------------------------

def test_the_port_matches_the_manifest():
    """The gateway routes to this port; a disagreement is a rail the shell cannot reach."""
    assert config.PORT == MANIFEST["ports"]["backend"]


def test_retrieval_and_answer_budgets_are_sane():
    assert 1 <= config.TOP_K <= 20
    assert 200 <= config.MAX_TOKENS <= 4096


def test_the_voice_backend_default_is_one_the_seam_understands():
    assert config.VOICE_BACKEND in voice.BACKENDS


def test_state_and_seed_are_separate_trees():
    """Mutable state is a mounted volume; the corpus is baked read-only into the image. If
    these ever nest, an ingest writes into the read-only mount."""
    assert Path(config.DB_PATH).parent == config.DATA_DIR
    assert config.UPLOADS_DIR.parent == config.DATA_DIR
    assert config.DATA_DIR not in config.SEED_KB_DIR.parents


def test_the_seed_default_points_at_the_corpus_that_ships():
    assert config.SEED_KB_DIR == RAIL_ROOT / "seed" / "knowledge-base"
    assert config.SEED_KB_DIR.is_dir()


# --- ensure_dirs --------------------------------------------------------------------------------

def test_ensure_dirs_creates_the_volume_layout(tmp_path, monkeypatch):
    """The container mounts an EMPTY volume at /srv/var, so nothing below it exists on the
    first boot and store.init() would fail on the very first connect."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    config.ensure_dirs()
    assert (tmp_path / "var").is_dir()
    assert (tmp_path / "var" / "uploads").is_dir()


def test_ensure_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    config.ensure_dirs()
    config.ensure_dirs()
    assert (tmp_path / "var" / "uploads").is_dir()


# --- env overrides -----------------------------------------------------------------------------

def test_the_container_env_actually_overrides_the_dev_defaults(monkeypatch, tmp_path):
    """Every setting is read once at import. A renamed variable does not raise: the rail
    boots on its dev defaults and writes the index outside the mounted volume, where the next
    container start cannot find it."""
    env = {
        "GEMINI_CX_DATA_DIR": str(tmp_path / "srv" / "var"),
        "GEMINI_CX_DB": str(tmp_path / "srv" / "var" / "gemini_cx.db"),
        "GEMINI_CX_UPLOADS_DIR": str(tmp_path / "srv" / "var" / "uploads"),
        "GEMINI_CX_SEED_DIR": str(tmp_path / "srv" / "seed"),
        "GEMINI_CX_PORT": "9999",
        "GEMINI_CX_RAG_MODEL": "@other-rag",
        "GEMINI_CX_EMBED_MODEL": "@other-embed",
        "GEMINI_CX_TOP_K": "11",
        "GEMINI_CX_MAX_TOKENS": "1234",
        "GEMINI_CX_VOICE_BACKEND": "BROWSER",
        "GEMINI_CX_VOICE_SPEAKER": "af_other",
        "GEMINI_CX_WARM_ON_BOOT": "1",
        "PLATFORM_STANDALONE": "true",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    try:
        importlib.reload(config)
        assert config.DATA_DIR == Path(env["GEMINI_CX_DATA_DIR"])
        assert config.DB_PATH == env["GEMINI_CX_DB"]
        assert config.UPLOADS_DIR == Path(env["GEMINI_CX_UPLOADS_DIR"])
        assert config.SEED_KB_DIR == Path(env["GEMINI_CX_SEED_DIR"])
        assert config.PORT == 9999
        assert config.RAG_MODEL == "@other-rag"
        assert config.EMBED_MODEL == "@other-embed"
        assert config.TOP_K == 11
        assert config.MAX_TOKENS == 1234
        assert config.VOICE_BACKEND == "browser"      # normalised, so 'BROWSER' still resolves
        assert config.VOICE_SPEAKER == "af_other"
        assert config.WARM_ON_BOOT is True
        assert config.STANDALONE is True
    finally:
        for k in env:
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True),
    ("0", False), ("false", False), ("no", False), ("", False), ("maybe", False)])
def test_the_standalone_escape_hatch_only_opens_for_explicit_truth(raw, expected, monkeypatch):
    """PLATFORM_STANDALONE turns the 401 off. Anything ambiguous must read as closed."""
    monkeypatch.setenv("PLATFORM_STANDALONE", raw)
    try:
        importlib.reload(config)
        assert config.STANDALONE is expected
    finally:
        monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
        importlib.reload(config)


def test_standalone_is_closed_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    importlib.reload(config)
    assert config.STANDALONE is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True),
    ("0", False), ("false", False), ("no", False), ("", False), ("maybe", False)])
def test_the_boot_warm_only_turns_on_for_explicit_truth(raw, expected, monkeypatch):
    """GEMINI_CX_WARM_ON_BOOT parks both models on the card for 30 minutes at every container
    start. Anything ambiguous must read as off."""
    monkeypatch.setenv("GEMINI_CX_WARM_ON_BOOT", raw)
    try:
        importlib.reload(config)
        assert config.WARM_ON_BOOT is expected
    finally:
        monkeypatch.delenv("GEMINI_CX_WARM_ON_BOOT", raising=False)
        importlib.reload(config)


def test_the_boot_warm_is_off_when_the_variable_is_absent(monkeypatch):
    """The default the flag exists for: a container nobody told otherwise loads nothing at
    boot, and its first question pays one cold load instead."""
    monkeypatch.delenv("GEMINI_CX_WARM_ON_BOOT", raising=False)
    importlib.reload(config)
    assert config.WARM_ON_BOOT is False
