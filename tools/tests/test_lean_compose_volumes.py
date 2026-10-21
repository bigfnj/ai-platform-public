"""A service in BOTH compose files persists in the lean install whatever the full stack persists.

Recorded 2026-09-11 and still true until 2026-10-08: terminal-fun was the one lean rail with no
data volume, so its saves and player-name registry lived in the container layer, and the
documented lean repair path (install.ps1's `up -d --build`) destroyed every save with no message,
while the full stack mounted `terminal_fun_data:/data` and its image assumes one.

Checked by target path, per service: every container path the full stack backs with a NAMED
volume must be backed by a named volume in the lean file too. Bind mounts and variable-chosen
sources are not compared (a lean box legitimately binds differently), and a lean-only service
has nothing to compare against.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "deploy" / "docker-compose.yml"
LEAN = REPO / "deploy" / "installer" / "docker-compose.installer.yml"


def _named_targets(service: dict, declared: set[str]) -> set[str]:
    targets = set()
    for v in service.get("volumes") or []:
        if isinstance(v, str):
            src, _, rest = v.partition(":")
            tgt = rest.split(":", 1)[0]
        else:
            src, tgt = v.get("source"), v.get("target")
        if src in declared and tgt:
            targets.add(tgt)
    return targets


def missing_in_lean(full: dict, lean: dict) -> dict[str, list[str]]:
    full_vols = set(full.get("volumes") or {})
    lean_vols = set(lean.get("volumes") or {})
    out: dict[str, list[str]] = {}
    for name, svc in (lean.get("services") or {}).items():
        twin = (full.get("services") or {}).get(name)
        if not twin:
            continue
        gap = _named_targets(twin, full_vols) - _named_targets(svc, lean_vols)
        if gap:
            out[name] = sorted(gap)
    return out


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_shared_service_persists_the_same_paths_in_the_lean_install():
    full, lean = _load(FULL), _load(LEAN)
    shared = set(full["services"]) & set(lean["services"])
    assert len(shared) >= 5, "almost nothing to compare, so this proves nothing"
    assert missing_in_lean(full, lean) == {}


def test_the_check_can_fail():
    """The recorded defect's own shape, and a declared-but-unmounted volume, both reported."""
    full = {"volumes": {"tf_data": None},
            "services": {"terminal-fun": {"volumes": ["tf_data:/data"]},
                         "other": {"volumes": ["tf_data:/srv"]}}}
    lean = {"volumes": {"tf_data": None},
            "services": {"terminal-fun": {"tmpfs": ["/tmp"]},
                         "other": {"volumes": ["./host-dir:/srv"]}}}
    assert missing_in_lean(full, lean) == {"terminal-fun": ["/data"], "other": ["/srv"]}
