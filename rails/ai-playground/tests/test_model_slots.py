"""Every model this rail calls is a declared slot, and the header chips show each one.

The Embedding Lab's "describe this model" draft (bench/describe.py, behind
POST /api/bench/models/describe) called `@chat` through an environment variable no manifest named.
So the chips could not show it, and tools/rail_smoke.py's role check, which is scoped to the
roles the running rails DECLARE, could not see it break. It is the `describe` slot now.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_playground import config
from ai_playground.api import app as appmod
from ai_playground.bench import describe

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "rail.json")
                      .read_text(encoding="utf-8"))


def test_the_describe_call_uses_the_declared_setting():
    assert describe._MODEL is config.DESCRIBE_MODEL
    slot = next(s for s in MANIFEST["model_slots"] if s["slot"] == "describe")
    assert slot["env"] == "AI_PLAYGROUND_DESCRIBE_MODEL" and slot["role"] == "chat"


def test_the_chips_carry_every_declared_slot(api, monkeypatch):
    """The capabilities payload lists exactly the manifest's slots, the describe one following
    its setting. modelstate.resolve is captured rather than run: it would call the broker."""
    seen = {}

    def capture(specs, *a, **k):
        seen["specs"] = list(specs)
        return {"broker": "ok", "models": []}

    monkeypatch.setattr(appmod.modelstate, "resolve", capture)
    r = TestClient(api).get("/api/capabilities", headers={"X-Platform-User": "alice"})
    assert r.status_code == 200, r.text
    assert [s for s, _l, _ref in seen["specs"]] == [s["slot"] for s in MANIFEST["model_slots"]]
    assert ("describe", "Model notes", config.DESCRIBE_MODEL) in seen["specs"]
