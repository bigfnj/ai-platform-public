"""web/tests/browser/railcheck.py finds its checks in one module per rail (web/tests/browser/
railchecks/). The checks themselves need Playwright and built bundles, so they are not a target
here. What is decidable without a browser is that the command line names, orders and finds every
check, and that a module lost from the tree, or out of step with the RAILS table, stops it rather
than leaving a check out of a run without a word.

In the public snapshot, which excludes the withheld rails' modules, the same tests prove the
command line still loads there and still names every check.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BROWSER = REPO / "web" / "tests" / "browser"
_fresh = itertools.count()


@pytest.fixture(scope="module")
def railcheck():
    """railcheck.py, loaded by path (web/tests/browser is not a package). It puts its own directory
    on sys.path to import railchecks/; that is undone afterwards so no other test module sees it."""
    path_before = list(sys.path)
    spec = importlib.util.spec_from_file_location("railcheck", BROWSER / "railcheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["railcheck"] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path[:] = path_before
        for name in [n for n in sys.modules if n in ("railcheck", "railchecks") or n.startswith("railchecks.")]:
            del sys.modules[name]


# --- this tree ------------------------------------------------------------------------------

def test_every_check_is_found_or_withheld_once_in_table_order(railcheck):
    found, withheld = list(railcheck.CHECKS), railcheck.WITHHELD
    assert set(found).isdisjoint(withheld)
    assert set(found) | set(withheld) == set(railcheck.RAILS)
    assert found == [n for n in railcheck.RAILS if n in railcheck.CHECKS]


def test_only_a_published_tree_withholds_and_only_checks_of_a_rail_it_lacks(railcheck):
    if not railcheck.published_tree():
        assert railcheck.WITHHELD == []
    lacking = [n for n in railcheck.WITHHELD
               if all(railcheck.frontend_dir(p).is_dir() for p in railcheck.RAILS[n].split("+"))]
    assert lacking == []


def test_a_check_is_defined_in_the_module_of_its_first_frontend(railcheck):
    """The header's rule, which is also how the public snapshot decides what to exclude."""
    misplaced = [n for n, (spec, fn) in railcheck.CHECKS.items()
                 if fn.__module__ != "railchecks." + railcheck.module_of(spec)]
    assert misplaced == []


# --- load(), on a throwaway package ---------------------------------------------------------

TABLE = {"a-one": "alpha", "b-one": "beta-rail", "a-two": "alpha", "s-one": "shell+alpha"}
FULL = {"alpha": ["a-one", "a-two"], "beta_rail": ["b-one"], "shell": ["s-one"]}


def _package(tmp_path, monkeypatch, modules: dict[str, list[str]]) -> Path:
    """A railchecks-shaped package (an __init__, a harness, and module -> the names its CHECKS
    defines) under a name no other test uses, so the import system cannot hand back a cached one."""
    pkg = tmp_path / f"fakechecks{next(_fresh)}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "harness.py").write_text("", encoding="utf-8")
    for mod, names in modules.items():
        body = "".join(f"def c{i}(page):\n    return []\n\n\n" for i in range(len(names)))
        body += "CHECKS = {" + ", ".join(f"{n!r}: c{i}" for i, n in enumerate(names)) + "}\n"
        (pkg / f"{mod}.py").write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return pkg


def _everywhere(part: str) -> bool:
    return True


def test_the_whole_table_loads_in_its_own_order(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, FULL)
    checks, withheld = railcheck.load(TABLE, pkg, False, _everywhere)
    assert list(checks) == ["a-one", "b-one", "a-two", "s-one"] and withheld == []
    assert checks["s-one"][0] == "shell+alpha" and checks["s-one"][1](None) == []


def test_a_check_the_table_does_not_name_stops_it(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, dict(FULL, alpha=["a-one", "a-two", "a-three"]))
    with pytest.raises(SystemExit, match=r"defined but not in RAILS \['a-three'\]"):
        railcheck.load(TABLE, pkg, False, _everywhere)


def test_a_check_its_module_does_not_define_stops_it(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, dict(FULL, alpha=["a-one"]))
    with pytest.raises(SystemExit, match=r"in RAILS but not defined \['a-two'\]"):
        railcheck.load(TABLE, pkg, False, _everywhere)


def test_a_module_no_table_entry_reaches_stops_it(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, dict(FULL, gamma=["g-one"]))
    with pytest.raises(SystemExit, match=r"no RAILS entry reaches .*gamma\.py"):
        railcheck.load(TABLE, pkg, False, _everywhere)


def test_a_missing_module_stops_a_private_tree(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, {k: v for k, v in FULL.items() if k != "beta_rail"})
    with pytest.raises(SystemExit, match=r"beta_rail\.py is missing, so b-one cannot run"):
        railcheck.load(TABLE, pkg, False, lambda part: part != "beta-rail")


def test_a_published_tree_names_the_checks_of_a_rail_it_lacks(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, {k: v for k, v in FULL.items() if k != "beta_rail"})
    checks, withheld = railcheck.load(TABLE, pkg, True, lambda part: part != "beta-rail")
    assert withheld == ["b-one"] and list(checks) == ["a-one", "a-two", "s-one"]


def test_a_published_tree_that_has_the_rail_still_needs_its_module(railcheck, tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, {k: v for k, v in FULL.items() if k != "beta_rail"})
    with pytest.raises(SystemExit, match=r"beta_rail\.py is missing"):
        railcheck.load(TABLE, pkg, True, _everywhere)
