"""run-tests.ps1: what its summary and exit code say about targets that checked nothing.

Drives the REAL script, copied into a scratch repo with a handful of tiny targets, under both
PowerShell engines. The scratch repo gets a thin venv (no pip, ~0.2 s) whose site-packages
bridges to this interpreter's, so pytest is the real one and nothing is installed anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# edu-suite's real dashboard app: run-tests special-cases its PYTHONPATH, and the scratch rail below
# copies its LAYOUT (package under src). The public snapshot withholds the rail, and with it the
# reason for the special case, so the edu scenario runs only where the rail is.
EDU = REPO / "rails" / "edu-suite" / "apps" / "dashboard"
HAS_EDU = EDU.is_dir() and (EDU / "src" / "dashboard" / "__init__.py").is_file()

# The pytest targets' table, which the scratch repo gets beside its copy of the script (a mutation
# of the real table reaches the scenarios that way). Its core target names these five directories;
# they must exist or pytest fails the core target with "file or directory not found". Distinct
# basenames, because rootdir-less non-package test files with one name collide on import.
TABLE = "tools/test-targets.json"
CORE_DIRS = json.loads((REPO / TABLE).read_text(encoding="utf-8"))["core"]["paths"]

PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_broken():\n    assert False, 'gamma is broken on purpose'\n"
# A suite whose only tests are live ones: run-tests deselects them with -k 'not LiveTests', so
# pytest selects nothing and exits 5. RC028 still sees a test_*.py here, so nothing else notices.
# unittest.TestCase, the shape job-aid's LiveTests take: pytest collects those whatever the name.
LIVE_ONLY = ("import unittest\n\n\nclass LiveTests(unittest.TestCase):\n"
             "    def test_reaches_a_real_job_board(self):\n        self.assertTrue(True)\n")


# Records the live opt-ins its pytest process sees, where the PowerShell body can print them.
# The live tests check these variables themselves (CLI_LIVE for the Claude CLI, JOB_AID_LIVE_TESTS
# for job-aid's keyed boards), so what run-tests.ps1 hands its pytest IS the guard.
LIVE_PROBE = ("import os\nfrom pathlib import Path\n\n\ndef test_records_the_live_opt_ins():\n"
              "    seen = ' '.join(k + '=' + os.environ.get(k, '<unset>')\n"
              "                    for k in ('CLI_LIVE', 'JOB_AID_LIVE_TESTS'))\n"
              "    Path(__file__).resolve().parents[3].joinpath('live_seen.txt').write_text(\n"
              "        seen, encoding='utf-8')\n")


# A rail whose test imports the shared libraries and asserts they are THIS tree's. The scratch
# venv bridges to the repo venv, whose editable installs point at the main checkout, so a rail
# target without this tree's packages on its PYTHONPATH imports another checkout's copy: run from
# a worktree, every rail suite then tested against code the run never touched (found 2026-10-08).
SHARED_PROBE = ("from pathlib import Path\n\nimport edu_media_core\nimport platform_core\n\n\n"
                "def test_the_shared_libraries_are_this_trees():\n"
                "    root = Path(__file__).resolve().parents[3]\n"
                "    for mod in (platform_core, edu_media_core):\n"
                "        got = Path(mod.__file__).resolve()\n"
                "        assert got.is_relative_to(root), f'{mod.__name__} came from {got}'\n")


def make_repo(base: Path) -> Path:
    root = base / "repo"
    root.mkdir()
    shutil.copy2(REPO / "run-tests.ps1", root / "run-tests.ps1")
    for i, d in enumerate(CORE_DIRS):
        (root / d).mkdir(parents=True)
        (root / d / f"test_core_{i}.py").write_text(PASSING, encoding="utf-8")
    shutil.copy2(REPO / TABLE, root / TABLE)
    for pkg in ("packages/platform_core/platform_core", "packages/edu-media-core/src/edu_media_core"):
        (root / pkg).mkdir(parents=True)
        (root / pkg / "__init__.py").write_text("", encoding="utf-8")
    for rail, body in (("alpha", PASSING), ("beta", LIVE_ONLY), ("gamma", FAILING),
                       ("delta", LIVE_PROBE), ("epsilon", SHARED_PROBE)):
        (root / "rails" / rail / "tests").mkdir(parents=True)
        (root / "rails" / rail / "tests" / f"test_{rail}.py").write_text(body, encoding="utf-8")
    # edu-suite's shape, copied from the real rail: the package under <app>/src, its tests beside
    # it, and a test that imports the package with no sys.path help of its own (as
    # test_ownership.py does).
    if HAS_EDU:
        pkg = root / EDU.relative_to(REPO)
        (pkg / "src" / "dashboard").mkdir(parents=True)
        (pkg / "src" / "dashboard" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "tests").mkdir()
        (pkg / "tests" / "test_dashboard_alone.py").write_text(
            "import dashboard\n\n\ndef test_the_package_imports_alone():\n    assert dashboard.__file__\n",
            encoding="utf-8")
    (root / "web" / "tests").mkdir(parents=True)
    (root / "web" / "tests" / "speech-lang.test.js").write_text("process.exit(0)\n", encoding="utf-8")
    # A stand-in for tools/venv_doctor.py: -Doctor's contract with it is the argv it passes and
    # the exit code it hands back, and the real doctor's verdicts are tested in test_venv_doctor.
    (root / "tools" / "venv_doctor.py").write_text(STUB_DOCTOR, encoding="utf-8")
    venv = root / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    bridge = [p for p in site.getsitepackages() if p.endswith("site-packages")][-1]
    (venv / "Lib" / "site-packages" / "_bridge.pth").write_text(
        f"import site; site.addsitedir({bridge!r})\n", encoding="utf-8")
    return root


STUB_DOCTOR = ("import sys\nprint('DOCTOR ARGS', ' '.join(sys.argv[1:]))\n"
               "print('DOCTOR PY', sys.executable)\n"
               "sys.exit(0 if '--rail' in sys.argv else 3)\n")


def thin_venv(path: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True)
    bridge = [p for p in site.getsitepackages() if p.endswith("site-packages")][-1]
    (path / "Lib" / "site-packages" / "_bridge.pth").write_text(
        f"import site; site.addsitedir({bridge!r})\n", encoding="utf-8")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


SCENARIOS = [("full", ""), ("live-only", "-Rail beta"), ("no-node", "-Rail web-core-speech"),
             ("all-pass", "-Rail alpha"), ("doctor", "-Doctor"), ("doctor-rail", "-Doctor -Rail alpha")]

# (name, args, before the run, after it). The live opt-ins: a value left in the caller's shell must
# not reach a plain run, -IncludeLive must set both, and either way the caller gets its own back.
_SEEN = "Write-Output ('SEEN=' + (Get-Content -Raw (Join-Path $root 'live_seen.txt')))"
_CALLER = "Write-Output ('CALLER=' + [string]$env:CLI_LIVE + '|' + [string]$env:JOB_AID_LIVE_TESTS)"
LIVE_SCENARIOS = [
    ("live-leak", "-Rail delta", "$env:CLI_LIVE = '1'; $env:JOB_AID_LIVE_TESTS = '1'",
     f"{_SEEN}; {_CALLER}"),
    ("live-on", "-Rail delta -IncludeLive",
     "Remove-Item Env:CLI_LIVE, Env:JOB_AID_LIVE_TESTS -ErrorAction SilentlyContinue",
     f"{_SEEN}; {_CALLER}"),
    ("shared", "-Rail epsilon", "", ""),
    ("pythonpath", "-Rail alpha", "$env:PYTHONPATH = 'caller-own-path'",
     "Write-Output ('PP=' + [string]$env:PYTHONPATH + '|'); Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue"),
] + ([("edu", "-Rail edu-suite", "", "")] if HAS_EDU else [])


@pytest.fixture(scope="module")
def runs(run_ps, shell, tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Windows venv layout")
    root = make_repo(tmp_path_factory.mktemp("rt"))
    system = os.environ.get("SystemRoot", r"C:\Windows")
    body = f"""
$ErrorActionPreference = 'Continue'
# No node on PATH, so the node target has to report that it never ran.
$env:Path = {q(os.path.join(system, 'System32'))} + ';' + {q(system)}
"""
    body += f"$root = {q(root)}\n"
    for name, args, before, after in [(n, a, "", "") for n, a in SCENARIOS] + LIVE_SCENARIOS:
        body += f"""
Write-Output '<<<{name}>>>'
{before}
& {q(root / 'run-tests.ps1')} {args} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
{after}
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = (int(text.rsplit("EXIT=", 1)[1].split()[0]), text)
    return out


def test_every_target_passing_exits_0(runs):
    """Positive control: exit 2 below would mean nothing if a clean target also produced it."""
    code, out = runs["all-pass"]
    assert code == 0, out
    assert "0 failed; 0 did not run" in out


def test_a_target_whose_tests_are_all_deselected_exits_2(runs):
    code, out = runs["live-only"]
    assert code == 2, out
    assert "NO TESTS" in out and "1 did not run" in out
    assert "INCOMPLETE: 1 target(s) checked nothing (beta)" in out
    assert "1 deselected" in out   # pytest's own last words, printed under the table


def test_a_node_target_without_node_exits_2(runs):
    code, out = runs["no-node"]
    assert code == 2, out
    assert "NO NODE" in out and "INCOMPLETE: 1 target(s) checked nothing (web-core-speech)" in out


def test_a_failure_still_exits_1_and_the_unrun_targets_are_still_counted(runs):
    code, out = runs["full"]
    assert code == 1, out
    assert "1 failed; 2 did not run" in out, out
    assert "gamma is broken on purpose" in out


def test_doctor_hands_back_the_doctors_exit_code(runs):
    """-Doctor used to `return` (exit 0) whatever it found; a doctor that cannot fail is advice."""
    code, out = runs["doctor"]
    assert code == 3, out
    assert "DOCTOR ARGS --repo " in out and "--rail" not in out


def test_doctor_passes_the_rail_filter_through(runs):
    code, out = runs["doctor-rail"]
    assert code == 0, out
    assert "--rail alpha" in out


def test_a_live_opt_in_left_in_the_callers_shell_does_not_reach_a_plain_run(runs):
    """The live tests check CLI_LIVE / JOB_AID_LIVE_TESTS themselves. A value someone set by hand
    earlier must not quietly turn a plain run into one that spends quota."""
    code, out = runs["live-leak"]
    assert code == 0, out
    assert "SEEN=CLI_LIVE=<unset> JOB_AID_LIVE_TESTS=<unset>" in out, out
    assert "CALLER=1|1" in out, "the caller's own values must come back after the run"


def test_a_rail_imports_the_shared_libraries_from_this_tree(runs):
    code, out = runs["shared"]
    assert code == 0, out
    assert "1 passed" in out, out


def test_the_callers_pythonpath_comes_back_after_the_run(runs):
    code, out = runs["pythonpath"]
    assert code == 0, out
    assert "PP=caller-own-path|" in out, out


@pytest.mark.skipif(not HAS_EDU, reason="edu-suite is withheld from this tree")
def test_edu_suites_package_imports_from_its_src_root(runs):
    """edu-suite's PYTHONPATH named apps/dashboard, where nothing is importable; four of its test
    files passed only because an earlier file pushed src onto sys.path for them."""
    code, out = runs["edu"]
    assert code == 0, out
    assert "1 passed" in out, out


@pytest.fixture(scope="module")
def test_venv_run(run_ps, shell, tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Windows venv layout")
    root = make_repo(tmp_path_factory.mktemp("tv"))
    thin_venv(root / ".venv-test")
    r = run_ps(shell, f"& {q(root / 'run-tests.ps1')} -Doctor -Rail alpha *>&1 | ForEach-Object {{ [string]$_ }}\n"
                      "Write-Output \"EXIT=$LASTEXITCODE\"\n", timeout=120)
    return root, r.stdout


def test_a_test_venv_is_preferred_over_the_broker_services_venv(test_venv_run):
    """.venv is the interpreter the platform-broker service runs from on a deployed box; tests that
    pip-install into it change the service's packages. With .venv-test present, run-tests uses it."""
    root, out = test_venv_run
    assert "EXIT=0" in out, out
    assert f"DOCTOR PY {root / '.venv-test'}" in out, out


def test_without_a_test_venv_the_repo_venv_is_used_as_before(runs):
    code, out = runs["doctor-rail"]
    assert "DOCTOR PY " in out and "\\.venv\\Scripts" in out and ".venv-test" not in out, out


# --- the frontend type-check targets ------------------------------------------------------------

# A stand-in for a frontend's own node_modules\typescript\bin\tsc: it records its argv, and when
# BROKEN exists beside the frontend it reports one error the way `tsc --pretty false` does.
FAKE_TSC = (
    "const fs = require('fs'), path = require('path');\n"
    "const fe = path.resolve(__dirname, '..', '..', '..');\n"
    "fs.appendFileSync(path.join(fe, 'tsc-argv.txt'), process.argv.slice(2).join(' ') + '\\n');\n"
    "if (fs.existsSync(path.join(fe, 'BROKEN'))) {\n"
    "  console.log('rails/alpha/frontend/src/main.ts(3,7): error TS2322: Type \\'string\\' is not assignable to type \\'number\\'.');\n"
    "  process.exit(2);\n"
    "}\n")


@pytest.fixture(scope="module")
def type_runs(run_ps, shell, tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Windows venv layout")
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node on PATH for the type-check targets")
    root = make_repo(tmp_path_factory.mktemp("types"))
    alpha = root / "rails" / "alpha" / "frontend"
    (alpha / "node_modules" / "typescript" / "bin").mkdir(parents=True)
    (alpha / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    (alpha / "node_modules" / "typescript" / "bin" / "tsc").write_text(FAKE_TSC, encoding="utf-8")
    epsilon = root / "rails" / "epsilon" / "frontend"              # a frontend never npm-installed
    epsilon.mkdir(parents=True)
    (epsilon / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    system = os.environ.get("SystemRoot", r"C:\Windows")
    body = f"$env:Path = {q(os.path.dirname(node))} + ';' + {q(os.path.join(system, 'System32'))}\n"
    for name, args, before in (("list", "-List", ""), ("types-pass", "-Rail alpha", ""),
                               ("types-fail", "-Rail alpha", f"New-Item -ItemType File {q(alpha / 'BROKEN')} | Out-Null"),
                               ("types-none", "-Rail epsilon", "")):
        body += f"""
Write-Output '<<<{name}>>>'
{before}
& {q(root / 'run-tests.ps1')} {args} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        code = (text.rsplit("EXIT=", 1)[1].split() or ["0"])[0]     # -List sets no exit code
        out[name] = (int(code), text)
    out["argv"] = (alpha / "tsc-argv.txt").read_text(encoding="utf-8").splitlines()
    out["logs"] = root / ".test-logs"
    return out


def test_every_frontend_with_a_tsconfig_is_a_types_target(type_runs):
    _, out = type_runs["list"]
    assert "alpha-types" in out and "rails\\alpha\\frontend" in out, out
    assert "epsilon-types" in out and "gamma-types" not in out, out  # gamma has no frontend


def test_a_clean_frontend_passes_and_tsc_is_run_no_emit_against_its_tsconfig(type_runs):
    code, out = type_runs["types-pass"]
    assert code == 0, out
    assert type_runs["argv"][0] == f"--noEmit -p {'rails' + chr(92) + 'alpha' + chr(92) + 'frontend' + chr(92)}tsconfig.json --pretty false", type_runs["argv"]


def test_a_type_error_fails_the_target_and_names_the_file(type_runs):
    code, out = type_runs["types-fail"]
    assert code == 1, out
    assert "alpha-types" in out and "FAIL (2)" in out, out
    assert "rails/alpha/frontend/src/main.ts(3,7): error TS2322" in out, out


def test_a_frontend_without_node_modules_is_a_named_skip_not_a_pass(type_runs):
    code, out = type_runs["types-none"]
    assert code == 2, out
    assert "NO MODULES" in out and "INCOMPLETE: 1 target(s) checked nothing (epsilon-types)" in out, out
    assert "rails\\epsilon\\frontend" in out and "npm ci" in out, out
    assert "has no node_modules, so it was not type-checked" in out, out


def test_include_live_sets_both_opt_ins_and_then_restores_the_caller(runs):
    code, out = runs["live-on"]
    assert code == 0, out
    assert "SEEN=CLI_LIVE=1 JOB_AID_LIVE_TESTS=1" in out, out
    assert "CALLER=|" in out, "a caller that had neither must not be left with them set"


# --- the plan's order is the same under either engine -------------------------------------------

# Rail names whose culture order differs from their ordinal order, one pair per engine: Windows
# PowerShell 5.1 (NLS) passes over a hyphen, so it put co-worker after course-builder; pwsh (ICU)
# puts an underscore before a digit, so x_a before x0a. Ordinal is the order Python's sorted()
# gives the public CI driver.
ORDER_RAILS = ("co-worker", "course-builder", "x_a", "x0a")


@pytest.fixture(scope="module")
def order_run(run_ps, shell, tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Windows venv layout")
    root = make_repo(tmp_path_factory.mktemp("order"))
    for rail in ORDER_RAILS:
        (root / "rails" / rail / "tests").mkdir(parents=True)
        (root / "rails" / rail / "tests" / f"test_{rail.replace('-', '_')}_order.py").write_text(
            PASSING, encoding="utf-8")
        (root / "rails" / rail / "frontend").mkdir()
        (root / "rails" / rail / "frontend" / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    r = run_ps(shell, f"& {q(root / 'run-tests.ps1')} -List *>&1 | ForEach-Object {{ [string]$_ }}\n",
               timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return root, [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def test_the_plan_lists_the_rails_in_ordinal_order_under_either_engine(order_run):
    """-List printed co-worker after course-builder under 5.1 and before it under pwsh, because
    both loops over the rails sorted by the engine's culture. Both are ordinal now."""
    root, names = order_run
    rails = {p.name for p in (root / "rails").iterdir() if p.is_dir()}
    assert set(ORDER_RAILS) <= set(names), names                      # the parse itself worked
    pytest_rows = [n for n in names if n in rails]
    assert pytest_rows == sorted(rails), pytest_rows
    types_rows = [n[:-len("-types")] for n in names
                  if n.endswith("-types") and n[:-len("-types")] in rails]
    assert types_rows == sorted(ORDER_RAILS), types_rows
