"""Mutation harness for openmaic-app's network. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_openmaic_app_network.py

Run this, not just pytest, before trusting a change to any of:
  * the `networks:` keys of openmaic-app and the openmaic rail in BOTH compose files;
  * the top-level `openmaic_app_net` declaration in either;
  * the two service-name URLs the pair reach each other by (OPENMAIC_APP_URL, the shim default
    inside openmaic-app's OLLAMA_BASE_URL).

openmaic-app is third-party code, and every rail but terminal-fun and openmaic believes an
identity header from anything on the network it shares with them. Each guard here fails QUIETLY
when it is lost: the app keeps working, the rail keeps working, and the app simply becomes able to
reach every rail again. Each is broken on purpose below to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held. Each control is
a rewrite that means the same to compose, and must SURVIVE. The override test renders with the
compose CLI, so the baseline refuses to run where it would be skipped.
"""
from __future__ import annotations

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
SUITE = [str(REPO / "tools" / "tests" / "test_openmaic_app_network.py"),
         str(REPO / "tools" / "tests" / "test_terminal_fun_network.py")]
RUN_TIMEOUT = 300

FULL = "deploy/docker-compose.yml"
LEAN = "deploy/installer/docker-compose.installer.yml"

OWN = "test_openmaic_app_is_on_its_own_network_only"
RAIL_NETS = "test_the_rail_joins_default_and_the_app_network"
ONLY = "test_nothing_but_the_rail_and_the_app_joins_the_app_network"
BRIDGE = "test_the_app_network_is_an_ordinary_bridge"
AGREE = "test_both_compose_files_agree"
REACH_APP = "test_the_rail_reaches_the_app_by_its_service_name"
REACH_SHIM = "test_the_app_reaches_the_shim_by_the_rails_service_name"
OVERRIDE = "test_a_box_override_that_only_builds_the_app_keeps_the_layout"
LAYOUT = "test_nothing_else_joins_the_isolated_network_or_changes_networks"

FULL_APP_NETS = ("    networks:\n      - openmaic_app_net\n"
                 "    volumes:\n      - openmaic_app_data:/app/data   # generated")
LEAN_APP_NETS = ("    networks:\n      - openmaic_app_net\n"
                 "    volumes:\n      # KEY MUST STAY")
FULL_RAIL_NETS = '      - default\n      - openmaic_app_net\n    expose:\n      - "8900"\n'
NET_KEY = "\n  openmaic_app_net:\n"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    # --- openmaic-app's own key ----------------------------------------------------------------
    (
        "full stack: openmaic-app is back on the default network as well (the defect)",
        [(FULL, FULL_APP_NETS, FULL_APP_NETS.replace(
            "      - openmaic_app_net\n", "      - openmaic_app_net\n      - default\n"))],
        [OWN, AGREE, LAYOUT],
    ),
    (
        "full stack: openmaic-app loses its networks key, so compose puts it on default",
        [(FULL, FULL_APP_NETS, FULL_APP_NETS.replace("    networks:\n      - openmaic_app_net\n",
                                                     ""))],
        [OWN, AGREE, LAYOUT],
    ),
    (
        "full stack: openmaic-app borrows the rail's network namespace",
        [(FULL, FULL_APP_NETS, '    network_mode: "service:openmaic"\n' + FULL_APP_NETS)],
        [OWN],
    ),
    (
        "lean install: openmaic-app is back on the default network as well",
        [(LEAN, LEAN_APP_NETS, LEAN_APP_NETS.replace(
            "      - openmaic_app_net\n", "      - openmaic_app_net\n      - default\n"))],
        [OWN, AGREE],
    ),
    # --- who else is on the app network --------------------------------------------------------
    (
        # A published rail on purpose: a withheld one's block is not in the public snapshot, and
        # the case would read PATTERN there.
        "full stack: a second rail joins the app network",
        [(FULL, '    expose:\n      - "8830"\n',
                '    networks:\n      - default\n      - openmaic_app_net\n'
                '    expose:\n      - "8830"\n')],
        [ONLY, AGREE, LAYOUT],
    ),
    (
        "lean install: a second rail joins the app network",
        [(LEAN, '    expose:\n      - "8740"\n',
                '    networks:\n      - default\n      - openmaic_app_net\n'
                '    expose:\n      - "8740"\n')],
        [ONLY, AGREE],
    ),
    # --- the rail's two networks ---------------------------------------------------------------
    (
        "full stack: the rail drops default (the gateway loses it)",
        [(FULL, FULL_RAIL_NETS, FULL_RAIL_NETS.replace("      - default\n", ""))],
        [RAIL_NETS, AGREE, LAYOUT],
    ),
    (
        "full stack: the rail is not on the app network (the proxy and the shim go dark)",
        [(FULL, FULL_RAIL_NETS, FULL_RAIL_NETS.replace("      - openmaic_app_net\n", ""))],
        [RAIL_NETS, REACH_APP, ONLY, AGREE],
    ),
    # --- the network itself --------------------------------------------------------------------
    (
        "full stack: the app network is made internal (an external endpoint is cut off)",
        [(FULL, NET_KEY, NET_KEY + "    internal: true\n")],
        [BRIDGE, AGREE],
    ),
    (
        "full stack: the app network is joined but never declared (compose refuses the file)",
        [(FULL, NET_KEY, "\n")],
        [BRIDGE, OVERRIDE],
    ),
    (
        "the two files disagree: lean's app network turns inter-container traffic off",
        [(LEAN, NET_KEY, NET_KEY + "    driver_opts:\n"
                                   '      com.docker.network.bridge.enable_icc: "false"\n')],
        [AGREE],
    ),
    # --- the names the two reach each other by -------------------------------------------------
    (
        "full stack: the app's shim default names the gateway, which the app network lacks",
        [(FULL, "${OPENMAIC_LLM_BASE_URL:-http://openmaic:8900/api/llm/v1}",
                "${OPENMAIC_LLM_BASE_URL:-http://gateway:8700/openmaic/api/llm/v1}")],
        [REACH_SHIM],
    ),
    (
        "full stack: the rail proxies to a name that is not the app's service",
        [(FULL, "      OPENMAIC_APP_URL: http://openmaic-app:3000\n",
                "      OPENMAIC_APP_URL: http://localhost:3000\n")],
        [REACH_APP],
    ),
]

#: Rewrites that mean the same to compose. Each must SURVIVE.
CONTROLS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "openmaic-app's networks in mapping form",
        [(FULL, FULL_APP_NETS, FULL_APP_NETS.replace("      - openmaic_app_net\n",
                                                     "      openmaic_app_net: {}\n"))],
    ),
    (
        "the rail's two networks listed the other way round",
        [(FULL, FULL_RAIL_NETS, '      - openmaic_app_net\n      - default\n'
                                '    expose:\n      - "8900"\n')],
    ),
]


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    """One pytest run over both files (one rootdir, one conftest). -1 means it timed out."""
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider", "-rfs", *extra],
                        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, partial + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def failed_names(out: str) -> set[str]:
    """Test names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def apply(label: str, edits: list[tuple[str, str, str]], original: dict[Path, bytes]) -> bool:
    """Write every edit (each `old` exactly once), recording each file's bytes first."""
    for rel, old, new in edits:
        path = REPO / rel
        original.setdefault(path, path.read_bytes())
        current = path.read_text(encoding="utf-8")
        n = current.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
            return False
        mr.write_like(path, current.replace(old, new), original[path])
    return True


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
    # Every named test must EXIST before its absence from a FAILED line can mean anything: a
    # renamed test would otherwise turn every mutation naming it into a silent SURVIVED.
    _, collected = run_suite(("--collect-only",))
    named = {t for _, _, tests in MUTATIONS for t in tests}
    missing = sorted(t for t in named if t not in collected)
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    summary = [ln for ln in out.splitlines() if " passed" in ln]
    if any("skipped" in ln for ln in summary):
        # The override test skips without a compose CLI, and a mutation naming it would then
        # read as SURVIVED for a test that never ran.
        print("BASELINE SKIPPED TESTS -- " + " | ".join(summary))
        return 2
    print("baseline green: " + " | ".join(summary) + "\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original: dict[Path, bytes] = {}
        try:
            if not apply(label, edits, original):
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missed = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missed else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missed:
                    print(f"      expected to fail but did not: {', '.join(missed)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original.items():
                mr.restore_exact(path, raw)

    controls: list[tuple[str, str]] = []
    for label, edits in CONTROLS:
        original = {}
        try:
            if not apply(label, edits, original):
                controls.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            verdict = "SURVIVED" if code == 0 else "TIMEOUT" if code == -1 else "BROKE"
            controls.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
