"""Regression tests for the bench path-traversal + identity fail-closed hardening (audit fixes)."""
import pytest

from ai_playground import config
from ai_playground.api import app as appmod
from ai_playground.bench import assets


def test_model_dir_rejects_parent_traversal():
    with pytest.raises(ValueError):
        assets.model_dir("..")


def test_model_dir_rejects_nested_traversal():
    with pytest.raises(ValueError):
        assets.model_dir("../../etc")


def test_model_dir_rejects_dot():
    with pytest.raises(ValueError):
        assets.model_dir(".")


def test_model_dir_allows_plain_id():
    p = assets.model_dir("bge-small-en-v1.5")
    assert p.name == "bge-small-en-v1.5"
    assert p.resolve().parent == config.MODELS_DIR.resolve()


def test_valid_model_id():
    assert appmod._valid_model_id("bge-small-en-v1.5")
    assert appmod._valid_model_id("ms-marco-minilm-l6")
    assert not appmod._valid_model_id("..")
    assert not appmod._valid_model_id("../x")
    assert not appmod._valid_model_id("a/b")
    assert not appmod._valid_model_id("a\\b")
    assert not appmod._valid_model_id("")


def test_safe_rel_path():
    assert appmod._safe_rel_path("onnx/model_quantized.onnx")
    assert appmod._safe_rel_path("tokenizer.json")
    assert not appmod._safe_rel_path("../secret")
    assert not appmod._safe_rel_path("/etc/passwd")
    assert not appmod._safe_rel_path("C:\\x")
    assert not appmod._safe_rel_path("a/../../b")
    assert not appmod._safe_rel_path("")
    assert not appmod._safe_rel_path(None)


def test_require_admin_fails_closed_without_header(monkeypatch):
    # Deployed topology (STANDALONE off): a header-less call must be rejected, not treated as admin.
    monkeypatch.setattr(config, "STANDALONE", False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        appmod.deps.require_admin(appmod.deps.Identity(None, False))
    assert e.value.status_code == 403


def test_require_admin_allows_admin_identity(monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    ident = appmod.deps.Identity("system", True)
    assert appmod.deps.require_admin(ident) is ident
