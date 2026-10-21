"""The public CI suite runs the same targets run-tests.ps1 does.

public-snapshot-dryrun.yml runs the built snapshot's tests through
.github/scripts/run_public_suite.py, which restates run-tests.ps1's discovery rules because the
script itself is Windows-shaped and cannot run on the runner. A restatement drifts: add a node
target to run-tests.ps1 and CI would simply never run it, green. So the two plans are compared
here, on this tree, target by target: each target's test paths, its PYTHONPATH and its pytest
arguments, read from `run-tests.ps1 -List -Json`. The pytest targets' PYTHONPATH is not restated
any more (both read tools/test-targets.json), but a reader can still ignore part of the table:
the driver's restatement gave every rail its source root alone, and this compared paths only.

The only targets the driver may lack are run-tests.ps1's `edu-suite-*` file targets: named files
inside a rail the public snapshot withholds, so they cannot exist where the driver runs.

Inside a snapshot, a node target whose file is absent is named on every run: skipped when
publish.json withholds it, a failure when publish would have shipped it (the second half of this
file). Both used to be dropped without a word, so a target lost by mistake read as withheld.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_DRIVER = REPO / ".github" / "scripts" / "run_public_suite.py"
_TABLE = "tools/test-targets.json"
if not _DRIVER.is_file():
    pytest.skip(".github/ is not published, so the public CI driver is not part of this tree; "
                "this comparison runs in the private repo.", allow_module_level=True)

_NEEDS_PWSH = pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="needs pwsh on Windows: run-tests.ps1 -List is the other half of the comparison")


def _driver():
    spec = importlib.util.spec_from_file_location("_run_public_suite", _DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo_relative(p: str) -> str:
    """A PYTHONPATH entry run-tests.ps1 built (this tree's own path) as the driver writes it."""
    return Path(p).resolve().relative_to(REPO).as_posix()


def _run_tests_plan() -> dict[str, dict[str, list[str]]]:
    """{target: {paths, pypath, args}}, from the plan run-tests.ps1 itself builds."""
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(REPO / "run-tests.ps1"), "-List", "-Json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return {t["Name"]: {"paths": [p.replace("\\", "/") for p in t["Paths"]],
                        "pypath": [_repo_relative(p) for p in t["PyPath"]],
                        "args": t["PytestArgs"]}
            for t in json.loads(r.stdout)}


def _plan_of(targets) -> dict[str, dict[str, list[str]]]:
    """The driver's plan, keyed and shaped as _run_tests_plan's."""
    return {t["name"]: {"paths": list(t["paths"]), "pypath": list(t["pypath"]),
                        "args": list(t.get("args", []))} for t in targets}


@_NEEDS_PWSH
def test_the_driver_runs_every_target_run_tests_runs():
    ours = _plan_of(_driver().plan(REPO))
    theirs = _run_tests_plan()
    assert "core" in theirs and len(theirs) > 5, theirs        # the parse itself worked
    # Every rail target's PYTHONPATH starts in its own rail: a witness that the comparison below
    # sees PYTHONPATH at all.
    assert all(t["pypath"] and t["pypath"][0].startswith(f"rails/{n}/")
               for n, t in theirs.items() if (REPO / "rails" / n).is_dir()), theirs
    missing = sorted(n for n in theirs if n not in ours and not n.startswith("edu-suite-"))
    extra = sorted(n for n in ours if n not in theirs)
    assert missing == [] and extra == [], f"run-tests.ps1 only: {missing}; driver only: {extra}"
    differ = {n: {k: (ours[n][k], theirs[n][k]) for k in ours[n] if ours[n][k] != theirs[n][k]}
              for n in ours if ours[n] != theirs[n]}
    assert differ == {}, differ


def test_a_rail_targets_pythonpath_is_the_tables_rule(tmp_path):
    """Its source root (its own entry, else src/, else backend/, else the rail dir), then the
    shared libraries: one rail per branch of the rule, where no pwsh is needed to check it."""
    drv = _driver()
    rails = ("a-src", "b-backend", "c-bare", "d-own")
    snap = _snapshot(tmp_path, [f"rails/{r}/tests/test_x.py" for r in rails]
                     + ["rails/a-src/src/x.py", "rails/a-src/backend/x.py",
                        "rails/b-backend/backend/x.py", "rails/d-own/src/x.py"])
    table = json.loads((snap / _TABLE).read_text(encoding="utf-8"))
    table["rail"]["source_root_of"] = {"d-own": "apps/x/src"}
    (snap / _TABLE).write_text(json.dumps(table), encoding="utf-8")
    shared = table["rail"]["shared"]
    assert len(shared) >= 2, shared
    got = {n: p for n, _dirs, p in drv.rail_targets(snap)}
    assert got == {"a-src": ["rails/a-src/src", *shared],
                   "b-backend": ["rails/b-backend/backend", *shared],
                   "c-bare": ["rails/c-bare", *shared],
                   "d-own": ["rails/d-own/apps/x/src", *shared]}, got


# --- an absent node target is named, and only a withheld one may pass -------------------------

def _rails() -> tuple[str, str]:
    """(a rail publish.json ships, a rail it withholds), read from the real config, so these
    tests follow the allowlist rather than pinning today's."""
    cfg = json.loads((REPO / "tools" / "publish.json").read_text(encoding="utf-8"))
    withheld = sorted(p.name for p in (REPO / "rails").iterdir()
                      if p.is_dir() and p.name not in cfg["rails"])
    return sorted(cfg["rails"])[0], withheld[0]


def _snapshot(tmp_path: Path, present: list[str]) -> Path:
    snap = tmp_path / "snap"
    snap.mkdir(parents=True)
    (snap / "run-tests.ps1").write_text("", encoding="utf-8")
    (snap / _TABLE).parent.mkdir(parents=True)                  # every snapshot ships the table
    shutil.copy2(REPO / _TABLE, snap / _TABLE)
    for f in present:
        (snap / f).parent.mkdir(parents=True, exist_ok=True)
        (snap / f).write_text("", encoding="utf-8")
    return snap


def _targets(drv, monkeypatch, node, tsc=()) -> None:
    """These NODE_TARGETS, and these (-types name, frontend dir) as what the checkout type-checks."""
    monkeypatch.setattr(drv, "NODE_TARGETS", list(node))
    monkeypatch.setattr(drv, "tsc_targets", lambda _root: list(tsc))


def _stubbed(drv, monkeypatch, targets, tsc=()) -> None:
    """The driver with these targets and every target's process faked as a pass."""
    def fake_run(cmd, **_kw):
        out = "ℹ pass 1\n" if cmd[0] == "node" else "1 passed in 0.01s\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    _targets(drv, monkeypatch, targets, tsc)
    monkeypatch.setattr(drv, "shutil", types.SimpleNamespace(which=lambda _name: "node"))
    monkeypatch.setattr(drv, "subprocess", types.SimpleNamespace(run=fake_run))
    monkeypatch.setattr(drv, "_typecheck",
                        lambda _snap, fe, _node: subprocess.CompletedProcess([fe], 0, "", ""))


def test_an_absent_node_target_is_kept_in_the_plan_with_why(tmp_path, monkeypatch):
    drv = _driver()
    published, withheld = _rails()
    present = "web/tests/present.test.js"
    targets = [("here", present),
               ("withheld-rail", f"rails/{withheld}/frontend/tests/x.test.js"),
               ("published-rail", f"rails/{published}/frontend/tests/x.test.js"),
               ("outside-rails", "web/tests/vanished.test.js")]
    _targets(drv, monkeypatch, targets)
    got = {t["name"]: t for t in drv.plan(_snapshot(tmp_path, [present]))}
    assert [n for n in got if n != "core"] == [n for n, _f in targets]
    assert "absent" not in got["here"]
    assert got["withheld-rail"]["withheld"] is True, got["withheld-rail"]
    assert "rail not in the allowlist" in got["withheld-rail"]["absent"]
    for name in ("published-rail", "outside-rails"):
        assert got[name]["withheld"] is False and got[name]["absent"].startswith("MISSING"), got[name]


def test_a_run_names_every_withheld_target_and_still_passes(tmp_path, monkeypatch, capsys):
    drv = _driver()
    _published, withheld = _rails()
    present = "web/tests/present.test.js"
    _stubbed(drv, monkeypatch, [("here", present),
                                ("withheld-rail", f"rails/{withheld}/frontend/tests/x.test.js")])
    assert drv.run(_snapshot(tmp_path, [present])) == 0
    out = capsys.readouterr().out
    assert "skip  withheld-rail" in out, out
    assert "| withheld-rail | withheld by publish.json" in out, out
    assert "withheld (not in this snapshot by design): withheld-rail" in out, out
    assert "2/2 targets pass, 1 withheld" in out, out          # core and `here`; not the skip


def test_a_run_fails_on_a_target_file_publish_would_have_shipped(tmp_path, monkeypatch, capsys):
    drv = _driver()
    published, _withheld = _rails()
    _stubbed(drv, monkeypatch, [("published-rail", f"rails/{published}/frontend/tests/x.test.js"),
                                ("outside-rails", "web/tests/vanished.test.js")])
    assert drv.run(_snapshot(tmp_path, [])) == 1
    out = capsys.readouterr().out
    assert "FAIL  published-rail" in out and "FAIL  outside-rails" in out, out
    assert "FAILED: published-rail, outside-rails" in out, out


def test_without_publish_py_an_absent_target_cannot_pass_as_withheld(tmp_path, monkeypatch,
                                                                     capsys):
    """The rules that would say "withheld" are not there: a third outcome, and it fails closed."""
    drv = _driver()
    _published, withheld = _rails()
    monkeypatch.setattr(drv, "PUBLISH", tmp_path / "no-publish.py")
    _stubbed(drv, monkeypatch, [("withheld-rail", f"rails/{withheld}/frontend/tests/x.test.js")])
    assert drv.run(_snapshot(tmp_path, [])) == 1
    assert "is not here to say whether the snapshot withholds it" in capsys.readouterr().out


def test_plan_exits_1_only_for_a_missing_target(tmp_path, monkeypatch, capsys):
    drv = _driver()
    published, withheld = _rails()
    _targets(drv, monkeypatch, [("w", f"rails/{withheld}/frontend/tests/x.test.js")])
    assert drv.main(["x", "plan", str(_snapshot(tmp_path / "a", []))]) == 0
    assert "withheld by publish.json" in capsys.readouterr().out
    _targets(drv, monkeypatch, [("p", f"rails/{published}/frontend/tests/x.test.js")])
    assert drv.main(["x", "plan", str(_snapshot(tmp_path / "b", []))]) == 1


def test_a_run_echoes_what_a_cp1252_console_cannot_encode(tmp_path):
    """node's test reporter writes U+2714 and U+2139, and the driver echoes every target's output.
    Under a console whose encoding is cp1252 (a Windows default) that print raised
    UnicodeEncodeError and ended the run, unless PYTHONIOENCODING=utf-8 was set by hand. Run as
    its own process, because the console encoding is fixed when the interpreter starts."""
    code = "\n".join([
        "import importlib.util, sys",
        f"spec = importlib.util.spec_from_file_location('drv', {str(_DRIVER)!r})",
        "drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)",
        "drv.run = lambda _snap: print('\\u2714 a node test \\u2139 tests 1', flush=True) or 0",
        f"sys.exit(drv.main(['driver', 'run', {str(_snapshot(tmp_path, []))!r}]))",
    ])
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=120)
    err = r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, err
    assert "✔ a node test ℹ tests 1" in r.stdout.decode("utf-8"), (r.stdout, err)


def test_every_target_names_a_file_in_this_tree():
    """Nothing is withheld from the private tree, so an absent file here is a typo or a rename,
    whatever publish.json would say about it in a snapshot."""
    assert [(t["name"], t["paths"]) for t in _driver().plan(REPO) if "absent" in t] == []


# --- the -types targets follow the same rule ----------------------------------------------------

def test_a_withheld_rails_types_target_is_named_and_skipped(tmp_path, monkeypatch, capsys):
    drv = _driver()
    published, withheld = _rails()
    _stubbed(drv, monkeypatch, [], tsc=[(f"{published}-types", f"rails/{published}/frontend"),
                                        (f"{withheld}-types", f"rails/{withheld}/frontend")])
    snap = _snapshot(tmp_path, [f"rails/{published}/frontend/tsconfig.json"])
    assert drv.run(snap) == 0
    out = capsys.readouterr().out
    assert f"skip  {withheld}-types  rails/{withheld}/frontend/tsconfig.json: withheld" in out, out
    assert f"| {published}-types | PASS |" in out, out
    assert "2/2 targets pass, 1 withheld" in out, out           # core and the shipped -types


def test_a_shipped_frontend_without_its_tsconfig_fails(tmp_path, monkeypatch, capsys):
    drv = _driver()
    published, _withheld = _rails()
    _stubbed(drv, monkeypatch, [], tsc=[(f"{published}-types", f"rails/{published}/frontend")])
    assert drv.run(_snapshot(tmp_path, [f"rails/{published}/frontend/package.json"])) == 1
    out = capsys.readouterr().out
    assert f"FAIL  {published}-types  rails/{published}/frontend/tsconfig.json: MISSING" in out, out
    assert f"FAILED: {published}-types" in out, out


def test_the_types_list_is_the_checkouts_not_the_snapshots(tmp_path, monkeypatch):
    """Discovered in the snapshot, a frontend that lost its tsconfig.json had no target at all,
    the same silence as a dropped node target. The list is taken from SOURCE instead."""
    drv = _driver()
    published, _withheld = _rails()
    source = tmp_path / "checkout"
    (source / "rails" / published / "frontend").mkdir(parents=True)
    (source / "rails" / published / "frontend" / "tsconfig.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(drv, "SOURCE", source)
    monkeypatch.setattr(drv, "NODE_TARGETS", [])
    got = {t["name"]: t for t in drv.plan(_snapshot(tmp_path / "s", []))}
    assert got[f"{published}-types"]["absent"].startswith("MISSING"), got
