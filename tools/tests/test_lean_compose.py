"""The lean compose file keeps what the full stack keeps, and compose owns its own volumes.

terminal-fun was the only lean rail with no data volume, so /data sat in the container layer and
the installer's repair path (`up -d --build`) destroyed every save (record, 2026-09-11). And the
installer pre-created every named volume for an `external: true` neither file declares, making them
without compose's labels so each `up` warned about them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "deploy" / "docker-compose.yml"
LEAN = REPO / "deploy" / "installer" / "docker-compose.installer.yml"


def _cfg(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _named_targets(svc: dict) -> set[str]:
    out = set()
    for v in svc.get("volumes") or []:
        if isinstance(v, str):
            parts = v.split(":")
            if len(parts) >= 2 and not parts[0].startswith((".", "/", "$")):
                out.add(parts[1])
        elif isinstance(v, dict) and v.get("type") == "volume":
            out.add(v["target"])
    return out


def test_every_named_volume_the_full_stack_mounts_the_lean_stack_mounts_too():
    full, lean = _cfg(FULL)["services"], _cfg(LEAN)["services"]
    gaps = {n: sorted(_named_targets(full[n]) - _named_targets(lean[n])) for n in set(full) & set(lean)}
    assert {n: g for n, g in gaps.items() if g} == {}


def test_terminal_fun_has_the_full_stacks_uid_split():
    """Lean terminal-fun ran games on the backend's uid, so an escaped game shell could read the
    broker token from /proc/1/environ once lean boxes had one. Same privilege keys as the full stack."""
    keys = ("user", "cap_drop", "cap_add", "security_opt")
    full = _cfg(FULL)["services"]["terminal-fun"]
    lean = _cfg(LEAN)["services"]["terminal-fun"]
    assert {k: lean.get(k) for k in keys} == {k: full.get(k) for k in keys}
    assert lean["user"] == "0:0" and sorted(lean["cap_add"]) == ["SETGID", "SETUID"]


def test_every_mounted_named_volume_is_declared():
    cfg = _cfg(LEAN)
    declared = set(cfg.get("volumes") or {})
    for name, svc in cfg["services"].items():
        for v in svc.get("volumes") or []:
            src = v.split(":")[0] if isinstance(v, str) else v.get("source", "")
            if src and not src.startswith((".", "/", "$")):
                assert src in declared, (name, src)


def _env(svc: dict) -> dict:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        return {e.split("=", 1)[0]: (e.split("=", 1)[1] if "=" in e else None) for e in env}
    return dict(env)


BUNDLED = REPO / "deploy" / "Dockerfile.gateway.bundled"

# What a lean service may go without that the same service on the full stack is handed, and why.
# (service, a regex the WHOLE key must match) -> the reason. Every other key the full stack hands
# a service, the lean stack hands it too, because a setting a rail reads and compose never passes
# is a deploy/.env line that silently changes nothing (Admin > Models > Delete 502'd on every lean
# box for want of PLATFORM_OLLAMA_URL). The test after this one holds these two to their reasons.
LEAN_ENV_ALLOWED = {
    ("gateway", r"PLATFORM_APP_[A-Z0-9_]+_URL"):
        "a rail's backend route: the bundled image's ENV sets one for every rail it carries, and "
        "the others are rails the lean stack has no service for",
    ("gateway", r"PLATFORM_[A-Z0-9_]+_DIST"):
        "a frontend dist: baked into the bundled image, whose ENV names each one it carries, where "
        "the full stack mounts host-built dists; the others are rails with no lean service",
}


def test_every_setting_the_full_stack_hands_a_service_the_lean_stack_hands_it_too():
    full, lean = _cfg(FULL)["services"], _cfg(LEAN)["services"]
    gaps = {}
    for name in sorted(set(full) & set(lean)):
        missing = sorted(k for k in set(_env(full[name])) - set(_env(lean[name]))
                         if not any(s == name and re.fullmatch(rx, k) for s, rx in LEAN_ENV_ALLOWED))
        if missing:
            gaps[name] = missing
    assert gaps == {}, gaps


def test_the_gateway_settings_the_lean_stack_leaves_out_are_baked_in_or_for_an_absent_rail():
    """The allowance above, checked: each omitted gateway key is in the bundled image's ENV, or
    names a rail the lean stack runs no service for (a URL's host, a dist's rail)."""
    from urllib.parse import urlparse
    image_env = set(re.findall(r"\b(PLATFORM_[A-Z0-9_]+)=", BUNDLED.read_text(encoding="utf-8")))
    lean = _cfg(LEAN)["services"]
    full_gw, lean_gw = _env(_cfg(FULL)["services"]["gateway"]), _env(lean["gateway"])
    unexplained = []
    for k in sorted(set(full_gw) - set(lean_gw)):
        if k in image_env:
            continue
        url = re.fullmatch(r"PLATFORM_APP_([A-Z0-9_]+)_URL", k)
        dist = re.fullmatch(r"PLATFORM_([A-Z0-9_]+)_DIST", k)
        if url and urlparse(str(full_gw[k])).hostname not in lean:
            continue
        if dist and dist.group(1).lower().replace("_", "-") not in lean:
            continue
        unexplained.append(k)
    assert unexplained == [], unexplained
    assert image_env, f"read no PLATFORM_ setting from {BUNDLED.name}'s ENV"


def test_compose_owns_its_volumes_and_no_script_pre_creates_them():
    for p in (FULL, LEAN):
        ext = {k: v for k, v in (_cfg(p).get("volumes") or {}).items() if v and v.get("external")}
        assert ext == {}, (p.name, ext)
    hits = []
    for path in sorted((REPO / "deploy").rglob("*.ps1")):
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.lstrip().startswith("#") and re.search(r"'volume',\s*'create'|volume\s+create\s+[\w$]", line):
                hits.append(f"{path.relative_to(REPO)}:{n}")
    assert hits == [], hits
