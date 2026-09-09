"""The role map itself: what counts as a role when roles.json is read back in.

roles.json is a hand-edited file, so it grows annotations. Every `_comment` in it used to come
back out of Settings.roles() as a real role — listed by GET /v1/roles, offered in the admin
picker, and audited at startup. A downstream 8 GB map reported 18 roles for 16 real ones, with
one annotation classified as an embedder because its prose contained the word "embed".

set_role() has always REFUSED to create such a name. Reading them in was the half that
disagreed, so these tests pin both ends against the one shared pattern.
"""
import json

import pytest

from app.config import DEFAULT_ROLES, BrokerSettings


def _settings(tmp_path, overlay: dict) -> BrokerSettings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "roles.json"
    path.write_text(json.dumps(overlay), encoding="utf-8")
    return BrokerSettings(roles_file=str(path))


def test_annotation_keys_are_not_roles(tmp_path):
    s = _settings(tmp_path, {
        "_comment": "this map is sized for an 8 GB card, embed stays resident",
        "_local_delta": "chat downsized from the shipped default",
        "chat": "gemma3:4b",
    })
    roles = s.roles()
    assert "_comment" not in roles
    assert "_local_delta" not in roles
    assert roles["chat"] == "gemma3:4b"


def test_annotations_do_not_inflate_the_role_count(tmp_path):
    """The reported symptom: 18 roles where the map defines 16."""
    annotated = _settings(tmp_path / "annotated", {
        "_comment": "x", "_local_delta": "y", "chat": "gemma3:4b"})
    plain = _settings(tmp_path / "plain", {"chat": "gemma3:4b"})
    assert len(annotated.roles()) == len(plain.roles()) == len(DEFAULT_ROLES)


def test_a_real_overlay_still_overrides_the_default(tmp_path):
    s = _settings(tmp_path, {"chat": "gemma3:4b"})
    assert s.roles()["chat"] == "gemma3:4b"
    assert DEFAULT_ROLES["chat"] != "gemma3:4b", "fixture must differ from the shipped default"


def test_set_role_rejects_what_roles_now_ignores(tmp_path):
    """Both ends of the same rule. If these ever disagree again, one of them is the bug."""
    s = _settings(tmp_path, {"chat": "gemma3:4b"})
    with pytest.raises(ValueError, match="invalid role name"):
        s.set_role("_comment", "gemma3:4b")


def test_hyphenated_and_numeric_role_names_survive(tmp_path):
    """The filter must not be so eager it drops legitimate names — the shipped map has
    'recipe-icon', 'chat-large', 'ai-voice-normalize'."""
    s = _settings(tmp_path, {"recipe-icon": "flux-schnell", "chat-large": "qwen3.6:27b"})
    roles = s.roles()
    assert roles["recipe-icon"] == "flux-schnell"
    assert roles["chat-large"] == "qwen3.6:27b"
