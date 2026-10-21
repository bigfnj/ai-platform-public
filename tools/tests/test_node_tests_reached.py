"""Every node test file in the tree is run by something, and every published one by the public CI.

run-tests.ps1 registers each node test file as a target of its own by hand, and
.github/scripts/run_public_suite.py restates that list for the public snapshot. Three rails drive
their frontend tests from a pytest file instead (`test_frontend_helpers.py`, `_run("x.test.js")`),
and TeachTown's vocab-pictures.test.js require()s site-escaping.test.js. Nothing checked that a
NEW node test landed in any of them: test_public_suite_plan.py compares the two hand lists with
each other, so a file added to neither ran nowhere and every suite stayed green.

A file is REACHED when:
  * run-tests.ps1 runs it as a node target (a quoted path on one of the script's lines, which
    the last tree test below holds equal to the script's own `-List` plan);
  * a pytest file in a directory run-tests.ps1 collects names it in a string literal, inside the
    same rail or package (a wrapper that runs it under node; a sentence in a docstring is not a
    literal, and another rail's `visible-poll.test.js` is not this one); or
  * a reached node test file require()s or imports it by a relative path.
For the public CI the same rules run over the driver's NODE_TARGETS and the pytest directories it
runs, for each file publish.json ships, and a wrapper counts only if it ships too.

The tree check runs in the public snapshot as well, which holds publish's share of the files: its
floor and the ways in it must use are the snapshot's there (tree_floors.py), and in the private
tree that share is read the way the snapshot will read it, so a gap shows here first.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tree_floors

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / ".github" / "scripts" / "run_public_suite.py"
#: A scan that found a handful saw nothing. The private tree held 39 node test files on 2026-10-14
#: and publish.py ships 26 of them; a snapshot's floor is the one the published-files test below
#: has held publish's share to since then, and tree_floors.reach holds that share to it here too.
NODE_FLOOR = tree_floors.Floor("node test files", private=30, published=20)
#: How each tree reaches its node test files, every way in it has (a rule that reached nothing
#: would be untested by the tree check). The one require() chain is TeachTown's, in edu-suite,
#: which publish.py withholds; a snapshot reaches its files by run-tests targets and pytest
#: wrappers alone, and the require rule is still tested on its own below. The private tree holds
#: publish's share of its files to the snapshot's set, so a chain that ships is caught here.
WAYS_IN = {"private": {"a", "wrapped", "required"}, "snapshot": {"a", "wrapped"}}
PRUNE = {".git", "node_modules", ".venv", ".venv-test", "__pycache__", ".test-logs", "dist"}
#: run-tests.ps1's core target (tools/test-targets.json, which the driver reads too): pytest
#: collects these directly.
CORE_DIRS = tuple(json.loads((REPO / "tools" / "test-targets.json").read_text(encoding="utf-8"))
                  ["core"]["paths"])
NODE_TEST = re.compile(r"\.test\.m?js$")
_QUOTED = re.compile(r"""'([^'\n]+\.test\.m?js)'""")
_RELATIVE = re.compile(r"""(?:require\s*\(|import\s*\(|\bfrom\s+|\bimport\s+)\s*['"]"""
                       r"""(\.\.?/[^'"\n]+?\.test\.m?js)['"]""")


def _own_checkout() -> bool:
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO


def _walk(pred) -> list[str]:
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".venv")]
        out += [(Path(root) / f).relative_to(REPO).as_posix() for f in files if pred(f)]
    return sorted(out)


def node_test_files() -> list[str]:
    """Every tracked *.test.js / *.test.mjs: `git ls-files` in a checkout, a pruned walk in a
    built tree that is not its own git top level (the public snapshot)."""
    if _own_checkout():
        # -z: without it git quotes a path holding a non-ASCII character, and the quoted form,
        # ending in `"`, would not even match the pattern below.
        ls = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "*.test.js", "*.test.mjs"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
        return sorted(p for p in ls.stdout.split("\0") if NODE_TEST.search(p))
    return _walk(lambda f: bool(NODE_TEST.search(f)))


def list_plan() -> set[str] | str:
    """The node test files in run-tests.ps1's own `-List` plan, or why it could not plan."""
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(REPO / "run-tests.ps1"), "-List"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return r.stdout + r.stderr
    out: set[str] = set()
    for line in r.stdout.splitlines():
        _name, _, paths = line.strip().partition(" ")
        out |= {p.strip().replace("\\", "/") for p in paths.split(",") if NODE_TEST.search(p.strip())}
    return out


def run_tests_node_targets(text: str | None = None) -> set[str]:
    """The node test files run-tests.ps1 names: every quoted *.test.js / *.test.mjs path on a line
    that is not a comment. Read from the text rather than `-List`, which needs pwsh and a repo
    venv; test_the_script_text_and_the_list_plan_agree holds the two together where both exist, so
    a path named but never wired up as a target still fails there."""
    if text is None:
        text = (REPO / "run-tests.ps1").read_text(encoding="utf-8")
    return {m.replace("\\", "/") for line in text.splitlines()
            if not line.lstrip().startswith("#") for m in _QUOTED.findall(line)}


def pytest_dirs() -> list[str]:
    """Every directory run-tests.ps1 hands pytest: core's five, then each rail's `tests` dirs
    that hold a test_*.py (not under native/, node_modules/ or .venv/), as its discovery does."""
    out = [d for d in CORE_DIRS if (REPO / d).is_dir()]
    rails = REPO / "rails"
    for rail in sorted(p for p in rails.iterdir() if p.is_dir()) if rails.is_dir() else []:
        out += sorted(d.relative_to(REPO).as_posix() for d in rail.rglob("tests")
                      if d.is_dir()
                      and not ({"native", "node_modules", ".venv"} & set(d.relative_to(rail).parts))
                      and any(d.glob("test_*.py")))
    return out


def wrapper_sources(dirs) -> dict[str, str]:
    """{path: source} of every test_*.py pytest collects under `dirs`."""
    out: dict[str, str] = {}
    for d in dirs:
        for f in sorted((REPO / d).rglob("test_*.py")):
            if not ({"node_modules", "__pycache__"} & set(f.parts)):
                out[f.relative_to(REPO).as_posix()] = f.read_text(encoding="utf-8", errors="replace")
    return out


def unit(path: str) -> str:
    """The rail or package a path belongs to: a wrapper reaches only its own unit's files."""
    parts = path.split("/")
    return "/".join(parts[:2]) if parts[0] in ("rails", "apps", "packages", "services") else parts[0]


def reached(files, targets, wrappers: dict[str, str], read) -> dict[str, str]:
    """{file: what reaches it} for each of `files` that is reached; `read(path)` is a node test
    file's source, for the require()/import chain."""
    by: dict[str, str] = {f: "a run-tests target" for f in files if f in targets}
    for f in files:
        if f in by:
            continue
        name = re.compile(r"""["'](?:[^"'\n]*[/\\])?""" + re.escape(f.rsplit("/", 1)[-1])
                          + r"""["']""")
        hit = next((w for w, src in wrappers.items() if unit(w) == unit(f) and name.search(src)),
                   None)
        if hit:
            by[f] = f"wrapped by {hit}"
    known = set(files)
    grew = True
    while grew:
        grew = False
        for f in list(by):
            for rel in _RELATIVE.findall(read(f)):
                dep = os.path.normpath(os.path.join(os.path.dirname(f), rel)).replace("\\", "/")
                if dep in known and dep not in by:
                    by[dep] = f"required by {f}"
                    grew = True
    return by


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8", errors="replace")


def ways_in(by: dict[str, str]) -> set[str]:
    """Which rules reached the files of `reached()`'s answer: "a" (target), "wrapped", "required"."""
    return {v.split(" ", 1)[0] for v in by.values()}


# --- the tree ----------------------------------------------------------------------------------

def test_every_node_test_file_is_run_by_a_target_or_a_wrapper():
    files = node_test_files()
    tree_floors.reach(files, NODE_FLOOR)
    targets, wrappers = run_tests_node_targets(), wrapper_sources(pytest_dirs())
    by = reached(files, targets, wrappers, _read)
    unrun = [f for f in files if f not in by]
    assert unrun == [], (
        "node test file(s) nothing runs: add each as a run-tests.ps1 node target (and to "
        f"run_public_suite.py's NODE_TARGETS if it ships), or drive it from a pytest file: {unrun}")
    if tree_floors.is_snapshot():
        assert ways_in(by) == WAYS_IN["snapshot"], by
        return
    assert ways_in(by) == WAYS_IN["private"], by
    # What a snapshot will find when it runs this test: publish's share of the files, reached by
    # the targets and the wrappers that ship with them.
    ship = tree_floors.published_share()
    share = ship(files)
    kept = set(ship(list(wrappers)))
    by_share = reached(share, targets, {w: s for w, s in wrappers.items() if w in kept}, _read)
    unrun = [f for f in share if f not in by_share]
    assert unrun == [], (
        "published node test file(s) a snapshot would find nothing runs, because what reaches "
        f"them here does not ship: {unrun}")
    assert ways_in(by_share) == WAYS_IN["snapshot"], by_share


@pytest.mark.skipif(not DRIVER.is_file() or not (REPO / "tools" / "publish.py").is_file(),
                    reason=".github/ and tools/publish.py are not published, so the public CI "
                           "driver and the rules that build its snapshot are not in this tree")
def test_every_published_node_test_file_is_run_by_the_public_ci(publish):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_node_reach_driver", DRIVER)
    drv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drv)
    cfg = publish.load_config()
    files = node_test_files()
    shipped = set(publish.select(cfg, files)[0])
    dirs = (list(drv.target_table(REPO)["core"]["paths"])
            + [d for _n, ds, _p in drv.rail_targets(REPO) for d in ds])
    wrappers = wrapper_sources(dirs)
    wrappers = {w: s for w, s in wrappers.items() if w in set(publish.select(cfg, list(wrappers))[0])}
    published = [f for f in files if f in shipped]
    assert len(published) >= NODE_FLOOR.published, published
    by = reached(published, {f for _n, f in drv.NODE_TARGETS}, wrappers, _read)
    unrun = [f for f in published if f not in by]
    assert unrun == [], (
        "published node test file(s) the public CI never runs: add each to run_public_suite.py's "
        f"NODE_TARGETS (and run-tests.ps1), or drive it from a shipped pytest file: {unrun}")


def test_the_script_text_and_the_list_plan_agree():
    """The checks above read run-tests.ps1's lines; what it RUNS is its `-List` plan. They must
    be the same set, or a path the script names but never wires up would pass as a target."""
    if os.name != "nt" or shutil.which("pwsh") is None:
        pytest.skip("needs pwsh on Windows: run-tests.ps1 -List is the other half")
    plan = list_plan()
    if isinstance(plan, str):
        if "no repo venv" in plan:
            pytest.skip("run-tests.ps1 cannot plan in a tree with no repo venv beside it")
        pytest.fail(f"run-tests.ps1 -List failed:\n{plan}")
    static = run_tests_node_targets()
    assert len(static) >= 25 and static == plan, (sorted(static - plan), sorted(plan - static))


# --- the rule itself, on a file list of its own -----------------------------------------------

_SCRIPT = """
# A comment naming 'web/tests/commented.test.js' runs nothing.
$t = 'web\\tests\\present.test.js'
foreach ($ui in @(@{ Name = 'x-ui'; Path = 'rails\\x\\frontend\\tests\\flow.test.js' })) { }
"""


def test_the_tree_check_fails_on_a_new_file_nothing_runs(monkeypatch):
    """The tree check itself, with an untracked dummy added to the files it finds."""
    real = node_test_files()
    monkeypatch.setattr(sys.modules[__name__], "node_test_files",
                        lambda: [*real, "web/tests/dummy-nothing-runs.test.js"])
    with pytest.raises(AssertionError, match="dummy-nothing-runs.test.js"):
        test_every_node_test_file_is_run_by_a_target_or_a_wrapper()


def test_the_tree_check_fails_on_a_scan_cut_below_its_trees_floor(monkeypatch):
    """This tree's own node test files, cut to one under the floor of the tree they are in."""
    keep = tree_floors.own(NODE_FLOOR)
    cut = node_test_files()[:keep - 1]
    monkeypatch.setattr(sys.modules[__name__], "node_test_files", lambda: cut)
    with pytest.raises(AssertionError, match=f"under its floor of {keep}:"):
        test_every_node_test_file_is_run_by_a_target_or_a_wrapper()


def test_the_tree_check_fails_when_publish_ships_less_than_a_snapshots_floor(monkeypatch):
    """The private tree's whole scan, with publish's real share of it cut to one under a
    snapshot's floor: what withholding more node test files would do to the public snapshot."""
    if not (REPO / "tools" / "publish.py").is_file():
        pytest.skip("tools/publish.py is withheld from a snapshot, so there is no share of the "
                    "scan to hold to a floor here; this runs in the private repo")
    real = tree_floors.published_share()
    monkeypatch.setattr(tree_floors, "published_share",
                        lambda: lambda paths: real(paths)[:NODE_FLOOR.published - 1])
    with pytest.raises(AssertionError, match=f"under a snapshot's floor of {NODE_FLOOR.published}"):
        test_every_node_test_file_is_run_by_a_target_or_a_wrapper()


def test_the_tree_check_fails_when_a_published_file_is_reached_only_by_what_publish_withholds(
        monkeypatch):
    """In the private tree, the check reads publish's share as the snapshot will: one of its own
    files driven only by a wrapper publish withholds runs nowhere in public."""
    if not (REPO / "tools" / "publish.py").is_file():
        pytest.skip("tools/publish.py is withheld from a snapshot, so there is no share to read; "
                    "this runs in the private repo")
    real = tree_floors.published_share()
    files = node_test_files()
    shipped = set(real(files))
    by = reached(files, run_tests_node_targets(), wrapper_sources(pytest_dirs()), _read)
    wrapped, how = next((f, v) for f, v in sorted(by.items())
                        if f in shipped and v.startswith("wrapped by "))
    wrapper = how.removeprefix("wrapped by ")
    monkeypatch.setattr(tree_floors, "published_share",
                        lambda: lambda paths: [p for p in real(paths) if p != wrapper])
    with pytest.raises(AssertionError, match=f"does not ship: .*{re.escape(wrapped)}"):
        test_every_node_test_file_is_run_by_a_target_or_a_wrapper()


def test_a_new_node_test_file_nothing_runs_is_named():
    files = ["web/tests/present.test.js", "web/tests/dummy.test.js"]
    by = reached(files, run_tests_node_targets(_SCRIPT), {}, lambda _p: "")
    assert sorted(by) == ["web/tests/present.test.js"], by


def test_the_script_parse_skips_comments_and_reads_both_registration_shapes():
    assert run_tests_node_targets(_SCRIPT) == {"web/tests/present.test.js",
                                               "rails/x/frontend/tests/flow.test.js"}


def test_a_wrapper_reaches_its_own_rails_file_only_and_only_by_a_literal():
    files = ["rails/a/frontend/tests/visible-poll.test.js", "rails/b/frontend/tests/visible-poll.test.js",
             "rails/c/frontend/tests/prose.test.js"]
    wrappers = {
        "rails/a/tests/test_frontend_helpers.py": 'def test_poll():\n    _run("visible-poll.test.js", 4)\n',
        "rails/c/tests/test_frontend_helpers.py": '"""frontend/tests/prose.test.js is run elsewhere."""\n',
    }
    by = reached(files, set(), wrappers, lambda _p: "")
    assert by == {"rails/a/frontend/tests/visible-poll.test.js":
                  "wrapped by rails/a/tests/test_frontend_helpers.py"}, by


def test_a_file_a_reached_file_requires_is_reached_and_an_unreached_ones_is_not():
    files = ["rails/x/tests/main.test.js", "rails/x/tests/sub.test.js", "rails/x/tests/deep.test.mjs",
             "rails/x/tests/orphan.test.js", "rails/x/tests/orphan-dep.test.js"]
    src = {"rails/x/tests/main.test.js": "await require('./sub.test.js')\n",
           "rails/x/tests/sub.test.js": "import { x } from './deep.test.mjs'\n",
           "rails/x/tests/orphan.test.js": "require('./orphan-dep.test.js')\n"}
    by = reached(files, {"rails/x/tests/main.test.js"}, {}, lambda p: src.get(p, ""))
    assert sorted(by) == ["rails/x/tests/deep.test.mjs", "rails/x/tests/main.test.js",
                          "rails/x/tests/sub.test.js"], by
