"""The rail template: one generator, two directions.

    python tools/rail_template.py check           # does every rail match the template?
    python tools/rail_template.py check --diff    # ...and show what drifted
    python tools/rail_template.py sync            # rewrite the invariant files from the template
    python tools/rail_template.py sync --rail X   # ...for one rail
    python tools/rail_template.py new <id>        # scaffold a new rail from its manifest

WHY A GENERATOR RATHER THAN A DOCUMENT
--------------------------------------
`docs/RAIL_CONTRACT.md` already says which files must be identical across rails. Saying it did
not make it true. Measured before this tool existed:

    identity.py     6 copies, 1 implementation   (2 on disk — the difference was line endings)
    modelstate.py   11 copies, 5 implementations (of a resolver the contract calls identical)
    broker.py       9 copies, 9 implementations  (legitimately per-rail)

A template that lives only in prose drifts silently, because nothing compares the copies. So the
template IS this program, and `check` regenerates every invariant file and diffs it against
disk — the same trick `gofmt -l` uses. Creation and enforcement cannot disagree, because they
are the same code path.

THREE TIERS, AND WHY broker.py IS NOT IN TIER 1
-----------------------------------------------
  invariant    generated, byte-identical, `check` fails on any difference
  conventional shape enforced by rail_conformance.py, content free
  free         genuinely per-rail; the generator seeds it and never looks again

`broker.py` is free by design: rails call different endpoints with different timeouts, and
forcing one file would either bloat every rail or push the differences somewhere worse. What IS
required of it is a SURFACE — roles(), models() -> list[dict], status(), BrokerError — because
that surface is the only reason modelstate.py can be identical everywhere. RC022 checks it.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAILS = REPO / "rails"
TEMPLATES = Path(__file__).resolve().parent / "rail_templates"

# The canonical broker facade. modelstate.py is generated against exactly this surface, so a
# rail whose facade differs cannot use the shared resolver — which is how five variants of it
# came to exist.
FACADE = ("roles", "models", "status")
FACADE_ERROR = "BrokerError"


class Rail:
    """A rail's manifest plus the paths derived from it."""

    def __init__(self, manifest: Path):
        self.manifest = manifest
        self.data = json.loads(manifest.read_text(encoding="utf-8"))
        self.id = str(self.data.get("id") or manifest.parent.name)
        self.root = manifest.parent

    @property
    def package_dir(self) -> Path:
        explicit = self.data.get("package_path")
        if explicit:
            return self.root / str(explicit)
        pkg = str(self.data.get("package") or "")
        sub = "src" if self.data.get("layout") == "src" else "backend"
        return self.root / sub / pkg

    @property
    def broker_import(self) -> str:
        """The import line modelstate.py uses to reach its facade.

        The one thing that legitimately varies in that file. Declared per rail via
        `broker_module` in the manifest; the default is the rail's own broker.py. edu-suite
        reaches the platform media package instead, which is why this is not hardcoded.
        """
        mod = str(self.data.get("broker_module") or "").strip()
        if not mod:
            return "from . import broker"
        if "." in mod:
            head, _, tail = mod.rpartition(".")
            return f"from {head} import {tail} as broker"
        return f"from . import {mod} as broker"

    def identity_path(self) -> Path | None:
        """Where identity.py lives. Rails with an api/ subpackage keep it there."""
        for cand in (self.package_dir / "api" / "identity.py", self.package_dir / "identity.py"):
            if cand.is_file():
                return cand
        return None

    def modelstate_path(self) -> Path | None:
        p = self.package_dir / "modelstate.py"
        return p if p.is_file() else None


def rails() -> list[Rail]:
    return [Rail(m) for m in sorted(RAILS.glob("*/rail.json"))]


def render(name: str, rail: Rail) -> str:
    """Render a template for a rail. Newlines are normalised to LF deliberately: the only
    difference between two identity.py copies was CRLF vs LF, which no reader would ever see."""
    text = (TEMPLATES / f"{name}.tmpl").read_text(encoding="utf-8")
    text = text.replace("{{BROKER_IMPORT}}", rail.broker_import)
    return text.replace("\r\n", "\n")


def invariants(rail: Rail) -> list[tuple[str, Path]]:
    """(template name, destination) for every tier-1 file this rail should carry."""
    out = []
    p = rail.identity_path()
    if p:
        out.append(("identity.py", p))
    p = rail.modelstate_path()
    if p:
        out.append(("modelstate.py", p))
    return out


def cmd_check(args) -> int:
    drifted = 0
    checked = 0
    for rail in rails():
        if args.rail and rail.id != args.rail:
            continue
        for name, dest in invariants(rail):
            checked += 1
            want = render(name, rail)
            have = dest.read_text(encoding="utf-8").replace("\r\n", "\n")
            if want == have:
                continue
            drifted += 1
            rel = dest.relative_to(REPO).as_posix()
            print(f"  DRIFT  {rail.id:24s} {rel}")
            if args.diff:
                for line in difflib.unified_diff(
                        want.splitlines(), have.splitlines(),
                        "template", rel, lineterm="", n=1):
                    print("         " + line)
    print(f"\n{checked - drifted}/{checked} invariant file(s) match the template")
    if drifted:
        print("run `python tools/rail_template.py sync` to adopt the template, or change the "
              "template if the rail is right")
    return 1 if drifted else 0


def cmd_sync(args) -> int:
    wrote = 0
    for rail in rails():
        if args.rail and rail.id != args.rail:
            continue
        for name, dest in invariants(rail):
            want = render(name, rail)
            have = dest.read_text(encoding="utf-8").replace("\r\n", "\n")
            if want != have:
                dest.write_text(want, encoding="utf-8", newline="\n")
                print(f"  wrote  {dest.relative_to(REPO).as_posix()}")
                wrote += 1
    print(f"\n{wrote} file(s) rewritten from the template")
    return 0


def facade_module(rail) -> "Path | None":
    """The file providing this rail's broker facade, following `broker_module`.

    Returns None for a rail that does no model work at all. meeting-atlas and workstation are
    the real cases: no HTTP client compiled in, no modelstate, nothing to resolve.
    """
    mod = str(rail.data.get("broker_module") or "broker").strip()
    if "." in mod:                       # a platform package, e.g. edu_media_core.broker_media
        head, _, tail = mod.rpartition(".")
        for base in (REPO / "packages").glob("*/src"):
            cand = base / head.replace(".", "/") / f"{tail}.py"
            if cand.is_file():
                return cand
        return None
    cand = rail.package_dir / f"{mod}.py"
    return cand if cand.is_file() else None


def facade_gaps(rail) -> list[str]:
    """Which canonical names this rail's facade is missing. Empty means conformant.

    Only meaningful for a rail with a modelstate.py: that file is generated against exactly
    this surface, so a rail without one has nothing to satisfy.
    """
    import ast
    if rail.modelstate_path() is None:
        return []
    src = facade_module(rail)
    if src is None:
        return ["<no broker facade found>"]
    tree = ast.parse(src.read_text(encoding="utf-8"))
    have = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    have |= {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    have |= {t.id for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    # An IMPORTED name counts: iep re-exports BrokerError from platform_core rather than
    # defining its own, which is correct — the surface is what matters, not its origin.
    have |= {(a.asname or a.name) for n in tree.body
             if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    return [n for n in (*FACADE, FACADE_ERROR) if n not in have]


def cmd_facade(args) -> int:
    bad = 0
    for rail in rails():
        if rail.modelstate_path() is None:
            print(f"  {rail.id:24s} n/a (no modelstate — this rail does no model work)")
            continue
        gaps = facade_gaps(rail)
        src = facade_module(rail)
        where = src.relative_to(REPO).as_posix() if src else "?"
        if gaps:
            bad += 1
            print(f"  {rail.id:24s} MISSING {gaps}  [{where}]")
        else:
            print(f"  {rail.id:24s} ok  [{where}]")
    print(f"\n{'all rails expose the canonical facade' if not bad else f'{bad} rail(s) incomplete'}")
    return 1 if bad else 0


def cmd_new(args) -> int:
    """Scaffold a rail from its manifest.

    The manifest is the input, not an output: write rails/<id>/rail.json first (the schema is
    docs/rail-manifest.schema.json), then run this. That ordering is deliberate — the manifest
    is the contract every checker reads, so a rail that starts from one cannot be born
    disagreeing with itself.
    """
    man = RAILS / args.id / "rail.json"
    if not man.is_file():
        print(f"no manifest at {man.relative_to(REPO).as_posix()}")
        print("write it first — see docs/rail-manifest.schema.json and any existing rail.json")
        return 1
    rail = Rail(man)
    pkg = rail.package_dir
    made = []
    for d in (pkg, pkg / "api", rail.root / "frontend" / "src", rail.root / "tests",
              rail.root / "docs", rail.root / "deploy"):
        if not d.exists():
            d.mkdir(parents=True)
            made.append(d)
    init = pkg / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
        made.append(init)

    # Tier 1: generated, and from here on `check` owns them.
    dest_identity = pkg / "api" / "identity.py" if (pkg / "api").is_dir() else pkg / "identity.py"
    for name, dest in (("identity.py", dest_identity), ("modelstate.py", pkg / "modelstate.py")):
        if dest.exists():
            continue
        dest.write_text(render(name, rail), encoding="utf-8", newline="\n")
        made.append(dest)

    for d in made:
        print(f"  created  {d.relative_to(REPO).as_posix()}")
    print(f"\n{len(made)} path(s) created for {rail.id!r}.")
    print("\nStill yours to write (tier 3 — genuinely per-rail):")
    print(f"  {(pkg / 'broker.py').relative_to(REPO).as_posix()}   "
          f"expose roles/models/status/BrokerError (RC022)")
    print(f"  {(pkg / 'config.py').relative_to(REPO).as_posix()}")
    print(f"  {(pkg / 'api' / 'app.py').relative_to(REPO).as_posix()}   "
          f"require Depends(identity) app-wide, docs_url=None")
    print(f"  {(rail.root / 'tests' / 'test_auth.py').relative_to(REPO).as_posix()}   "
          f"assert the 401 (RC021)")
    print("\nThen register it in the 8 places RC024 checks, and run:")
    print("  python tools/rail_conformance.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="fail if any rail has drifted from the template")
    c.add_argument("--rail")
    c.add_argument("--diff", action="store_true", help="show what differs")
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("sync", help="rewrite the invariant files from the template")
    s.add_argument("--rail")
    s.set_defaults(fn=cmd_sync)

    f = sub.add_parser("facade", help="report which rails expose the canonical broker surface")
    f.set_defaults(fn=cmd_facade)

    n = sub.add_parser("new", help="scaffold a rail from an existing rails/<id>/rail.json")
    n.add_argument("id")
    n.set_defaults(fn=cmd_new)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
