#!/usr/bin/env python3
"""Rail conformance checker — verifies every rail agrees with its own manifest.

    python tools/rail_conformance.py            # human report, exit 1 on any FAIL
    python tools/rail_conformance.py --json     # machine-readable
    python tools/rail_conformance.py --rail co-worker
    python tools/rail_conformance.py --rule RC005
    python tools/rail_conformance.py --strict --allow unbuilt-tree   # a WARN fails too

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
import warnings
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
    level: str  # "fail" | "warn" | "skip"
    message: str
    where: str = ""
    # The VACUOUS condition this finding is an instance of, or "". Set only at the exact emit
    # site of a known-vacuous warning or skip, never per rule: RC016 also warns when it cannot
    # import the gateway settings, and that one is a real degradation --strict must not excuse.
    allowance: str = ""

    def line(self) -> str:
        loc = f"  [{self.where}]" if self.where else ""
        return f"{self.level.upper():4} {self.rule}  {self.rail:24} {self.message}{loc}"


# Warnings that are vacuous BY CONSTRUCTION in a tree --strict has to pass on: a CI checkout and
# a publish snapshot. `--strict --allow <name>` exempts exactly one of these, by name, and
# nothing else. Each entry says why its condition cannot mean anything there, because an
# allowance nobody can justify is just a quieter way of switching the check off.
VACUOUS: dict[str, str] = {
    "pseudonyms-absent": (
        "RC018 reads rails/iep/local/pseudonyms.json, which is gitignored, so no CI checkout and "
        "no publish snapshot can ever hold it. The publish leak scan (gate 1) folds the same "
        "names in from the maintainer's tree instead."),
    "unbuilt-tree": (
        "RC016 and RC032 skip each rail whose frontend has not been built, so they skip every "
        "rail when none is. dist/ is gitignored, so a fresh clone and a publish snapshot always "
        "look like that, and the rules are then inert rather than wrong."),
}


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

    def vite_config(self) -> Path:
        """The rail's vite config: beside its frontend sources, wherever those live.

        RC009 and RC010 read `<root>/frontend/vite.config.ts` directly, after frontend_src()
        had already been taught about relocated frontends. On edu-suite (apps/dashboard/
        frontend/) they read "", returned a bare [], and were counted as having run and passed
        while checking nothing. Deriving the path from frontend_src() keeps every rule that
        reads the frontend agreeing about where it is.
        """
        return self.frontend_src().parent / "vite.config.ts"

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
            return read(css), rel(css)
        mod = self.frontend_src() / "module.tsx"
        if mod.is_file():
            return read(mod), rel(mod)
        return "", ""


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p)


def _ast_parse(src: str) -> ast.Module:
    """ast.parse, quietly. A rail's non-raw docstring holding `\\s` made every run print
    `SyntaxWarning: "\\s" is an invalid escape sequence` (Python 3.12+; a hidden
    DeprecationWarning before): noise on a gate whose clean output is the signal, and not this
    tool's to report. A real SyntaxError still raises."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(src)


_READ_CACHE: dict[str, tuple[int, int, str]] = {}


def read(p: Path) -> str:
    """A file's text, "" if it is not a file. Cached on (path, mtime, size): a full run read 451
    paths 2,425 times (20.7 MB), and a file rewritten between two reads (a test's fixture, an
    editor) is read again rather than served stale."""
    try:
        st = p.stat()
    except OSError:
        return ""
    if not p.is_file():
        return ""
    key = str(p)
    hit = _READ_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    text = p.read_text(encoding="utf-8", errors="replace")
    _READ_CACHE[key] = (st.st_mtime_ns, st.st_size, text)
    return text


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
        tree = _ast_parse(src)
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


def gateway_max_request_bytes() -> dict[str, Any] | None:
    """The gateway's MAX_REQUEST_BYTES mirror (catalog id -> bytes), or None when it cannot be
    read. Mirrored for the reason ROOT_ASSETS is: the gateway container cannot see rails/."""
    v = _literal_assign(GATEWAY / "config.py", "MAX_REQUEST_BYTES")
    return dict(v) if isinstance(v, dict) else None


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
    literal. Every default is now an expression, `str(RAILS / "<rail>" / ... / "dist")`,
    so the regex matched nothing and the rule skipped every rail while reporting green.
    The incident its docstring cites (five defaults left pointing at pre-monorepo
    directories, so a host-native gateway resolved barely half the rails) was fixed by moving
    to that expression form, and the fix is what blinded the guard.

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


def F(rid: str, m: Manifest, msg: str, where: str = "", level: str = "fail",
      allowance: str = "") -> Finding:
    return Finding(rule=rid, rail=m.id, level=level, message=msg, where=where,
                   allowance=allowance)


def S(rid: str, m: Manifest, why: str, allowance: str = "") -> Finding:
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

    `allowance` matters only when a rule skips EVERY rail and so becomes inert, which --strict
    treats as a warning: the inert warning inherits the allowance when all its skips share it.
    """
    return Finding(rule=rid, rail=m.id, level="skip", message=why, where="",
                   allowance=allowance)


# --- where the rules live --------------------------------------------------
# The rules are written in rail_conformance_rules/, beside this file, in files named for the ids
# they hold, and _run_rules() runs each file INTO this module rather than importing it as a
# module of its own. They were all in this file until 2026-10-16, when it reached two thirds of
# publish.json's data_max_bytes, and running them here keeps the one namespace they were written
# in. A rule reads REPO, RAILS, COMPOSE, read() and this file's other names when it is called, so
# a test that points one of them at a fixture (monkeypatch.setattr(conformance, "COMPOSE", cf))
# reaches every rule wherever it is written, and the rules, their order and every finding are
# the one file's.
#
# What one namespace asks of a rule file. It uses this file's names and its own, never another
# rule file's: a helper two files use is defined here, under "helpers the rule files share".
# It binds no name that another file binds, because the one that ran later would silently
# replace the other. It has no docstring, which would replace this file's. And every file in
# the directory runs, once, in the order of its name. tools/tests/test_rail_conformance.py holds
# all four (TestRuleFiles).
#
# The order the files run in is the order --rules lists the rules and a run checks them. They
# run in one step, below the helpers they share.

RULE_DIR = Path(__file__).resolve().parent / "rail_conformance_rules"
#: The rule files run so far, in the order they ran.
_RULE_FILES: list[str] = []


def _run_rules(*names: str) -> None:
    """Run each named file of RULE_DIR into this module's namespace, in order (see above)."""
    for name in names:
        path = RULE_DIR / f"{name}.py"
        exec(compile(path.read_bytes(), str(path), "exec"), globals())
        _RULE_FILES.append(name)


# --- helpers the rule files share ------------------------------------------
# Each is used by rules in more than one file, so it is defined here (see above).

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


def _compose_service(src: str, name: str) -> str | None:
    """The text of one two-space-indented service block. Regex rather than a YAML parse:
    pyyaml is not stdlib and this tool must run on a bare checkout."""
    start = re.search(rf"^  {re.escape(name)}:\s*(?:#.*)?$", src, re.M)
    if not start:
        return None
    nxt = re.search(r"^  [a-z][a-z0-9_-]*:", src[start.end():], re.M)
    return src[start.end(): start.end() + nxt.start()] if nxt else src[start.end():]


def _callee(node: ast.expr) -> str:
    """The final name of a callable expression: `identity` and `deps.identity` both -> identity."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


# The selector parsing RC012's unscoped_selectors (rc012.py) and RC032 (rc028_rc035.py) both
# read. global_selectors is RC032's alone, and it shares the rest with RC012.
def _selector_list(head: str) -> list[str]:
    """A selector list split at its top-level commas only: `.x :is(a, b)` is one selector, and
    splitting inside the parentheses made ` b)` a classless selector of its own."""
    parts: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(head):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(head[start:i])
            start = i + 1
    parts.append(head[start:])
    return parts


def _top_level_selectors(css: str) -> list[str]:
    """Every rule's selector list, split at its commas, with at-rule bodies (@media, @supports,
    @layer) descended and @keyframes / @font-face / @page / @property skipped: those hold no
    selectors that match elements."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    stack: list[str] = []
    buf = ""
    for ch in css:
        if ch == "{":
            head = buf.strip()
            buf = ""
            stack.append(head)
            inert = any(s.startswith(("@keyframes", "@-webkit-keyframes", "@font-face", "@page",
                                      "@property")) for s in stack)
            if head and not head.startswith("@") and not inert:
                out += [re.sub(r"\s+", " ", p.strip()) for p in _selector_list(head) if p.strip()]
        elif ch == "}":
            if stack:
                stack.pop()
            buf = ""
        elif ch == ";":
            buf = ""
        else:
            buf += ch
    return out


def _root_led_to_wrapper(first: str, sel: str, wrapper: str) -> bool:
    """`:root[data-theme='dark'] .ma` and `html[data-x] .ma .b`: a :root / html qualifier
    that leads to the wrapper, which scopes the selector however it starts."""
    return bool(wrapper and re.match(r"(?::root|html)\b", first)
                and re.search(rf"\.{re.escape(wrapper)}(?![\w-])", sel))


def global_selectors(css: str, wrapper: str) -> list[str]:
    """Every selector in `css` whose first compound carries no class and is not a `:root` / `html`
    qualifier leading to the wrapper: html, body, :root, #root, *, a bare element. Each matches
    the shell's own page, whichever rail's markup is on screen."""
    out: list[str] = []
    for sel in _top_level_selectors(css):
        if re.fullmatch(r"from|to|[\d.]+%", sel):
            continue
        first = re.split(r"\s*[ >+~]\s*", sel, maxsplit=1)[0]
        if _root_led_to_wrapper(first, sel, wrapper):
            continue
        if not re.findall(r"\.(-?[A-Za-z_][\w-]*)", re.sub(r":not\([^)]*\)", "", first)):
            out.append(sel)
    return out


_run_rules("rc001_rc004", "rc005_rc008", "rc009_rc011", "rc012", "rc013_rc020", "rc021_rc024",
           "rc025_rc027", "rc028_rc035")


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

    An empty directory skeleton is not a rail. A gitignored local scaffold (bouquet's, once) is
    every subdirectory, zero files. Reporting it as "outside the contract" is noise, and noise
    is how a report stops being read — so require at least one actual file.

    A frontend is `<dir>/frontend/` OR a vite config anywhere under the rail. Keying on the
    first alone is the hardcoded layout RC009/RC010 had: a multi-app rail like edu-suite keeps
    its frontend at apps/dashboard/frontend/, and an unmanifested rail shaped that way would
    have been invisible here. There is no manifest to follow (no package_path), so this asks
    for the file every rail frontend on the platform has: the vite config that builds its
    federation remote. `_walk` prunes node_modules and vendored trees, and only directories
    with no rail.json are walked at all.
    """
    out = []
    # A tree with no rails/ at all has no unmanifested rail; main() says it found no manifest.
    for d in sorted(RAILS.iterdir() if RAILS.is_dir() else []):
        if not d.is_dir() or (d / "rail.json").is_file():
            continue
        if not (d / "frontend").is_dir() and not _walk(d, "vite*.config.*"):
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
    # --strict and --warn-only answer opposite questions, so asking both is a mistake to report,
    # not a precedence rule to guess at.
    exit_mode = ap.add_mutually_exclusive_group()
    exit_mode.add_argument("--warn-only", action="store_true",
                           help="always exit 0; report findings without failing a build")
    exit_mode.add_argument("--strict", action="store_true",
                           help="also exit 1 on any WARN, and on a rule that skipped every "
                                "rail, unless --allow names its condition")
    ap.add_argument("--allow", action="append", default=[], choices=sorted(VACUOUS),
                    metavar="CONDITION",
                    help="with --strict, do not fail on this named known-vacuous condition "
                         f"(repeatable; one of: {', '.join(sorted(VACUOUS))})")
    ap.add_argument("--rules", action="store_true",
                    help="list the enforced rules and exit (the contract's live index)")
    ap.add_argument("--verbose", action="store_true",
                    help="also print every skip: which rule declined which rail, and why")
    args = ap.parse_args()
    if args.allow and not args.strict:
        # Without --strict no warning fails the run, so an allowance would do nothing while
        # reading as if it had been granted something.
        ap.error("--allow only has an effect with --strict")

    if args.rules:
        # docs/RAIL_CONTRACT.md points here rather than restating the list, so the prose
        # cannot drift from what is actually enforced.
        print(f"{len(RULES)} enforced rules:\n")
        for rid, summary, _fn in RULES:
            print(f"  {rid}  {summary}")
        return 0

    manifests, findings = load_manifests()
    # A typo'd filter selected nothing and reported "no findings", exit 0: a clean bill for a
    # rail or rule that was never checked. A name that matches nothing is a usage error.
    known_rules = {rid for rid, _s, _fn in RULES}
    bad_rules = [r for r in args.rule or [] if r not in known_rules]
    if bad_rules:
        ap.error(f"no rule named {', '.join(bad_rules)} (see --rules for the {len(RULES)} ids)")
    known_rails = sorted(m.id for m in manifests)
    bad_rails = [r for r in args.rail or [] if r not in known_rails]
    if bad_rails:
        ap.error(f"no rail named {', '.join(bad_rails)} (rail ids: {', '.join(known_rails)})")
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

    # --strict. Exit on `fails` alone meant every "this rule could not look" warning announced
    # itself and passed, and so did publish gate 7: make catalog.py's _ENTRIES a non-literal and
    # RC003 warned on every rail while the gate printed `clean`. Under --strict a warning is a
    # failure, and so is an inert rule, which is the same "checked nothing" at rule scale.
    # The only way out is an allowance NAMED in VACUOUS and carried by the exact finding that
    # is vacuous. An inert rule inherits one only when every one of its skips carries it, so a
    # rule that skips some rails for the vacuous reason and some for another is not excused.
    allowed = set(args.allow)
    inert_allowance: dict[str, str] = {}
    for rid in inert:
        tags = {f.allowance for f in skips if f.rule == rid}
        inert_allowance[rid] = tags.pop() if len(tags) == 1 else ""
    used: dict[str, int] = {a: 0 for a in sorted(allowed)}
    unallowed: list[str] = []
    # "" is never in `allowed` (argparse `choices` refuses it), so an untagged finding can never
    # be excused, whatever was passed.
    for f in warns:
        if f.allowance in allowed:
            used[f.allowance] += 1
        else:
            unallowed.append(f"{f.rule} {f.rail}: {f.message}")
    for rid in inert:
        if inert_allowance[rid] in allowed:
            used[inert_allowance[rid]] += 1
        else:
            unallowed.append(f"{rid}: checked nothing this run (skipped all {len(selected)} "
                             f"rails)")
    # No manifest at all: every rule ran over nothing, no rule could even be called inert, and the
    # run printed "no findings" and exited 0, --strict included (a moved rails/, a checkout without
    # one, a load that found nothing). The same "checked nothing", at run scale; never allowable.
    nothing = "" if manifests else (f"no rail manifest was found ({RAILS.name}/*/rail.json), so no "
                                    f"rule checked anything")
    if nothing:
        unallowed.append(nothing)
    if args.warn_only:
        code = 0
    elif fails or (args.strict and unallowed):
        code = 1
    else:
        code = 0

    if args.json:
        print(json.dumps({
            "rails": [m.id for m in selected],
            "unmanifested": unmanifested_rails(),
            "counts": {"fail": len(fails), "warn": len(warns),
                       "skip": len(skips), "ran": ran},
            "inert_rules": inert,
            "strict": args.strict,
            "allowed": used,
            "unallowed": unallowed if args.strict else [],
            "findings": [f.__dict__ for f in findings],
        }, indent=2))
        return code

    print(f"rail conformance — {len(selected)} manifest(s), {len(RULES)} rule(s)\n")
    order = {rid: i for i, (rid, _, _) in enumerate(RULES)}
    shown = [f for f in findings if f.level != "skip" or args.verbose]
    for f in sorted(shown, key=lambda f: (order.get(f.rule, 99), f.rail)):
        # An allowed warning is still PRINTED. The allowance changes the exit code, never what
        # the run admits to having been unable to check.
        excused = f.level == "warn" and f.allowance in allowed
        print(f.line() + (f"  (allowed: {f.allowance})" if excused else ""))
    if not shown:
        print("  no findings — every rail agrees with its manifest.")

    stray = unmanifested_rails()
    if stray:
        print(f"\nnot yet under contract (no rail.json): {', '.join(stray)}")
    for rid in inert:
        tag = inert_allowance[rid]
        mark = f"  (allowed: {tag})" if tag in allowed else ""
        print(f"\nWARN {rid}  checked nothing this run — it skipped all "
              f"{len(selected)} rails, so it is currently decoration{mark}")
    if nothing:
        print(f"\nWARN  {nothing}")
    checks = ran + len(skips)
    print(f"\n{checks} checks: {ran} ran, {len(skips)} skipped "
          f"({len(skipped_by_rule)} rule(s) skipped at least one rail)")
    # The inert-rule WARNs printed above are warnings too. Counting only `warns` printed
    # `0 fail, 0 warn` directly under a WARN line, which is the summary contradicting the report.
    print(f"{len(fails)} fail, {len(warns) + len(inert) + (1 if nothing else 0)} warn"
          + (f" (incl. {len(inert)} inert rule(s))" if inert else ""))
    if args.strict:
        # Each allowance with its count, zeros included: an allowance that matched nothing this
        # run is worth seeing, because it is either a tree where the condition does not arise
        # or a tag that stopped being emitted.
        granted = ", ".join(f"{a} x{n}" for a, n in used.items()) or "none"
        print(f"strict: {len(unallowed)} warning(s) not allowed "
              f"({'FAIL' if unallowed else 'clean'}); allowances used: {granted}")
    return code


if __name__ == "__main__":
    sys.exit(main())
