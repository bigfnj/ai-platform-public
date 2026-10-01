"""Runtime settings — the env contract, the standalone flag, and the two prompts.

The same code has to run standalone (broker on localhost, data under ``./data``) and in the
container (broker via ``host.docker.internal``, mutable state on a mounted volume, seed baked
read-only). Everything that differs between those two is an environment variable read here, so
a rename in a compose file that nothing reads is a silent misconfiguration.

The settings are module constants snapshotted at import, so these tests load a *second,
private* copy of ``config.py`` under a throwaway name rather than reloading the real module —
reloading it in place would blow away the tmp-path redirection the whole suite depends on.
"""
from __future__ import annotations

import importlib.util

import pytest

from smb_partner import api, config

ENV_KEYS = ["SMB_PARTNER_DATA_DIR", "SMB_PARTNER_DB", "SMB_PARTNER_UPLOADS_DIR",
            "SMB_PARTNER_AUDIO_DIR", "SMB_PARTNER_SEED_DIR", "SMB_PARTNER_PORT",
            "SMB_PARTNER_RAG_MODEL", "SMB_PARTNER_EMBED_MODEL", "SMB_PARTNER_TOP_K",
            "SMB_PARTNER_MAX_TOKENS", "SMB_PARTNER_VOICE_MAX_TOKENS",
            "SMB_PARTNER_VOICE_BACKEND", "SMB_PARTNER_VOICE_LANG", "SMB_PARTNER_VOICE_SPEAKER",
            "SMB_PARTNER_WARM_ON_BOOT", "PLATFORM_STANDALONE", "SMB_PARTNER_STANDALONE"]


def fresh_config(monkeypatch, **env):
    """A private copy of ``config.py`` evaluated against the given environment."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("smb_partner_config_probe", config.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the standalone escape hatch ------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
def test_platform_standalone_enables_the_escape_hatch(monkeypatch, value):
    assert fresh_config(monkeypatch, PLATFORM_STANDALONE=value).STANDALONE is True


@pytest.mark.parametrize("value", ["", "0", "no", "false", "off", "maybe"])
def test_anything_else_leaves_the_gate_closed(monkeypatch, value):
    assert fresh_config(monkeypatch, PLATFORM_STANDALONE=value).STANDALONE is False


def test_an_absent_flag_is_not_standalone(monkeypatch):
    """The whole identity gate hinges on this: a container that never sets the variable must
    fail closed, not open."""
    assert fresh_config(monkeypatch).STANDALONE is False


def test_the_retired_per_rail_flag_no_longer_works(monkeypatch):
    """``SMB_PARTNER_STANDALONE`` was one of five per-rail names replaced by the single
    platform-wide flag. Honouring it again would reintroduce a rail-specific way to disable
    identity that nothing else on the platform knows to look for."""
    assert fresh_config(monkeypatch, SMB_PARTNER_STANDALONE="1").STANDALONE is False


# --- paths ------------------------------------------------------------------------------------

def test_the_data_paths_derive_from_one_root(monkeypatch, tmp_path):
    """One variable moves the whole mutable state onto the mounted volume."""
    fresh = fresh_config(monkeypatch, SMB_PARTNER_DATA_DIR=str(tmp_path / "srv"))
    assert fresh.DATA_DIR == tmp_path / "srv"
    assert fresh.DB_PATH == str(tmp_path / "srv" / "smb_partner.db")
    assert fresh.UPLOADS_DIR == tmp_path / "srv" / "uploads"
    assert fresh.AUDIO_CACHE_DIR == tmp_path / "srv" / "audio"


def test_each_path_can_still_be_overridden_on_its_own(monkeypatch, tmp_path):
    fresh = fresh_config(monkeypatch, SMB_PARTNER_DATA_DIR=str(tmp_path / "srv"),
                         SMB_PARTNER_DB=str(tmp_path / "elsewhere.db"))
    assert fresh.DB_PATH == str(tmp_path / "elsewhere.db")


def test_the_seed_knowledge_base_defaults_next_to_the_package(monkeypatch):
    """Baked read-only into the image; overridable for the container's /srv/seed mount."""
    fresh = fresh_config(monkeypatch)
    assert fresh.SEED_KB_DIR.name == "knowledge-base"
    assert fresh.SEED_KB_DIR.parent.name == "seed"


def test_ensure_dirs_creates_the_writable_tree_and_is_idempotent(monkeypatch, tmp_path):
    fresh = fresh_config(monkeypatch, SMB_PARTNER_DATA_DIR=str(tmp_path / "srv"))
    fresh.ensure_dirs()
    fresh.ensure_dirs()
    assert fresh.DATA_DIR.is_dir()
    assert fresh.UPLOADS_DIR.is_dir()
    assert fresh.AUDIO_CACHE_DIR.is_dir()
    # The read-only seed is never created — a missing mount must be visible, not papered over.
    assert not fresh.SEED_KB_DIR.is_relative_to(fresh.DATA_DIR)


# --- models and budgets -------------------------------------------------------------------------

def test_the_two_resident_models_default_to_roles_not_concrete_names(monkeypatch):
    """Role indirection is what lets the broker swap the underlying model without a rail
    rebuild, and it is what ``modelstate`` resolves for the chips."""
    fresh = fresh_config(monkeypatch)
    assert fresh.RAG_MODEL == "@smb-partner-rag"
    assert fresh.EMBED_MODEL == "@embed"


def test_the_models_are_overridable_for_a_pinned_deployment(monkeypatch):
    fresh = fresh_config(monkeypatch, SMB_PARTNER_RAG_MODEL="qwen2.5:7b",
                         SMB_PARTNER_EMBED_MODEL="nomic-embed-text")
    assert fresh.RAG_MODEL == "qwen2.5:7b"
    assert fresh.EMBED_MODEL == "nomic-embed-text"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
def test_the_boot_warm_turns_on_only_for_explicit_truth(monkeypatch, value):
    """SMB_PARTNER_WARM_ON_BOOT parks both models on the card for 30 minutes at every container
    start, so it has to be asked for in so many words."""
    assert fresh_config(monkeypatch, SMB_PARTNER_WARM_ON_BOOT=value).WARM_ON_BOOT is True


@pytest.mark.parametrize("value", ["", "0", "no", "false", "off", "maybe"])
def test_anything_else_leaves_the_boot_warm_off(monkeypatch, value):
    assert fresh_config(monkeypatch, SMB_PARTNER_WARM_ON_BOOT=value).WARM_ON_BOOT is False


def test_an_absent_warm_flag_loads_nothing_at_boot(monkeypatch):
    """The default the flag exists for: a container nobody told otherwise loads nothing at
    boot, and its first question pays one cold load instead."""
    assert fresh_config(monkeypatch).WARM_ON_BOOT is False


def test_the_api_slots_track_the_configured_models(monkeypatch):
    """The header chips must describe the models this process will actually call."""
    assert [ref for _, _, ref in api.MODEL_SLOTS] == [config.RAG_MODEL, config.EMBED_MODEL]


def test_the_numeric_settings_parse_as_numbers(monkeypatch):
    fresh = fresh_config(monkeypatch, SMB_PARTNER_PORT="9001", SMB_PARTNER_TOP_K="8",
                         SMB_PARTNER_MAX_TOKENS="1200",
                         SMB_PARTNER_VOICE_MAX_TOKENS="180")
    assert (fresh.PORT, fresh.TOP_K, fresh.MAX_TOKENS, fresh.VOICE_MAX_TOKENS) == \
        (9001, 8, 1200, 180)


def test_the_default_port_matches_the_manifest(monkeypatch):
    assert fresh_config(monkeypatch).PORT == 8870


def test_a_spoken_answer_gets_a_tighter_budget_than_a_written_one(monkeypatch):
    """A partner on a phone between meetings will not listen to 700 tokens."""
    fresh = fresh_config(monkeypatch)
    assert fresh.VOICE_MAX_TOKENS < fresh.MAX_TOKENS


def test_voice_settings_are_normalised(monkeypatch):
    fresh = fresh_config(monkeypatch, SMB_PARTNER_VOICE_BACKEND="  BROKER ",
                         SMB_PARTNER_VOICE_SPEAKER="  af_heart  ")
    assert fresh.VOICE_BACKEND == "broker"
    assert fresh.VOICE_SPEAKER == "af_heart"


# --- prompts --------------------------------------------------------------------------------------

def test_the_screen_prompt_demands_grounding_and_inline_citations():
    assert "ONLY from the provided context" in config.SYSTEM_PROMPT
    assert "[1], [2]" in config.SYSTEM_PROMPT
    assert "say so plainly" in config.SYSTEM_PROMPT


def test_the_spoken_prompt_keeps_the_same_grounding_contract_without_the_markup():
    """Same contract, shaped for the ear: a synthesizer reading "[1]" aloud is the failure."""
    assert "ONLY from the provided context" in config.VOICE_SYSTEM_PROMPT
    assert "no markdown" in config.VOICE_SYSTEM_PROMPT
    assert "no inline citations" in config.VOICE_SYSTEM_PROMPT
    assert config.VOICE_SYSTEM_PROMPT != config.SYSTEM_PROMPT
