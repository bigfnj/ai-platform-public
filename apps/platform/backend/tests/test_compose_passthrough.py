"""Every gateway setting deploy/.env.example offers actually reaches the gateway container.

A deployment doc told the operator to set PLATFORM_SESSION_TTL_HOURS, and
deploy/docker-compose.yml never passed it to the gateway, so the value changed nothing; the
model-pool scan's PLATFORM_DESCRIBE_MODEL / _CAP had the same gap. Compose only forwards what
its `environment:` block names, so an .env.example line the gateway reads but compose does not
list is a setting that silently does nothing on the full stack.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from platform_gateway_app.config import GatewaySettings

# Skipped, and SAID to be skipped, where pyyaml is absent, as tools/tests/test_compose_env_scope.py
# does: compose structure needs a YAML parser, and a regex over YAML would be a second parser.
yaml = pytest.importorskip("yaml", reason="install pyyaml; compose structure needs a YAML parser")

REPO = Path(__file__).resolve().parents[4]
GATEWAY_SRC = REPO / "apps" / "platform" / "backend" / "platform_gateway_app"


def _gateway_reads() -> set[str]:
    """PLATFORM_* names the gateway reads: its settings fields, plus direct os.environ reads."""
    names = {f"PLATFORM_{n.upper()}" for n in GatewaySettings.model_fields}
    for py in GATEWAY_SRC.glob("*.py"):
        names |= set(re.findall(r"""environ(?:\.get)?\(\s*["'](PLATFORM_[A-Z0-9_]+)""",
                                py.read_text(encoding="utf-8")))
    return names


def _offered() -> set[str]:
    text = (REPO / "deploy" / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*(PLATFORM_[A-Z0-9_]+)=", text, re.MULTILINE))


def _compose_gateway_env() -> set[str]:
    doc = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    env = doc["services"]["gateway"]["environment"]
    return set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}


def test_the_named_settings_reach_the_gateway():
    env = _compose_gateway_env()
    for name in ("PLATFORM_SESSION_TTL_HOURS", "PLATFORM_DESCRIBE_MODEL", "PLATFORM_DESCRIBE_CAP"):
        assert name in env, f"{name} is not passed to the gateway container"


def test_every_offered_gateway_setting_is_passed_through():
    offered_and_read = _offered() & _gateway_reads()
    assert {"PLATFORM_SESSION_TTL_HOURS", "PLATFORM_COOKIE_SECURE"} <= offered_and_read, (
        "the parsers found too little; this check would pass on anything")
    missing = sorted(offered_and_read - _compose_gateway_env())
    assert not missing, f".env.example offers gateway settings compose never passes: {missing}"
