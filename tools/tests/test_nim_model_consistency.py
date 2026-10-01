"""The NVIDIA NIM chat model is named in three files that must agree.

WHY THIS EXISTS. On 2026-09-11 all three still said `nvidia/nemotron-mini-4b-instruct`, which
NVIDIA had retired. Every check the platform owns stayed green: the API key authenticated, the
broker was healthy, `nim.available()` returned True because it only tests for a key, and the
rail's own `/api/nim/probe` would have caught it but nothing calls it unattended. The failure was
reachable only by a user pressing the toggle and getting a 404 mid-answer.

Three copies with nothing comparing them is the mechanism, not the model id. The lean installer
compose is the one that matters most: a downstream consumer installs from it, cannot contribute
back, and so inherits a dead cloud model with no way to tell us.

Deliberately NOT asserting a specific id. Pinning one here would mean a legitimate model swap has
to be made in four places instead of three, and the fourth is a test - which is how a test starts
being the thing people edit to make red go away. It asserts only that the three agree, plus that
the id NVIDIA retired cannot come back.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent

ENV_VAR = "AI_PLAYGROUND_NVIDIA_CHAT_MODEL"
RETIRED = "nvidia/nemotron-mini-4b-instruct"

RAIL_DEFAULT = REPO / "rails" / "ai-playground" / "src" / "ai_playground" / "nim.py"
FULL_COMPOSE = REPO / "deploy" / "docker-compose.yml"
LEAN_COMPOSE = REPO / "deploy" / "installer" / "docker-compose.installer.yml"

# tools/rail_smoke.py is withheld from the public snapshot (publish.json paths.exclude). Only the
# THIRD test below reads it; the first two read rails/ai-playground, deploy/docker-compose.yml and
# deploy/installer/docker-compose.installer.yml, all of which ship. So the guard is per-test rather
# than module-level: a module-level skip would silently disable the agreement check on the two
# compose files a downstream consumer actually installs from, which is the case this file exists
# for -- "the lean installer compose is the one that matters most", per the docstring above.
RAIL_SMOKE = TOOLS / "rail_smoke.py"
_SMOKE_WITHHELD = pytest.mark.skipif(
    not RAIL_SMOKE.is_file(),
    reason="tools/rail_smoke.py is withheld from the public snapshot, so the live NIM probe it "
           "would be checked for is not part of this artifact; the static agreement checks in "
           "this file still run.",
)


def _rail_default() -> str:
    """The fallback in `os.environ.get(ENV_VAR, "<model>")`, which is what runs when compose
    passes nothing -- a bare `python -m ai_playground`, or any test importing the module."""
    text = RAIL_DEFAULT.read_text(encoding="utf-8")
    m = re.search(rf'os\.environ\.get\(\s*"{ENV_VAR}"\s*,\s*"([^"]+)"\s*\)', text)
    assert m, f"no {ENV_VAR} default found in {RAIL_DEFAULT.name}; did the lookup change shape?"
    return m.group(1)


def _compose_default(path: Path) -> str:
    """The `${VAR:-<model>}` default. Read with a regex rather than a YAML parse on purpose:
    the value lives inside a shell-interpolation string, so a parser hands back the whole
    `${...}` expression and the assertion would pass on two files that disagree."""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf'\$\{{{ENV_VAR}:-([^}}]+)\}}', text)
    assert m, f"no {ENV_VAR} default found in {path.name}"
    return m.group(1).strip()


def test_all_three_sites_name_the_same_model():
    rail = _rail_default()
    full = _compose_default(FULL_COMPOSE)
    lean = _compose_default(LEAN_COMPOSE)
    assert rail == full == lean, (
        "the NIM chat model disagrees across the three sites that set it:\n"
        f"  {RAIL_DEFAULT.relative_to(REPO)}: {rail}\n"
        f"  {FULL_COMPOSE.relative_to(REPO)}: {full}\n"
        f"  {LEAN_COMPOSE.relative_to(REPO)}: {lean}\n"
        "A rail started outside compose uses the first; this deployment uses the second; a "
        "downstream lean install uses the third."
    )


def test_the_retired_model_cannot_come_back():
    for path in (RAIL_DEFAULT, FULL_COMPOSE, LEAN_COMPOSE):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if RETIRED not in line:
                continue
            # The comments explaining the retirement quote the id, and must stay quotable.
            stripped = line.strip()
            assert stripped.startswith("#"), (
                f"{path.relative_to(REPO)}:{i} sets the retired model {RETIRED}, which returns "
                "404 for a valid key. Pick a model verified by a real completion, not by "
                "appearing in /v1/models."
            )


@_SMOKE_WITHHELD
def test_the_model_is_reachable_by_a_documented_command():
    """The guard above is static; nothing here calls NVIDIA. The live check belongs to
    `tools/rail_smoke.py --deep`, which probes the configured model through the rail's own
    `/api/nim/probe`. This test asserts the wiring exists, so the pair cannot silently become
    static-only again."""
    smoke = RAIL_SMOKE.read_text(encoding="utf-8")
    assert "/api/nim/probe" in smoke, (
        "tools/rail_smoke.py no longer probes the NIM endpoint, so a retired cloud model would "
        "again be discoverable only by a user pressing the toggle."
    )
