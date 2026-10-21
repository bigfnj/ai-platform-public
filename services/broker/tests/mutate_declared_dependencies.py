"""Mutation harness for the declared-dependency check. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_declared_dependencies.py

The check guards against a failure that is invisible on the box that has the package already:
an import nobody declared. So each mutation recreates one, or blinds the scan that should see it,
and names the test that must notice.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
PY = sys.executable
SUITE = ["tests/test_declared_dependencies.py"]

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "platform-core is undeclared again, as it was for weeks",
        "pyproject.toml",
        '    "platform-core",\n',
        "",
        "test_every_load_time_import_is_a_declared_dependency",
    ),
    (
        "a new module-level import arrives without a declaration",
        "app/registry.py",
        "from typing import Literal\n",
        "from typing import Literal\n\nimport yaml  # noqa: F401\n",
        "test_every_load_time_import_is_a_declared_dependency",
    ),
    (
        "the scan reads `import x` but not `from x import y`, so platform_core is invisible",
        "tests/test_declared_dependencies.py",
        "            elif isinstance(node, ast.ImportFrom):\n"
        "                if node.level == 0 and node.module:",
        "            elif False:\n"
        "                if node.level == 0 and node.module:",
        "test_the_scan_sees_the_imports_it_exists_for",
    ),
    (
        "an import inside a module-level try block is not seen",
        "tests/test_declared_dependencies.py",
        "            elif isinstance(node, ast.Try):",
        "            elif False:",
        "test_a_guarded_module_level_import_is_still_seen",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BROKER)
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=env)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        # A baseline that ran out of time is its own verdict, and nothing below can mean
        # anything after it.
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = BROKER / relpath
        # Bytes, not text: read_text/write_text translate line endings on Windows, so a
        # "restore" could rewrite an LF file as CRLF. The pattern is matched in the file's own
        # line endings and only its span replaced (mutate_bytes); restore_exact proves the
        # original bytes are back.
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected)
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
