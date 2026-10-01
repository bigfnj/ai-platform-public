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
import functools
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


def gateway_root_assets() -> dict[str, list[str]] | None:
    """The gateway's ROOT_ASSETS mirror, or None when it cannot be read.

    Mirrored rather than read from the manifests for the same reason APP_CATALOG mirrors
    `description`: the gateway CONTAINER cannot see rails/ at all -- compose mounts only each
    rail's built dist. This rule is the price of that mirroring.
    """
    v = _literal_assign(GATEWAY / "config.py", "ROOT_ASSETS")
    if v is None:
        return None
    return {k: list(x) for k, x in v.items()}


def _root_prefix_overlaps(a: str, b: str) -> bool:
    """Whether two declared root paths can ever match the same request.

    A trailing slash is a directory prefix, so `/logos/` swallows `/logos/x.svg`; anything
    else is an exact path. Equality counts, and so does containment in either direction --
    checking only equality would let one rail declare `/avatars/` while another declares
    `/avatars/teacher.png` and call that no collision, when in fact the second rail never
    sees a request.
    """
    if a == b:
        return True
    if a.endswith("/") and b.startswith(a):
        return True
    return b.endswith("/") and a.startswith(b)


def gateway_rail_slots() -> dict[str, list[dict[str, str]]]:
    return _literal_assign(GATEWAY / "rails_models.py", "RAIL_MODEL_SLOTS") or {}


def gateway_config_text() -> str:
    return read(GATEWAY / "config.py")


@functools.lru_cache(maxsize=1)
def gateway_field_defaults() -> "tuple[dict[str, object] | None, str]":
    """The gateway settings' IN-CODE defaults, read off the model rather than off the source.

    RC016 used to regex the source for `<ident>_dist: str = "..."`, which needed a quoted
    literal. Every default is now an expression, `str(RAILS / "edu-suite" / ... / "dist")`,
    so the regex matched nothing and the rule skipped all 15 rails while reporting green.
    The incident its docstring cites (five defaults left pointing at pre-monorepo
    directories, so a host-native gateway resolved 6 of 11 rails) was fixed by moving to
    that expression form, and the fix is what blinded the guard.

    Returns (defaults, "") on success and (None, reason) on failure, so a rule can ANNOUNCE
    that it could not look rather than pass silently. A check that cannot fail is worse than
    no check, because it stops the next person looking.
    """
    for p in (REPO / "apps" / "platform" / "backend", REPO / "packages" / "platform_core"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        from platform_gateway_app.config import GatewaySettings  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - any import failure is the same outcome here
        return None, f"{type(exc).__name__}: {exc}"
    return {n: f.default for n, f in GatewaySettings.model_fields.items()}, ""


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


def S(rid: str, m: Manifest, why: str) -> Finding:
    """A rule declaring it had nothing to check on this rail.

    `0 fail, 0 warn` could not tell "checked and clean" from "did not run", and 33 of this
    file's 392 rule-runs were the second. A rail with no `status_route` skips four rules, a
    non-lean rail skips three, and every one of them reported as green. RC018 already refuses
    to do that -- it WARNs when `pseudonyms.json` is absent rather than passing, because "a
    check that quietly does nothing is worse than no check" -- and this is that principle
    applied to the summary line.

    Deliberately a third level rather than a warn, and the distinction is load-bearing:

        warn  a control RAN and ran DEGRADED, and someone should act (RC018's missing names)
        skip  there was nothing here to check, and that is the correct outcome

    Collapsing them would make every clean run of a 14-rail tree print 33 warnings nobody can
    act on, which is how a warning stream gets ignored. Skips are counted, not printed, unless
    --verbose asks for them.
    """
    return Finding(rule=rid, rail=m.id, level="skip", message=why, where="")


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


#: The unprefixed names a rail may read the shared broker secret from, either of which
#: satisfies RC005. Both are UNPREFIXED for the same reason: one platform-wide credential,
#: not a per-rail setting.
#:
#:   BROKER_AUTH_TOKEN       the literal value. The original, still supported, still what
#:                           every deployed compose file passes today.
#:   BROKER_AUTH_TOKEN_FILE  a PATH whose contents are the value, and the form that wins
#:                           when both are set. Added 2026-09-10 so the secret stops living
#:                           in the process environment, where `docker inspect`, a crash
#:                           dump and /proc/<pid>/environ all expose it — the last of those
#:                           reachable from an in-container shell escape running as the
#:                           backend's uid, and this is the credential all nine rails share.
#:
#: Either name alone passes. A rail that reads ONLY the file form is correct on a stack that
#: has switched over; a rail that reads only the value form is correct on the one that has
#: not. Requiring both would fail every rail for supporting the deployment it is not on.
TOKEN_ENV_NAMES = ("BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE")


@rule("RC005", "The broker token is read from the unprefixed BROKER_AUTH_TOKEN(_FILE).")
def rc005(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Every rail's compose service is handed BROKER_AUTH_TOKEN, unprefixed, because it is
    one platform-wide shared secret rather than a per-rail setting. A rail that reads only
    its own prefixed spelling gets an empty token and sends no Authorization header — which
    is invisible until the broker starts enforcing, then every model call 401s at once.

    Since 2026-09-10 the token may instead arrive as BROKER_AUTH_TOKEN_FILE, a path whose
    contents are the secret (see TOKEN_ENV_NAMES for why, and why either name suffices). This
    rule is the INVENTORY of where the token is read, so it had to learn the second spelling
    in the same change that introduced it: a rule that knows only the old name turns red on
    the first rail to move, and a red checker that is "expected" is a checker nobody reads.

    A prefixed alias IS allowed (pydantic AliasChoices), as long as an unprefixed name is
    among the names actually consulted.

    ALSO satisfied by delegating to the shared platform_core BrokerClient, which injects the
    bearer centrally (`broker_token()` reads the unprefixed names). Most rails here do that
    rather than rolling their own client, and flagging them for "never reading the token"
    would be nine false alarms — the fastest way to get a checker switched off.

    Matched against exact string CONSTANTS in the parsed AST, not raw text. A substring
    search over the source finds the name in a comment explaining the variable and passes a
    rail that never reads it — which is precisely how this defect stayed hidden. Note that
    exact-constant matching is also what keeps the two names distinct: "BROKER_AUTH_TOKEN" is
    a prefix of "BROKER_AUTH_TOKEN_FILE", so a substring search could never have told a rail
    that reads only the file form from one that reads the value.
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
            if isinstance(node, ast.Constant) and node.value in TOKEN_ENV_NAMES:
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

    msg = (f"backend never reads {' or '.join(TOKEN_ENV_NAMES)}, so it sends no broker "
           f"Authorization header and every /v1/* call 401s once the broker enforces a token")
    where = rel(m.backend_dir())
    if prefixed_field:
        attr, where = prefixed_field
        msg = (f"reads the token ONLY as {prefix}{attr.upper()}. The canonical name is the "
               f"unprefixed BROKER_AUTH_TOKEN (or BROKER_AUTH_TOKEN_FILE), which is what "
               f"every other rail reads and what "
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


def _ast_slot_ids(m: Manifest) -> set[str]:
    """Slot ids read from the parse tree, for the rails the regexes cannot follow.

    Three rails build their spec list in a VARIABLE before passing it to modelstate.resolve --
    job-aid and edu-suite assign it, openmaic assigns conditionally and then appends -- so the
    inline-literal regex declined all three. openmaic was added by the same session that added
    that regex and landed outside it, which is the tell.

    The shape matched is EXACTLY the documented one: a 3-tuple whose first two elements are
    string constants, inside a function that calls modelstate.resolve. Looser than that and it
    starts collecting ("broker", ok) and ("done", x) from the same functions -- measured, not
    guessed -- and a checker that cries wolf gets switched off as surely as a vacuous one.
    """
    found: set[str] = set()
    for p in m.py_sources():
        try:
            tree = ast.parse(read(p))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(n, ast.Attribute) and n.attr == "resolve"
                       and _callee(n.value) == "modelstate" for n in ast.walk(fn)):
                continue
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Tuple) and len(n.elts) == 3):
                    continue
                a, b = n.elts[0], n.elts[1]
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and isinstance(b, ast.Constant) and isinstance(b.value, str)):
                    continue
                if re.fullmatch(r"[a-z][a-z0-9-]*", a.value):
                    found.add(a.value)
    return found


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
        return [S("RC007", m, "no status_route — the rail renders no chips, so it has "
                              "no slot ids to agree with the manifest")]
    # `kind: image` slots are exempt, and the reason is the same one the broker's audit_roles
    # gives for MEDIA_IMAGE_BACKENDS: an image backend (flux-schnell, sdxl-turbo) is loaded by
    # the media worker from the HF cache and NEVER appears in Ollama's tag list. A four-state
    # chip for one could only ever read "missing", so recipe-book deliberately renders two
    # chips against three declared slots. That divergence is stated in the manifest by the
    # kind, not inferred here.
    declared = {str(s["slot"]) for s in m.slots()
                if s.get("slot") and s.get("kind") != "image"}
    if not declared:
        return [S("RC007", m, "manifest declares no Ollama-backed model slots")]

    found: set[str] = set()
    where = ""
    for p in m.py_sources():
        src = read(p)
        # Three idioms, because rails really do use three. The third — the slot list written
        # inline into `modelstate.resolve([...])` — was missing, and the skip accounting is
        # what surfaced it: RC007 was silently declining ELEVEN of fourteen rails and
        # reporting `0 fail, 0 warn`, so it genuinely checked three. Eight of the eleven
        # declare their slots exactly this way (finance, recipe-book, job-aid, bouquet,
        # edu-suite, iep-goals, ai-playground, terminal-fun).
        for block in re.findall(
            r"(?:MODEL_SLOTS[^=]*=\s*\[|def _model_slots\(\)[^:]*:.*?return\s*\["
            r"|modelstate\.resolve\(\s*\[)(.*?)\]",
            src, re.S,
        ):
            ids = re.findall(r"\(\s*[\"']([a-z][a-z0-9-]*)[\"']\s*,", block)
            if ids:
                found.update(ids)
                where = where or rel(p)
    if not found:
        found = _ast_slot_ids(m)
        where = where or rel(m.backend_dir())
    if not found:
        return [S("RC007", m, "no MODEL_SLOTS / _model_slots() / modelstate.resolve() spec "
                              "could be read — the chip code spells its slots a way neither "
                              "the regexes nor the AST scan can follow, and guessing would be "
                              "a false accusation")]
    out: list[Finding] = []
    # A rail with `also_serves` runs ONE image as more than one tile and legitimately declares a
    # different slot set per tile -- edu-suite renders a "writer" slot only under IEP_ONLY=1,
    # for the Present Levels tile, and its own manifest describes the edu-suite tile. The
    # divergence is stated by also_serves rather than inferred, so only the direction that
    # cannot be explained that way is reported.
    extra = set() if m.data.get("also_serves") else found - declared
    for slot in sorted(extra):
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
        return [S("RC008", m, "no status_route — no chips, so no four-state contract "
                              "to honour")]
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
        return [S("RC012", m, "no theme.css and no module.tsx — no styles to scope")]
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
    prefix = str(m.data.get("env_prefix") or "")
    for s in m.slots():
        if s.get("pinned_default_ok"):
            continue
        env, role = str(s.get("env") or ""), str(s.get("role") or "")
        if not env:
            continue
        # A slot may name SEVERAL env vars ("A + B"), which bouquet's writer slot does with
        # a note claiming "the @role rule is checked against the pair". It was not: the raw
        # string went straight into both regexes, neither could match it, and the slot was
        # skipped in silence. Each name is now checked on its own.
        names = [n.strip() for n in env.split("+") if n.strip()]
        matched = 0
        for name in names:
            # removeprefix, not a slice. `env[len(prefix):]` assumes the var STARTS with the
            # rail's prefix; edu-suite declares env_prefix EDU_SUITE_ and a var EDU_LLM_MODEL,
            # so the slice produced attr="del" and the pydantic branch below could never match
            # anything. It survived only because that rail happens to use the os.environ form.
            attr = (name.removeprefix(prefix) if name.startswith(prefix) else name).lower()
            want = f"@{role}" if role else ""
            for p, src in blob.items():
                # pydantic-settings style:  synthesis_model: str = "@co-worker-synthesis"
                for hit in re.finditer(
                        rf"^\s*{re.escape(attr)}:\s*str\s*=\s*\"([^\"]*)\"", src, re.M):
                    matched += 1
                    out += _rc013_check(m, s, hit.group(1), want, name, rel(p))
                # os.environ style:  MODEL = os.environ.get("GEMINI_CX_RAG_MODEL", "@gemini-cx-rag")
                # os.getenv is the same thing and was the blind spot: matching only environ.get
                # meant this rule read 0 findings while edu-suite sat on four live pinned models.
                for hit in re.finditer(
                    rf"os\.(?:environ\.get|getenv)\(\s*[\"']{re.escape(name)}[\"']\s*,"
                    rf"\s*[\"']([^\"']*)[\"']",
                    src,
                ):
                    matched += 1
                    out += _rc013_check(m, s, hit.group(1), want, name, rel(p))
        if not matched:
            # ANNOUNCE it. A slot this rule could not find a default for is not a slot that
            # passed; measured before this change, 1 of 18 slots was skipped this way and
            # nothing said so.
            out.append(F("RC013", m, f"slot {s.get('slot')!r} declares env {env!r} but no "
                                     f"in-code default matching it was found, so this slot "
                                     f"was NOT checked", rel(m.path), level="warn"))
    return out


def _rc013_check(m: Manifest, s: dict, got: str, want: str, env: str, where: str) -> list[Finding]:
    """One default, against the role the manifest declares for it.

    The role was previously READ and interpolated into the message as "(expected '@x')" and
    then never compared: the only condition was `startswith("@")`. So a rail defaulting to a
    DIFFERENT @role passed while the message claimed to know better. Live instance at the
    time: bouquet defaulted three slots to generic roles where its manifest declares
    rail-specific ones, compose corrected them, and standalone dev was left wrong, which is
    precisely the split this rule's docstring says it exists to catch.
    """
    if not got.startswith("@"):
        return [F("RC013", m, f"slot {s.get('slot')!r} default for {env} is {got!r}, "
                              f"not an @role"
                              + (f" (expected {want!r})" if want else ""), where)]
    if want and got != want:
        return [F("RC013", m, f"slot {s.get('slot')!r} default for {env} is {got!r} but the "
                              f"manifest declares {want!r}; compose may correct this, which "
                              f"leaves standalone dev pointing at the wrong role", where)]
    return []


@rule("RC014", "Every compose file passes the broker token under a canonical unprefixed name.")
def rc014(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RC005 checks what the rail READS; this checks what deploy/ WRITES.

    Either canonical spelling counts (TOKEN_ENV_NAMES): the value in BROKER_AUTH_TOKEN, or a
    path in BROKER_AUTH_TOKEN_FILE. This rule is the other half of the same 2026-09-10 change
    — teaching RC005 the file form and not this one would mean the compose file that switches
    over reports "passes no broker token" for all nine services, which is the opposite of what
    happened. Note the regex below must not let BROKER_AUTH_TOKEN_FILE satisfy a check for
    BROKER_AUTH_TOKEN by prefix: the names are captured whole and compared as a set.

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
    canonical = set(TOKEN_ENV_NAMES)
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
        names = set(re.findall(r"^\s+((?:[A-Z][A-Z0-9_]*_)?BROKER_AUTH_TOKEN(?:_FILE)?):",
                               block, re.M))
        if not names:
            out.append(F("RC014", m, f"service passes no broker token, so the rail cannot "
                                     f"authenticate to the broker once one is enforced",
                         rel(cf), level="warn"))
        elif not (names & canonical):
            out.append(F("RC014", m, f"service passes the token as {sorted(names)[0]} rather "
                                     f"than a canonical {' / '.join(TOKEN_ENV_NAMES)}", rel(cf)))
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

    ⚠ That last paragraph was the INTENT and not the behaviour until 2026-09-17. Checking the
    directory instead of the bundle is not enough, because `dist/` is gitignored: in a tree
    where nothing has been built the directory does not exist either, so the rule failed on
    every rail. It had been failing the `public-snapshot-dryrun.yml` merge gate on EVERY push
    for at least a month -- gates 1-6 clean, gate 7 nine RC016 fails -- and a merge gate that
    is always red is not a merge gate, because nobody can tell a real leak from the standing
    failure. `--verify-build` does not save it: `build_shell()` builds `apps/platform/frontend`
    and no rail frontend.

    So the question the rule asks is now comparative. A rail missing its dist while SIBLINGS
    have theirs is the rotted default this rule was written for. Every rail missing it is an
    unbuilt tree, which is a fact about the checkout and not a defect in it -- that skips.
    """
    cfg_path = GATEWAY / "config.py"
    # Read the MODEL's defaults, not the source text. The old regex needed a quoted literal
    # and every default is now `str(RAILS / ... )`, so it matched nothing and this rule
    # skipped all 15 rails while reporting green. Measured before this change: 0 inspected,
    # 15 skipped.
    defaults, why = gateway_field_defaults()
    if defaults is None:
        # ANNOUNCE the degradation. Returning [] here is what made the old shape dangerous:
        # "I could not look" and "I looked and it is fine" printed identically.
        return [F("RC016", m, f"cannot read the gateway settings to check dist defaults "
                              f"({why}); this rule inspected NOTHING", rel(cfg_path),
                  level="warn")]
    # Is ANY rail in this tree built? Scanned over every manifest, not just this one, because
    # the distinction that matters is comparative and a per-rail view cannot see it.
    any_built = any(
        Path(str(defaults[f"{other_id.replace('-', '_')}_dist"])).is_dir()
        for other in _all for other_id in other.catalog_ids()
        if defaults.get(f"{other_id.replace('-', '_')}_dist")
    )

    out: list[Finding] = []
    inspected = 0
    for app_id in m.catalog_ids():
        ident = app_id.replace("-", "_")
        if f"{ident}_dist" not in defaults:
            continue  # RC004 owns "the setting is missing"
        raw = defaults[f"{ident}_dist"]
        if not raw:
            continue  # an empty default is "resolve at runtime", not a rotted path
        inspected += 1
        if not Path(str(raw)).is_dir():
            if not any_built:
                # Nothing anywhere is built. A fresh clone and a publish snapshot both look
                # like this, and neither has a rotted default -- it has no frontends yet.
                return [S("RC016", m, "no rail in this tree has a built frontend, so a missing "
                                      "dist says the tree is unbuilt, not that this default "
                                      "rotted. Build one and the rule becomes comparative.")]
            out.append(F("RC016", m, f"{ident}_dist default points at {str(raw)!r}, which "
                                     f"does not exist while OTHER rails in this tree are built "
                                     f"— resolved_app_dists() will skip this rail and it will "
                                     f"serve no bundle, without an error",
                         rel(cfg_path)))
    if m.catalog_ids() and not inspected:
        # Same reasoning as the import guard: a rail whose default this rule could not find
        # must say so rather than count as clean.
        out.append(F("RC016", m, "no *_dist default was found for any of this manifest's "
                                 "catalog ids, so nothing was checked", rel(cfg_path),
                     level="warn"))
    return out


@rule("RC015", "A rail with a status_route polls it, so its chips cannot freeze.")
def rc015(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The four states describe LIVE residency, and residency changes with nobody touching
    the UI: the broker evicts on a keep_alive expiry, and asking a question warms a model
    back up. So a one-shot fetch on mount renders a state that is right for about a second
    and silently wrong afterwards.

    LIVE, and not inert. This used to say "every rail on this platform sets status_route:
    null, because per-rail chips are a deferred follow-on". That stopped being true when the
    chips landed and the sentence was never updated: measured 2026-09-15, ELEVEN of fourteen
    manifests declare a real route (`/api/capabilities`, or co-worker's `/api/models`) and
    only ai-voice, meeting-atlas and workstation are null.

    Worth correcting rather than leaving, because "inert" is an instruction to stop reading:
    anyone triaging a finding from this rule would have dismissed it. It also makes RC017's
    aside about RC007/RC008 already owning chip correctness true, which the inert framing
    would have denied.

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
        return [S("RC017", m, "no TypeScript sources — nothing renders a header yet")]
    # PER FILE, not over the concatenation. Joining every .tsx in the rail meant the import
    # could sit in one file and the `<RailHeader` in another, and either could be dead code:
    # a rail that imported the name in types.ts and rendered a hand-rolled header everywhere
    # else passed. Requiring ONE file to do both is what the rule always meant.
    for p in srcs:
        text = read(p)
        if "RailHeader" in text and "@web-core" in text and "<RailHeader" in text:
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


#: Dependency callables that really perform the platform-identity check, i.e. that answer a
#: header-less caller with 401, directly or through another of these. A route gated by anything
#: else (a feature flag, a rate limiter) is not gated at all, so accepting a bare `Depends(...)`
#: would pass exactly the routes this rule exists to catch. A rail that spells its dependency
#: some other way belongs in this set, not outside the rule.
#:
#: `require_owner` (finance) and `_identity` (job-aid) are here for a reason worth keeping: the
#: PUBLIC copy of this checker carries only the first three names, because finance and job-aid
#: are withheld from the public mirror and its author never met them. Running that set here
#: reds finance, which is correctly gated app-wide at rails/finance/src/finance/api/app.py:209
#: and 401s on both an absent and a blank header. A checker imported from a smaller tree
#: arrives pre-blinded, and a false alarm on a correct rail is how a checker gets switched off.
_IDENTITY_DEPS = frozenset({"identity", "_identity", "require_admin", "require_owner", "owner_id"})

#: The decorator methods that register a route on an app or a router.
_ROUTE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "head", "options",
                               "trace", "route", "api_route", "websocket"})


def _callee(node: ast.expr) -> str:
    """The final name of a callable expression: `identity` and `deps.identity` both -> identity."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _depends_name(node: ast.expr) -> str:
    """The dependency inside a `Depends(x)` / `Depends(deps.x)` expression, or ""."""
    if isinstance(node, ast.Call) and _callee(node.func) == "Depends" and node.args:
        return _callee(node.args[0])
    return ""


def _gates(keywords: list[ast.keyword]) -> bool:
    """True when a `dependencies=[Depends(identity), ...]` keyword carries a real identity gate.

    Used for all three places FastAPI accepts one: the `FastAPI(...)` app, an `APIRouter(...)`,
    and an individual route decorator.
    """
    for kw in keywords:
        if kw.arg != "dependencies":
            continue
        elts = kw.value.elts if isinstance(kw.value, (ast.List, ast.Tuple)) else []
        if any(_depends_name(e) in _IDENTITY_DEPS for e in elts):
            return True
    return False


def _signature_gated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when a handler takes `ident: X = Depends(identity)` (or any other _IDENTITY_DEPS)."""
    args = fn.args
    defaults = [*args.defaults, *[d for d in args.kw_defaults if d is not None]]
    return any(_depends_name(d) in _IDENTITY_DEPS for d in defaults)


def _status_route_gate(m: Manifest, srcs: dict[Path, str]) -> tuple[bool, bool, str]:
    """Is the manifest's `status_route` actually behind the identity gate?

    Returns (found, gated, where). `found` is False when no handler for that path could be
    located at all, which is a SILENT SKIP by design: a route registered some way this cannot
    read (a dynamically built path, a mounted sub-app) is a rail this tool cannot judge, and
    guessing there would produce the false alarm that gets a checker switched off.

    A route counts as gated when any of the four things FastAPI actually honours is present:
    an app-wide `FastAPI(dependencies=[Depends(identity)])` in the same module, a gated
    `APIRouter(dependencies=[...])` it is registered on, a `dependencies=[...]` on its own
    decorator, or an identity dependency in the handler's signature.
    """
    route = str(m.data.get("status_route") or "")
    found = gated = False
    where = ""
    for p, src in srcs.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # An app-level gate is per MODULE, not per rail: a rail may mount a second FastAPI() for
        # a shim, and "some app in this rail is gated" would let an un-gated main app hide
        # behind it.
        app_gated = any(isinstance(n, ast.Call) and _callee(n.func) == "FastAPI"
                        and _gates(n.keywords) for n in ast.walk(tree))
        prefixes: dict[str, str] = {}
        gated_routers: set[str] = set()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and _callee(n.value.func) == "APIRouter"):
                continue
            pfx = next((k.value.value for k in n.value.keywords if k.arg == "prefix"
                        and isinstance(k.value, ast.Constant)
                        and isinstance(k.value.value, str)), "")
            for t in n.targets:
                if isinstance(t, ast.Name):
                    prefixes[t.id] = pfx
                    if _gates(n.value.keywords):
                        gated_routers.add(t.id)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in n.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in _ROUTE_DECORATORS and dec.args):
                    continue
                first = dec.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                owner = _callee(dec.func.value)
                if prefixes.get(owner, "") + first.value != route:
                    continue
                found = True
                if (app_gated or owner in gated_routers or _gates(dec.keywords)
                        or _signature_gated(n)):
                    gated = True
                elif not where:
                    where = f"{rel(p)}:{n.lineno}"
    return found, gated, where


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
        return [S("RC021", m, "no FastAPI app — there is no surface to gate")]
    blob = "\n".join(srcs.values())
    out: list[Finding] = []

    # The header is spelled three ways depending on how it is read: the FastAPI parameter name
    # (x_platform_user), a raw header lookup (x-platform-user), or the canonical form in prose.
    reads_identity = re.search(r"x[_-]platform[_-]user", blob, re.I)
    if "401" not in blob or not reads_identity:
        out.append(F("RC021", m, "no 401 for a request with no X-Platform-User — a sibling "
                                 "container can call this rail directly", rel(m.path)))

    # The substring pass above is a floor, not the check. `blob` is every .py file in the rail
    # concatenated, so ANY rail containing the three characters "401" anywhere — a comment, an
    # unrelated status code, a docstring — satisfied it while serving its model state to any
    # container on the compose network. That is not hypothetical: two rails were doing exactly
    # that while this file reported `0 fail, 0 warn`. RC005's own docstring already makes this
    # argument ("a substring search over the source finds the name in a comment explaining the
    # variable and passes a rail that never reads it"); the lesson was learned there and not
    # carried across. So ask the question that matters — is THIS route gated — of the parse tree.
    route = str(m.data.get("status_route") or "")
    if route:
        found, gated, where = _status_route_gate(m, srcs)
        if found and not gated:
            out.append(F("RC021", m, f"the declared status_route {route} has no identity "
                                     f"dependency — a sibling container reads this rail's model "
                                     f"state with no X-Platform-User at all (gate it per-route "
                                     f"with Depends(identity), or app-wide via "
                                     f"FastAPI(dependencies=[Depends(identity)]))", where))

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
        return [S("RC022", m, "no modelstate.py — this rail does no model work, so it "
                              "needs no broker facade")]
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
            continue
        # THIRD form: an unquoted key with an explicit value (`openmaic: openMaic,`). A
        # single-word id whose imported binding is camelCased cannot use JS shorthand, so this
        # is the only spelling available to it -- and it was being read as "not registered",
        # which is a false alarm on a correctly wired rail. A checker that cries wolf gets
        # switched off just as surely as one that never fires.
        m = re.match(r"^([A-Za-z_][\w]*)\s*:", line)
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
    """roles.lean.json is what the installer copies over services/broker/roles.json on a small
    card. A per-rail role missing from it does not fail the install — it fails that rail's first
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


@rule("RC028", "The rail has a test suite.")
def rc028(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RAIL_CONTRACT: a rail under contract carries its own tests.

    smb-partner-enablement and gemini-cx were imported from the public sibling on 2026-08-20
    and shipped with no tests/ directory at all. Nothing caught it for three weeks, because
    run-tests.ps1 builds its plan from the directories that EXIST -- a rail with no suite is
    not a failing target, it is a target that was never created. `-List` showed 12 targets for
    14 rails and read as correct.

    That is the same shape as every other drift this file exists to catch: the contract already
    knows which rails exist, so "a rail under contract has tests" is checkable from the manifest
    rather than from whatever happens to be on disk.

    Discovery deliberately mirrors run-tests.ps1 (any dir named `tests`, minus the vendored
    trees) so a suite this rule accepts is a suite the runner will actually run. Requiring an
    actual test_*.py inside is the point: an empty tests/ directory would satisfy both this
    rule and the runner's plan while running nothing, which is exactly the vacuous-green state
    the rule is meant to prevent.
    """
    src = next((d for d in (m.root / "src", m.root / "backend") if d.is_dir()), None)
    if src is None:
        return []  # no backend to test (none today; keeps the rule honest if one appears)
    skip = {"native", "node_modules", ".venv", "__pycache__"}
    for d in m.root.rglob("tests"):
        if not d.is_dir() or skip & set(d.relative_to(m.root).parts):
            continue
        if any(d.rglob("test_*.py")):
            return []
    return [F("RC028", m, "no test suite -- run-tests.ps1 builds its plan from the directories "
                          "that exist, so this rail is not a failing target, it is a target "
                          "that was never created", rel(m.root))]

# --- runner ----------------------------------------------------------------


@rule("RC029", "Root-origin asset claims agree with the gateway, collide with nobody, "
               "and avoid reserved paths.")
def rc029(m: Manifest, allm: list[Manifest]) -> list[Finding]:
    """`root_assets` lets a rail serve paths OUTSIDE its own /<id>/ namespace.

    Only a rail that wraps a third-party app needs it: openmaic fronts an upstream Next.js app
    whose source carries absolute `<img src="/logos/...">` literals, and Next's basePath does not
    rewrite a string literal in JSX, so the browser resolves it against the origin root.

    Three things are checked, because each fails differently and none of them loudly:

      * The gateway's ROOT_ASSETS mirror must match the manifest. The gateway container cannot
        see rails/, so the list is necessarily duplicated; a drifted copy means the rail declares
        a path nothing routes, or the gateway routes a path the rail no longer serves.
      * No two rails may claim overlapping paths. The origin root is an exhaustible shared
        resource and a collision is SILENT -- one rail simply serves the other's images. This is
        the same failure RC002 exists for, one namespace over.
      * Nothing may claim a platform-reserved prefix, or a rail could shadow the shell's own
        /assets/ bundle or the /api/ surface for every user at once.

    Numbered 029 rather than 028, which it is called upstream: this repo's RC028 already means
    "the rail has a test suite", and an id is quoted in commit messages and passed to --rule.
    """
    mine = list(m.data.get("root_assets") or [])
    mirror = gateway_root_assets()
    out: list[Finding] = []
    where = rel(GATEWAY / "config.py")

    if mirror is None:
        if mine:
            out.append(F("RC029", m, "could not read the gateway's ROOT_ASSETS literal, so "
                                     "nothing here is verified — a checker/gateway mismatch, "
                                     "not a rail defect", where, level="warn"))
        return out
    if not mine and not mirror.get(m.id):
        return [S("RC029", m, "claims no root-origin assets, which is the normal case for a "
                              "rail that lives entirely under its own /<id>/")]

    declared = mirror.get(m.id, [])
    if sorted(declared) != sorted(mine):
        out.append(F("RC029", m, f"gateway ROOT_ASSETS has {sorted(declared)!r} for this rail "
                                 f"but the manifest declares {sorted(mine)!r}", where))

    for prefix in mine:
        for reserved in ("/api/", "/assets/", "/ws/"):
            if prefix == reserved.rstrip("/") or prefix.startswith(reserved):
                out.append(F("RC029", m, f"{prefix!r} claims the platform-reserved {reserved!r}",
                             rel(m.path)))
        # A rail's own /<id>/ is already its namespace; claiming it at the root is a mistake
        # that would shadow its own federated bundle.
        for other in allm:
            if prefix.startswith(f"/{other.id}/") or prefix == f"/{other.id}":
                out.append(F("RC029", m, f"{prefix!r} is inside a rail namespace "
                                         f"(/{other.id}/), which the gateway already routes",
                             rel(m.path)))

    # Collisions, checked against every OTHER rail's manifest.
    for other in allm:
        if other.id == m.id:
            continue
        for a in mine:
            for b in (other.data.get("root_assets") or []):
                if _root_prefix_overlaps(a, b):
                    out.append(F("RC029", m, f"root path {a!r} overlaps {b!r} claimed by "
                                             f"'{other.id}' — one rail would silently serve the "
                                             f"other's assets", rel(m.path)))
    return out


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
    ap.add_argument("--verbose", action="store_true",
                    help="also print every skip: which rule declined which rail, and why")
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
    skips = [f for f in findings if f.level == "skip"]

    # Coverage is a FLOOR, not a measurement, and saying so is the point. A rule that has not
    # been migrated to S() still returns a bare [] and is counted as having run, exactly as it
    # was before. So the number can only understate how much was skipped, never overstate it --
    # which is the one direction that is safe for a number people will read as reassurance.
    ran = len(RULES) * len(selected) - len(skips)
    if args.rule:
        ran = len([r for r in RULES if r[0] in args.rule]) * len(selected) - len(skips)
    skipped_by_rule: dict[str, int] = {}
    for f in skips:
        skipped_by_rule[f.rule] = skipped_by_rule.get(f.rule, 0) + 1
    # The guard on the guard. A rule that skipped every rail this run checked nothing at all,
    # and is decoration until something changes. Only meaningful on an unfiltered run: with
    # --rail or --rule, skipping everything selected is the expected outcome, not a finding.
    inert = sorted(r for r, n in skipped_by_rule.items()
                   if n == len(selected) and not args.rail and not args.rule)

    if args.json:
        print(json.dumps({
            "rails": [m.id for m in selected],
            "unmanifested": unmanifested_rails(),
            "counts": {"fail": len(fails), "warn": len(warns),
                       "skip": len(skips), "ran": ran},
            "inert_rules": inert,
            "findings": [f.__dict__ for f in findings],
        }, indent=2))
        return 0 if args.warn_only or not fails else 1

    print(f"rail conformance — {len(selected)} manifest(s), {len(RULES)} rule(s)\n")
    order = {rid: i for i, (rid, _, _) in enumerate(RULES)}
    shown = [f for f in findings if f.level != "skip" or args.verbose]
    for f in sorted(shown, key=lambda f: (order.get(f.rule, 99), f.rail)):
        print(f.line())
    if not shown:
        print("  no findings — every rail agrees with its manifest.")

    stray = unmanifested_rails()
    if stray:
        print(f"\nnot yet under contract (no rail.json): {', '.join(stray)}")
    for rid in inert:
        print(f"\nWARN {rid}  checked nothing this run — it skipped all "
              f"{len(selected)} rails, so it is currently decoration")
    checks = ran + len(skips)
    print(f"\n{checks} checks: {ran} ran, {len(skips)} skipped "
          f"({len(skipped_by_rule)} rule(s) skipped at least one rail)")
    print(f"{len(fails)} fail, {len(warns)} warn")
    return 0 if args.warn_only or not fails else 1


if __name__ == "__main__":
    sys.exit(main())
