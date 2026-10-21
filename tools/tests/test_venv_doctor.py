"""tools/venv_doctor.py: the repo venv checked against what every manifest DECLARES.

A rail declaring a package the venv lacks fails at pytest COLLECTION, which run-tests reports as
an ordinary failure. The doctor exists to name that as an environment problem first, so each test
here pins one way it could stop doing so. Installation and importability are injected: nothing
here depends on what this box's venv happens to hold.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLING = {"pytest": "9.1", "jsonschema": "4.0", "httpx2": "2.13.1", "pyyaml": "6.0"}


@pytest.fixture(scope="module")
def doctor(venv_doctor):
    return venv_doctor


def manifest(path: Path, name: str, deps: list[str], optional: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[project]", f'name = "{name}"', "dependencies = ["]
    lines += [f'  "{d}",' for d in deps] + ["]"]
    if optional:
        lines.append("[project.optional-dependencies]")
        lines += [f"{k} = [" + ", ".join(f'"{x}"' for x in v) + "]" for k, v in optional.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    manifest(tmp_path / "packages" / "core-lib" / "pyproject.toml", "platform-core", ["present>=1"])
    manifest(tmp_path / "rails" / "alpha" / "pyproject.toml", "alpha",
             ["present>=1", "absent-one>=0.1", "torch>=2", "platform-core",
              "winonly; sys_platform == 'no-such-platform'"],
             {"dev": ["dev-tool"], "gpu": ["gpu-thing"]})
    manifest(tmp_path / "rails" / "beta" / "backend" / "pyproject.toml", "beta", ["old-one>=2"])
    # Vendored code under node_modules is not ours to satisfy.
    manifest(tmp_path / "rails" / "alpha" / "frontend" / "node_modules" / "x" / "pyproject.toml",
             "vendored", ["never-me"])
    return tmp_path


def run(doctor, root, installed: dict, broken=(), rail=None, core_broken=()):
    """Everything the doctor would ask the venv is injected, the editable core packages too: on a
    runner without them installed (a bare Linux container) the positive control failed for a
    reason no test here is about."""
    lines: list[str] = []
    code = doctor.run(root, rail, version_of=lambda n: installed.get(doctor.norm(n)),
                      importer=lambda n: "ImportError: boom" if doctor.norm(n) in broken else None,
                      out=lines.append,
                      core=lambda m: (None, "ModuleNotFoundError: gone") if m in core_broken
                      else (f"/tree/{m}/__init__.py", None))
    return code, "\n".join(lines)


def pip_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if "-m pip install" in ln), "")


def test_every_declared_dependency_satisfied_exits_0(doctor, tree):
    """Positive control: the failures below mean nothing if a clean venv also fails."""
    installed = {**TOOLING, "present": "1.5", "absent-one": "1", "torch": "2.6", "dev-tool": "1",
                 "old-one": "2.0"}
    code, out = run(doctor, tree, installed)
    assert code == 0, out
    assert "every declared dependency is installed" in out and not pip_line(out)


def test_a_missing_declared_dependency_fails_and_gets_a_pip_line(doctor, tree):
    code, out = run(doctor, tree, {**TOOLING, "present": "1.5", "old-one": "2.0"})
    assert code == 1, out
    assert "MISSING  absent-one>=0.1" in out and "declared in rails/alpha/pyproject.toml" in out
    assert "MISSING  dev-tool" in out                          # the dev group IS a test dependency
    line = pip_line(out)
    assert '"absent-one>=0.1"' in line and '"dev-tool"' in line
    # Deliberately absent: reported with its reason, never offered for install.
    assert "absent   torch>=2" in out and "torch" not in line
    for never in ("gpu-thing", "winonly", "never-me"):        # non-test extra, false marker, vendored
        assert never not in out, never
    assert "platform-core" not in line                         # satisfied by the tree itself


def test_a_version_below_the_declared_floor_fails_without_a_forced_reinstall(doctor, tree):
    installed = {**TOOLING, "present": "1.5", "absent-one": "1", "torch": "2.6", "dev-tool": "1",
                 "old-one": "1.0"}
    code, out = run(doctor, tree, installed)
    assert code == 1, out
    assert "VERSION  old-one>=2  (installed 1.0" in out
    assert "old-one" not in pip_line(out)


def test_an_installed_package_that_will_not_import_fails(doctor, tree):
    installed = {**TOOLING, "present": "1.5", "absent-one": "1", "torch": "2.6", "dev-tool": "1",
                 "old-one": "2.0"}
    code, out = run(doctor, tree, installed, broken={"present"})
    assert code == 1, out
    assert "BROKEN   present>=1  (ImportError: boom" in out


def test_a_core_package_that_will_not_import_fails(doctor, tree):
    """A stale editable install (the checkout moved) is the failure -Doctor was first written for."""
    installed = {**TOOLING, "present": "1.5", "absent-one": "1", "torch": "2.6", "dev-tool": "1",
                 "old-one": "2.0"}
    code, out = run(doctor, tree, installed, core_broken={"platform_core"})
    assert code == 1, out
    assert "BROKEN   platform_core  (ModuleNotFoundError: gone" in out


def test_httpx2_is_required_test_tooling(doctor, tree):
    """Starlette's TestClient prefers httpx2 and warns on every suite without it; no manifest
    declares it because runtime code uses httpx, so the doctor names it itself."""
    installed = {"pytest": "9", "jsonschema": "4", "pyyaml": "6", "present": "1.5", "absent-one": "1",
                 "torch": "2.6", "dev-tool": "1", "old-one": "2.0"}
    code, out = run(doctor, tree, installed)
    assert code == 1, out
    assert "MISSING  httpx2" in out and '"httpx2"' in pip_line(out)


def test_rail_narrows_the_report_to_that_rail(doctor, tree):
    code, out = run(doctor, tree, {**TOOLING}, rail="beta")
    assert "beta:" in out and "alpha:" not in out and "absent-one" not in out
    assert code == 1 and "MISSING  old-one>=2" in out


# --- --requirements: the install-side twin CI uses --------------------------------------------

def _names(doctor, lines) -> set[str]:
    return {doctor.norm(doctor.parse(line)[0]) for line in lines}


def _installed_by_pip(doctor, lines) -> dict[str, str]:
    """What `pip install -r` of these lines leaves behind: each line whose marker applies here,
    at a version above any floor."""
    return {doctor.norm(doctor.parse(line)[0]): "999" for line in lines if doctor.parse(line)[2]}


def test_requirements_is_exactly_what_the_doctor_checks(doctor, tree):
    lines = doctor.requirements(tree)
    assert _names(doctor, lines) == {*TOOLING, "present", "absent-one", "dev-tool", "old-one",
                                     "winonly"}
    # Never offered for install: the GPU stack, a project the tree itself provides, a non-test
    # optional group, a manifest under node_modules.
    for never in ("torch", "platform-core", "gpu-thing", "never-me"):
        assert never not in _names(doctor, lines), never
    # The marker travels with the line, so pip decides on the machine that installs.
    assert "winonly; sys_platform == 'no-such-platform'" in lines


def test_installing_the_requirements_satisfies_the_doctor_and_every_line_is_needed(doctor, tree):
    """The property CI relies on, both ways: install the list and the doctor passes; drop any
    one applicable line and it fails, so the list carries nothing the check does not need."""
    lines = doctor.requirements(tree)
    full = _installed_by_pip(doctor, lines)
    code, out = run(doctor, tree, full)
    assert code == 0, out
    for line in lines:
        if not doctor.parse(line)[2]:
            continue
        name = doctor.norm(doctor.parse(line)[0])
        code, _ = run(doctor, tree, {k: v for k, v in full.items() if k != name})
        assert code == 1, f"{line!r} is listed but the doctor passes without it"


def test_requirements_narrow_to_one_rail(doctor, tree):
    assert _names(doctor, doctor.requirements(tree, "beta")) == {*TOOLING, "old-one"}


def test_the_real_requirements_carry_the_tooling_and_no_gpu_stack(doctor):
    names = _names(doctor, doctor.requirements(REPO))
    assert set(doctor.TEST_TOOLING) <= names
    assert not names & set(doctor.ABSENT_BY_DESIGN)


def test_discovery_covers_core_and_every_rail_with_a_manifest(doctor):
    """Against the real tree: a rail added with a pyproject.toml is checked with no edit here."""
    groups = doctor.discover(REPO)
    expected = {d.name for d in (REPO / "rails").iterdir()
                if d.is_dir() and any(p for p in d.rglob("pyproject.toml")
                                      if not set(p.relative_to(d).parts) & doctor.SKIP_DIRS)}
    assert set(groups) - {"core"} == expected
    core = {p.relative_to(REPO).as_posix() for p in groups["core"]}
    assert {"services/broker/pyproject.toml", "apps/platform/backend/pyproject.toml",
            "packages/platform_core/pyproject.toml"} <= core


# --- --rail must name something -----------------------------------------------------------

def test_an_unknown_rail_is_an_error_not_a_clean_bill(doctor, tree, capsys):
    """It used to narrow to nothing, check only the test tooling, and exit 0: a typo read as a
    healthy venv."""
    assert doctor.main(["--repo", str(tree), "--rail", "no-such-rail"]) == 2
    assert "no target 'no-such-rail'" in capsys.readouterr().out


def test_requirements_refuses_an_unknown_rail_too(doctor, tree, capsys):
    assert doctor.main(["--repo", str(tree), "--rail", "no-such-rail", "--requirements"]) == 2
    assert "no target" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["core", "alpha", "beta", "alpha-store"])
def test_core_a_rail_and_a_rail_sub_target_are_accepted(doctor, tree, name):
    """`alpha-store` is the run-tests sub-target shape (edu-suite-store)."""
    groups, _ = doctor.scope(tree, name)
    assert groups


def test_a_rail_without_a_manifest_says_it_declares_nothing(doctor, tree):
    (tree / "rails" / "gamma" / "backend").mkdir(parents=True)
    installed = {**TOOLING, "present": "1.5"}
    code, out = run(doctor, tree, installed, rail="gamma")
    assert code == 0, out
    assert "gamma declares no Python dependencies (no pyproject.toml)" in out


def test_a_run_tests_node_target_is_accepted(doctor, tree, capsys):
    """run-tests.ps1 forwards -Rail here under -Doctor, and its node targets are named literally
    there, not by a rails/ directory: refusing them would break `-Doctor -Rail web-core-speech`."""
    (tree / "run-tests.ps1").write_text(
        "$targets += @{ Name = 'web-core-speech'; Paths = @($webTest); PyPath = @(); Node = $true }\n",
        encoding="utf-8")
    groups, _ = doctor.scope(tree, "web-core-speech")
    assert groups == {}
    assert doctor.main(["--repo", str(tree), "--rail", "web-node-typo"]) == 2
