# RC009-RC011: the vite config, the federation remote, and the compose service.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import re


@rule("RC009", "The vite dev server and its API proxy use the declared ports.")
def rc009(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Reads the config at Manifest.vite_config(), which follows a relocated frontend.

    A missing config is a WARN, not a skip and never a bare []. Every manifest declares a dev
    port and a federation name (RC001), so "no vite config" does not mean "nothing to check":
    the dev server those describe lives somewhere this rule could not find. Returning [] is
    exactly how this rule counted edu-suite as checked while it hardcoded <root>/frontend/.
    """
    vc = m.vite_config()
    src = read(vc)
    if not src:
        return [F("RC009", m, f"no vite config at {rel(vc)}, so the dev port and API proxy "
                              f"were NOT checked", rel(vc), level="warn")]
    ports = m.data.get("ports") or {}
    out: list[Finding] = []
    hit = re.search(r"\bport:\s*(\d{4})", src)
    if hit and ports.get("dev") and int(hit.group(1)) != ports["dev"]:
        out.append(F("RC009", m, f"vite server.port is {hit.group(1)} but the manifest "
                                 f"declares dev port {ports['dev']}", rel(vc)))
    targets = {int(t) for t in re.findall(r"target:\s*[\"'](?:https?|ws)://127\.0\.0\.1:(\d+)", src)}
    if targets and ports.get("backend") and targets != {ports["backend"]}:
        out.append(F("RC009", m, f"dev proxy targets port(s) {sorted(targets)} but the "
                                 f"manifest declares backend {ports['backend']}", rel(vc)))
    return out


@rule("RC010", "Federation name and base path match the manifest and the shell's import.")
def rc010(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    vc = m.vite_config()   # follows a relocated frontend; see RC009 for why a miss WARNs
    src = read(vc)
    out: list[Finding] = []
    fed = str(m.data.get("federation_name") or "")
    if not src:
        # Only the vite half is blind. The three shell-side checks below need no rail config,
        # so they still run, which is why this is one warning and not an early return.
        out.append(F("RC010", m, f"no vite config at {rel(vc)}, so the federation name and "
                                 f"base path were NOT checked", rel(vc), level="warn"))
    else:
        hit = re.search(r"name:\s*[\"']([a-z_][a-z0-9_]*)[\"']", src)
        if hit and fed and hit.group(1) != fed:
            out.append(F("RC010", m, f"vite federation name is {hit.group(1)!r} but the "
                                     f"manifest says {fed!r}", rel(vc)))
        hit = re.search(r"base:\s*[\"']([^\"']+)[\"']", src)
        if hit and hit.group(1) != f"/{m.id}/":
            out.append(F("RC010", m, f"vite base is {hit.group(1)!r}; the gateway serves "
                                     f"this bundle from '/{m.id}/'", rel(vc)))
    # Mounting a rail takes THREE agreeing declarations in the shell, and until 2026-08-24 this
    # rule checked only the middle one. Module federation resolves remote names at BUILD time, so
    # each missing piece fails differently and none of them at the point you'd look:
    #   * no vite `remotes` entry  -> the lazy import cannot resolve; the shell build fails
    #   * no lazy import           -> the tile falls through to ComingSoon, silently
    #   * no remotes.d.ts entry    -> tsc TS2307 before any of the above runs
    # The publish tooling grew the same check the hard way (a snapshot shipped three lazy imports
    # of remotes vite no longer declared, and a <Module /> whose binding had been removed), so it
    # belongs here, where it guards every build rather than only a publish.
    app = read(SHELL / "App.tsx")
    if app and fed and f"'{fed}/module'" not in app and f'"{fed}/module"' not in app:
        out.append(F("RC010", m, f"the shell never lazy-imports '{fed}/module', so the rail "
                                 f"cannot mount", rel(SHELL / "App.tsx")))
    shell_vite = SHELL.parent / "vite.config.ts"
    sv = read(shell_vite)
    if sv and fed and not re.search(rf"^\s*{re.escape(fed)}\s*:\s*['\"]/", sv, re.M):
        out.append(F("RC010", m, f"the shell's vite config declares no federation remote "
                                 f"{fed!r}; the lazy import cannot resolve and the shell build "
                                 f"fails", rel(shell_vite)))
    dts = read(SHELL / "remotes.d.ts")
    if dts and fed and f"module '{fed}/module'" not in dts and f'module "{fed}/module"' not in dts:
        out.append(F("RC010", m, f"remotes.d.ts declares no module {fed + '/module'!r}, so tsc "
                                 f"cannot resolve the shell's import (TS2307)",
                     rel(SHELL / "remotes.d.ts")))
    return out


@rule("RC011", "A compose service exists for the rail, exposing its backend port.")
def rc011(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    src = read(COMPOSE)
    if not src:
        return []
    svc = m.compose_service()
    block = _compose_service(src, svc)
    if block is None:
        return [F("RC011", m, f"no compose service named '{svc}'", rel(COMPOSE))]
    port = m.container_port()
    if isinstance(port, int) and f'"{port}"' not in block and f"- {port}" not in block:
        return [F("RC011", m, f"compose service '{svc}' does not expose the declared "
                              f"container port {port}", rel(COMPOSE))]
    return []
