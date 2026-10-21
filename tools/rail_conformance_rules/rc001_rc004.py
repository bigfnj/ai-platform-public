# RC001-RC004: the manifest itself, the ports it claims, and the gateway's catalog and routing.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import re
from functools import lru_cache


@rule("RC001", "The manifest exists, parses, and declares every required key.")
def rc001(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    required = ["id", "label", "icon", "package", "federation_name", "css_wrapper",
                "layout", "env_prefix", "ports", "status_route", "model_slots"]
    out = [F("RC001", m, f"manifest is missing required key '{k}'", rel(m.path))
           for k in required if k not in m.data]
    # The id normally IS the directory name. One rail here legitimately differs: the rail
    # with catalog id 'iep-goals' (the catalog's 'iep' is Present Levels, a second instance
    # of the edu-suite image). Such a rail declares "dir" explicitly rather than being exempt,
    # so the mismatch is stated in the manifest instead of being silently tolerated.
    expected_dir = str(m.data.get("dir") or m.data.get("id") or "")
    if expected_dir and expected_dir != m.root.name:
        out.append(F("RC001", m, f"manifest id '{m.data.get('id')}' (dir '{expected_dir}') != "
                                 f"directory name '{m.root.name}'", rel(m.path)))
    ports = m.data.get("ports") or {}
    for k in ("backend", "dev"):
        if not isinstance(ports.get(k), int):
            out.append(F("RC001", m, f"ports.{k} must be an integer", rel(m.path)))
    if not m.backend_dir().is_dir():
        out.append(F("RC001", m, f"declared layout '{m.data.get('layout')}' + package "
                                 f"'{m.data.get('package')}' resolves to "
                                 f"{rel(m.backend_dir())}, which does not exist", rel(m.path)))
    return out


@rule("RC002", "Backend and dev ports are unique across every rail in the tree.")
def rc002(m: Manifest, allm: list[Manifest]) -> list[Finding]:
    """Compared against ALL rails, not just manifested ones.

    Checking manifests against each other only would leave the tool blind to exactly the
    rails not yet under contract — and that blind spot is not theoretical: the first attempt
    at fixing the 5240 collision moved recipe-book onto 5250, which ai-playground (no
    manifest) already had. A uniqueness rule that only sees half the tree is worse than none,
    because it reports green.
    """
    out: list[Finding] = []
    mine = m.data.get("ports") or {}

    for kind in ("backend", "dev"):
        port = mine.get(kind)
        if not isinstance(port, int):
            continue
        clashes = {o.id for o in allm
                   if o.id != m.id and (o.data.get("ports") or {}).get(kind) == port}
        if kind == "dev":
            # The walk labels a config by its DIRECTORY, and a rail's id need not match it:
            # iep-goals lives in a directory of another name (rail.json "dir"). Comparing the
            # label with the id alone made iep-goals clash with its own vite.config.ts the day
            # that file gained the server block rail.json already declared.
            clashes |= {rid for rid, p in _all_vite_ports().items()
                        if rid not in (m.id, m.root.name) and p == port}
        if clashes:
            out.append(F("RC002", m, f"{kind} port {port} is also claimed by "
                                     f"{', '.join(sorted(clashes))}", rel(m.path)))
    return out


@lru_cache(maxsize=1)
def _all_vite_ports() -> dict[str, int]:
    """Every vite dev port declared anywhere under rails/, keyed by a readable owner label.

    Includes unmanifested rails and secondary configs (smb-partner's standalone mobile
    build has its own server on its own port), because a collision with one of those breaks
    a dev server just as thoroughly as a collision between two manifests.
    """
    out: dict[str, int] = {}
    for vc in _walk(RAILS, "vite*.config.ts"):
        hit = re.search(r"^\s*port:\s*(\d{4})", read(vc), re.M)
        if not hit:
            continue
        # rails/<id>/... -> <id>; name the config when a rail has more than one.
        parts = vc.relative_to(RAILS).parts
        owner = parts[0]
        if vc.name != "vite.config.ts":
            owner = f"{owner} ({vc.name})"
        out[owner] = int(hit.group(1))
    return out


@rule("RC003", "The manifest's id, label and icon match the gateway's catalog.")
def rc003(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Also checks any `also_serves` ids. One image here backs two catalog tiles: the
    edu-suite dashboard serves both 'edu-suite' and 'iep' (Present Levels, the same image
    run with IEP_ONLY=1 against its own volumes). Without this, the second tile's
    registration is checked by nothing at all.
    """
    where = rel(GATEWAY / "catalog.py")
    entries = gateway_catalog()
    if entries is None:
        # Names _ENTRIES because that is the literal gateway_catalog() reads. This used to say
        # APP_CATALOG, which stopped being a literal when it became a sorted(...) call; the
        # message outlived that change and pointed the reader at the wrong assignment.
        return [F("RC003", m, "could not read the catalog's _ENTRIES literal, so nothing "
                              "here is verified — this is a checker/catalog mismatch, not a "
                              "rail defect", where, level="warn")]
    out: list[Finding] = []
    for app_id in m.catalog_ids():
        entry = next((e for e in entries if e.get("id") == app_id), None)
        if entry is None:
            out.append(F("RC003", m, f"no catalog entry for '{app_id}' — the shell cannot "
                                     f"draw a rail item", where))
            continue
        if app_id != m.id:
            continue  # a secondary tile carries its own label/icon by design
        # `description` is mirrored here rather than read from rail.json at runtime because the
        # GATEWAY CONTAINER CANNOT SEE THE MANIFESTS — compose mounts only each rail's built
        # dist, so /app has no rails/ tree at all. Mirroring is the price; this check is what
        # keeps the copy honest.
        for key in ("label", "icon", "description"):
            want, got = m.data.get(key), entry.get(key)
            if want != got:
                out.append(F("RC003", m, f"catalog {key} is {got!r} but the manifest says "
                                         f"{want!r}", where))
    return out


@rule("RC004", "The gateway routes and mounts the rail on its declared backend port.")
def rc004(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    cfg = gateway_config_text()
    if not cfg:
        return []
    out: list[Finding] = []
    where = rel(GATEWAY / "config.py")
    port = (m.data.get("ports") or {}).get("backend")

    # Each catalog tile needs its OWN route + dist, even when two tiles share one image:
    # 'iep' (Present Levels) proxies to a different container than 'edu-suite' and serves a
    # different dist, so checking only the primary id would leave the second tile unverified.
    for app_id in m.catalog_ids():
        ident = app_id.replace("-", "_")
        primary = app_id == m.id

        hit = re.search(rf"^\s*app_{ident}_url:\s*str\s*=\s*\"([^\"]+)\"", cfg, re.M)
        if not hit:
            out.append(F("RC004", m, f"no app_{ident}_url setting — the proxy has no route to "
                                     f"/{app_id}/api/*", where))
        elif primary and isinstance(port, int) and f":{port}" not in hit.group(1):
            # Only the primary tile is expected on the manifest's declared port; a second
            # instance of the same image deliberately runs its own container elsewhere.
            out.append(F("RC004", m, f"app_{ident}_url default {hit.group(1)!r} does not use "
                                     f"the declared backend port {port}", where))

        if not re.search(rf"^\s*{ident}_dist:\s*str\s*=", cfg, re.M):
            out.append(F("RC004", m, f"no {ident}_dist setting — the federated bundle will "
                                     f"not be served at /{app_id}/", where))
        # Both mapping helpers must know the id, or the rail is registered but unreachable.
        for fn in ("app_backends", "resolved_app_dists"):
            body = _func_body(cfg, fn)
            if body and f'"{app_id}"' not in body:
                out.append(F("RC004", m, f"{fn}() does not map \"{app_id}\"", where))
    return out


def _func_body(src: str, name: str) -> str:
    """Crude but adequate: the text from `def name(` to the next top-level `def `."""
    start = src.find(f"    def {name}(")
    if start == -1:
        start = src.find(f"def {name}(")
    if start == -1:
        return ""
    nxt = src.find("\n    def ", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]
