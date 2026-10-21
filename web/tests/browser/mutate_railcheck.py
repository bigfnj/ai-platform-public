"""Mutation harness for railcheck.py: break the fix, REBUILD the bundle, prove the browser check
goes red. NOT a test file.

    <toolbox python> web/tests/browser/mutate_railcheck.py              # every mutation
    <toolbox python> web/tests/browser/mutate_railcheck.py om- shell-   # those whose check starts so
    <toolbox python> web/tests/browser/mutate_railcheck.py --list       # name them; build nothing

Each mutation is applied to a rail's source, the rail is rebuilt with `npm run build`, and the
named railcheck check must FAIL. A check against a bundle that was never rebuilt proves nothing
(the old bytes pass), so every mutation also asserts that the set of hashed assets in `dist`
changed: that is the witness that the bytes under test are the mutated ones. The baseline must
pass every check first, and at the end each touched rail is rebuilt from the restored source
and the checks must pass again.

WHERE A MUTATION LIVES. railmutations/<rail>.py (with `-` spelled `_`) holds the mutations of
the checks railchecks/<rail>.py defines, so each module railcheck.py's RAILS reaches has one of
the same name beside it, and it goes wherever that rail goes. load() finds them from RAILS, and
refuses a module file RAILS never reaches, a mutation filed under a module whose rail or checks
are not its own, and, in this repo, a missing module. The public snapshot withholds the withheld
rails' modules (tools/publish.json): there a missing module is named by its file and skipped,
and only where the tree does not carry its rail either.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(HERE) not in sys.path:       # imported from elsewhere, not run as a script
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "tests"))
from mutation_runner import mutate_bytes, parse_harness_args, restore_exact, run_bounded  # noqa: E402
from railcheck import EXTRA_BUILDS, RAILS, frontend_dir, module_of, published_tree  # noqa: E402
from railmutations import Mutation  # noqa: E402  (this directory)

NPM = "npm.cmd" if sys.platform == "win32" else "npm"

#: Files in railmutations/ that hold no rail's mutations.
SHARED = ("__init__",)


def load(package: Path, published: bool, present: Callable[[str], bool],
         shared: tuple[str, ...] = SHARED) -> tuple[list[Mutation], list[str]]:
    """(MUTATIONS, withheld): every rail module's mutations, module by module, and the file
    names of the modules this tree withholds.

    The modules are railcheck.py's: one per module_of(a RAILS entry), its rail that name with
    `_` spelled `-`. A module may be absent only from a published tree, and only when `present`
    says its rail is absent too; anything else is a module lost by mistake. That, a module file
    no RAILS entry reaches, and a mutation of another rail or of a check another module defines
    would each leave a case out of every run without a word, so each stops every command,
    --list included."""
    mods = sorted({module_of(spec) for spec in RAILS.values()})
    stray = sorted(p.name for p in package.glob("*.py") if p.stem not in mods and p.stem not in shared)
    if stray:
        raise SystemExit(f"mutate_railcheck: no RAILS entry reaches {package.name}/{', '.join(stray)}, "
                         f"so its mutations would never run")
    found: list[Mutation] = []
    withheld: list[str] = []
    for mod in mods:
        rail = mod.replace("_", "-")
        if not (package / f"{mod}.py").is_file():
            if not published or present(rail):
                raise SystemExit(f"mutate_railcheck: {package.name}/{mod}.py is missing, so {rail}'s "
                                 f"mutations cannot run")
            withheld.append(f"{mod}.py")
            continue
        cases = importlib.import_module(f"{package.name}.{mod}").MUTATIONS
        wrong = [m.label for m in cases if m.rail != rail or module_of(RAILS.get(m.expect, "")) != mod]
        if wrong:
            raise SystemExit(f"mutate_railcheck: {package.name}/{mod}.py holds mutations of another "
                             f"rail, or of a check railchecks/{mod}.py does not define: {wrong}")
        found += cases
    return found, withheld


MUTATIONS, WITHHELD = load(HERE / "railmutations", published_tree(),
                           lambda rail: frontend_dir(rail).is_dir())


def frontend(rail: str) -> Path:
    return frontend_dir(rail)


def source(m: Mutation) -> Path:
    # "web/..." is web-core, which the shell (and every rail) builds from source.
    return REPO / m.file if m.file.startswith("web/") else frontend(m.rail) / "src" / m.file


#: Where tools/tests/mutation_anchors.py finds a case's file when it counts the anchors (a pure
#: path function: no table here names a directory that file is relative to).
anchor_target = source


def assets(rail: str) -> frozenset[str]:
    # Every bundle the rail builds (a second one too, railcheck.EXTRA_BUILDS), each name prefixed
    # with its directory, so a mutation that changes only the second bundle still shows.
    out: set[str] = set()
    for dist in ("dist", *EXTRA_BUILDS.get(rail, {}).values()):
        d = frontend(rail) / dist / "assets"
        if d.is_dir():
            out.update(f"{dist}/{p.name}" for p in d.iterdir())
    return frozenset(out)


def build(rail: str) -> None:
    for script in ("build", *EXTRA_BUILDS.get(rail, {})):
        r = run_bounded([NPM, "run", script], cwd=frontend(rail), timeout=300)
        if r.code != 0:
            raise RuntimeError(f"{rail} {script} failed:\n{r.out[-1500:]}")


def railcheck(*names: str) -> tuple[int, str]:
    r = run_bounded([sys.executable, str(HERE / "railcheck.py"), *names], cwd=REPO, timeout=300)
    return r.code, r.out


def failed(out: str, name: str) -> bool:
    return any(line.startswith(f"FAIL {name} ") for line in out.splitlines())


def main(argv: list[str] | None = None) -> int:
    # --list (printed below, with each case's module and check), and an optional filter: only the
    # mutations whose expected check starts with one of the args. Any other option exits 2.
    listing, only, _ = parse_harness_args(None, filter=True, argv=argv)
    todo = [m for m in MUTATIONS if not only or any(m.expect.startswith(o) for o in only)]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # A module this published tree withholds, with its rail, is named by its file on every run.
    # Its cases are not: they are the withheld rail's own source, quoted.
    for name in WITHHELD:
        print(f"  SKIPPED  railmutations/{name}: its rail is not in this published tree")
    if listing:
        # One line per mutation, tab-separated: module, check, label. Nothing is built or run.
        for m in todo:
            print("\t".join((module_of(m.rail), m.expect, m.label)))
        print(f"{len(todo)} mutations" + (f"; {len(WITHHELD)} modules withheld" if WITHHELD else ""))
        return 0
    if published_tree():
        # A mutant whose rail the snapshot withholds is skipped and NAMED; railcheck.py skips
        # its check the same way. In this repo every rail is here and nothing is skipped.
        gone = [m for m in todo if not frontend(m.rail).is_dir()]
        for m in gone:
            print(f"  SKIPPED  {m.label}: {m.rail} is not in this published tree")
        todo = [m for m in todo if m not in gone]
    if not todo:
        print("no mutation left to run")
        return 0 if published_tree() else 2
    # Every dist a mutation's check mounts: the shell checks need recipe-book, and
    # shell-topbar-layers terminal-fun as well.
    rails = sorted({m.rail for m in todo} | {part for m in todo for part in RAILS[m.expect].split("+")})
    # Filtered, the baseline is the selected mutations' own checks: only their rails were built.
    checks = sorted({m.expect for m in todo}) if only else []
    for rail in rails:
        build(rail)
    code, out = railcheck(*checks)
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.")
        print(out[-1500:])
        return 2
    print(f"baseline: {', '.join(checks) if checks else 'every railcheck check'} passes on fresh builds "
          f"of {', '.join(rails)}\n")

    verdicts: list[tuple[str, str]] = []
    for m in todo:
        path = source(m)
        original = path.read_bytes()
        mutated, missed = original, None
        for i, (old, new) in enumerate(((m.old, m.new), *m.also)):
            n, mutated = mutate_bytes(mutated, old, new)
            if n != 1:
                missed = (i, n)
                break
        if missed:
            print(f"  ERROR    {m.label}: edit {missed[0]}'s pattern found {missed[1]} times, expected 1")
            verdicts.append((m.label, "PATTERN"))
            continue
        before = assets(m.rail)
        try:
            path.write_bytes(mutated)
            build(m.rail)
            rebuilt = assets(m.rail) != before
            code, out = railcheck(m.expect)
            red = code != 0 and failed(out, m.expect)
            verdict = "FIRED" if red and rebuilt else ("NOT-REBUILT" if not rebuilt else "SURVIVED")
            verdicts.append((m.label, verdict))
            print(f"  {verdict:<9}{m.label}")
            print(f"           rebuilt: {'yes' if rebuilt else 'NO, the dist did not change'}; "
                  f"{m.expect} -> {'FAIL' if red else 'passed'}")
            detail = [ln for ln in out.splitlines() if ln.startswith("     ")][:3]
            for ln in detail:
                print(f"         {ln.strip()}")
            if verdict != "FIRED":
                print(out[-1200:])
        finally:
            restore_exact(path, original)

    for rail in rails:
        build(rail)
    code, out = railcheck(*checks)
    print(f"\nrestored tree, rebuilt: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [v for v in verdicts if v[1] != "FIRED"]
    print(f"{len(verdicts) - len(bad)}/{len(verdicts)} mutations fired the right check")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
