"""Does the repo venv hold what the code DECLARES? Reads every pyproject.toml and checks each one.

    .venv\\Scripts\\python.exe tools\\venv_doctor.py [--rail <id>]      (run-tests.ps1 -Doctor runs this)
    python tools/venv_doctor.py --requirements > reqs.txt              (CI installs from this)

Why it exists. The venv is built by hand from docs/INSTALL.md, and the rails' manifests move on
without it: python-multipart, cryptography, numpy and openai were all declared by a rail and
absent from a venv built per the docs. The affected rails then ERROR DURING COLLECTION, and
run-tests reports that as an ordinary test failure rather than as a missing package. The doctor
this replaces checked four packages named by hand, which is the same restatement-that-drifts
this repo has a checker for everywhere else.

What it checks, per group (`core` = every manifest outside rails/, then one group per rail):
  - each requirement in [project].dependencies, plus the optional groups the TESTS need
    (TEST_GROUPS), marker-evaluated;
  - installed?  (importlib.metadata, by distribution name, so no import-name table is needed)
  - the installed version satisfies the declared specifier?
  - it actually IMPORTS?  (a half-installed wheel passes the metadata check and fails here)
A requirement naming a project that lives in this tree (platform-core, edu-media-core) is
satisfied by the tree itself: run-tests puts those source roots on PYTHONPATH.

Exit 0 when nothing is missing, mismatched or broken; 1 otherwise, with one `pip install` line
for the missing ones. Absences that are DELIBERATE are listed in ABSENT_BY_DESIGN, each with the
reason, and reported as such rather than silently skipped.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:          # pragma: no cover - packaging ships with pip and pytest
    Requirement = None

REPO = Path(__file__).resolve().parents[1]

# Optional-dependency groups the test suites need. Any other group (platform_core's `db`) is a
# deployment choice, not something run-tests depends on.
TEST_GROUPS = ("dev", "test", "tests")

# Needed by the tests and declared by NO manifest (tools/ has no pyproject). Each says why.
TEST_TOOLING = {
    "pytest": "every target runs under it",
    "jsonschema": "tools/tests/test_rail_manifest_schema.py, the only check that the manifest "
                  "schema still describes the shipped manifests; it FAILS without it, naming this "
                  "fix (it used to skip instead)",
    "pyyaml": "tools/publish.py's compose gate (without it the gate runs degraded and refuses "
              "--push) and the tests that read compose YAML: test_rebuild_stale.py FAILS without "
              "it; test_compose_env_scope.py and test_env_example.py skip their YAML cases",
    "httpx2": "Starlette's TestClient backend (it ships in starlette[full]); with only httpx "
              "present every TestClient suite emits StarletteDeprecationWarning. Runtime code "
              "keeps using httpx, so no rail manifest declares it",
}

# Declared by a manifest and absent from the TEST venv on purpose. A declared dependency that is
# missing and NOT named here fails the doctor. Keys are normalised distribution names.
_MEDIA = ("the GPU media stack. Never in this venv: installing it once upgraded numpy 1.26 to "
          "2.4.6 and broke every image path. It lives in venvs/media-venv and tts-venv, and the "
          "tests stub it into sys.modules")
ABSENT_BY_DESIGN = {
    "torch": _MEDIA, "torchaudio": _MEDIA, "transformers": _MEDIA, "tts": _MEDIA,
    "diffusers": _MEDIA, "accelerate": _MEDIA,
    "ollama": "edu-media-core's translate client; its tests replace the module in sys.modules",
    "soundfile": "edu-media-core's TTS writer, imported lazily inside the functions that use it",
    "ddgs": "edu-media-core's image search, imported lazily inside the function that uses it",
    "watchdog": "edu-suite slide-audio's file watcher; slide-audio has no tests/ dir, so no "
                "run-tests target imports it",
    "scikit-learn": "finance's anomaly scan (finance/anomaly.py); no finance test imports it",
    "weasyprint": "iep's PDF engine, imported lazily; it needs native GTK/Pango libraries, and "
                  "the degrade-without-it path is what iep's tests assert",
    "playwright": "iep's chromium PDF fallback (dev group), imported lazily and needs a browser "
                  "download",
    "rendercv": "job-aid's resume renderer, imported lazily; [full] bundles a Typst engine",
    "trafilatura": "recipe-book's URL text extraction, imported lazily inside extraction.py",
}

# Editable installs record one absolute checkout path and go stale silently when a checkout moves;
# that once left NOTHING runnable for three days. Printed with the path they resolve to.
CORE_IMPORTS = ("platform_core", "platform_gateway_app")

SKIP_DIRS = {"node_modules", ".venv", "native", ".git", "dist", "build", "__pycache__"}


def norm(name: str) -> str:
    """PEP 503 normalisation, so `Pillow`, `pillow` and `PyMuPDF`/`pymupdf` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


# --- discovery ----------------------------------------------------------------------------------

def discover(repo: Path) -> dict[str, list[Path]]:
    """{group: [pyproject.toml]}: `core` for everything outside rails/, then one per rail dir."""
    groups: dict[str, list[Path]] = {}
    for p in sorted(repo.rglob("pyproject.toml")):
        rel = p.relative_to(repo).parts
        if any(part in SKIP_DIRS for part in rel[:-1]):
            continue
        group = rel[1] if rel[0] == "rails" and len(rel) > 2 else "core"
        groups.setdefault(group, []).append(p)
    return dict(sorted(groups.items(), key=lambda kv: (kv[0] != "core", kv[0])))


def project_name(pyproject: Path) -> str | None:
    return (tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}).get("name")


def declared(pyproject: Path) -> list[str]:
    proj = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
    reqs = list(proj.get("dependencies") or [])
    for group, items in (proj.get("optional-dependencies") or {}).items():
        if group in TEST_GROUPS:
            reqs += list(items)
    return reqs


# --- checking -----------------------------------------------------------------------------------

@dataclass
class Finding:
    group: str
    requirement: str
    name: str
    status: str            # ok | in-tree | absent-by-design | MISSING | VERSION | BROKEN
    detail: str = ""
    where: str = ""        # "declared in <manifest>", or why a tooling package is needed


def parse(req: str) -> tuple[str, object | None, bool]:
    """(name, specifier or None, applies-here). Without `packaging`, name only and always applies."""
    if Requirement is None:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        return (m.group(1) if m else req.strip()), None, True
    try:
        r = Requirement(req)
    except InvalidRequirement:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        return (m.group(1) if m else req.strip()), None, True
    applies = r.marker.evaluate(dict(default_environment(), extra="")) if r.marker else True
    return r.name, (r.specifier if str(r.specifier) else None), applies


def installed_version(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


_DIST_MODULES: dict[str, list[str]] | None = None


def import_error(name: str) -> str | None:
    """Import the distribution's top-level module; return the error text, or None when it imports."""
    global _DIST_MODULES
    if _DIST_MODULES is None:
        _DIST_MODULES = {}
        for mod, dists in md.packages_distributions().items():
            for d in dists:
                _DIST_MODULES.setdefault(norm(d), []).append(mod)
    mods = [m for m in _DIST_MODULES.get(norm(name), []) if not m.startswith("_") and m != "tests"]
    if not mods:
        return None                     # nothing importable recorded (a pure-data dist): metadata only
    mod = next((m for m in mods if norm(m) == norm(name)), sorted(mods)[0])
    try:
        importlib.import_module(mod)
    except (Exception, SystemExit) as e:   # noqa: BLE001 - any failure is the finding
        return f"import {mod}: {type(e).__name__}: {e}"
    return None


def check_one(group: str, req: str, where: str, in_tree: set[str],
              version_of: Callable[[str], str | None],
              importer: Callable[[str], str | None]) -> Finding | None:
    name, spec, applies = parse(req)
    if not applies:
        return None
    f = Finding(group, req, name, "ok", where=where)
    if norm(name) in in_tree:
        f.status = "in-tree"
        return f
    v = version_of(name)
    if v is None:
        reason = ABSENT_BY_DESIGN.get(norm(name))
        f.status, f.detail = ("absent-by-design", reason) if reason else ("MISSING", "not installed")
        return f
    if spec is not None and not spec.contains(v, prereleases=True):
        f.status, f.detail = "VERSION", f"installed {v}, declared {spec}"
        return f
    err = importer(name)
    if err:
        f.status, f.detail = "BROKEN", err
    else:
        f.detail = v
    return f


def core_location(mod: str) -> tuple[str | None, str | None]:
    """(where `mod` imports from, None) or (None, the error)."""
    try:
        m = importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001 - any failure is the finding
        return None, f"{type(e).__name__}: {e}"
    return getattr(m, "__file__", "builtin"), None


def scope(repo: Path, rail: str | None = None) -> tuple[dict[str, list[Path]], set[str]]:
    """(groups to check, in-tree project names). In-tree names come from EVERY manifest, so a
    rail narrowed to on its own still treats platform-core as satisfied by the tree."""
    groups = discover(repo)
    in_tree = {norm(n) for ps in groups.values() for p in ps if (n := project_name(p))}
    if rail:
        # run-tests.ps1 forwards its -Rail here under -Doctor, and its fixed targets (the node
        # tests, edu-suite-store) are named literally there; a rail target is a rails/ directory.
        ps1 = repo / "run-tests.ps1"
        named = (set(re.findall(r"Name = '([^']+)'", ps1.read_text(encoding="utf-8")))
                 if ps1.is_file() else set())
        known = (rail == "core" or rail in named or (repo / "rails" / rail).is_dir()
                 or any(rail.startswith(g + "-") for g in groups))
        if not known:
            # It used to narrow to nothing, check only the test tooling and exit 0, so a typo
            # read as a clean bill of health.
            raise UnknownRail(f"no target {rail!r}: give `core` or a rails/ directory "
                              f"(groups: {', '.join(groups)})")
        groups = {g: ps for g, ps in groups.items() if rail == g or rail.startswith(g + "-")}
    return groups, in_tree


class UnknownRail(ValueError):
    """--rail named neither `core` nor a rail directory."""


def requirements(repo: Path, rail: str | None = None) -> list[str]:
    """Every requirement the TEST venv must hold, one pip line each: the test tooling plus each
    declared dependency, minus projects in this tree and the ABSENT_BY_DESIGN set.

    The install-side twin of run(). CI installs exactly this list and then runs the doctor, so
    what gets installed and what gets checked are one definition and cannot drift apart; a hand
    list in the workflow is how the first CI run missed numpy for three rails. Markers stay in the
    line for pip to evaluate on the machine that installs.
    """
    groups, in_tree = scope(repo, rail)
    reqs = list(TEST_TOOLING) + [r for ps in groups.values() for p in ps for r in declared(p)]
    keep: dict[str, None] = {}
    for req in reqs:
        name = norm(parse(req)[0])
        if name in in_tree or name in ABSENT_BY_DESIGN:
            continue
        keep.setdefault(req.strip(), None)
    return sorted(keep, key=lambda r: (norm(parse(r)[0]), r))


def run(repo: Path, rail: str | None = None,
        version_of: Callable[[str], str | None] = installed_version,
        importer: Callable[[str], str | None] = import_error,
        out=print,
        core: Callable[[str], tuple[str | None, str | None]] = core_location) -> int:
    groups, in_tree = scope(repo, rail)
    if rail and not groups:
        out(f"note: {rail} declares no Python dependencies (no pyproject.toml), so there is "
            f"nothing of its own to check; only the test tooling and the core imports are checked")

    findings: list[Finding] = []
    for t, why in TEST_TOOLING.items():
        f = check_one("test tooling", t, f"needed by {why}", set(), version_of, importer)
        if f:
            findings.append(f)
    for g, paths in groups.items():
        for p in paths:
            where = "declared in " + p.relative_to(repo).as_posix()
            for req in declared(p):
                f = check_one(g, req, where, in_tree, version_of, importer)
                if f:
                    findings.append(f)

    for mod in CORE_IMPORTS:
        where, err = core(mod)
        if err:
            findings.append(Finding("core", mod, mod, "BROKEN", err, "an editable install (pip install -e)"))
        else:
            out(f"  OK   {mod} -> {where}")

    bad_states = ("MISSING", "VERSION", "BROKEN")
    order = ["test tooling"] + list(groups)
    said: set[str] = set()     # a shared reason (the media stack covers six packages) prints once
    for g in order:
        fs = [f for f in findings if f.group == g]
        if not fs:
            continue
        counts = {s: sum(1 for f in fs if f.status == s) for s in
                  ("ok", "in-tree", "absent-by-design", *bad_states)}
        tally = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        out(f"\n{g}: {len(fs)} declared ({tally})")
        for f in fs:
            if f.status in bad_states:
                out(f"  {f.status:8} {f.requirement}  ({f.detail}; {f.where})")
            elif f.status == "absent-by-design":
                why = "same reason as above" if f.detail in said else f.detail
                said.add(f.detail)
                out(f"  absent   {f.requirement}  (by design: {why})")

    # Deduplicated across groups: one package declared by six rails is one install.
    bad = [f for f in findings if f.status in bad_states]
    unused = sorted(set(ABSENT_BY_DESIGN) - {norm(f.name) for f in findings})
    if unused and not rail:
        out(f"\n(absent-by-design entries no manifest here declares: {', '.join(unused)})")
    if not bad:
        out("\nvenv doctor: every declared dependency is installed, current and importable.")
        return 0
    missing = sorted({f.requirement for f in bad if f.status == "MISSING"}, key=str.lower)
    out(f"\nvenv doctor: {len(bad)} problem(s) across "
        f"{len({f.group for f in bad})} group(s).")
    if missing:
        out("install the missing ones (from the manifests, never the GPU media stack):")
        out(f'  "{sys.executable}" -m pip install ' + " ".join(f'"{r}"' for r in missing))
    if any(f.status == "VERSION" for f in bad):
        out("VERSION mismatches are not in that line on purpose: resolve them by hand, because a "
            "forced reinstall of a shared package (numpy) is how this venv broke before.")
    if any(f.status == "BROKEN" for f in bad):
        out("BROKEN means installed but not importable: reinstall that package "
            "(pip install --force-reinstall --no-deps <name>), or for the editable core packages "
            "pip install -e packages\\platform_core -e services\\broker -e apps\\platform\\backend")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--rail", help="only the group for this run-tests target (core, a rail id)")
    ap.add_argument("--requirements", action="store_true",
                    help="print what the test venv must hold as pip lines, then exit "
                         "(pip install -r <that file>; CI does)")
    a = ap.parse_args(argv)
    try:
        if a.requirements:
            print("\n".join(requirements(a.repo.resolve(), a.rail)))
            return 0
        return run(a.repo.resolve(), a.rail)
    except UnknownRail as exc:
        print(f"venv doctor: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
