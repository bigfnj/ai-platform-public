"""docs/rail-manifest.schema.json - every shipped rail.json must validate against it.

Nothing else reads the schema. The checkers read manifests directly, so the schema drifted
unnoticed until 11 of 16 manifests failed it: env_prefix allowed one word (CO_WORKER_ did not
match), bouquet's "A + B" slot env was rejected, and broker_module, which rail_template.py
generates an import from, was not declared at all. Editors, and anyone writing a new rail
from the schema, were being told that the real manifests were wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# A skip, not an import error: an undeclared jsonschema made the whole `core` target fail to
# COLLECT on a fresh venv (one pytest call runs broker, gateway, core and tools together), so one
# missing package silenced several hundred unrelated tests. `run-tests.ps1 -Doctor` reports it.
jsonschema = pytest.importorskip("jsonschema", reason="install jsonschema; run-tests.ps1 -Doctor")

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "docs" / "rail-manifest.schema.json").read_text(encoding="utf-8"))
MANIFESTS = sorted(REPO.glob("rails/*/rail.json"))


def validator():
    cls = jsonschema.validators.validator_for(SCHEMA)
    cls.check_schema(SCHEMA)
    return cls(SCHEMA)


def test_there_are_manifests_to_check():
    assert MANIFESTS, "no rails/*/rail.json found; the glob or the layout changed"


@pytest.mark.parametrize("man", MANIFESTS, ids=[m.parent.name for m in MANIFESTS])
def test_manifest_validates(man):
    errors = sorted(validator().iter_errors(json.loads(man.read_text(encoding="utf-8"))),
                    key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors)


def test_unknown_keys_are_still_rejected():
    """The fixes above loosened patterns; they must not have loosened additionalProperties. A
    REAL manifest plus one stray key: a toy document also fails `required`, so it passed even
    with additionalProperties deleted."""
    real = json.loads(MANIFESTS[0].read_text(encoding="utf-8"))
    assert validator().is_valid(real)
    assert not validator().is_valid({**real, "no_such_key": 1})


@pytest.mark.parametrize("path,ok", [
    ("/logos/", True), ("/logo-horizontal.png", True), ("/logo@2x.png", True),
    ("/_next/static/", True), ("/icons/apple-touch-icon", True), ("/a..b", True),
    ("/..", False), ("/.", False), ("/../etc/passwd", False), ("/a/../b", False),
    ("/a/./b", False), ("logos/", False), ("/", False), ("//x", False),
])
def test_root_assets_pattern(path, ok):
    """A trailing slash is a directory prefix, anything else an exact path (the gateway's own
    rule). Dot segments are refused: the gateway forwards these paths unchanged."""
    pat = SCHEMA["properties"]["root_assets"]["items"]["pattern"]
    assert bool(jsonschema.validators.validator_for(SCHEMA)(
        {"type": "string", "pattern": pat}).is_valid(path)) is ok


@pytest.mark.parametrize("prefix,ok", [
    ("CO_WORKER_", True), ("SMB_PARTNER_", True), ("OPENMAIC_", True),
    ("CO__WORKER_", False), ("CO_WORKER", False), ("co_worker_", False),
])
def test_env_prefix_pattern(prefix, ok):
    pat = SCHEMA["properties"]["env_prefix"]["pattern"]
    assert bool(jsonschema.validators.validator_for(SCHEMA)(
        {"type": "string", "pattern": pat}).is_valid(prefix)) is ok
