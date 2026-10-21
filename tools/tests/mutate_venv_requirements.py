"""Mutation harness for venv_doctor.py's --requirements (the list CI installs) and its --rail
scope. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_venv_requirements.py

Copies the doctor, its test, the tools conftest and every pyproject.toml in the tree (the
discovery tests read the real layout) into a temp directory, breaks the COPY of the doctor one
way at a time, and runs the copied test there. This tree is never written.

A wrong requirements list fails quietly in both directions: one line short and CI installs less
than the dev venv holds, so a test that needs it errors at collection on the runner only; one
line long and CI pulls in the GPU stack the doctor keeps out on purpose. Each mutation is one of
those, and each must turn the suite red and name its test.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
D = "tools/venv_doctor.py"
TEST = "tools/tests/test_venv_doctor.py"
# offline_guard.py because the conftest imports the network guard from beside itself.
FILES = (D, TEST, "tools/tests/conftest.py", "tools/tests/offline_guard.py")
SKIP_DIRS = {"node_modules", ".venv", "native", ".git", "dist", "build", "__pycache__"}
EXPECTED_PASSING = 20       # the witness: the copied suite really collected and ran (20 since
                            # the unknown-rail and accepted-target tests)
TIMEOUT_S = 180

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the GPU stack is no longer filtered out, so CI would install torch",
        D,
        "        if name in in_tree or name in ABSENT_BY_DESIGN:\n            continue",
        "        if name in in_tree:\n            continue",
        "test_requirements_is_exactly_what_the_doctor_checks",
    ),
    (
        "projects this tree provides are offered to pip (platform-core from PyPI)",
        D,
        "        if name in in_tree or name in ABSENT_BY_DESIGN:\n            continue",
        "        if name in ABSENT_BY_DESIGN:\n            continue",
        "test_requirements_is_exactly_what_the_doctor_checks",
    ),
    (
        "the test tooling no manifest declares is left out",
        D,
        "    reqs = list(TEST_TOOLING) + [r for ps",
        "    reqs = [] + [r for ps",
        "test_installing_the_requirements_satisfies_the_doctor_and_every_line_is_needed",
    ),
    (
        "only [project].dependencies are read, so test groups (pytest-asyncio) go missing",
        D,
        "[r for ps in groups.values() for p in ps for r in declared(p)]",
        "[r for ps in groups.values() for p in ps for r in "
        "((tomllib.loads(p.read_text(encoding='utf-8')).get('project') or {})"
        ".get('dependencies') or [])]",
        "test_installing_the_requirements_satisfies_the_doctor_and_every_line_is_needed",
    ),
    (
        "markers are stripped, so a platform-only dependency installs everywhere",
        D,
        "        keep.setdefault(req.strip(), None)",
        "        keep.setdefault(req.split(';')[0].strip(), None)",
        "test_requirements_is_exactly_what_the_doctor_checks",
    ),
    (
        "--rail is ignored, so a narrowed list installs every rail",
        D,
        "    groups, in_tree = scope(repo, rail)\n    reqs = ",
        "    groups, in_tree = scope(repo, None)\n    reqs = ",
        "test_requirements_narrow_to_one_rail",
    ),
    # --- a --rail that names nothing is an error, not a clean bill -------------------
    (
        "an unknown --rail narrows to nothing and exits 0 again",
        D,
        "        if not known:\n",
        "        if False:\n",
        "test_an_unknown_rail_is_an_error_not_a_clean_bill",
    ),
    (
        "a target run-tests.ps1 names literally (a node target) is refused",
        D,
        "rail in named or ",
        "False or ",
        "test_a_run_tests_node_target_is_accepted",
    ),
    (
        "a rail sub-target (alpha-store) is refused",
        D,
        '                 or any(rail.startswith(g + "-") for g in groups))',
        "                 or False)",
        "test_core_a_rail_and_a_rail_sub_target_are_accepted",
    ),
    (
        "a rail with no manifest is checked in silence, as if it had been",
        D,
        "    if rail and not groups:\n",
        "    if False:\n",
        "test_a_rail_without_a_manifest_says_it_declares_nothing",
    ),
]


def run_suite(root: Path) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=str(root))   # nothing imports a platform package
    try:
        p = mr.run_tree([PY, "-m", "pytest", TEST, "-v", "--no-header", "-p", "no:cacheprovider"],
                        cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT after {TIMEOUT_S}s\n{exc.stdout or ''}{exc.stderr or ''}"
    return p.returncode, p.stdout + p.stderr


def fresh_copy(dest: Path) -> None:
    for rel in FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest / rel)
    for p in REPO.rglob("pyproject.toml"):
        rel = p.relative_to(REPO)
        if set(rel.parts[:-1]) & SKIP_DIRS:
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest / rel)


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    real = mr.snapshot(REPO / rel for rel in {*FILES, *(m[1] for m in MUTATIONS)}
                       if (REPO / rel).is_file())
    with tempfile.TemporaryDirectory(prefix="mutate-venv-requirements-") as tmp:
        root = Path(tmp) / "tree"
        fresh_copy(root)
        code, out = run_suite(root)
        passed = out.count(" PASSED")
        if code != 0 or passed != EXPECTED_PASSING or "test_venv_doctor.py::" not in out:
            print(f"BASELINE IS NOT GREEN ({passed}/{EXPECTED_PASSING} passed, exit {code}) -- "
                  "fix that before trusting any mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green in {root}: {passed}/{EXPECTED_PASSING} passed\n")

        results: list[tuple[str, str]] = []
        for label, relpath, old, new, expected in MUTATIONS:
            fresh_copy(root)
            path = root / relpath
            original = path.read_text(encoding="utf-8")
            n = original.count(old)
            if n != 1:
                print(f"  ERROR             {label}: pattern found {n} times in {relpath}, expected 1")
                results.append((label, "PATTERN"))
                continue
            path.write_text(original.replace(old, new), encoding="utf-8", newline="")
            code, out = run_suite(root)
            fired = code != 0
            named = mr.named_failed(out, expected)
            verdict = "FIRED" if fired and named else ("FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))

    # Proven, not assumed: every real file this harness copies or mutates is byte-identical
    # to what it found (raises TREE IS DIRTY otherwise).
    mr.assert_unchanged(real)
    print(f"real tree: untouched ({len(real)} files byte-identical)")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired the right test "
          f"(the real tree was never written)")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
