#!/usr/bin/env python3
"""Rail conformance checker — verifies every rail agrees with its own manifest.

    python tools/rail_conformance.py            # human report, exit 1 on any FAIL
    python tools/rail_conformance.py --json     # machine-readable
    python tools/rail_conformance.py --rail co-worker
    python tools/rail_conformance.py --rule RC005

WHY THIS EXISTS, AND WHY IT IS NOT A SHARED LIBRARY
---------------------------------------------------
Rails are independent deployables: separate images, separate dependency sets, separate
config idioms. That is a deliberate architectural choice and this tool does not fight it.
It imports nothing from any rail and installs nothing into any rail. It reads the tree.

What it replaces is not shared code but *shared assumptions* — the facts every rail
restates in six places (its own source, its vite config, its Dockerfile, the compose
service, the gateway's routing/dist registries, the admin model panel). Each restatement
is a chance to drift, and every drift found so far was silent: a chip that says one slot
name while the admin panel says another, a dev port two rails both claim, a broker token
read from a variable nothing sets. None of those break a build. They just quietly lie.

So each rail publishes rails/<id>/rail.json (docs/rail-manifest.schema.json) and this
checker asserts the rest of the tree agrees. When a check fails, the manifest is the
contract and the code is the defect — unless the manifest is what drifted, which is a
human judgement, not something this tool should guess.

STDLIB ONLY, ON PURPOSE. This has to run on a clean checkout with no install step, so it
uses `ast` for the gateway's Python registries (they are literal assignments, so
ast.literal_eval is both safe and exact) and targeted regex for vite/compose/CSS. Where a
check is a heuristic rather than a proof, the rule says so in its docstring and prefers a
false negative to a false alarm: a checker that cries wolf gets disabled, and then it
protects nothing.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
RAILS = REPO / "rails"
GATEWAY = REPO / "apps" / "platform" / "backend" / "platform_gateway_app"
SHELL = REPO / "apps" / "platform" / "frontend" / "src"
COMPOSE = REPO / "deploy" / "docker-compose.yml"

# The four state names are the cross-rail visual language. A rail that computes or renders
# a different set is not "slightly different", it is lying to an operator who has learned
# what the colours mean everywhere else.
CHIP_STATES = ("missing", "cold", "warming", "loaded")

# Shared design tokens a rail must let INHERIT rather than redefine (web/THEMING.md rule 2).
INHERIT_ONLY_TOKENS = ("accent", "muted", "good")


@lru_cache(maxsize=1)
def _rail_template():
    """Import tools/rail_template.py as a module. The template generator and this checker share
    one definition of what "invariant" means, so they cannot disagree about it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rail_template", Path(__file__).resolve().parent / "rail_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- findings ---------------------------------------------------------------


@dataclass
class Finding:
    rule: str
    rail: str
    level: str  # "fail" | "warn"
    message: str
    where: str = ""

    def line(self) -> str:
        loc = f"  [{self.where}]" if self.where else ""
        return f"{self.level.upper():4} {self.rule}  {self.rail:24} {self.message}{loc}"


@dataclass
class Manifest:
    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.data.get("id") or self.path.parent.name)

    @property
    def root(self) -> Path:
        return self.path.parent

    def catalog_ids(self) -> list[str]:
        """Every catalog tile this rail's image backs — its own id first, then any
        `also_serves`. Normally just one; edu-suite backs 'edu-suite' + 'iep'."""
        extra = self.data.get("also_serves")
        return [self.id, *(extra if isinstance(extra, list) else [])]

    def slots(self) -> list[dict[str, Any]]:
        s = self.data.get("model_slots")
        return s if isinstance(s, list) else []

    def panel_slots(self) -> list[dict[str, Any]]:
        return [s for s in self.slots() if s.get("admin_panel")]

    def backend_dir(self) -> Path:
        """Where the rail's Python package lives, per its declared layout.

        `package_path` overrides both layouts for a rail that fits neither: edu-suite is a
        multi-app rail whose dashboard backend is at apps/dashboard/src/dashboard, and
        guessing wrong there means every source-reading rule silently checks nothing.
        """
        explicit = str(self.data.get("package_path") or "")
        if explicit:
            return self.root / explicit
        pkg = str(self.data.get("package") or "")
        if self.data.get("layout") == "src":
            return self.root / "src" / pkg
        return self.root / "backend" / pkg

    def frontend_src(self) -> Path:
        """The rail's frontend sources, following a relocated frontend.

        Most rails keep them at <root>/frontend/src. edu-suite serves its tiles from
        apps/dashboard/frontend/src, and hardcoding the common layout meant every TS-reading
        rule silently skipped it — a coverage hole that read as a pass. Walk up from
        `package_path` (apps/dashboard/src/dashboard -> apps/dashboard) to find the sibling.
        """
        default = self.root / "frontend" / "src"
        if default.is_dir():
            return default
        pkg = self.data.get("package_path")
        if pkg:
            cur = (self.root / str(pkg)).resolve()
            while cur != self.root.resolve() and cur.parent != cur:
                cur = cur.parent
                candidate = cur / "frontend" / "src"
                if candidate.is_dir():
                    return candidate
        return default

    def compose_service(self) -> str:
        """The compose service name, when it differs from the id. edu-suite's service is
        called `dashboard` — the rail was named after what it became, the service after what
        it was."""
        return str(self.data.get("compose_service") or self.id)

    def container_port(self) -> Any:
        """The port the backend listens on INSIDE its container, when that differs from the
        standalone port in `ports.backend`. The edu-suite family runs three separate
        containers that all listen on 8800; the gateway's 8800/8801/8802 are host-native
        defaults for standalone dev, overridden per-container in compose."""
        explicit = self.data.get("container_port")
        return explicit if isinstance(explicit, int) else (self.data.get("ports") or {}).get("backend")

    def py_sources(self) -> list[Path]:
        """The rail's own Python, including any sibling packages it owns.

        `extra_packages` matters for a multi-app rail: edu-suite's dashboard delegates every
        broker call to packages/edu-media-core, so reading only the dashboard package finds
        no token handling and reports a rail that is in fact correct.
        """
        dirs = [self.backend_dir()]
        extra = self.data.get("extra_packages")
        if isinstance(extra, list):
            dirs += [self.root / str(e) for e in extra]
        out: list[Path] = []
        for d in dirs:
            if d.is_dir():
                out += [p for p in sorted(d.rglob("*.py")) if "__pycache__" not in p.parts]
        return out

    def ts_sources(self) -> list[Path]:
        d = self.frontend_src()
        if not d.is_dir():
            return []
        return sorted([*d.rglob("*.ts"), *d.rglob("*.tsx")])

    def style_text(self) -> tuple[str, str]:
        """(text, where) for the rail's styles.

        Most rails ship frontend/src/theme.css. terminal-fun ships a template literal in
        module.tsx instead, so fall back to the module and say so — the theming rules apply
        to the CSS wherever it is written.
        """
        css = self.frontend_src() / "theme.css"
        if css.is_file():
            return css.read_text(encoding="utf-8", errors="replace"), rel(css)
        mod = self.frontend_src() / "module.tsx"
        if mod.is_file():
            return mod.read_text(encoding="utf-8", errors="replace"), rel(mod)
        return "", ""


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


# --- parsing the gateway's registries --------------------------------------


def _literal_assign(py: Path, name: str) -> Any:
    """Return the literal value assigned to `name` at module level, or None.

    ast.literal_eval rather than import: the gateway pulls in pydantic and platform_core,
    which a clean checkout has no obligation to have installed. These registries are plain
    literals, so parsing them is exact — not a heuristic.
    """
    src = read(py)
    if not src:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
    return None


def gateway_catalog() -> list[dict[str, Any]] | None:
    """The catalog's raw entries.

    Reads ``_ENTRIES`` — the literal list catalog.py writes before it derives the sorted,
    display-ordered ``APP_CATALOG`` from it. RC003 only checks id/label/icon, which are the
    same in either, and ``_ENTRIES`` is a plain literal so ast.literal_eval stays exact
    (``APP_CATALOG`` is now a ``sorted(...)`` call and cannot be literal-eval'd). Returns None
    (not []) when the literal cannot be read at all, because those are different problems: an
    empty list means "the rail is not registered", while unparseable means "this tool cannot
    tell", and reporting the second as the first blames every rail for one refactor.
    """
    return _literal_assign(GATEWAY / "catalog.py", "_ENTRIES")


def gateway_rail_slots() -> dict[str, list[dict[str, str]]]:
    return _literal_assign(GATEWAY / "rails_models.py", "RAIL_MODEL_SLOTS") or {}


def gateway_config_text() -> str:
    return read(GATEWAY / "config.py")


# --- the rules -------------------------------------------------------------
# Each rule is a function (manifest, all_manifests) -> list[Finding]. Registered with an
# id and a one-line summary that doubles as the contract statement in the report and in
# docs/RAIL_CONTRACT.md.

RULES: list[tuple[str, str, Callable[[Manifest, list[Manifest]], list[Finding]]]] = []


def rule(rid: str, summary: str):
    def deco(fn):
        RULES.append((rid, summary, fn))
        return fn
    return deco


def F(rid: str, m: Manifest, msg: str, where: str = "", level: str = "fail") -> Finding:
    return Finding(rule=rid, rail=m.id, level=level, message=msg, where=where)


@rule("RC001", "The manifest exists, parses, and declares every required key.")
def rc001(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    required = ["id", "label", "icon", "package", "federation_name", "css_wrapper",
                "layout", "env_prefix", "ports", "status_route", "model_slots"]
    out = [F("RC001", m, f"manifest is missing required key '{k}'", rel(m.path))
           for k in required if k not in m.data]
    # The id normally IS the directory name. One rail here legitimately differs: rails/iep/
    # serves catalog id 'iep-goals' (the catalog's 'iep' is Present Levels, a second instance
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
            clashes |= {rid for rid, p in _all_vite_ports().items()
                        if rid != m.id and p == port}
        if clashes:
            out.append(F("RC002", m, f"{kind} port {port} is also claimed by "
                                     f"{', '.join(sorted(clashes))}", rel(m.path)))
    return out


#: Directories never worth descending into. `native` is the big one here: ai-voice vendors
#: five engine runtimes (GPT-SoVITS alone ships a full site-packages), so a naive rglob over
#: rails/ spends ~30s walking third-party trees to find nothing. Filtering AFTER the walk, as
#: the original did, still pays that cost — the walk has to prune.
_SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "dist", "__pycache__", ".git",
                        "native", "site-packages", ".pytest_cache"})


def _walk(root: Path, filename_glob: str) -> list[Path]:
    """rglob with pruning. Same results as Path.rglob for our purposes, minus the detour
    through vendored dependency trees."""
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in _SKIP_DIRS:
                    stack.append(p)
            elif fnmatch(p.name, filename_glob):
                out.append(p)
    return sorted(out)


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
        return [F("RC003", m, "could not read the catalog's APP_CATALOG literal, so nothing "
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


@rule("RC005", "The broker token is read from the unprefixed BROKER_AUTH_TOKEN.")
def rc005(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Every rail's compose service is handed BROKER_AUTH_TOKEN, unprefixed, because it is
    one platform-wide shared secret rather than a per-rail setting. A rail that reads only
    its own prefixed spelling gets an empty token and sends no Authorization header — which
    is invisible until the broker starts enforcing, then every model call 401s at once.

    A prefixed alias IS allowed (pydantic AliasChoices), as long as the unprefixed name is
    among the names actually consulted.

    ALSO satisfied by delegating to the shared platform_core BrokerClient, which injects the
    bearer centrally (`_auth_headers()` reads the unprefixed name). Most rails here do that
    rather than rolling their own client, and flagging them for "never reading the token"
    would be nine false alarms — the fastest way to get a checker switched off.

    Matched against exact string CONSTANTS in the parsed AST, not raw text. A substring
    search over the source finds the name in a comment explaining the variable and passes a
    rail that never reads it — which is precisely how this defect stayed hidden.
    """
    if not m.slots():
        return []  # a rail that does no model work needs no token
    sources = m.py_sources()
    if not sources:
        return []

    reads_token = False
    prefixed_field: tuple[str, str] | None = None  # (attr, file) for a better message
    prefix = str(m.data.get("env_prefix") or "")
    for p in sources:
        try:
            tree = ast.parse(read(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "BROKER_AUTH_TOKEN":
                reads_token = True
            # Delegation to the shared client counts: it sends the header for them.
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "platform_core") and any(a.name == "BrokerClient" for a in node.names):
                reads_token = True
            # A pydantic-settings field named *_auth_token under a rail env_prefix resolves
            # to <PREFIX>BROKER_AUTH_TOKEN, which nothing in deploy/ sets.
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id.endswith("broker_auth_token"):
                prefixed_field = (node.target.id, rel(p))
    if reads_token:
        return []

    msg = ("backend never reads BROKER_AUTH_TOKEN, so it sends no broker Authorization "
           "header and every /v1/* call 401s once the broker enforces a token")
    where = rel(m.backend_dir())
    if prefixed_field:
        attr, where = prefixed_field
        msg = (f"reads the token ONLY as {prefix}{attr.upper()}. The canonical name is the "
               f"unprefixed BROKER_AUTH_TOKEN, which is what every other rail reads and what "
               f"deploy/docker-compose.yml passes to all nine services; a rail that accepts "
               f"only its prefixed spelling works under whichever compose file was bent to "
               f"match it and silently gets no token under the other")
    return [F("RC005", m, msg, where)]


@rule("RC006", "Panel slots have a matching gateway RAIL_MODEL_SLOTS entry, and vice versa.")
def rc006(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    where = rel(GATEWAY / "rails_models.py")
    panel = gateway_rail_slots().get(m.id)
    mine = {str(s["slot"]): s for s in m.panel_slots() if s.get("slot")}
    if panel is None:
        if mine:
            return [F("RC006", m, f"declares admin-panel slots {sorted(mine)} but the rail "
                                  f"is absent from RAIL_MODEL_SLOTS, so Admin -> Rails "
                                  f"cannot repoint it", where)]
        return []
    theirs = {str(s.get("slot")): s for s in panel}
    out: list[Finding] = []
    for slot in sorted(set(mine) - set(theirs)):
        out.append(F("RC006", m, f"slot '{slot}' is admin_panel but RAIL_MODEL_SLOTS has "
                                 f"{sorted(theirs)}", where))
    for slot in sorted(set(theirs) - set(mine)):
        out.append(F("RC006", m, f"RAIL_MODEL_SLOTS declares slot '{slot}' which the "
                                 f"manifest does not", where))
    for slot in sorted(set(mine) & set(theirs)):
        w, g = str(mine[slot].get("role")), str(theirs[slot].get("role"))
        if w != g:
            out.append(F("RC006", m, f"slot '{slot}' role mismatch: manifest {w!r} vs "
                                     f"panel {g!r}", where))
        w, g = str(mine[slot].get("env")), str(theirs[slot].get("env"))
        if w != g:
            out.append(F("RC006", m, f"slot '{slot}' env mismatch: manifest {w!r} vs "
                                     f"panel {g!r}", where))
    return out


@rule("RC007", "The rail's own chip slot ids match the manifest.")
def rc007(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The rail declares its chip slots as (slot, label, ref) tuples — MODEL_SLOTS at module
    level, or a _model_slots() helper. Both spellings are in use; find whichever is present
    and compare the slot ids only. Labels are cosmetic and deliberately not enforced.

    Heuristic by necessity (the tuples reference config attributes, so they are not literal
    and ast.literal_eval cannot touch them). It extracts the first string of each tuple and
    only reports when it found some — never when it found none, so an unrecognised spelling
    is a silent skip rather than a false accusation.
    """
    if m.data.get("status_route") is None:
        return []
    declared = {str(s["slot"]) for s in m.slots() if s.get("slot")}
    if not declared:
        return []

    found: set[str] = set()
    where = ""
    for p in m.py_sources():
        src = read(p)
        for block in re.findall(
            r"(?:MODEL_SLOTS[^=]*=\s*\[|def _model_slots\(\)[^:]*:.*?return\s*\[)(.*?)\]",
            src, re.S,
        ):
            ids = re.findall(r"\(\s*[\"']([a-z][a-z0-9-]*)[\"']\s*,", block)
            if ids:
                found.update(ids)
                where = where or rel(p)
    if not found:
        return []
    out: list[Finding] = []
    for slot in sorted(found - declared):
        out.append(F("RC007", m, f"chip code declares slot '{slot}' which the manifest does "
                                 f"not (manifest: {sorted(declared)})", where))
    for slot in sorted(declared - found):
        out.append(F("RC007", m, f"manifest declares slot '{slot}' which the chip code does "
                                 f"not (code: {sorted(found)})", where))
    return out


@rule("RC008", "Model chips use the four-state contract, not a two-state resident flag.")
def rc008(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """missing / cold / warming / loaded, on both sides of the wire. The red-vs-blue
    distinction is the operationally useful one: red needs an `ollama pull`, blue just needs
    someone to ask a question. A two-state dot collapses those into one useless "off".
    """
    if m.data.get("status_route") is None:
        return []
    out: list[Finding] = []

    py = "\n".join(read(p) for p in m.py_sources())
    if py and not all(f'"{s}"' in py or f"'{s}'" in py for s in CHIP_STATES):
        missing = [s for s in CHIP_STATES if f'"{s}"' not in py and f"'{s}'" not in py]
        out.append(F("RC008", m, f"backend never emits chip state(s) {missing} — the "
                                 f"four-state contract is not implemented server-side",
                     rel(m.backend_dir())))
    if re.search(r'"resident"\s*:', py):
        out.append(F("RC008", m, "backend emits a boolean \"resident\" field; the contract "
                                 "is a four-valued \"state\"", rel(m.backend_dir())))
    if re.search(r'"broker_reachable"\s*:', py):
        out.append(F("RC008", m, "backend emits \"broker_reachable\"; the contract envelope "
                                 "key is \"broker\": \"ok\" | \"unreachable\"",
                     rel(m.backend_dir())))

    # Frontend: only a dot driven by MODEL residency is a violation. A binary dot is correct
    # for things that really are binary — smb-partner's voice backend, terminal-fun's PTY
    # connection — so keying on a generic `? 'on' : 'off'` would flag those forever, and a
    # rule that cries wolf gets suppressed. Key on the model-residency field instead.
    for p in m.ts_sources():
        src = read(p)
        for hit in re.finditer(r"dot \$\{[^}]*\}", src):
            if re.search(r"\.resident\b", hit.group(0)):
                out.append(F("RC008", m, "frontend renders a model dot from a boolean "
                                         ".resident; the contract is a four-valued .state",
                             rel(p)))
        if re.search(r"\.resident\b", src):
            out.append(F("RC008", m, "frontend still reads the removed boolean .resident "
                                     "field", rel(p)))
    return out


@rule("RC009", "The vite dev server and its API proxy use the declared ports.")
def rc009(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    vc = m.root / "frontend" / "vite.config.ts"
    src = read(vc)
    if not src:
        return []
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
    vc = m.root / "frontend" / "vite.config.ts"
    src = read(vc)
    out: list[Finding] = []
    fed = str(m.data.get("federation_name") or "")
    if src:
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


def _compose_service(src: str, name: str) -> str | None:
    """The text of one two-space-indented service block. Regex rather than a YAML parse:
    pyyaml is not stdlib and this tool must run on a bare checkout."""
    start = re.search(rf"^  {re.escape(name)}:\s*(?:#.*)?$", src, re.M)
    if not start:
        return None
    nxt = re.search(r"^  [a-z][a-z0-9_-]*:", src[start.end():], re.M)
    return src[start.end(): start.end() + nxt.start()] if nxt else src[start.end():]


@rule("RC012", "Styles are scoped to the declared wrapper and inherit the shared palette.")
def rc012(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """web/THEMING.md rules 1, 2 and 4. Checks the two failures that actually break a
    palette: redefining an inherited token, and painting a status dot a literal hex so it
    stops tracking --good/--critical.
    """
    text, where = m.style_text()
    if not text:
        return []
    wrapper = str(m.data.get("css_wrapper") or "")
    out: list[Finding] = []
    if wrapper and not re.search(rf"[.\s]{re.escape(wrapper)}\b\s*\{{", text):
        out.append(F("RC012", m, f"no '.{wrapper} {{' block — the manifest says styles are "
                                 f"scoped there", where))
    for tok in INHERIT_ONLY_TOKENS:
        if re.search(rf"^\s*--{tok}\s*:", text, re.M):
            out.append(F("RC012", m, f"redefines --{tok}, which must inherit the chosen "
                                     f"palette (THEMING.md rule 2)", where))
    # Driven by the four STATE NAMES, not by a `.dot.` class literal: co-worker names its
    # element .cw-dot, so a `\.dot\.` pattern silently exempted it — it passed this rule while
    # hardcoding the same four hexes as everyone else. Match any selector ending in a
    # dot-ish class plus a state suffix.
    dot_state = re.compile(
        rf"[.\w-]*dot[\w-]*\.({'|'.join(CHIP_STATES)})\b[^{{]*\{{[^}}]*?"
        rf"background:\s*(#[0-9a-fA-F]{{3,6}})",
        re.S,
    )
    for hit in dot_state.finditer(text):
        out.append(F("RC012", m, f"'{hit.group(1)}' status dot painted with literal "
                                 f"{hit.group(2)}; use the shared status tokens (--critical / "
                                 f"--warning / --good) so it reads on every palette", where))
    return out


@rule("RC013", "Model slots reference an @role, so Admin -> Rails stays authoritative.")
def rc013(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """A rail that pins a concrete model name silently ignores the admin panel: the admin
    repoints the role, the rail keeps using the pin, and nothing reports the disagreement.
    Checks the IN-CODE default, not the compose override — a compose-only fix leaves
    standalone dev pinned, which is how this class of bug survived its last cleanup.

    Set pinned_default_ok on the slot (with a note) for a deliberate exception.
    """
    out: list[Finding] = []
    blob = {p: read(p) for p in m.py_sources()}
    for s in m.slots():
        if s.get("pinned_default_ok"):
            continue
        env, role = str(s.get("env") or ""), str(s.get("role") or "")
        if not env:
            continue
        attr = env[len(str(m.data.get("env_prefix") or "")):].lower() or env.lower()
        for p, src in blob.items():
            # pydantic-settings style:  synthesis_model: str = "@co-worker-synthesis"
            for hit in re.finditer(rf"^\s*{re.escape(attr)}:\s*str\s*=\s*\"([^\"]*)\"", src, re.M):
                if not hit.group(1).startswith("@"):
                    out.append(F("RC013", m, f"slot '{s.get('slot')}' in-code default "
                                             f"{hit.group(1)!r} is not an @role (expected "
                                             f"'@{role}')", rel(p)))
            # os.environ style:  MODEL = os.environ.get("GEMINI_CX_RAG_MODEL", "@gemini-cx-rag")
            # os.getenv is the same thing and was the blind spot: matching only environ.get
            # meant this rule read 0 findings while edu-suite sat on four live pinned models.
            for hit in re.finditer(
                rf"os\.(?:environ\.get|getenv)\(\s*[\"']{re.escape(env)}[\"']\s*,"
                rf"\s*[\"']([^\"']*)[\"']",
                src,
            ):
                if not hit.group(1).startswith("@"):
                    out.append(F("RC013", m, f"slot '{s.get('slot')}' default for {env} is "
                                             f"{hit.group(1)!r}, not an @role (expected "
                                             f"'@{role}')", rel(p)))
    return out


@rule("RC014", "Every compose file passes the broker token under the canonical unprefixed name.")
def rc014(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RC005 checks what the rail READS; this checks what deploy/ WRITES.

    Both matter, and only together. co-worker read the token solely as
    CO_WORKER_BROKER_AUTH_TOKEN, so whoever wired the installer compose bent that file to spell
    it the same way — which made the rail work there and silently tokenless under
    deploy/docker-compose.yml, where all nine services get the unprefixed name. Fixing the rail
    without fixing the compose file just moves the disagreement.

    A rail with no model slots needs no token and is skipped. So is a service that pulls the
    whole gitignored deploy/.env via `env_file:` — that file is where BROKER_AUTH_TOKEN
    actually lives, so those services get it without naming it, and flagging them would be a
    false alarm on a rail that authenticates correctly today.
    """
    if not m.slots():
        return []
    canonical = "BROKER_AUTH_TOKEN"
    out: list[Finding] = []
    for cf in (COMPOSE, REPO / "deploy" / "installer" / "docker-compose.installer.yml"):
        src = read(cf)
        if not src:
            continue
        block = _compose_service(src, m.compose_service())
        if block is None:
            continue  # RC011 owns "the service is missing"; a profile-gated file may omit it
        if re.search(r"^\s*env_file:", block, re.M):
            continue  # inherits the whole .env, token included
        # The prefix group must be OPTIONAL and must itself end in '_'. Written as
        # [A-Z][A-Z0-9_]*BROKER_AUTH_TOKEN it consumes a character before the literal, so the
        # bare canonical name never matches and every correctly-wired service reports as
        # passing no token at all — nine false alarms, which is how a rule gets ignored.
        names = set(re.findall(r"^\s+((?:[A-Z][A-Z0-9_]*_)?BROKER_AUTH_TOKEN):", block, re.M))
        if not names:
            out.append(F("RC014", m, f"service passes no broker token, so the rail cannot "
                                     f"authenticate to the broker once one is enforced",
                         rel(cf), level="warn"))
        elif canonical not in names:
            out.append(F("RC014", m, f"service passes the token as {sorted(names)[0]} rather "
                                     f"than the canonical {canonical}", rel(cf)))
    return out


@rule("RC016", "The gateway's <id>_dist default points at a directory that exists.")
def rc016(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """`resolved_app_dists()` SKIPS a rail whose dist is missing rather than failing — its own
    docstring says "the app just won't load its bundle". Combined with a stale default, that is
    a rail serving nothing, silently.

    Which is what had happened: five defaults still pointed at pre-monorepo standalone
    directories that no longer existed, so a host-native gateway resolved 6 of 11 rails. Nobody
    noticed because compose overrides every one of these with PLATFORM_*_DIST, so containers
    were fine — the value only matters for standalone dev, which is exactly where nobody looks.

    Checks the DEFAULT in config.py, deliberately, not the runtime value. The runtime value is
    whatever the deployment sets; the default is the thing that rots.

    Reports the directory rather than remoteEntry.js: a rail that simply has not been built yet
    is a normal state, and failing on that would make the checker cry wolf on a fresh clone.
    """
    cfg_path = GATEWAY / "config.py"
    cfg = gateway_config_text()
    if not cfg:
        return []
    out: list[Finding] = []
    for app_id in m.catalog_ids():
        ident = app_id.replace("-", "_")
        hit = re.search(rf"^\s*{ident}_dist:\s*str\s*=\s*r?\"([^\"]+)\"", cfg, re.M)
        if not hit:
            continue  # RC004 owns "the setting is missing"
        p = Path(hit.group(1))
        if not p.is_dir():
            out.append(F("RC016", m, f"{ident}_dist default points at {hit.group(1)!r}, which "
                                     f"does not exist — resolved_app_dists() will skip this "
                                     f"rail and it will serve no bundle, without an error",
                         rel(cfg_path)))
    return out


@rule("RC015", "A rail with a status_route polls it, so its chips cannot freeze.")
def rc015(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The four states describe LIVE residency, and residency changes with nobody touching
    the UI: the broker evicts on a keep_alive expiry, and asking a question warms a model
    back up. So a one-shot fetch on mount renders a state that is right for about a second
    and silently wrong afterwards.

    Inert here today — every rail on this platform sets status_route: null, because per-rail
    chips are a deferred follow-on. It is carried anyway so the two copies of this checker do
    not quietly diverge, and so the rule is already in place the day chips land.

    It is not hypothetical. The sibling public repo shipped a rail that fetched its status
    route once in a mount effect with an empty dep array and never again; its chips sat on
    `cold` for a model that was loaded and actively answering, while the shell's own top-bar
    widget, which does poll, correctly showed it resident. Every other rule passed on that
    rail, because the payload SHAPE was perfect. Liveness is a property of the caller, not the
    envelope, so no amount of shape-checking would ever have caught it.

    Deliberately narrow. It looks at the files that touch the status route (directly or via
    an accessor exported from the rail's api module) and only complains when NOT ONE of them
    sets an interval. A rail that polls by some other mechanism, or whose fetch site this
    misses, is a silent pass — a false negative, which is the side to err on.
    """
    route = str(m.data.get("status_route") or "")
    if not route:
        return []
    srcs = {p: read(p) for p in m.ts_sources()}
    if not srcs:
        return []

    # The accessor(s) wrapping the route. Two shapes are in use and both must be found:
    #   export const capabilities = () => getJSON('/api/capabilities')   (gemini-cx)
    #   export const api = { capabilities: () => getJSON('/api/capabilities'), ... }  (bouquet)
    # Matching only the first meant bouquet and recipe-book looked like they never polled --
    # they both do, at 6s, from module.tsx. The rule reported two false positives the moment
    # their manifests stopped saying status_route: null.
    accessors: set[str] = set()
    for src in srcs.values():
        for hit in re.finditer(
            rf"export\s+(?:const|function)\s+(\w+)[^\n]*{re.escape(route)}", src
        ):
            accessors.add(hit.group(1))
        for hit in re.finditer(rf"(\w+)\s*:\s*\([^)]*\)\s*=>[^\n]*{re.escape(route)}", src):
            accessors.add(hit.group(1))

    # Every file that either names the route or calls one of its accessors. The polling has
    # to live in one of them. `.name(` as well as `name(`, since the object shape is called
    # as a method.
    touching = {
        p for p, src in srcs.items()
        if route in src or any(re.search(rf"(?:\.|\b){a}\s*\(", src) for a in accessors)
    }
    if not touching:
        return []
    if any("setInterval" in srcs[p] for p in touching):
        return []
    where = sorted(touching, key=lambda p: (p.name != "module.tsx", str(p)))[0]
    return [F("RC015", m, f"fetches {route} but nothing sets an interval, so the chips "
                          f"render their page-load state forever — a model that loads "
                          f"afterwards keeps reporting the state it had at mount",
              rel(where))]


@rule("RC017", "The rail's frontend renders the shared RailHeader from @web-core.")
def rc017(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """One header for every rail: icon · bold title · muted subtext · model chips · a rule.
    It lives in web/src/RailHeader.tsx (+ ModelChips) so it stays identical everywhere; see
    RAIL_CONTRACT.md. A rail that hand-rolls its own header is exactly the drift this whole
    contract exists to stop, so require the shared component rather than trusting each rail to
    reproduce the shape.

    Checks for BOTH the import from '@web-core' and a `<RailHeader` usage, so re-exporting the
    name without rendering it does not pass. Chips are NOT required here — a rail with no
    foreground model (workstation, ai-voice) legitimately renders <RailHeader> without them;
    RC007/RC008 already own chip correctness.

    Relocated frontends ARE covered as of the frontend_src fix: edu-suite serves its two tiles
    from apps/dashboard/frontend/src, which this rule could not see, so it skipped the rail
    entirely and reported nothing — a coverage hole that read exactly like a pass.
    """
    srcs = m.ts_sources()
    if not srcs:
        return []
    text = "\n".join(read(p) for p in srcs)
    imports_it = "RailHeader" in text and "@web-core" in text
    renders_it = "<RailHeader" in text
    if imports_it and renders_it:
        return []
    return [F("RC017", m, "frontend does not use the shared RailHeader from '@web-core' — every "
                          "rail's header must be <RailHeader> (web/src/RailHeader.tsx) so the "
                          "icon / title / subtext / chips / rule stay identical; see "
                          "RAIL_CONTRACT.md", rel(m.frontend_src()))]


@rule("RC018", "No quarantined student identifier appears in any tracked file.")
def rc018(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The iep rail holds records about real children. `local/` keeps the SOURCE documents out of
    git, but nothing stopped the NAMES inside them walking out into code — and they did: golden
    assertions, unit fixtures copied from a real case, and edge-case comments naming whose sheet
    exhibited the bug. Fixed 2026-08-23; this rule is what stops it coming back, because the leak
    is a natural by-product of debugging against real data, not carelessness.

    Repo-wide by design, though it is registered against one rail: a name can land in any file.
    It runs once (on the owning rail) and scans every tracked path.

    The identifiers are read from the GITIGNORED rails/iep/local/pseudonyms.json, so the checker
    itself never contains them. Two consequences, both deliberate:
      * on a clean checkout the guard cannot run, and says so as a WARN rather than passing
        silently — a check that quietly does nothing is worse than no check;
      * a finding reports FILE and LINE only, never the matched text, because echoing the name
        into a terminal or CI log just moves the leak.

    Matching is case-SENSITIVE, so an ordinary lowercase noun that happens to also be one of the
    surnames does not trip the capitalised name. (This docstring deliberately does not spell out
    an example: the publish gate scans case-INSENSITIVELY, and an illustrative surname here would
    fail that scan — the same "do not restate the identifier" rule this rule itself enforces.)
    Add genuine exceptions to "scan_allow" in the pseudonyms file rather than loosening this.
    """
    if m.id != "iep-goals":          # run once, not once per rail
        return []
    names_file = REPO / "rails" / "iep" / "local" / "pseudonyms.json"
    if not names_file.is_file():
        return [F("RC018", m, "cannot verify: rails/iep/local/pseudonyms.json is absent, so the "
                              "student-identifier scan did NOT run (expected on a clean checkout; "
                              "on the box that holds the data, restore it)", "", level="warn")]
    try:
        spec = json.loads(names_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [F("RC018", m, f"cannot verify: pseudonyms.json is unreadable ({exc.__class__.__name__})",
                  rel(names_file))]

    tokens: set[str] = set()
    for row in spec.get("students", []):
        real = str(row.get("real", "")).strip()
        if not real:
            continue
        tokens.add(real)                                   # "Last, First" - always
        surname, _, first = real.partition(",")
        surname, first = surname.strip(), first.strip()
        if len(surname) > 2:
            tokens.add(surname)                            # the identifying half
        # A bare first name is weak identification on its own, and some collide with ordinary
        # proper nouns elsewhere in the monorepo (a vendor product line, say). Scanning those
        # produces noise that trains you to ignore the rule, so a student may set
        # "scan_first_name": false and rely on the full-name + surname tokens instead.
        if len(first) > 2 and row.get("scan_first_name", True):
            tokens.add(first)
    allow = [a for a in spec.get("scan_allow", []) if a]
    # Path prefixes exempted wholesale, for subtrees of third-party product documentation
    # where a first name collides with an unrelated vendor proper noun. Keep these NARROW:
    # a broad prefix silently switches the guard off for a whole subtree.
    allow_paths = [a for a in spec.get("scan_allow_paths", []) if a]
    if not tokens:
        return [F("RC018", m, "cannot verify: pseudonyms.json lists no students", rel(names_file))]

    try:
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                                 check=True).stdout.decode("utf-8", "replace").split("\0")
    except (OSError, subprocess.CalledProcessError) as exc:
        return [F("RC018", m, f"cannot verify: git ls-files failed ({exc.__class__.__name__})", "")]

    out: list[Finding] = []
    for relpath in tracked:
        if not relpath:
            continue
        if any(relpath.startswith(a) for a in allow_paths):
            continue
        path = REPO / relpath
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                                       # binary or unreadable: nothing to leak
        if not any(t in text for t in tokens):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(a in line for a in allow):
                continue
            if any(t in line for t in tokens):
                # Name deliberately omitted: see the docstring.
                out.append(F("RC018", m, "a quarantined student identifier appears here — replace "
                                         "it with its pseudonym from rails/iep/local/pseudonyms.json "
                                         "(real names belong only in local/)", f"{relpath}:{n}"))
    return out


@rule("RC019", "The rail declares user-facing copy for the admin Rail Manager.")
def rc019(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Every rail needs a `description`: one or two sentences a person choosing whether to enable
    it would actually read.

    It is a separate field from the three that already exist because each of those answers a
    different question and none answers this one. `label` is a tile caption ("Voice Studio"),
    `notes` is developer caveats (9p rename semantics, port collisions), and a rail's
    `RailHeader subtitle` lives inside the federated bundle — which the catalog cannot reach,
    since the whole point is describing rails that are NOT currently mounted.

    Length is bounded on both sides on purpose. Too short and it restates the label; too long and
    a card grid turns into an essay. The schema enforces 40-400; this rule makes a MISSING one
    fail, which the schema cannot (it is an optional property so old manifests keep validating).
    """
    desc = m.data.get("description")
    if not desc:
        return [F("RC019", m, "manifest has no 'description' — the Rails Catalog would show this "
                              "rail with no explanation of what it is for", rel(m.path))]
    if not isinstance(desc, str) or not (40 <= len(desc) <= 400):
        return [F("RC019", m, f"'description' should be 40-400 characters of user-facing copy "
                              f"(got {len(desc) if isinstance(desc, str) else type(desc).__name__})",
                  rel(m.path))]
    if desc.strip().lower().startswith(str(m.data.get("label", "")).lower() + " "):
        return [F("RC019", m, "'description' just restates the label; say what the rail is FOR",
                  rel(m.path), level="warn")]
    return []


@rule("RC020", "No shared component reaches into this rail's tree by name.")
def rc020(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The dependency arrow points one way: a rail may use the broker and the shell, and neither
    may use a rail. A shared component that hardcodes a path into rails/<id> makes that rail
    load-bearing for the whole platform, which is the opposite of a pluggable component.

    This is not hypothetical. The broker used to resolve edu_media_core out of
    rails/edu-suite/packages/, so removing edu-suite would have broken image generation and
    XTTS for every rail — recipe-book icons and bouquet vision included, neither of which has
    heard of edu-suite. That one moved to packages/edu-media-core. The broker still reaches
    into rails/ai-voice/native for the host-native voice engines, which is the remaining warn.

    Scope is deliberate. `deploy/` is EXEMPT: orchestrating rails is exactly its job, and every
    compose build context and dist mount names a rail legitimately. Generic enumeration is fine
    too — the gateway's `RAILS = REPO_ROOT / "rails"` discovers rails without naming one, which
    is how a shell is supposed to work. Only naming a SPECIFIC rail is a finding.

    Comments are stripped first, so an explanatory reference does not fail the rule.

    FAIL as of 2026-08-24: all three original violations are gone. edu_media_core and the XTTS
    clips moved to packages/edu-media-core, and the voice engines' location became deployment
    configuration (BROKER_VOICE_ENGINES_DIR) rather than a hardcoded path. It carried WARN while
    that was in progress, because a permanently-red gate is one nobody reads.
    """
    roots = [REPO / "services", REPO / "packages", REPO / "apps" / "platform"]
    out: list[Finding] = []
    # rails/<id>  or  "rails" / "<id>" as path segments (the form that hid these for months).
    pat = re.compile(rf"rails[/\\]{re.escape(m.id)}\b"
                     rf"|[\"']rails[\"']\s*/\s*[\"']{re.escape(m.id)}[\"']")
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.py")):
            # Tests are excluded: this rule is about RUNTIME coupling, and a regression test
            # legitimately names the path it exists to keep out of the runtime. Only `#`
            # comments are stripped below, so a docstring describing the old coupling would
            # otherwise fail the rule that documents it.
            if ({".venv", "node_modules", "tests"} & set(p.parts)) or p.name.startswith("test_"):
                continue
            for i, raw in enumerate(read(p).splitlines(), 1):
                line = raw.split("#", 1)[0]
                if pat.search(line):
                    out.append(F("RC020", m, f"shared code hardcodes a path into this rail "
                                             f"({raw.strip()[:70]}) — the broker and shell must "
                                             f"not depend on a rail", f"{rel(p)}:{i}"))
    return out


@rule("RC021", "A rail with an API fails closed on a missing platform identity.")
def rc021(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The gateway authenticates every request and sets X-Platform-User, stripping any client
    copy. A request without it did not come through the gateway — on this platform that means a
    sibling container — so the only safe response is 401.

    Eight of fourteen rails got this wrong, in three flavours. Three had an INVERTED admin gate
    (`if user is not None and not is_admin: 403`), which rejects a named non-admin but waves
    through a caller with no header at all. Three defaulted the user to the string "?". Two had
    no identity code whatsoever. The worst case was workstation, which read the header only to
    label its audit line and then opened an SSH session on the host as Admin.

    Three things are checked, because each catches a different flavour:
      - a 401 exists for the header-less case
      - the escape hatch is the platform-wide PLATFORM_STANDALONE, not a per-rail name
      - the inverted predicate is gone, and Header(default="?") with it

    A rail with no FastAPI app is skipped; there is nothing to gate.
    """
    srcs = {p: read(p) for p in m.py_sources()}
    if not any("FastAPI(" in s for s in srcs.values()):
        return []
    blob = "\n".join(srcs.values())
    out: list[Finding] = []

    # The header is spelled three ways depending on how it is read: the FastAPI parameter name
    # (x_platform_user), a raw header lookup (x-platform-user), or the canonical form in prose.
    reads_identity = re.search(r"x[_-]platform[_-]user", blob, re.I)
    if "401" not in blob or not reads_identity:
        out.append(F("RC021", m, "no 401 for a request with no X-Platform-User — a sibling "
                                 "container can call this rail directly", rel(m.path)))

    for p, src in srcs.items():
        for i, raw in enumerate(src.splitlines(), 1):
            line = raw.split("#", 1)[0]
            if re.search(r'Header\(\s*default\s*=\s*[\"\']\?[\"\']', line):
                out.append(F("RC021", m, "identity defaults to \"?\" — a header-less caller is "
                                         "treated as a real user", f"{rel(p)}:{i}"))
            # Only an `if ...:` branch is the bug. The same predicate as an ASSIGNMENT is a
            # legitimate non-security use (recipe-book decides a contributor's category with
            # it), and it also appears inside docstrings describing the fix.
            if (line.lstrip().startswith("if ") and line.rstrip().endswith(":")
                    and re.search(r"user is not None and not .*is_admin", line)):
                out.append(F("RC021", m, "inverted admin gate: this rejects a NAMED non-admin "
                                         "but passes a caller with no identity at all",
                             f"{rel(p)}:{i}"))
            for legacy in ("EDU_STANDALONE", "IEP_STANDALONE", "AI_PLAYGROUND_STANDALONE",
                           "GEMINI_CX_STANDALONE", "SMB_PARTNER_STANDALONE"):
                if legacy in line:
                    out.append(F("RC021", m, f"uses the retired {legacy}; the escape hatch is "
                                             f"PLATFORM_STANDALONE on every rail",
                                 f"{rel(p)}:{i}"))
    return out


@rule("RC022", "The rail's broker facade exposes the canonical surface.")
def rc022(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """roles(), models() -> list[dict], status(), and a BrokerError.

    Only required of a rail with a modelstate.py, because that file is GENERATED against
    exactly this surface. When the surface varied, the resolver had to vary with it: eleven
    copies of modelstate.py had grown into five distinct implementations of a function the
    contract calls identical everywhere. One of them unwrapped a dict because its rail's
    models() returned the UI picker shape under the same name.

    The facade module is the rail's own broker.py unless the manifest declares `broker_module`
    (edu-suite reaches a platform package; job-aid's is called broker_status).
    """
    try:
        tpl = _rail_template()
    except Exception as exc:                      # noqa: BLE001 - tool missing is not a rail fault
        return [F("RC022", m, f"cannot load tools/rail_template.py ({exc})", "", level="warn")]
    rail = tpl.Rail(m.path)
    if rail.modelstate_path() is None:
        return []
    gaps = tpl.facade_gaps(rail)
    if not gaps:
        return []
    src = tpl.facade_module(rail)
    where = rel(src) if src else rel(m.path)
    return [F("RC022", m, f"broker facade is missing {gaps} — modelstate.py is generated "
                          f"against roles/models/status/BrokerError and cannot read this one",
              where)]


@rule("RC023", "Generated files match the rail template.")
def rc023(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """identity.py and modelstate.py are emitted by tools/rail_template.py and must match it
    byte for byte.

    A contract that only SAYS files are identical does not make them so — nothing was comparing
    the copies, and they drifted. Two identity.py copies differed solely in line endings, which
    no reviewer would ever see. `rail_template.py sync` adopts the template; changing the
    template and syncing is how a change to these files is made.
    """
    try:
        tpl = _rail_template()
    except Exception as exc:                      # noqa: BLE001
        return [F("RC023", m, f"cannot load tools/rail_template.py ({exc})", "", level="warn")]
    rail = tpl.Rail(m.path)
    out: list[Finding] = []
    for name, dest in tpl.invariants(rail):
        want = tpl.render(name, rail)
        have = dest.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        if want != have:
            out.append(F("RC023", m, f"{name} has drifted from the template — run "
                                     f"`python tools/rail_template.py check --diff --rail {m.id}`",
                         rel(dest)))
    return out


def _deskpet_registered(lines_ts: str) -> set[str]:
    """Catalog ids present in the RAIL map in deskpet/lines.ts.

    The map mixes two JS forms: a quoted key for a hyphenated id ("recipe-book": recipeBook)
    and shorthand for a single-word one (bouquet,). Matching only the quoted form reported
    finance, iep and workstation as unregistered when they are registered.
    """
    body = lines_ts.split("const RAIL", 1)[-1]
    body = body.split("};", 1)[0]
    ids: set[str] = set()
    for raw in body.splitlines():
        line = raw.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        m = re.match(r'^["\'"]([^"\']+)["\']\s*:', line)
        if m:
            ids.add(m.group(1)); continue
        m = re.match(r"^([A-Za-z_][\w]*)$", line)
        if m:
            ids.add(m.group(1))
    return ids


@rule("RC024", "The rail has a deskpet quip bank, registered under its catalog id.")
def rc024(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RAIL_CONTRACT step 6: a bank at deskpet/quips/<catalog-id>.json plus an entry in the
    RAIL map in deskpet/lines.ts.

    WARN, not FAIL. Four rails shipped without one (co-worker, gemini-cx, smb-partner,
    meeting-atlas) because nothing checked, and writing 100+ quips is not something to block a
    build on. Without the bank `pickIdle` falls through to the generic pool and `pickArrive`
    returns the bare label, so the rail is quietly less alive than its neighbours and no one
    can tell why.

    Banks key off the CATALOG id, not the directory — which is how ai-voice and iep-goals
    shipped without theirs even though their folders look right.
    """
    deskpet = SHELL / "deskpet"
    if not deskpet.is_dir():
        return []
    lines_ts = read(deskpet / "lines.ts") if (deskpet / "lines.ts").is_file() else ""
    out: list[Finding] = []
    for cid in m.catalog_ids():
        bank = deskpet / "quips" / f"{cid}.json"
        if not bank.is_file():
            out.append(F("RC024", m, f"no deskpet quip bank at deskpet/quips/{cid}.json — the "
                                     f"pet falls back to generic lines for this rail",
                         rel(deskpet / "quips"), level="warn"))
            continue
        if cid not in _deskpet_registered(lines_ts):
            out.append(F("RC024", m, f"quip bank {cid}.json exists but is not registered in the "
                                     f"RAIL map in deskpet/lines.ts, so it is never read",
                         rel(deskpet / "lines.ts"), level="warn"))
    return out


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
        return []                        # not offered by the installer; nothing to agree with
    pid = _lean_profile(m.root.name)
    if not pid:
        return []                        # unprofiled means always installed
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
        return []
    src, where = read(BUNDLED), rel(BUNDLED)
    if not src:
        return []
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
    """roles.lean.json is what the installer copies over services/broker/roles.json on a small
    card. A per-rail role missing from it does not fail the install — it fails that rail's first
    model call, at runtime, as an @role the broker cannot expand.

    Image slots are exempt by design: the lean profile runs with the broker media pipeline off
    (BROKER_MEDIA_ENABLED=false) and recipe-book's icons pre-rendered into its seed, both stated
    in deploy/installer/env.lean.example. Nothing there ever invokes @recipe-icon.
    """
    if m.root.name not in _lean_services():
        return []
    raw = read(ROLES_LEAN)
    if not raw:
        return []
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


# --- runner ----------------------------------------------------------------


def load_manifests() -> tuple[list[Manifest], list[Finding]]:
    out: list[Manifest] = []
    errs: list[Finding] = []
    for p in sorted(RAILS.glob("*/rail.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errs.append(Finding("RC001", p.parent.name, "fail",
                                f"rail.json does not parse: {exc}", rel(p)))
            continue
        out.append(Manifest(path=p, data=data))
    return out, errs


def unmanifested_rails() -> list[str]:
    """Rail directories with a frontend but no manifest — in the tree yet outside the
    contract, which is the state every drift so far started from.

    An empty directory skeleton is not a rail. `rails/bouquet/` is a gitignored local scaffold:
    every subdirectory, zero files. Reporting it as "outside the contract" is noise, and noise
    is how a report stops being read — so require at least one actual file.
    """
    out = []
    for d in sorted(RAILS.iterdir()):
        if not d.is_dir() or (d / "rail.json").is_file() or not (d / "frontend").is_dir():
            continue
        if not any(p.is_file() for p in d.rglob("*")):
            continue
        out.append(d.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--rail", action="append", help="only this rail (repeatable)")
    ap.add_argument("--rule", action="append", help="only this rule id (repeatable)")
    ap.add_argument("--warn-only", action="store_true",
                    help="always exit 0; report findings without failing a build")
    ap.add_argument("--rules", action="store_true",
                    help="list the enforced rules and exit (the contract's live index)")
    args = ap.parse_args()

    if args.rules:
        # docs/RAIL_CONTRACT.md points here rather than restating the list, so the prose
        # cannot drift from what is actually enforced.
        print(f"{len(RULES)} enforced rules:\n")
        for rid, summary, _fn in RULES:
            print(f"  {rid}  {summary}")
        return 0

    manifests, findings = load_manifests()
    selected = [m for m in manifests if not args.rail or m.id in args.rail]

    for rid, _summary, fn in RULES:
        if args.rule and rid not in args.rule:
            continue
        for m in selected:
            try:
                findings.extend(fn(m, manifests))
            except Exception as exc:  # a broken rule must not mask the other rules
                findings.append(Finding(rid, m.id, "warn",
                                        f"rule crashed: {type(exc).__name__}: {exc}"))

    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]

    if args.json:
        print(json.dumps({
            "rails": [m.id for m in selected],
            "unmanifested": unmanifested_rails(),
            "counts": {"fail": len(fails), "warn": len(warns)},
            "findings": [f.__dict__ for f in findings],
        }, indent=2))
        return 0 if args.warn_only or not fails else 1

    print(f"rail conformance — {len(selected)} manifest(s), {len(RULES)} rule(s)\n")
    order = {rid: i for i, (rid, _, _) in enumerate(RULES)}
    for f in sorted(findings, key=lambda f: (order.get(f.rule, 99), f.rail)):
        print(f.line())
    if not findings:
        print("  no findings — every rail agrees with its manifest.")

    stray = unmanifested_rails()
    if stray:
        print(f"\nnot yet under contract (no rail.json): {', '.join(stray)}")
    print(f"\n{len(fails)} fail, {len(warns)} warn")
    return 0 if args.warn_only or not fails else 1


if __name__ == "__main__":
    sys.exit(main())
