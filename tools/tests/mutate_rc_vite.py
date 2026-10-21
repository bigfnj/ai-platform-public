"""Mutation harness for RC009/RC010 following a relocated frontend. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_rc_vite.py

Both rules read `<root>/frontend/vite.config.ts` and returned a bare [] when it was not there.
edu-suite keeps its frontend at apps/dashboard/frontend/, so on that rail both rules read
nothing, reported nothing, and were counted as having run. The fix routes them through
`Manifest.vite_config()` (derived from `frontend_src()`) and makes a missing config a WARN.
`unmanifested_rails()` had the same hardcoded `<dir>/frontend` key and now also accepts a vite
config anywhere under the rail (pruned walk), so the last two mutations live here too.

Every mutation below puts one piece of the old behaviour back. Each must turn a NAMED test red:
a mutation that fails some other test proves only that the file was touched.
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
# The interpreter running THIS harness, and this tree's own source roots: a worktree has no
# .venv, and the venv's editable installs point at one checkout, so a run from a worktree
# would otherwise import that checkout's packages instead of this tree's.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
SUITE = ["tools/tests/test_rail_conformance.py"]
RC = "tools/rail_conformance.py"
# RC009's and RC010's rule file, which the checker runs (its "where the rules live").
RC_009_011 = "tools/rail_conformance_rules/rc009_rc011.py"
T = "TestViteConfigFollowsTheFrontend::"
# A hung pytest must not hang the harness. The file runs in about a second.
TIMEOUT_S = 300

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "vite_config() goes back to the hardcoded default layout",
        RC,
        '        return self.frontend_src().parent / "vite.config.ts"',
        '        return self.root / "frontend" / "vite.config.ts"',
        T + "test_rc009_reads_a_relocated_dev_port",
    ),
    (
        "RC009 stops asking the manifest and reads <root>/frontend/ again",
        RC_009_011,
        "    vc = m.vite_config()\n    src = read(vc)\n    if not src:\n"
        '        return [F("RC009"',
        '    vc = m.root / "frontend" / "vite.config.ts"\n    src = read(vc)\n    if not src:\n'
        '        return [F("RC009"',
        T + "test_rc009_reads_a_relocated_dev_port",
    ),
    (
        "RC010 stops asking the manifest and reads <root>/frontend/ again",
        RC_009_011,
        "    vc = m.vite_config()   # follows a relocated frontend; see RC009 for why a miss WARNs",
        '    vc = m.root / "frontend" / "vite.config.ts"',
        T + "test_rc010_reads_a_relocated_federation_name",
    ),
    (
        "RC009 returns a bare [] again when it finds no config",
        RC_009_011,
        '        return [F("RC009", m, f"no vite config at {rel(vc)}, so the dev port and API proxy "\n'
        '                              f"were NOT checked", rel(vc), level="warn")]',
        "        return []",
        T + "test_a_missing_config_WARNS_rather_than_passing",
    ),
    (
        "RC010 says nothing when it finds no config",
        RC_009_011,
        '        out.append(F("RC010", m, f"no vite config at {rel(vc)}, so the federation name and "\n'
        '                                 f"base path were NOT checked", rel(vc), level="warn"))',
        "        pass",
        T + "test_a_missing_config_WARNS_rather_than_passing",
    ),
    (
        "RC010 returns early on a missing config, silencing its shell-side checks too",
        RC_009_011,
        '        out.append(F("RC010", m, f"no vite config at {rel(vc)}, so the federation name and "\n'
        '                                 f"base path were NOT checked", rel(vc), level="warn"))',
        '        return [F("RC010", m, f"no vite config at {rel(vc)}, so the federation name and "\n'
        '                                 f"base path were NOT checked", rel(vc), level="warn")]',
        T + "test_rc010_shell_half_still_runs_without_a_rail_config",
    ),
    # --- unmanifested_rails(): the same hardcoded layout, one level up ----------------
    (
        "unmanifested_rails keys on <dir>/frontend again, so a relocated one is invisible",
        RC,
        '        if not d.is_dir() or (d / "rail.json").is_file():\n'
        "            continue\n"
        '        if not (d / "frontend").is_dir() and not _walk(d, "vite*.config.*"):\n'
        "            continue",
        '        if not d.is_dir() or (d / "rail.json").is_file() or not (d / "frontend").is_dir():\n'
        "            continue",
        "test_unmanifested_rails_finds_a_relocated_frontend",
    ),
    (
        "unmanifested_rails walks into node_modules and finds a dependency's vite config",
        RC,
        '        if not (d / "frontend").is_dir() and not _walk(d, "vite*.config.*"):',
        '        if not (d / "frontend").is_dir() and not list(d.rglob("vite*.config.*")):',
        "test_unmanifested_rails_ignores_a_vite_config_inside_node_modules",
    ),
]


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_TREE_ENV,
                        timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"pytest did not finish within {TIMEOUT_S}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        # Bytes, restored byte for byte: the tree is CRLF on this box and the patterns are LF.
        raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n")
        n = text.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(text.replace(old, new).encode("utf-8"))
            code, out = run_suite()
            fired = code != 0
            named = mr.named_failed(out, expected)
            verdict = "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
