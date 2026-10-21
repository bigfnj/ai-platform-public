"""Every mutation harness answers `--list` without touching the tree, and refuses what it does
not take.

Most harnesses used to ignore argv: `--list`, the read-only habit, REWROTE the tree and ran
every suite, and about twenty more read argv as a label filter, so `--list` selected nothing and
still ran the baselines. Every harness now reads its command line through one helper before it
does anything: `parse_harness_args` (tools/tests/mutation_runner.py) for the Python ones,
`parseHarnessArgs` (web/tests/mutation_suite.mjs, which runMutations calls too) for the node ones.

What is checked here:
  * each tracked `mutate_*.py` with a `__main__` and each `mutate_*.mjs` calls that helper
    before anything runs (read from its source, BEFORE anything is launched: a harness that
    does not is never started here, because starting it is the very defect);
  * each one, launched with `--list`, ONE AT A TIME, exits 0, names at least one case, and
    leaves every file as it was: every tracked file's bytes, size and mtime (a write restored
    byte for byte still moves the mtime), and no new untracked file;
  * an unknown option is refused with exit 2 and a usage line, touching nothing;
  * no Python harness starts a child with a plain `subprocess` call (read from its source);
  * no Python harness judges FIRED by finding the expected name somewhere in the output (read
    from its source), but the few whose output is not pytest's, each listed with its reason;
  * no Python harness ends a node id at the first " - " of a summary line (read from its source);
  * each of these scans finds at least the floor of the tree it runs in, the private tree's or a
    public snapshot's (tree_floors.py), so a scan that saw nothing cannot pass them all.

The launches are sequential on purpose (each a short, read-only process): a parallel burst of
process starts is what exhausts desktop heap on a Windows box.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import mutation_runner as mr
import tree_floors

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
#: A scan that found a handful saw nothing. The private tree held 113 harnesses on 2026-10-14 and
#: 117 on 2026-10-19, 92 of them Python; publish.py ships 78 of those, 62 Python, and withholds
#: 39 and 30. A snapshot's floor is the private one less what publish withholds (100 - 39,
#: 80 - 30), and tree_floors.reach holds publish's own share of the private scan to it, so a
#: change that withholds more fails here before a snapshot is built.
HARNESS_FLOOR = tree_floors.Floor("mutation harnesses", private=100, published=61)
PY_HARNESS_FLOOR = tree_floors.Floor("Python mutation harnesses", private=80, published=50)
PRUNE = {".git", "node_modules", ".venv", ".venv-test", "__pycache__", ".test-logs", "dist",
         ".pytest_cache"}
#: One --list may take this long; it reads a table, so anything near it is a harness at work.
LIST_TIMEOUT = 60.0
#: Every listing ends on its count: "N mutations", then whatever the harness adds.
_COUNT = re.compile(r"^\d+ mutations\b", re.M)


def _git(*args: str) -> list[str] | None:
    """The paths `git -C REPO <args> -z` prints, or None when REPO is not its own checkout (the
    public snapshot is a built directory, checked by a walk instead). -z, because without it git
    quotes a path with a non-ASCII character in it, and the quoted form names no file."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != REPO:
            return None
        r = subprocess.run(["git", "-C", str(REPO), *args, "-z"], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in r.stdout.split("\0") if p]


def _walk() -> list[str]:
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".venv")]
        out += [(Path(root) / f).relative_to(REPO).as_posix() for f in files]
    return out


def tracked() -> list[str]:
    files = _git("ls-files")
    return sorted(files) if files is not None else sorted(_walk())


def harnesses() -> list[str]:
    """Every runnable harness: mutate_*.mjs, and mutate_*.py with a __main__ (a module of
    cases such as mutate_ops_backup_cases.py is not one)."""
    out = []
    for rel in tracked():
        name = rel.rsplit("/", 1)[-1]
        if not name.startswith("mutate_"):
            continue
        if name.endswith(".mjs"):
            out.append(rel)
        elif name.endswith(".py") and "__main__" in (REPO / rel).read_text(encoding="utf-8"):
            out.append(rel)
    return out


def snapshot(hashed: bool = True) -> dict[str, tuple]:
    """{path: (mtime_ns, size, sha256)} for every tracked file, plus each untracked one git does
    not ignore (as ("untracked",)), so a new file written by a harness shows too. Unhashed, for
    a run that must not even start: any write moves the mtime, and hashing costs a second."""
    snap: dict[str, tuple] = {}
    for rel in tracked():
        p = REPO / rel
        try:
            st = p.stat()
        except FileNotFoundError:
            snap[rel] = ("missing",)
            continue
        snap[rel] = (st.st_mtime_ns, st.st_size,
                     hashlib.sha256(p.read_bytes()).hexdigest() if hashed else "")
    for rel in _git("ls-files", "--others", "--exclude-standard") or []:
        snap.setdefault(rel, ("untracked",))
    return snap


def changed(before: dict, after: dict) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


# --- the static half: every harness reads its command line first --------------------------------

_DOES = re.compile(r"\b(runSuite|runBoundedSync|writeFileSync|restoreExact|spawnSync)\s*\(")


def parses_first(rel: str) -> str | None:
    """None when the harness reads its command line before it runs anything, else why not."""
    src = (REPO / rel).read_text(encoding="utf-8")
    if rel.endswith(".py"):
        tree = ast.parse(src)
        main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
                    None)
        if main is None:
            return "no main()"
        body = main.body
        if isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
            body = body[1:]                              # its docstring
        first = body[0]
        if not any(isinstance(n, ast.Call)
                   and "parse_harness_args" in (getattr(n.func, "attr", ""), getattr(n.func, "id", ""))
                   for n in ast.walk(first)):
            return "main()'s first statement does not call parse_harness_args"
        return None
    # A node harness is a top-level script: its column-0 statements run in order.
    top = [ln for ln in src.splitlines() if ln and not ln[0].isspace()]
    gate = next((i for i, ln in enumerate(top)
                 if re.match(r"(parseHarnessArgs|runMutations)\(", ln)), None)
    if gate is None:
        return "no top-level parseHarnessArgs(...) or runMutations(...) call"
    early = [ln for ln in top[:gate] if _DOES.search(ln) and not ln.startswith(("function", "import"))]
    if early:
        return f"runs something before reading its command line: {early[0]!r}"
    if top[gate].startswith("runMutations("):
        # The loop reads argv for it, so the loop itself must, before anything else it does.
        suite = (REPO / "web" / "tests" / "mutation_suite.mjs").read_text(encoding="utf-8")
        body = suite.partition("export function runMutations(")[2].partition("\nexport ")[0]
        first = next((ln.strip() for ln in body.splitlines()[1:] if ln.strip()
                      and not ln.strip().startswith("//")), "")
        if "parseHarnessArgs(" not in first:
            return "runMutations does not read its command line through parseHarnessArgs first"
    return None


def test_every_harness_reads_its_command_line_before_anything_runs():
    found = harnesses()
    tree_floors.reach(found, HARNESS_FLOOR)
    late = {h: why for h in found if (why := parses_first(h))}
    assert late == {}, late


def test_the_static_check_names_a_harness_that_ignores_its_command_line(tmp_path, monkeypatch):
    """The gate that keeps the launch test below from ever starting an unconverted harness."""
    py = tmp_path / "mutate_old.py"
    py.write_text("import sys\nMUTATIONS = [('x', 'f', 'a', 'b', 't')]\n\ndef main():\n"
                  "    only = sys.argv[1:]\n    return 0\n\nif __name__ == '__main__':\n"
                  "    sys.exit(main())\n", encoding="utf-8")
    js = tmp_path / "mutate_old.mjs"
    js.write_text("const MUTATIONS = [{ label: 'x' }]\nconst base = runSuite()\n"
                  "parseHarnessArgs({ cases: MUTATIONS })\n", encoding="utf-8")
    loop = tmp_path / "mutate_on_loop.mjs"
    loop.write_text("const mutations = [{ label: 'x' }]\nrunMutations({ mutations })\n", encoding="utf-8")
    suite = tmp_path / "web" / "tests" / "mutation_suite.mjs"
    suite.parent.mkdir(parents=True)
    suite.write_text("export function runMutations({ mutations }) {\n"
                     "  if (process.argv.includes('--list')) {\n  }\n}\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert parses_first("mutate_old.py") == "main()'s first statement does not call parse_harness_args"
    assert parses_first("mutate_old.mjs").startswith("runs something before reading its command line")
    assert parses_first("mutate_on_loop.mjs") == \
        "runMutations does not read its command line through parseHarnessArgs first"
    suite.write_text("export function runMutations({ mutations }) {\n"
                     "  if (parseHarnessArgs().listing) {\n  }\n}\n", encoding="utf-8")
    assert parses_first("mutate_on_loop.mjs") is None


# --- every child a Python harness starts goes through the shared runner -----------------------
#
# A plain subprocess.run(timeout=...) kills the child alone on a timeout and then waits on the
# pipe its grandchildren still hold, and it hands the child CLI_LIVE / JOB_AID_LIVE_TESTS if the
# developer's shell set them. mutation_runner's run_tree / run_bounded do neither. Three
# harnesses imported the runner (for its parser) and still ran their suites the plain way.

_SPAWNS = {"run", "Popen", "call", "check_call", "check_output"}


def plain_spawns(rel: str) -> list[str]:
    """`subprocess.<spawn>(` calls in a Python harness, as `line: name`; the strings a mutation
    case writes into another file are not calls, and are not read as any."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    return [f"{n.lineno}: subprocess.{n.func.attr}" for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _SPAWNS and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "subprocess"]


def test_every_python_harness_starts_its_children_through_the_shared_runner():
    found = [h for h in harnesses() if h.endswith(".py")]
    tree_floors.reach(found, PY_HARNESS_FLOOR)
    plain = {h: s for h in found if (s := plain_spawns(h))}
    assert plain == {}, plain


def test_the_spawn_check_names_a_plain_call_and_not_a_case_that_quotes_one(tmp_path, monkeypatch):
    (tmp_path / "mutate_plain.py").write_text(
        "import subprocess\nimport mutation_runner as mr\n\ndef main():\n"
        "    mr.run_tree(['x'], timeout=1)\n"
        "    return subprocess.run(['pytest'], timeout=5).returncode\n", encoding="utf-8")
    (tmp_path / "mutate_quotes.py").write_text(
        "MUTATIONS = [('x', 'f', 'subprocess.run(cmd, timeout=5)', 'pass', 't')]\n",
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert plain_spawns("mutate_plain.py") == ["6: subprocess.run"]
    assert plain_spawns("mutate_quotes.py") == []


# --- FIRED is pytest's own report of the named test, never a substring of the output ----------
#
# About thirty harnesses asked `expected in out`, which a traceback, an assertion message or a log
# line that merely names the test satisfies, and several asked it of a FAILED line, where `test_x`
# is found in `test_x_more`. Each asks mutation_runner.named_failed now. The few that judge output
# that is not pytest's (a conformance checker's findings, a node script's messages, a session
# fixture's ERROR) say so themselves: a module-level `NOT_A_PYTEST_VERDICT = "<why>"`. In the
# harness, not listed here, because several are withheld from the public snapshot and a shipped
# file may not name one (publish.withheld_refs).

_OUTPUT_NAMES = {"out", "output", "stdout", "ln", "line"}


def excused_because(rel: str) -> str:
    """The harness's own NOT_A_PYTEST_VERDICT, or "" when it declares none."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    for n in tree.body:
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
                and any(isinstance(t, ast.Name) and t.id == "NOT_A_PYTEST_VERDICT"
                        for t in n.targets)):
            return n.value.value.strip()
    return ""


def _output_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def substring_verdicts(rel: str) -> list[str]:
    """`<expected> in <output>` in a Python harness, as `line: source`: a name the case loop
    calls expect* (or one drawn from an expect* collection in a comprehension) looked for in a
    variable or attribute named for the output or one of its lines."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    drawn = {g.target.id for g in ast.walk(tree) if isinstance(g, ast.comprehension)
             and isinstance(g.target, ast.Name) and isinstance(g.iter, ast.Name)
             and g.iter.id.startswith("expect")}
    hits = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Compare) and isinstance(n.ops[0], (ast.In, ast.NotIn))
                and isinstance(n.left, ast.Name)):
            continue
        if not (n.left.id.startswith("expect") or n.left.id in drawn):
            continue
        if _output_name(n.comparators[0]) in _OUTPUT_NAMES:
            hits.append((n.lineno, f"{n.lineno}: {ast.unparse(n)}"))
    return [h for _, h in sorted(hits)]


def test_no_python_harness_judges_fired_by_a_substring_of_the_output():
    found = [h for h in harnesses() if h.endswith(".py")]
    tree_floors.reach(found, PY_HARNESS_FLOOR)
    hits = {h: s for h in found if (s := substring_verdicts(h))}
    why = {h: excused_because(h) for h in found}
    assert {h: s for h, s in hits.items() if not why[h]} == {}
    # Each excuse still has the comparison it is for, so it cannot outlive it and hide the next.
    assert sorted(h for h in found if why[h]) == sorted(h for h in hits if why[h])


def test_the_verdict_check_names_each_substring_shape_and_not_named_failed(tmp_path, monkeypatch):
    (tmp_path / "mutate_shapes.py").write_text(
        "def main(cases, out, run):\n"
        "    for label, expected in cases:\n"
        "        named = expected in out\n"
        "        named = expected in run.out\n"
        "        named = any(expected in ln for ln in out.splitlines())\n"
        "        named = [t for t in expected if t in out]\n"
        "        named = mr.named_failed(out, expected)\n"
        "        ok = WITNESS in out and all(d in out for d in details)\n", encoding="utf-8")
    (tmp_path / "mutate_excused.py").write_text(
        'NOT_A_PYTEST_VERDICT = (\n    "a checker prints "\n    "findings")\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert [h.split(":")[0] for h in substring_verdicts("mutate_shapes.py")] == ["3", "4", "5", "6"]
    assert excused_because("mutate_shapes.py") == ""
    assert excused_because("mutate_excused.py") == "a checker prints findings"


# --- no harness ends a node id at the first " - " of a summary line ----------------------------
#
# A parametrize id is free text and can hold " - " itself, so a parser that ends the id at the
# first " - " reads a test whose id quotes another test's as that other test: named_failed's own
# first parse did, refuted by its own mutation cases. Eleven harnesses kept a parser of that shape
# (ten split the line, one used a regex whose lazy name stopped at an optional " - " tail); each
# reads mutation_runner.named_failed now. Only a harness's functions are read: its tables quote
# other files' code, which is not its own parsing.

_LAZY_DASH_TAIL = re.compile(r"\?\)\(\?: - ")


def dash_cuts(rel: str) -> list[str]:
    """`line: source` for each place a Python harness's own code cuts text at " - ": a split or
    partition on it, or a regex whose lazy group stops at an optional " - " tail."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    hits: set[tuple[int, str]] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("split", "rsplit", "partition", "rpartition")
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == " - "):
                hits.add((n.lineno, f"{n.lineno}: {ast.unparse(n)}"))
            elif (isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and _LAZY_DASH_TAIL.search(n.value)):
                hits.add((n.lineno, f"{n.lineno}: {n.value!r}"))
    return [h for _, h in sorted(hits)]


def test_no_python_harness_ends_a_node_id_at_the_first_dash_of_a_line():
    found = [h for h in harnesses() if h.endswith(".py")]
    tree_floors.reach(found, PY_HARNESS_FLOOR)
    assert {h: s for h in found if (s := dash_cuts(h))} == {}


def test_the_dash_check_names_a_split_a_partition_and_a_lazy_regex_not_a_table(tmp_path,
                                                                              monkeypatch):
    (tmp_path / "mutate_cuts.py").write_text(
        "import re\n"
        "MUTATIONS = [('x', 'f.py', 'line.split(\" - \", 1)[0]', 'y', 't')]\n"
        "def failed(out):\n"
        "    a = [ln.split(' - ', 1)[0] for ln in out.splitlines()]\n"
        "    b = out.partition(' - ')[0]\n"
        "    c = re.findall(r'^FAILED \\S+?\\.py::(.+?)(?: - .*)?$', out)\n"
        "    return mr.named_failed(out, 'test_x')\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert [h.split(":")[0] for h in dash_cuts("mutate_cuts.py")] == ["4", "5", "6"]


# --- the floors: each scan fails when it finds less than its tree's floor -------------------------

_FLOORED = {
    "all": (test_every_harness_reads_its_command_line_before_anything_runs, HARNESS_FLOOR),
    "spawns": (test_every_python_harness_starts_its_children_through_the_shared_runner,
               PY_HARNESS_FLOOR),
    "verdicts": (test_no_python_harness_judges_fired_by_a_substring_of_the_output, PY_HARNESS_FLOOR),
    "dashes": (test_no_python_harness_ends_a_node_id_at_the_first_dash_of_a_line, PY_HARNESS_FLOOR),
}


def _counted(floor: tree_floors.Floor) -> list[str]:
    """The harnesses a scan under `floor` counts: every one, or the Python ones."""
    found = harnesses()
    return found if floor is HARNESS_FLOOR else [h for h in found if h.endswith(".py")]


@pytest.mark.parametrize("scan", sorted(_FLOORED))
def test_each_scan_fails_when_it_finds_less_than_its_trees_floor(scan, monkeypatch):
    """This tree's own harnesses, cut to one under the floor of the tree they are in."""
    check, floor = _FLOORED[scan]
    keep = tree_floors.own(floor)
    cut = _counted(floor)[:keep - 1]
    monkeypatch.setattr(sys.modules[__name__], "harnesses", lambda: cut)
    with pytest.raises(AssertionError, match=f"under its floor of {keep}:"):
        check()


@pytest.mark.parametrize("scan", sorted(_FLOORED))
def test_each_scan_fails_when_publish_ships_less_than_a_snapshots_floor(scan, monkeypatch):
    """The private tree's whole scan, with publish's real share of it cut to one under a
    snapshot's floor: what withholding more harnesses would do to the public snapshot."""
    if not (REPO / "tools" / "publish.py").is_file():
        pytest.skip("tools/publish.py is withheld from a snapshot, so there is no share of the "
                    "scan to hold to a floor here; this runs in the private repo")
    check, floor = _FLOORED[scan]
    real = tree_floors.published_share()
    monkeypatch.setattr(tree_floors, "published_share",
                        lambda: lambda paths: real(paths)[:floor.published - 1])
    with pytest.raises(AssertionError, match=f"under a snapshot's floor of {floor.published}"):
        check()


# --- the launches: --list touches nothing, an unknown option is refused -----------------------

def _argv(rel: str, *args: str) -> list[str]:
    return ([NODE, str(REPO / rel), *args] if rel.endswith(".mjs")
            else [sys.executable, "-X", "utf8", str(REPO / rel), *args])


def _env() -> dict:
    # No .pyc beside the harnesses, and UTF-8 out whatever the console's code page.
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")


def list_verdict(code: int, out: str, timed_out: bool) -> str | None:
    """None for a good --list run, else what is wrong with it."""
    if timed_out:
        return f"still running after {LIST_TIMEOUT:g} s: a harness at work, not a listing"
    if code != 0:
        return f"exit {code}"
    rows = [ln for ln in out.splitlines() if "\t" in ln and ln.split("\t")[-1].strip()]
    if not rows:
        return "named no case"
    if not _COUNT.search(out):
        return "no 'N mutations' count line"
    return None


def test_every_harness_lists_its_cases_and_changes_nothing():
    found = harnesses()
    late = [h for h in found if parses_first(h)]
    if late:
        pytest.fail(f"not launched, because they would run their mutations: {late} "
                    "(see test_every_harness_reads_its_command_line_before_anything_runs)")
    if NODE is None:
        pytest.skip("node is not on PATH; the node harnesses cannot be listed on this box")
    before = snapshot()
    bad: dict[str, str] = {}
    started = time.monotonic()
    for rel in found:                                   # one at a time, never in parallel
        r = mr.run_bounded(_argv(rel, "--list"), cwd=REPO, env=_env(), timeout=LIST_TIMEOUT)
        why = list_verdict(r.code, r.out, r.timed_out)
        if why:
            bad[rel] = f"{why}\n{r.out[-600:]}"
    took = time.monotonic() - started
    moved = changed(before, snapshot())
    assert moved == [], f"--list changed files under the harnesses: {moved}"
    assert bad == {}, "\n".join(f"{h}: {why}" for h, why in bad.items())
    print(f"{len(found)} harnesses listed in {took:.1f} s")


@pytest.mark.parametrize("rel", ["tools/tests/mutate_rc_coverage.py",
                                 "rails/openmaic/tests/mutate_capabilities.py",
                                 "web/tests/mutate_dictate_policy.mjs", "web/tests/mutate_poll.mjs"])
def test_an_unknown_option_is_refused_and_changes_nothing(rel):
    """One of each shape: Python with no filter and with one, a node harness with its own loop,
    and one on runMutations."""
    if not (REPO / rel).is_file():
        pytest.skip(f"{rel} is not in this tree")
    if rel.endswith(".mjs") and NODE is None:
        pytest.skip("node is not on PATH")
    assert parses_first(rel) is None
    before = snapshot(hashed=False)
    r = mr.run_bounded(_argv(rel, "--no-such-option"), cwd=REPO, env=_env(), timeout=LIST_TIMEOUT)
    assert changed(before, snapshot(hashed=False)) == []
    assert r.code == 2, r.out
    assert "unknown option" in r.out and "usage: " in r.out and "--list" in r.out, r.out


# --- parseHarnessArgs, the node twin, on argv of its own ----------------------------------------

_SUITE = REPO / "web" / "tests" / "mutation_suite.mjs"


def _node_parse(argv: list[str], filter: bool = True) -> subprocess.CompletedProcess:
    code = "\n".join([
        f"import {{ parseHarnessArgs }} from {_SUITE.as_uri()!r}",
        "const r = parseHarnessArgs({ cases: [{ label: 'a fires' }, { label: 'B case' }],",
        f"  controls: [{{ label: 'a control' }}], flags: ['--whole'], filter: {str(filter).lower()},",
        f"  argv: {argv!r} }})",
        "console.log(JSON.stringify({ listing: r.listing, only: r.only, flags: [...r.flags] }))",
    ])
    return subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_the_node_parser_lists_filters_and_refuses_like_the_python_one():
    if not _SUITE.is_file():
        pytest.skip("web/tests/mutation_suite.mjs is not in this tree")
    listed = _node_parse(["--list", "case"])
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == ["case\tB case", "1 mutations, 0 controls; listed, nothing run"]
    back = _node_parse(["x", "--whole"])
    assert back.returncode == 0 and back.stdout.strip() == \
        '{"listing":false,"only":["x"],"flags":["--whole"]}', back.stdout + back.stderr
    for argv, filt, why in ((["--bogus"], True, "unknown option '--bogus'"),
                            (["x"], False, "takes no label filter")):
        r = _node_parse(argv, filter=filt)
        assert r.returncode == 2 and why in r.stderr and "usage: node " in r.stderr, r.stderr


# --- the verdicts themselves, on runs of their own --------------------------------------------

def test_a_listing_is_judged_on_its_exit_its_rows_and_its_count():
    good = "case\ta label\ncontrol\ta control\n1 mutations, 1 controls; listed, nothing run\n"
    assert list_verdict(0, good, False) is None
    assert list_verdict(0, "core\ta label\n1 mutations\n", False) is None    # runMutations' rows
    assert list_verdict(2, good, False) == "exit 2"
    assert list_verdict(0, "baseline green: 9 passed\n6/6 mutations fired\n", False) == "named no case"
    assert list_verdict(0, "case\tx\n", False) == "no 'N mutations' count line"
    assert list_verdict(-1, "", True).startswith("still running")


def test_a_write_restored_byte_for_byte_still_counts_as_a_change(tmp_path, monkeypatch):
    """An unconverted harness restores every file it mutates, so equal bytes after the run
    prove nothing; the mtime is what shows it wrote. (Set by hand here: two writes inside one
    tick of a coarse clock could leave it where it was.)"""
    f = tmp_path / "a.py"
    f.write_bytes(b"x = 1\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "tracked", lambda: ["a.py"])
    before = snapshot()
    assert changed(before, snapshot()) == []
    f.write_bytes(b"x = 2\n")
    f.write_bytes(b"x = 1\n")                            # restored byte for byte
    os.utime(f, ns=(f.stat().st_atime_ns, before["a.py"][0] + 10_000_000))
    assert changed(before, snapshot()) == ["a.py"]
    f.write_bytes(b"x = 3\n")                            # and a change that was never restored
    os.utime(f, ns=(f.stat().st_atime_ns, before["a.py"][0]))
    assert changed(before, snapshot()) == ["a.py"]
