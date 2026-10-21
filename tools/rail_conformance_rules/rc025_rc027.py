# RC025-RC027: the lean installer path (a rail's profile, the bundled gateway image, roles.lean.json).
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import json
import re
from functools import lru_cache


# --- the lean installer path -------------------------------------------------
# RC001-RC024 assert the restatements that make a rail work in the FULL stack. The lean
# installer is a SECOND, parallel set of restatements and nothing checked it. meeting-atlas was
# ported into deploy/docker-compose.yml on 2026-08-24 and left out of every installer file, and
# this checker reported green the whole time; a downstream Podman install found it the hard way,
# as a rail in the nav that the gateway advertised and no backend answered.
#
# "Lean-installable" is DERIVED from docker-compose.installer.yml rather than declared in
# rail.json. That file already is the statement of which rails the installer offers, so keying
# off it needs no new manifest field (the schema is additionalProperties: false) and leaves one
# source of truth rather than two that can disagree.

INSTALLER = REPO / "deploy" / "installer" / "docker-compose.installer.yml"
BUNDLED = REPO / "deploy" / "Dockerfile.gateway.bundled"
LIB_RUNTIME = REPO / "deploy" / "installer" / "lib-runtime.ps1"
INSTALL_PS1 = REPO / "deploy" / "installer" / "install.ps1"
ROLES_LEAN = REPO / "deploy" / "installer" / "roles.lean.json"


@lru_cache(maxsize=1)
def _lean_services() -> dict[str, str]:
    """Rail directory -> installer compose service name, for every service built from rails/<dir>."""
    src = read(INSTALLER)
    out: dict[str, str] = {}
    for hit in re.finditer(r"^  ([a-z][a-z0-9_-]*):", src, re.M):
        body = _compose_service(src, hit.group(1)) or ""
        ctx = re.search(r"context:\s*\.\./\.\./rails/([a-z0-9_-]+)", body)
        if ctx:
            out[ctx.group(1)] = hit.group(1)
    return out


def _ps_array(src: str, anchor: str) -> str:
    """The balanced `@( ... )` opened by `anchor`, a regex whose match ENDS at the `@(`.

    Depth-counted rather than `@\\([^)]*\\)`, because the chooser entries carry human labels
    like 'Recipe Book (ships with seed)'. Scanning to the first ')' truncates the list, and the
    rule would then report a rail as missing while it is sitting three lines further down.
    """
    m = re.search(anchor, src)
    if not m:
        return ""
    start, depth = m.end() - 2, 0
    for k in range(start, len(src)):
        if src[k] == "(":
            depth += 1
        elif src[k] == ")":
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
    return ""


def _lean_profile(dir_name: str) -> str | None:
    """The profile id gating this rail's installer service, or None if it is unprofiled
    (terminal-fun, which the lean install always carries)."""
    svc = _lean_services().get(dir_name)
    if not svc:
        return None
    body = _compose_service(read(INSTALLER), svc) or ""
    hit = re.search(r"profiles:\s*\[\s*[\"']([^\"']+)", body)
    return hit.group(1) if hit else None


@rule("RC025", "A lean-installable rail's profile is both startable and selectable.")
def rc025(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Three files must agree or the rail silently never comes up.

    The installer compose names a profile on the service; `Get-ComposeProfiles` turns an
    enabled-app id into the `--profile` flag that starts it; install.ps1's two chooser lists are
    what put the id into PLATFORM_ENABLED_APPS to begin with. Miss the middle one and
    `compose up` skips the service. Miss the last and the profile can never be selected, so the
    middle one never matches. Both look identical from outside, and neither prints anything.

    Both have shipped. meeting-atlas once declared `profiles: ["meeting-atlas"]` with no entry
    in Get-ComposeProfiles, making it unstartable from the installer by construction; when that
    was fixed downstream, the chooser lists were still missing it, so only a box whose .env
    already named the rail would have come up.
    """
    if m.root.name not in _lean_services():
        return [S("RC025", m, "not lean-installable — absent from docker-compose.installer.yml")]
    pid = _lean_profile(m.root.name)
    if not pid:
        return [S("RC025", m, "unprofiled — always installed, so there is no profile "
                              "to make startable and selectable")]
    out: list[Finding] = []

    listed = re.findall(r"'([^']+)'", _ps_array(
        read(LIB_RUNTIME),
        r"function Get-ComposeProfiles\b[\s\S]*?foreach\s*\(\s*\$a\s+in\s+@\("))
    if pid not in listed:
        out.append(F("RC025", m, f"profile '{pid}' is declared on the installer service but "
                                 f"absent from Get-ComposeProfiles — compose skips the service "
                                 f"and reports nothing", rel(LIB_RUNTIME)))

    inst = read(INSTALL_PS1)
    for var in ("consoleRails", "OptionalRails"):
        block = _ps_array(inst, rf"\${var}\s*=\s*@\(")
        if not block:
            out.append(F("RC025", m, f"cannot locate ${var} in install.ps1; this rule went "
                                     f"blind rather than green", rel(INSTALL_PS1), level="warn"))
        elif pid not in re.findall(r"Id\s*=\s*'([^']+)'", block):
            out.append(F("RC025", m, f"not offered by ${var}, so '{pid}' never reaches "
                                     f"PLATFORM_ENABLED_APPS and its profile is never selected",
                         rel(INSTALL_PS1)))
    return out


@rule("RC026", "A lean-installable rail is baked into the bundled gateway image.")
def rc026(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The one lean-path restatement with no runtime escape hatch.

    The shell resolves module-federation remotes at BUILD time, so a rail whose frontend is not
    compiled into Dockerfile.gateway.bundled can never mount however the installer is
    configured. That file states exactly this in a comment, and meeting-atlas was missing from
    it anyway: a comment is not a check.

    Also checks the backend URL, because a stale port here is silent too — co-worker's moved
    8860 -> 8890 on import and a partial `up` against an older gateway is the failure that
    finds it.
    """
    svc = _lean_services().get(m.root.name)
    if not svc:
        return [S("RC026", m, "not lean-installable — absent from docker-compose.installer.yml")]
    src, where = read(BUNDLED), rel(BUNDLED)
    if not src:
        return [S("RC026", m, "Dockerfile.gateway.bundled is absent — the bundled image "
                              "cannot be checked")]
    # Strip `#` comments before matching, the idiom RC020 and RC021 already use. A COMMENTED-OUT
    # `COPY rails/<x>/frontend` satisfied a substring test while copying nothing, which is the
    # same class of defect as RC021's old `"401" in blob` -- the text is present and the
    # behaviour is absent.
    src = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    dir_name, stem = m.root.name, svc.upper().replace("-", "_")
    out: list[Finding] = []

    loop = re.search(r"for d in ([\s\S]*?); do", src)
    checks = [
        ("the stage-1 COPY of its frontend", f"COPY rails/{dir_name}/frontend" in src),
        ("its frontend in the npm build loop",
         bool(loop) and f"rails/{dir_name}/frontend" in loop.group(1)),
        ("the stage-2 COPY of its built dist", f"/build/rails/{dir_name}/frontend/dist" in src),
        (f"PLATFORM_{stem}_DIST", f"PLATFORM_{stem}_DIST=" in src),
    ]
    for what, ok in checks:
        if not ok:
            out.append(F("RC026", m, f"bundled gateway image is missing {what} — the shell "
                                     f"resolves remotes at build time, so no runtime setting "
                                     f"can mount this rail", where))

    url = re.search(rf"PLATFORM_APP_{stem}_URL=http://([a-z0-9_-]+):(\d+)", src)
    port = (m.data.get("ports") or {}).get("backend")
    if not url:
        out.append(F("RC026", m, f"bundled gateway image sets no PLATFORM_APP_{stem}_URL, so "
                                 f"the gateway has no backend to proxy to", where))
    else:
        if url.group(1) != svc:
            out.append(F("RC026", m, f"PLATFORM_APP_{stem}_URL points at host '{url.group(1)}' "
                                     f"but the compose service is '{svc}'", where))
        if isinstance(port, int) and int(url.group(2)) != port:
            out.append(F("RC026", m, f"PLATFORM_APP_{stem}_URL uses port {url.group(2)}; the "
                                     f"manifest declares backend {port}", where))
    return out


@rule("RC027", "A lean-installable rail's roles all resolve in roles.lean.json.")
def rc027(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """roles.lean.json is the role map the installer gives a small card (it writes it to the box's
    own deploy/local/roles.json, which the broker is registered to read). A per-rail role missing from it does not fail the install — it fails that rail's first
    model call, at runtime, as an @role the broker cannot expand.

    Image slots are exempt by design: the lean profile runs with the broker media pipeline off
    (BROKER_MEDIA_ENABLED=false) and recipe-book's icons pre-rendered into its seed, both stated
    in deploy/installer/env.lean.example. Nothing there ever invokes @recipe-icon.
    """
    if m.root.name not in _lean_services():
        return [S("RC027", m, "not lean-installable — absent from docker-compose.installer.yml")]
    raw = read(ROLES_LEAN)
    if not raw:
        return [S("RC027", m, "roles.lean.json is absent — no lean role map to resolve "
                              "against")]
    try:
        lean = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [F("RC027", m, f"roles.lean.json does not parse: {exc}", rel(ROLES_LEAN))]
    out: list[Finding] = []
    for slot in (m.data.get("model_slots") or []):
        role = slot.get("role")
        if not role or slot.get("kind") == "image":
            continue
        if role not in lean:
            out.append(F("RC027", m, f"@{role} has no entry in roles.lean.json — it resolves on "
                                     f"the 24 GB map only, so a lean install fails this rail's "
                                     f"first model call", rel(ROLES_LEAN)))
    return out
