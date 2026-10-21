"""Every anchor of every Python mutation harness, read from its tables, nothing of it run.

A case's anchor is the text it replaces (`old`). A harness counts it in its target as it runs and
reports PATTERN unless it occurs exactly once, so an anchor that stopped matching (the code moved
on under it) is found only when somebody runs that harness, and then reads as the harness's
problem rather than the change's. Two `mutate_rails_probe.py` cases sat at zero from the shell-a
merge until the 2026-10-15 delta audit counted every broker anchor by hand. The `--list` sweep in
test_mutation_harness_cli.py proves a harness lists its cases, not that they can still match.
test_mutation_anchors.py runs `read_harness` over every Python harness in core.

How a harness is read:
  * IMPORTED, never run: under a module name of its own, with its own directory first on
    sys.path as when it runs as a script, its `__main__` guard intact. The tests-a lane proved no
    harness does anything at import but build its tables (`--list` changes no file). sys.path,
    sys.modules and os.environ are put back afterwards.
  * Its TABLES: the module-level lists its main() hands parse_harness_args, and MUTATIONS and
    CONTROLS.
  * A ROW is read by the names the harness itself unpacks rows into, taken from its source:
    every tuple target in the module (`for label, relpath, old, new, expected in MUTATIONS:`,
    `label, path, old, new, expected = entry`, `for rel, old, new in edits:`). The anchor is the
    slot named in OLD_SLOTS, its replacement the one in NEW_SLOTS, the target the one in
    FILE_SLOTS, a list of edits the one in EDIT_SLOTS (its tuples read the same way, an
    (old, new) pair when nothing in the harness names one). A layout fits a row when the lengths
    agree (a starred name takes the rest) and each named slot holds what its name says; every
    layout that fits must read the row alike. A dataclass, namedtuple or dict row is read by its
    field names, and a row that fits no layout but holds one list of edit tuples is read as
    those edits.
  * A relative TARGET is joined to what the harness joins its slot to, read from its source
    (`BROKER / relpath`, `(RAIL / rel).resolve()`): a module-level directory, or the repo when
    the harness joins it to a directory of its own making (a copy of the tree). A row with no
    target slot edits the one file main() reads through module-level names (`REPO / MAIN`,
    `TEMPLATE.read_bytes()`).
  * A harness may define `anchor_target(case) -> Path`, a pure path function, when its targets
    depend on more than a module-level directory (railcheck's live under each rail's frontend).
  * Each anchor is COUNTED as mutation_runner.mutate_bytes counts it (in either line ending), and
    a case's edits of one file are applied in order, as the harnesses apply them, so a later edit
    is counted in what the earlier ones left.

A harness whose tables this cannot read declares why at module level, `ANCHORS_UNREAD = "<why>"`,
and is named every time the check runs; one that does not declare it fails the check, and so does
a declaration on a harness that reads, so an excuse cannot outlive what it excuses. The
declaration lives in the harness, not in a list here, because several harnesses are withheld from
the public snapshot and a shipped file may not name one (publish.withheld_refs). In that snapshot
every harness is still read, but a count is said, not judged (`check` says why).

Not a test file; test_mutation_anchors.py is. The node harnesses (`mutate_*.mjs`) are not read:
their tables are top-level `const`s of scripts with no import boundary, so reading one means
running it.
"""
from __future__ import annotations

import ast
import contextlib
import dataclasses
import importlib.util
import itertools
import os
import subprocess
import sys
from pathlib import Path, PurePath
from typing import Any, NamedTuple

import mutation_runner as mr

#: The names harnesses give the slot holding a case's target file.
FILE_SLOTS = ("relpath", "rel", "path", "path_s", "file", "target")
#: ... the text a case replaces.
OLD_SLOTS = ("old", "o")
#: ... what replaces it (`_` only beside an anchor slot: `for old, _ in edits`).
NEW_SLOTS = ("new", "n", "_new", "_")
#: ... a case's list of edits: (file, old, new) or (old, new) tuples. railcheck's `also` holds
#: the further (old, new) edits of its case's own file, applied after its `old`.
EDIT_SLOTS = ("edits", "also")
#: An edit pair's layout when no tuple target in the harness reads one.
PAIR = ["old", "new"]
#: Tables read even when main() does not hand them to parse_harness_args by name (a harness that
#: lists itself passes None).
TABLE_NAMES = ("MUTATIONS", "CONTROLS")
#: The module-level declaration of a harness this cannot read, and why.
UNREAD_MARK = "ANCHORS_UNREAD"
#: A harness's own path function for a case's target.
TARGET_HOOK = "anchor_target"

_counter = itertools.count()


class Anchor(NamedTuple):
    case: str       # the case's label
    file: Path      # its target, resolved
    old: str        # the text it replaces
    count: int      # occurrences in `file`, after the case's earlier edits of it


class Reading(NamedTuple):
    anchors: list[Anchor]
    problems: list[str]     # what could not be read, each naming the case (or the table)
    unread: str             # the harness's ANCHORS_UNREAD, "" when it declares none
    missing: list[tuple[str, Path]]     # (case, target) for a target not in this tree


class Edit(NamedTuple):
    slot: str | None        # the slot that named the target; None: the case's own (or implied)
    target: Any             # as the row holds it (str or path), or None
    old: str
    new: str | None         # None when the row names no replacement


# --- which harnesses ----------------------------------------------------------------------------

def python_harnesses(repo: Path) -> list[str]:
    """Every tracked `mutate_*.py` with a `__main__` (a module of cases has none), repo-relative
    with forward slashes: `git ls-files -z` in a checkout, a walk in a built tree."""
    listed = None
    try:
        top = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == repo.resolve():
            r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", "*mutate_*.py"],
                               capture_output=True, text=True, encoding="utf-8", timeout=120,
                               check=True)
            listed = [p for p in r.stdout.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        listed = None
    if listed is None:
        prune = {".git", "node_modules", "__pycache__", "dist", ".pytest_cache", ".test-logs"}
        listed = []
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in prune and not d.startswith(".venv")]
            listed += [(Path(root) / f).relative_to(repo).as_posix() for f in files]
    return sorted(rel for rel in listed
                  if rel.rsplit("/", 1)[-1].startswith("mutate_") and rel.endswith(".py")
                  and "__main__" in (repo / rel).read_text(encoding="utf-8"))


# --- importing a harness without running it -----------------------------------------------------

@contextlib.contextmanager
def _isolated(directory: Path):
    """sys.path with `directory` first (as a script run gets), no bytecode written beside the
    harness, and sys.path, sys.modules and os.environ as they were once the block ends."""
    path, modules, env = list(sys.path), set(sys.modules), dict(os.environ)
    bytecode = sys.dont_write_bytecode
    sys.path.insert(0, str(directory))
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = bytecode
        sys.path[:] = path
        for name in set(sys.modules) - modules:
            del sys.modules[name]
        if dict(os.environ) != env:
            os.environ.clear()
            os.environ.update(env)


def _load(path: Path):
    """The harness module at `path`, imported under a name of its own (never `__main__`)."""
    name = f"_anchors_{next(_counter)}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- reading its source -------------------------------------------------------------------------

def _names(node) -> list[str] | None:
    """A tuple target's names (`*rest` as "*rest"), or None when one element is not a name."""
    out = []
    for e in node.elts:
        if isinstance(e, ast.Name):
            out.append(e.id)
        elif isinstance(e, ast.Starred) and isinstance(e.value, ast.Name):
            out.append("*" + e.value.id)
        else:
            return None
    return out


def layouts(tree: ast.Module) -> list[list[str]]:
    """Every tuple the module unpacks a row into (for, comprehension and assignment targets,
    nested ones included) that names an anchor or an edit list, in source order, each once."""
    targets = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.For, ast.comprehension)):
            targets.append(n.target)
        elif isinstance(n, ast.Assign):
            targets += n.targets
    found: list[list[str]] = []
    stack = [t for t in targets if isinstance(t, (ast.Tuple, ast.List))]
    while stack:
        t = stack.pop(0)
        stack += [e for e in t.elts if isinstance(e, (ast.Tuple, ast.List))]
        names = _names(t)
        if names and set(names) & {*OLD_SLOTS, *EDIT_SLOTS} and names not in found:
            found.append(names)
    return found


def _main(tree: ast.Module) -> ast.FunctionDef | None:
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
                None)


def table_names(tree: ast.Module) -> list[str]:
    """The names main()'s parse_harness_args call reads, then TABLE_NAMES."""
    names: list[str] = []
    main = _main(tree)
    for call in ast.walk(main) if main is not None else ():
        if (isinstance(call, ast.Call) and "parse_harness_args" in
                (getattr(call.func, "attr", ""), getattr(call.func, "id", ""))):
            for arg in [*call.args, *(k.value for k in call.keywords)]:
                found = [n for n in ast.walk(arg) if isinstance(n, ast.Name)]
                names += [n.id for n in sorted(found, key=lambda n: (n.lineno, n.col_offset))]
            break
    return list(dict.fromkeys([*names, *TABLE_NAMES]))


def joined_to(tree: ast.Module, slot: str) -> list[ast.expr]:
    """What the module joins a target slot to: `<base> / slot` and `<base> / x.slot`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            r = n.right
            if ((isinstance(r, ast.Name) and r.id == slot)
                    or (isinstance(r, ast.Attribute) and r.attr == slot)):
                out.append(n.left)
    return out


def implied_targets(tree: ast.Module, module) -> list[Path]:
    """The files main() reads through module-level names only: `A / B` with both global, and
    `G.read_text()` / `G.read_bytes()` on a global path."""
    main = _main(tree)
    found: list[Path] = []

    def value(node):
        if isinstance(node, ast.Name):
            return getattr(module, node.id, None)
        return None

    for n in ast.walk(main) if main is not None else ():
        p = None
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            a, b = value(n.left), value(n.right)
            if isinstance(a, PurePath) and isinstance(b, (str, PurePath)):
                p = Path(a) / b
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in ("read_text", "read_bytes")):
            g = value(n.func.value)
            if isinstance(g, PurePath):
                p = Path(g)
        if p is not None and p.is_file() and p.resolve() not in found:
            found.append(p.resolve())
    return found


# --- reading a row ------------------------------------------------------------------------------

def _fields(row) -> dict[str, Any] | None:
    """A record row's fields by name: a dataclass, a namedtuple or a dict."""
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return {f.name: getattr(row, f.name) for f in dataclasses.fields(row)}
    if isinstance(row, tuple) and hasattr(row, "_fields"):
        return dict(zip(row._fields, row))
    if isinstance(row, dict):
        return dict(row)
    return None


def _is_file(v) -> bool:
    return isinstance(v, (str, PurePath))


def _is_edits(v) -> bool:
    return (isinstance(v, (list, tuple)) and all(isinstance(e, (tuple, list)) and e for e in v)
            and all(all(_is_file(x) or x is None for x in e) for e in v))


def _bind(layout: list[str], row: tuple) -> dict[str, Any] | None:
    """`layout`'s names bound to `row`'s values, or None when the lengths cannot agree."""
    star = next((i for i, n in enumerate(layout) if n.startswith("*")), None)
    if star is None:
        return dict(zip(layout, row)) if len(layout) == len(row) else None
    if len(row) < len(layout) - 1:
        return None
    after = layout[star + 1:]
    return {**dict(zip(layout[:star], row[:star])),
            **(dict(zip(after, row[len(row) - len(after):])) if after else {})}


def _read_fields(f: dict[str, Any], inner: list[list[str]]) -> list[Edit] | None:
    """The edits a row bound to names makes, in order, or None when a named slot does not hold
    what its name says (or the row names no anchor at all). A case that deletes its target
    (an empty anchor, its replacement None: course-builder's "resolver is deleted") makes no
    edit, so it reads as a case with none."""
    slot = next((s for s in FILE_SLOTS if s in f), None)
    if slot is not None and not _is_file(f[slot]):
        return None
    target = f[slot] if slot else None
    new_slot = next((s for s in NEW_SLOTS if s in f), None)
    new = f[new_slot] if new_slot else None
    if new is not None and not isinstance(new, str):
        return None
    edits: list[Edit] = []
    for k in OLD_SLOTS:
        if k in f:
            if not isinstance(f[k], str):
                return None
            if f[k] == "" and new_slot and new is None and slot:
                return []                                   # the whole file deleted
            edits.append(Edit(slot, target, f[k], new))
            break
    for k in EDIT_SLOTS:
        if k not in f:
            continue
        if not _is_edits(f[k]):
            return None
        for e in f[k]:
            got = _read_tuple(tuple(e), inner + [PAIR], inner + [PAIR])
            if got is None:
                return None
            edits += [g if g.target is not None else g._replace(slot=slot, target=target)
                      for g in got]
    return edits or None


def _read_tuple(row: tuple, outer: list[list[str]], inner: list[list[str]]) -> list[Edit] | None:
    """A tuple row read through every layout that fits it; None when none fits or two disagree."""
    readings: list[list[Edit]] = []
    for layout in outer:
        bound = _bind(layout, row)
        if bound is None:
            continue
        got = _read_fields(bound, inner)
        if got is not None and [(e.target, e.old) for e in got] not in [
                [(r.target, r.old) for r in x] for x in readings]:
            readings.append(got)
    if len(readings) == 1:
        return readings[0]
    if not readings:
        lists = [v for v in row if isinstance(v, (list, tuple)) and v and _is_edits(v)]
        if len(lists) == 1:     # a table with two row shapes: this one is its edits alone
            out: list[Edit] = []
            for e in lists[0]:
                got = _read_tuple(tuple(e), inner + [PAIR], inner + [PAIR])
                if got is None:
                    return None
                out += got
            return out
    return None


def read_row(row, layouts_: list[list[str]]) -> tuple[str, list[Edit]] | None:
    """(the case's label, its edits), or None when the row cannot be read."""
    fields = _fields(row)
    if fields is not None:
        edits = _read_fields(fields, layouts_)
        label = fields.get("label")
        return (label, edits) if isinstance(label, str) and edits is not None else None
    if not isinstance(row, (tuple, list)) or not row:
        return None
    row = tuple(row)
    edits = _read_tuple(row, layouts_, layouts_)
    if edits is None:
        return None
    label = next((b["label"] for lay in layouts_ if (b := _bind(lay, row))
                  and isinstance(b.get("label"), str)), None)
    if label is None:
        label = next((v for v in row if isinstance(v, str)), "")
    return label, edits


# --- the harness --------------------------------------------------------------------------------

def _counted(edits: list[tuple[int, str, Path, str, str | None]]) -> list[Anchor]:
    """Each (row, case, file, old, new) counted in order, a file's text carried from one edit of
    a row to its next, as the harnesses apply them (by row, not label: a control may share one)."""
    texts: dict[tuple[int, Path], bytes] = {}
    out = []
    for row, case, path, old, new in edits:
        raw = texts.get((row, path))
        if raw is None:
            raw = path.read_bytes()
        n, mutated = mr.mutate_bytes(raw, old, old if new is None else new)
        texts[(row, path)] = mutated
        out.append(Anchor(case, path, old, n))
    return out


def read_harness(repo: Path, rel: str) -> Reading:
    """Read the harness `rel` (repo-relative) and count each of its anchors in its target."""
    path = repo / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = layouts(tree)
    problems: list[str] = []
    missing: list[tuple[str, Path]] = []
    edits: list[tuple[int, str, Path, str, str | None]] = []
    with _isolated(path.parent):
        module = _load(path)
        unread = getattr(module, UNREAD_MARK, "")
        unread = unread.strip() if isinstance(unread, str) else ""
        hook = getattr(module, TARGET_HOOK, None)
        repo_base = Path(getattr(module, "REPO", repo))
        implied: list[Path] | None = None

        def bases(slot: str) -> list[Path]:
            out = []
            for left in joined_to(tree, slot):
                v = getattr(module, left.id, None) if isinstance(left, ast.Name) else None
                b = Path(v) if isinstance(v, PurePath) else repo_base
                if b not in out:
                    out.append(b)
            return out or [repo_base]

        names = table_names(tree)
        tables = [(n, getattr(module, n)) for n in names
                  if isinstance(getattr(module, n, None), (list, tuple)) and getattr(module, n)
                  and not isinstance(getattr(module, n)[0], str)]
        if not tables:
            problems.append(f"no table of cases with anchors among {', '.join(names)}")
        seen: set[int] = set()
        for name, table in tables:
            for row in table:
                if id(row) in seen:
                    continue
                seen.add(id(row))
                got = read_row(row, found)
                if got is None:
                    head = row[0] if isinstance(row, (tuple, list)) and row else row
                    problems.append(f"{name}: a row this cannot read: {str(head)[:100]!r}")
                    continue
                label, case_edits = got
                for e in case_edits:
                    if hook is not None:
                        resolved = Path(hook(row))
                    elif e.target is None:
                        if implied is None:
                            implied = implied_targets(tree, module)
                        if len(implied) != 1:
                            problems.append(f"{label}: no target slot, and main() reads "
                                            f"{len(implied)} files by module-level names, not 1")
                            break
                        resolved = implied[0]
                    elif Path(e.target).is_absolute():
                        resolved = Path(e.target)
                    else:
                        cands = [b / e.target for b in bases(e.slot)]
                        hits = list(dict.fromkeys(c.resolve() for c in cands if c.is_file()))
                        if len(hits) > 1:
                            problems.append(f"{label}: {e.target} names {len(hits)} files")
                            break
                        resolved = hits[0] if hits else cands[0]
                    if not resolved.is_file():
                        missing.append((label, resolved))
                        continue
                    edits.append((len(seen), label, resolved.resolve(), e.old, e.new))
    return Reading(_counted(edits), problems, unread, missing)


# --- the whole tree -----------------------------------------------------------------------------

class Report(NamedTuple):
    failures: list[str]     # each names the harness, and the case where there is one
    unread: list[str]       # `harness: why` for each that declares ANCHORS_UNREAD
    harnesses: int          # read
    anchors: int            # counted
    notes: list[str]        # a published tree's counts, said and not judged (see check)


def published(repo: Path) -> bool:
    """The public snapshot withholds tools/publish.py (and several rails' files, which the
    harnesses there skip as absent)."""
    return not (repo / "tools" / "publish.py").is_file()


def check(repo: Path) -> Report:
    """Every Python harness in `repo` read, and each anchor that does not occur exactly once in
    its target named, with its harness and case; so is a harness or row that cannot be read
    and does not say why, and a declaration on one that reads.

    In a checkout a target that is not there fails too. In a published tree (publish's OUTPUT:
    withheld rails' files gone, compose and the gateway's config de-wired, text substituted) the
    harnesses are still read, so an unreadable one fails there as here, but a count is a note,
    not a failure: the harnesses skip what publish drops, each its own way (env_example's
    HAS_JOB_AID, doc_claims' PRIVATE_ONLY, ops_scripts' ABSENT), and the anchors are kept, and
    this gates, in the checkout the snapshot is made from."""
    failures: list[str] = []
    unread: list[str] = []
    notes: list[str] = []
    pub = published(repo)
    read = counted = 0
    for rel in python_harnesses(repo):
        r = read_harness(repo, rel)
        if r.unread:
            if r.problems:
                unread.append(f"{rel}: {r.unread}")
            else:
                failures.append(f"{rel}: declares {UNREAD_MARK}, but every table reads; drop the "
                                f"declaration so its anchors are counted")
            continue
        read += 1
        counted += len(r.anchors)
        failures += [f"{rel}: {p} (read it, or declare {UNREAD_MARK} = \"<why>\" in the harness)"
                     for p in r.problems]
        if not pub:
            failures += [f"{rel}: [{case}] target not in this tree: {target}"
                         for case, target in r.missing]
        for a in r.anchors:
            if a.count != 1:
                where = a.file.relative_to(repo.resolve()) if repo.resolve() in a.file.parents \
                    else a.file
                (notes if pub else failures).append(
                    f"{rel}: [{a.case}] found {a.count} times in {Path(where).as_posix()}, "
                    f"expected 1: {a.old[:100]!r}")
    return Report(failures, unread, read, counted, notes)


def main(argv: list[str] | None = None) -> int:
    """`python tools/tests/mutation_anchors.py [REPO]`: the check, by hand, over any tree."""
    args = list(sys.argv[1:] if argv is None else argv)
    repo = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[2]
    report = check(repo)
    for line in report.unread:
        print(f"NOT READ  {line}")
    for line in report.notes:
        print(f"NOTE      {line} (a published tree: not judged)")
    for line in report.failures:
        print(f"FAIL      {line}")
    print(f"{report.anchors} anchors in {report.harnesses} harnesses read, "
          f"{len(report.unread)} declared unread, {len(report.failures)} failures")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
