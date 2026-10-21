"""The settings co-worker documents as operator knobs actually reach its container.

README promised `CO_WORKER_USER_NAME` / `CO_WORKER_USER_EMAIL` (and the two AUTO_SYNTHESIZE
knobs) "set in deploy/.env, forwarded by the compose file". Neither compose file passed any of
the four, so `synthesize._is_self` could never fire in a container: the brief went on writing
"Reply to <the inbox owner>" for their own sent mail, and the only fix the README offered was a
line in deploy/.env that reached nothing.

Two compose files run this rail (the full stack and the lean installer), and they drifted on the
broker token once before (see config.py), so both are checked. The second assertion catches the
other half of the same failure: a CO_WORKER_ key that names no Settings field is dropped by
`extra="ignore"` without a word, which reads exactly like a knob that works.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from co_worker_app.config import Settings

yaml = pytest.importorskip("yaml", reason="pyyaml reads the compose files")

REPO = Path(__file__).resolve().parents[4]
COMPOSE_FILES = [
    REPO / "deploy" / "docker-compose.yml",
    REPO / "deploy" / "installer" / "docker-compose.installer.yml",
]

# The knobs an operator sets in deploy/.env for this rail. INBOX_DIR / PROMPTS_DIR / BROKER_URL
# are container facts compose sets to literals, so they are not in this list.
OPERATOR_KNOBS = {
    "CO_WORKER_USER_NAME",
    "CO_WORKER_USER_EMAIL",
    # Whose inbox it is, by platform username: the only viewer whose refreshes carry a
    # requester (main.cli_requester). Unpassed, it is blank in the container and every pass
    # stays local however the broker is configured, which looks exactly like "Claude is off".
    "CO_WORKER_INBOX_OWNER",
    "CO_WORKER_AUTO_SYNTHESIZE",
    "CO_WORKER_AUTO_SYNTHESIZE_MIN_INTERVAL_S",
    "CO_WORKER_SYNTHESIS_MODEL",
}


def _env(compose: Path) -> dict[str, str]:
    service = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["co-worker"]
    env = service.get("environment") or {}
    if isinstance(env, list):  # the `- KEY=value` form, should either file ever switch to it
        env = dict(item.split("=", 1) for item in env)
    return {str(k): str(v) for k, v in env.items()}


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_operator_knob_is_passed_through_from_the_env_file(compose):
    env = _env(compose)
    missing = sorted(OPERATOR_KNOBS - set(env))
    assert not missing, f"{compose.name} never passes {missing} to co-worker"
    for key in OPERATOR_KNOBS:
        # Interpolated from deploy/.env, not pinned to a literal the operator cannot change.
        assert "${" + key in env[key], f"{compose.name}: {key}={env[key]!r} ignores deploy/.env"


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_prefixed_key_compose_passes_is_a_real_setting(compose):
    prefix = Settings.model_config["env_prefix"]
    fields = {prefix + name.upper() for name in Settings.model_fields}
    # The token fields are read through validation_alias, which bypasses the prefix; both
    # spellings are listed on the field itself.
    for name in ("broker_auth_token", "broker_auth_token_file"):
        fields.update(Settings.model_fields[name].validation_alias.choices)
    stray = sorted(k for k in _env(compose) if k.startswith(prefix) and k not in fields)
    assert not stray, f"{compose.name} passes {stray}, which Settings silently ignores"


def test_the_passed_values_are_the_ones_settings_reads(monkeypatch):
    """The compose defaults parse to the code defaults, so an operator who sets nothing gets
    exactly the behaviour they had before the pass-through existed."""
    monkeypatch.setenv("CO_WORKER_USER_NAME", "")
    monkeypatch.setenv("CO_WORKER_USER_EMAIL", "")
    monkeypatch.setenv("CO_WORKER_INBOX_OWNER", "")
    monkeypatch.setenv("CO_WORKER_AUTO_SYNTHESIZE", "true")
    monkeypatch.setenv("CO_WORKER_AUTO_SYNTHESIZE_MIN_INTERVAL_S", "900")
    s = Settings(_env_file=None)
    defaults = Settings.model_fields
    assert s.user_name == defaults["user_name"].default == ""
    assert s.user_email == defaults["user_email"].default == ""
    assert s.inbox_owner == defaults["inbox_owner"].default == ""
    assert s.auto_synthesize is defaults["auto_synthesize"].default is True
    assert s.auto_synthesize_min_interval_s == defaults["auto_synthesize_min_interval_s"].default

    monkeypatch.setenv("CO_WORKER_USER_NAME", "Owner One")
    monkeypatch.setenv("CO_WORKER_AUTO_SYNTHESIZE", "false")
    s = Settings(_env_file=None)
    assert (s.user_name, s.auto_synthesize) == ("Owner One", False)
