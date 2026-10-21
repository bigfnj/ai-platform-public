"""Mutation harness for chip-resolver coverage in the template tooling. NOT a pytest file.

    python tools\\tests\\mutate_chip_coverage.py

A rail that renders model chips but carries no modelstate.py used to be EXEMPT: the file's
existence decided whether the rail had a resolver at all, so `check` counted nothing, `sync`
wrote nothing, and RC022 and RC023 skipped it as "does no model work". One rail kept a
hand-rolled resolver there whose every chip said `cold`. Now the manifest decides
(Rail.renders_chips), and each step that acts on it is removed here in turn; the named test
must fail.

A verdict needs the expected test in pytest's FAILED lines, not merely anywhere in the
output, and the suite must import the tools from THIS tree (the witness below).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
TIMEOUT = 300
SUITE = ["tools/tests/test_rail_template.py", "tools/tests/test_rail_conformance.py"]

TPL = "tools/rail_template.py"
# RC023's rule file, which tools/rail_conformance.py runs (its "where the rules live").
RC = "tools/rail_conformance_rules/rc021_rc024.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a chip rail with no modelstate.py is exempt again (the old hole)",
        TPL,
        "        return p if p.is_file() or self.renders_chips() else None",
        "        return p if p.is_file() else None",
        "test_a_chip_rail_with_no_copy_still_has_its_path",
    ),
    (
        "check stops reporting a missing copy",
        TPL,
        "            if not dest.is_file():\n                drifted += 1\n"
        '                print(f"  MISSING',
        "            if False:\n                drifted += 1\n"
        '                print(f"  MISSING',
        "test_check_names_the_missing_copy_and_fails",
    ),
    (
        "sync only rewrites copies that already exist",
        TPL,
        "            if want != have:\n                dest.write_text(want",
        "            if want != have and have is not None:\n                dest.write_text(want",
        "test_sync_creates_the_copy_and_check_then_passes",
    ),
    (
        "sync invents the package directory a wrong manifest points at",
        TPL,
        "            if not dest.parent.is_dir():",
        "            dest.parent.mkdir(parents=True, exist_ok=True)\n"
        "            if not dest.parent.is_dir():",
        "test_sync_does_not_invent_a_package_directory",
    ),
    (
        "RC023 skips a missing copy instead of reporting it",
        RC,
        "        if not dest.is_file():\n"
        '            out.append(F("RC023", m, f"{name} is missing',
        "        if not dest.is_file():\n            continue\n"
        '            out.append(F("RC023", m, f"{name} is missing',
        "test_rc023_reports_the_missing_resolver",
    ),
    (
        "a chip rail renders chips even when every slot is an image backend",
        TPL,
        '        return any(isinstance(s, dict) and s.get("slot") and s.get("kind") != "image"',
        '        return any(isinstance(s, dict) and s.get("slot")',
        "test_an_image_only_rail_renders_no_chips",
    ),
]


def _env() -> dict:
    # The tools suite loads tools/*.py by path (tools/tests/conftest.py), so it needs no
    # PYTHONPATH; one inherited from the caller could only point somewhere else.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def witness() -> str:
    """Where the suite's loader finds rail_template.py. Must be this tree's."""
    code = ("import sys; sys.path.insert(0, 'tools/tests'); import conftest; "
            "print(conftest.load_tool('rail_template').__file__)")
    p = mr.run_tree([PY, "-c", code], cwd=REPO, capture_output=True, text=True,
                    env=_env(), timeout=60)
    return p.stdout.strip()


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-m", "pytest", *SUITE, "-q", "--no-header", "-rf",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(),
                        timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
            else (exc.stdout or "")
        return 124, out + f"\nHARNESS TIMEOUT after {TIMEOUT}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    where = witness()
    if not where or Path(where).resolve() != (REPO / TPL).resolve():
        print(f"WITNESS FAILED: the suite loads rail_template.py from {where!r}")
        return 2
    print(f"witness: rail_template.py loaded from {where}")

    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        # Bytes for the restore, so a file goes back exactly as it was: write_text on Windows
        # turns every LF into CRLF. The patterns are LF, so match against normalised text.
        original_bytes = path.read_bytes()
        original = original_bytes.decode("utf-8").replace("\r\n", "\n")
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(original.replace(old, new).encode("utf-8"))
            code, out = run_suite()
            fired = code != 0
            verdict = "FIRED" if fired and mr.named_failed(out, expected) else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original_bytes)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
