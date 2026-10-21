"""The role map itself: what counts as a role when roles.json is read back in.

roles.json is a hand-edited file, so it grows annotations. Every `_comment` in it used to come
back out of Settings.roles() as a real role — listed by GET /v1/roles, offered in the admin
picker, and audited at startup. A downstream 8 GB map reported 18 roles for 16 real ones, with
one annotation classified as an embedder because its prose contained the word "embed".

set_role() has always REFUSED to create such a name. Reading them in was the half that
disagreed, so these tests pin both ends against the one shared pattern.
"""
import json
from pathlib import Path

import pytest

from app.config import DEFAULT_ROLES, BrokerSettings

# tools/publish.py builds the public snapshot and is withheld from it (publish.json
# paths.exclude). It also de-wires the withheld rails' roles out of DEFAULT_ROLES and roles.json,
# so the two tests marked below, which state facts about the full private fleet, cannot hold in
# a published tree: every default left there is also in the overlay, and ROLE_RAIL still names
# the withheld rails' roles. Guarded on the FILE, as tools/tests/test_rail_smoke_roles.py is.
_FULL_FLEET_ONLY = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[3] / "tools" / "publish.py").is_file(),
    reason="tools/publish.py is absent, so this is a published snapshot, where the withheld "
           "rails' roles were de-wired from DEFAULT_ROLES and roles.json. This check runs in "
           "the private repo.",
)


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


# --- the shipped maps, checked statically ------------------------------------------------------
# These replace a runtime audit line. roles() always merges DEFAULT_ROLES underneath the overlay,
# so the only way one of these can be wrong is for a SHIPPED file to be wrong -- which a test
# catches at build time rather than at somebody's startup, and without adding noise to every
# small synthetic map in test_role_audit.py.

def _shipped(name: str) -> dict:
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_every_shipped_overlay_role_has_a_default_backstop():
    """THE OVERLAY MAY ONLY EVER REPOINT, NEVER INTRODUCE.

    `roles()` merges DEFAULT_ROLES with the overlay, so a role present ONLY in roles.json
    resolves fine here and resolves to the literal string on any box whose overlay is missing
    or malformed -- which Ollama then answers with a 404 the broker wraps as a 502. That is
    exactly what @ai-playground did for weeks: it lived in roles.json alone.
    """
    from app.config import DEFAULT_ROLES
    for path in ("services/broker/roles.json", "deploy/installer/roles.lean.json"):
        overlay = {k: v for k, v in _shipped(path).items() if not k.startswith("_")}
        orphans = sorted(set(overlay) - set(DEFAULT_ROLES))
        assert not orphans, f"{path} introduces roles with no DEFAULT_ROLES backstop: {orphans}"


@_FULL_FLEET_ONLY
def test_a_default_role_absent_from_the_overlay_is_legal():
    """The OTHER direction, pinned so nobody 'fixes' it into symmetry.

    overlay_roles() exists so the startup audit can say whether a bad role was configured here
    or inherited -- the difference between "fix your roles.json" and "you never had one".
    Syncing both maps to equality would destroy the only signal that tells those apart.
    """
    from app.config import DEFAULT_ROLES
    overlay = {k: v for k, v in _shipped("services/broker/roles.json").items()
               if not k.startswith("_")}
    assert set(DEFAULT_ROLES) - set(overlay), (
        "every default is now also in the overlay; the provenance signal is gone")


# Roles that are a model CLASS, not one rail's slot. Every other default is some rail's own.
_GENERIC_ROLES = {"chat", "chat-fast", "chat-large", "reasoning", "code", "vision", "embed"}


def test_every_per_rail_default_role_names_its_rail():
    """The other direction. A per-rail role with no ROLE_RAIL entry runs fine and is labelled
    `source: null` in the queue, so the one thing the queue is for (which rail is waiting)
    goes blank. Five roles had drifted that way. Holds in a published tree too: there the
    defaults only shrink."""
    from app.broker import ROLE_RAIL
    unnamed = sorted(set(DEFAULT_ROLES) - _GENERIC_ROLES - set(ROLE_RAIL))
    assert not unnamed, f"per-rail roles with no rail label: {unnamed}"


# A role more than one rail uses, or one a tile with no manifest of its own shares (IEP Present
# Levels is edu-suite's second tile and calls @iep beside iep-goals). Its label speaks for all of
# them, so it is held only to EXISTING, not to one manifest's label.
_SHARED_ROLES = {"iep", "edu", "openmaic"}


def test_every_role_a_rail_manifest_declares_names_its_rail():
    """Held to the manifests, so ROLE_RAIL cannot fall behind a new rail again: co-worker,
    smb-partner, gemini-cx, servicenow and openmaic had all shipped with no entry, and their
    queued jobs read `source: null`. A role one manifest declares carries that manifest's
    label; a shared one only has to be present. Runs in a published tree too, over the
    manifests that ship there."""
    from app.broker import ROLE_RAIL
    rails = Path(__file__).resolve().parents[3] / "rails"
    by_role: dict[str, set[str]] = {}
    for manifest in sorted(rails.glob("*/rail.json")):
        m = json.loads(manifest.read_text(encoding="utf-8"))
        for slot in m.get("model_slots") or []:
            role = slot.get("role")
            if role and role not in _GENERIC_ROLES:
                by_role.setdefault(role, set()).add(m.get("label") or m["id"])
    assert len(by_role) >= 5, f"read only {sorted(by_role)} from {rails}; the check is inert"
    problems = []
    for role, labels in sorted(by_role.items()):
        got = ROLE_RAIL.get(role)
        if got is None:
            problems.append(f"@{role}: no ROLE_RAIL entry (declared by {sorted(labels)})")
        elif role not in _SHARED_ROLES and len(labels) == 1 and got not in labels:
            problems.append(f"@{role}: ROLE_RAIL says {got!r}, its manifest says {min(labels)!r}")
    assert not problems, "\n".join(problems)


@_FULL_FLEET_ONLY
def test_every_role_a_rail_calls_exists_in_the_default_map():
    """ROLE_RAIL names the roles a RAIL actually calls. One missing from DEFAULT_ROLES is a
    hard UnknownRoleError at first use, on the rail's first request."""
    from app.broker import ROLE_RAIL
    from app.config import DEFAULT_ROLES
    missing = sorted(set(ROLE_RAIL) - set(DEFAULT_ROLES))
    assert not missing, f"rails call roles that DEFAULT_ROLES does not define: {missing}"
