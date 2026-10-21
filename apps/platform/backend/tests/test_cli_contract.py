"""The broker's CLI status block, as the broker really builds it, read by the gateway's view.

test_cli_view.py pins cli_view against a hand-written block, and the broker's own tests pin
the block it builds. Each side can stay green while the two drift apart (a renamed key, a
requester stored un-normalised, the per-requester map shaped differently), and the symptom
would be an owner who silently stops seeing their own outcome. This joins them: a real Broker
records outcomes for two owners on one role, and cli_view reads its real cli_state().
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# The broker from THIS tree; see test_rails_tab_probe_count.py for why the path is inserted.
_BROKER_DIR = Path(__file__).resolve().parents[4] / "services" / "broker"
if str(_BROKER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROKER_DIR))

from app.broker import Broker  # noqa: E402
from app.config import BrokerSettings  # noqa: E402
from platform_gateway_app.cli_view import cli_view  # noqa: E402


@pytest.fixture
def broker(tmp_path, monkeypatch):
    for key in ("BROKER_CLI_ENABLED", "BROKER_CLI_EXE", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES",
                "BROKER_CLI_OAUTH_TOKEN", "BROKER_CLI_OAUTH_TOKEN_FILE", "BROKER_CLI_WORKDIR",
                "BROKER_CLI_ROLES_FILE"):
        monkeypatch.delenv(key, raising=False)
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    return Broker(BrokerSettings(cli_enabled=True, cli_exe=str(exe),
                                 cli_owners="owner-one,owner-two", cli_roles="iep",
                                 cli_workdir=str(tmp_path),
                                 roles_file=str(tmp_path / "roles.json")))


def test_each_owner_sees_their_own_outcome_from_the_real_block(broker):
    broker._record_cli_outcome("@iep", {"ok": True, "at": time.time(),
                                        "requester": "owner-one"})
    broker._record_cli_outcome("@iep", {"ok": False, "at": time.time(),
                                        "reason": "rate_limited", "requester": "owner-two"})
    block = broker.cli_state()
    one = cli_view(block, " owner-one ", False)["last_by_role"]
    # A case variant is another account to the broker, so it is another account here too.
    assert cli_view(block, "Owner-One", False) is None
    two = cli_view(block, "owner-two", False)["last_by_role"]
    assert set(one) == {"@iep"} and one["@iep"]["ok"] is True
    assert two["@iep"]["ok"] is False and two["@iep"]["reason"] == "rate_limited"
    # Neither view names anybody: the outcomes are theirs.
    assert "requester" not in one["@iep"] and "requester" not in two["@iep"]


class _Stalled(Exception):
    reason = "stalled"


def test_an_owner_sees_an_interruption_and_its_cause_from_the_real_block(broker):
    """The broker's own interruption record (Broker._cli_interrupted, a stream the CLI cut off
    after its first token), read through the owner's view: the reason and its cause both reach
    the widget, under the keys the broker writes them with."""
    from app.broker import _CliAttempt
    broker._cli_interrupted(_CliAttempt(model="@iep", who="owner-one", rail=None, token=1),
                            _Stalled("no delta for 60 s"), 42)
    mine = cli_view(broker.cli_state(), "owner-one", False)["last_by_role"]
    assert {k: v for k, v in mine["@iep"].items() if k != "at"} == {
        "ok": False, "reason": "interrupted", "cause": "stalled"}


def test_the_gateway_offers_exactly_the_image_backends_the_broker_runs():
    """Two hand-kept lists of the media image backends: the broker's (what resolves without
    an Ollama tag) and the gateway's (what the Rails tab offers for an image slot). Nothing
    compared them, so a backend added to one only would be offered and then 404, or run and
    never be offerable."""
    from app.broker import MEDIA_IMAGE_BACKENDS as broker_side
    from platform_gateway_app.rails_models import MEDIA_IMAGE_BACKENDS as gateway_side
    assert sorted(b["name"] for b in gateway_side) == sorted(broker_side)


def test_the_gateway_reads_an_image_backend_s_name_as_the_broker_does():
    """Admin > Rails' PUT matched an image backend exactly while the broker matched it in any
    case, so the panel refused `FLUX-Schnell`, which the broker serves in every view."""
    from app.broker import image_backend as broker_side
    from platform_gateway_app.rails_models import image_backend as gateway_side
    for name in ("flux-schnell", "FLUX-Schnell", "SDXL-TURBO", "sdxl-turbo", "flux", "gemma3:4b",
                 "", None):
        assert gateway_side(name) == broker_side(name), name
