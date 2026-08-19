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
import re
import sys
from dataclasses import dataclass, field
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

    def slots(self) -> list[dict[str, Any]]:
        s = self.data.get("model_slots")
        return s if isinstance(s, list) else []

    def panel_slots(self) -> list[dict[str, Any]]:
        return [s for s in self.slots() if s.get("admin_panel")]

    def backend_dir(self) -> Path:
        """Where the rail's Python package lives, per its declared layout."""
        pkg = str(self.data.get("package") or "")
        if self.data.get("layout") == "src":
            return self.root / "src" / pkg
        return self.root / "backend" / pkg

    def frontend_src(self) -> Path:
        return self.root / "frontend" / "src"

    def py_sources(self) -> list[Path]:
        d = self.backend_dir()
        if not d.is_dir():
            return []
        return [p for p in sorted(d.rglob("*.py")) if "__pycache__" not in p.parts]

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


def gateway_catalog() -> list[dict[str, str]]:
    return _literal_assign(GATEWAY / "catalog.py", "APP_CATALOG") or []


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
    if m.data.get("id") and m.data["id"] != m.root.name:
        out.append(F("RC001", m, f"manifest id '{m.data['id']}' != directory name "
                                 f"'{m.root.name}'", rel(m.path)))
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


def _all_vite_ports() -> dict[str, int]:
    """Every vite dev port declared anywhere under rails/, keyed by a readable owner label.

    Includes unmanifested rails and secondary configs (smb-partner's standalone mobile
    build has its own server on its own port), because a collision with one of those breaks
    a dev server just as thoroughly as a collision between two manifests.
    """
    out: dict[str, int] = {}
    for vc in sorted(RAILS.rglob("vite*.config.ts")):
        if "node_modules" in vc.parts:
            continue
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


@rule("RC003", "The manifest's id, label and icon match the gateway's APP_CATALOG entry.")
def rc003(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    entry = next((e for e in gateway_catalog() if e.get("id") == m.id), None)
    if entry is None:
        return [F("RC003", m, "no APP_CATALOG entry — the shell cannot draw a rail item",
                  rel(GATEWAY / "catalog.py"))]
    out = []
    for key in ("label", "icon"):
        want, got = m.data.get(key), entry.get(key)
        if want != got:
            out.append(F("RC003", m, f"catalog {key} is {got!r} but the manifest says "
                                     f"{want!r}", rel(GATEWAY / "catalog.py")))
    return out


@rule("RC004", "The gateway routes and mounts the rail on its declared backend port.")
def rc004(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    cfg = gateway_config_text()
    if not cfg:
        return []
    out: list[Finding] = []
    ident = m.id.replace("-", "_")
    port = (m.data.get("ports") or {}).get("backend")
    where = rel(GATEWAY / "config.py")

    field_re = re.compile(rf"^\s*app_{ident}_url:\s*str\s*=\s*\"([^\"]+)\"", re.M)
    hit = field_re.search(cfg)
    if not hit:
        out.append(F("RC004", m, f"no app_{ident}_url setting — the proxy has no route to "
                                 f"/{m.id}/api/*", where))
    elif isinstance(port, int) and f":{port}" not in hit.group(1):
        out.append(F("RC004", m, f"app_{ident}_url default {hit.group(1)!r} does not use the "
                                 f"declared backend port {port}", where))

    if not re.search(rf"^\s*{ident}_dist:\s*str\s*=", cfg, re.M):
        out.append(F("RC004", m, f"no {ident}_dist setting — the federated bundle will not "
                                 f"be served at /{m.id}/", where))
    # Both mapping helpers must know the id, or the rail is registered but unreachable.
    for fn in ("app_backends", "resolved_app_dists"):
        body = _func_body(cfg, fn)
        if body and f'"{m.id}"' not in body:
            out.append(F("RC004", m, f"{fn}() does not map \"{m.id}\"", where))
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
    app = read(SHELL / "App.tsx")
    if app and fed and f"'{fed}/module'" not in app and f'"{fed}/module"' not in app:
        out.append(F("RC010", m, f"the shell never lazy-imports '{fed}/module', so the rail "
                                 f"cannot mount", rel(SHELL / "App.tsx")))
    return out


@rule("RC011", "A compose service exists for the rail, exposing its backend port.")
def rc011(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    src = read(COMPOSE)
    if not src:
        return []
    block = _compose_service(src, m.id)
    if block is None:
        return [F("RC011", m, "no compose service of this name", rel(COMPOSE))]
    port = (m.data.get("ports") or {}).get("backend")
    if isinstance(port, int) and f'"{port}"' not in block and f"- {port}" not in block:
        return [F("RC011", m, f"compose service does not expose the declared backend port "
                              f"{port}", rel(COMPOSE))]
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
                                 f"{hit.group(2)}; use --critical / --info / --warning / "
                                 f"--good so it reads on every palette", where))
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
            for hit in re.finditer(
                rf"os\.environ\.get\(\s*[\"']{re.escape(env)}[\"']\s*,\s*[\"']([^\"']*)[\"']",
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

    A rail with no model slots needs no token and is skipped.
    """
    if not m.slots():
        return []
    canonical = "BROKER_AUTH_TOKEN"
    out: list[Finding] = []
    for cf in (COMPOSE, REPO / "deploy" / "installer" / "docker-compose.installer.yml"):
        src = read(cf)
        if not src:
            continue
        block = _compose_service(src, m.id)
        if block is None:
            continue  # RC011 owns "the service is missing"; a profile-gated file may omit it
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
    contract, which is the state every drift so far started from."""
    out = []
    for d in sorted(RAILS.iterdir()):
        if d.is_dir() and not (d / "rail.json").is_file() and (d / "frontend").is_dir():
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
