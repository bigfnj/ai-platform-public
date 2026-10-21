"""Mutation harness for tools/tests/test_doc_claims.py. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_doc_claims.py

Copies every file the test reads (the docs, the broker's config.py, install.ps1, each rail's
rail.json and its .ts/.tsx sources) into a temp directory with the same layout, makes ONE
change a real edit would make, and runs the copied test there. This tree is never written.

Each mutation is a drift that has actually happened in this repo, or its mirror: a setting added
without a README line, a rail added without a ports row, an installer option the install page
never mentions. Each must turn the suite red AND name the test that owns that pairing.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# The shared runner: bounded runs that kill the whole process tree on a timeout.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
TEST = "tools/tests/test_doc_claims.py"
# CLAUDE.md is private: the public snapshot drops it, its test SKIPS there with a reason, and
# the one mutation that needs it is reported as SKIP rather than counted.
HAS_CLAUDE_MD = (REPO / "CLAUDE.md").is_file()
# Mutations whose anchor exists only in the PRIVATE README: absent (the public README has no
# rebuild command) they are reported as SKIP with that reason, never as a pattern error.
PRIVATE_ONLY = {"the README's rebuild command loses its backslash-r in transit"}
# + 6: test_build_output_is_never_read_as_a_doc, one case per build-output dir name.
EXPECTED_PASSING = (11 if HAS_CLAUDE_MD else 10) + 6
FILES = ("CLAUDE.md", "README.md", "docs/RAIL_CONTRACT.md", "docs/INSTALL.md",
         "services/broker/README.md", "services/broker/app/config.py",
         "deploy/installer/install.ps1", TEST,
         # Scanned by the dead-URL test; copied so a mutation in them has something to hit.
         "deploy/README.md", "deploy/INSTRUCTIONS.md",
         "deploy/Caddyfile", "deploy/installer/Caddyfile")
# The same never-source set as test_doc_claims.py (installed packages, tool state, BUILD OUTPUT:
# dist, dist-*, build, coverage, .vite), so the scratch copy holds exactly what the walks read.
_NOT_SOURCE = ("node_modules", ".venv", ".git", "__pycache__")


def _skip_dir(name: str) -> bool:
    return name in _NOT_SOURCE or name in ("dist", "build", "coverage", ".vite") or name.startswith("dist-")

CFG = "services/broker/app/config.py"
BRD = "services/broker/README.md"
RD = "README.md"
DEAD = "https://" + "platform.localhost"   # built, so this file does not contain it either
# A bare full-stack compose `up`, built for the same reason.
BARE_UP = "docker " + "compose up"
INSTR = "deploy/INSTRUCTIONS.md"
DOCT = "tools/tests/test_doc_claims.py"
BS = chr(92)        # a backslash, built rather than typed so no tool on the way can eat it
RC = "docs/RAIL_CONTRACT.md"
INS = "docs/INSTALL.md"
PS1 = "deploy/installer/install.ps1"
CL = "CLAUDE.md"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("a first-deploy code block goes back to a bare full-stack up", INSTR,
     "." + BS + "rebuild-stale.ps1                                   # every built service, one at a time",
     BARE_UP + " -d --build",
     "test_no_operator_doc_runs_a_bare_full_stack_compose_up"),
    ("prose advises a bare up (the credentials note's old form)", "deploy/README.md",
     "`docker compose up -d --build --no-deps <service>` on a", "`" + BARE_UP + " --build` on a",
     "test_no_operator_doc_runs_a_bare_full_stack_compose_up"),
    ("the detector exempts a fenced command whose comment says not", DOCT,
     "            if not fenced and _FORBIDS.search(line):",
     "            if _FORBIDS.search(line):",
     "test_the_bare_up_detector_reads_each_form"),
    ("a deploy doc sends the operator to an HTTPS address nothing serves", "deploy/README.md",
     "Open **http://localhost:1111**", "Open **" + DEAD + "**",
     "test_no_file_tells_the_operator_to_use_https_platform_localhost"),
    ("a Caddyfile COMMENT does it (proves non-Markdown files are scanned)",
     "deploy/installer/Caddyfile",
     "serves the gateway at http://localhost:1111,", "serves the gateway at " + DEAD + ",",
     "test_no_file_tells_the_operator_to_use_https_platform_localhost"),
    # The defect itself: the backslash-r of the command eaten as a carriage return.
    ("the README's rebuild command loses its backslash-r in transit", RD,
     "." + BS + "rebuild-stale.ps1                     # every built service",
     ".\nebuild-stale.ps1                     # every built service",
     "test_every_script_the_readme_names_exists"),
    # Present in BOTH READMEs (the public snapshot ships docs/README.public.md as README.md).
    ("the README names a script that does not exist", RD,
     "smoke-test.ps1 -Stage all -FullStack", "smoke-tests.ps1 -Stage all -FullStack",
     "test_every_script_the_readme_names_exists"),
    ("a broker setting is added and the README never names it", CFG,
     '    cli_model: str = ""\n', '    cli_model: str = ""\n    cli_brand_new: str = ""\n',
     "test_the_broker_readme_names_every_setting_and_no_other"),
    ("the broker README names a setting that does not exist", BRD,
     "| `BROKER_OLLAMA_TIMEOUT` |", "| `BROKER_OLLAMA_WAIT_SECONDS` |",
     "test_the_broker_readme_names_every_setting_and_no_other"),
    ("a rail drops out of the README's list", RD,
     "**openmaic**", "**the classroom rail**",
     "test_the_readme_names_every_rail"),
    ("a ports row carries the wrong number", RC,
     "| openmaic | 8900 | 5350 |", "| openmaic | 8901 | 5350 |",
     "test_the_ports_table_matches_every_manifest"),
    ("a rail has no ports row at all", RC,
     "| terminal-fun | 8730 | 5290 |\n", "",
     "test_the_ports_table_matches_every_manifest"),
    ("a manifest stops declaring its status_route", "rails/ai-playground/rail.json",
     '"status_route": "/api/capabilities"', '"status_route": null',
     "test_the_status_route_sentence_matches_the_manifests"),
    ("the sentence names a rail that does declare one", RC,
     "except ai-voice, meeting-atlas and workstation declares",
     "except ai-voice, meeting-atlas, workstation and terminal-fun declares",
     "test_the_status_route_sentence_matches_the_manifests"),
    ("a rail gains a status_route but renders no chips", "rails/workstation/rail.json",
     '"status_route": null', '"status_route": "/api/capabilities"',
     "test_every_rail_with_a_status_route_renders_model_chips"),
    ("the installer offers a rail the install page never mentions", PS1,
     "$OptionalRails = @(\n", "$OptionalRails = @(\n  @{ Id = 'bouquet'; Text = 'Bouquet Builder' }\n",
     "test_install_md_names_exactly_the_optional_rails_the_installer_offers"),
    ("the install page drops a rail the installer offers", INS,
     " Gemini CX, Meeting Atlas, AI Playground", " Gemini CX, AI Playground",
     "test_install_md_names_exactly_the_optional_rails_the_installer_offers"),
    ("a rail loses its CLAUDE.md row", CL,
     "| **servicenow** 🎓 | `rails/servicenow` |", "| **servicenow** 🎓 | (withheld) |",
     "test_claude_md_has_a_row_for_every_rail"),
]


def fresh_copy(dest: Path) -> None:
    for rel in FILES:
        if not (REPO / rel).is_file():
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest / rel)
    for man in (REPO / "rails").glob("*/rail.json"):
        rail = man.parent
        for root, dirs, files in os.walk(rail):
            dirs[:] = [d for d in dirs if not _skip_dir(d)]
            for f in files:
                if f == "rail.json" or f.endswith((".ts", ".tsx")):
                    src = Path(root) / f
                    out = dest / src.relative_to(REPO)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out)
    # Every PowerShell script too: the README-commands test resolves each .ps1 the README names.
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        for f in files:
            if f.endswith(".ps1"):
                src = Path(root) / f
                out = dest / src.relative_to(REPO)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out)


def run_suite(root: Path) -> mr.SuiteRun:
    return mr.run_bounded([PY, "-m", "pytest", TEST, "-v", "--no-header", "-p", "no:cacheprovider"],
                          cwd=root, env=dict(os.environ, PYTHONPATH=str(root)), timeout=120)


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    with tempfile.TemporaryDirectory(prefix="mutate-doc-claims-") as tmp:
        root = Path(tmp) / "tree"
        fresh_copy(root)
        run = run_suite(root)
        passed = run.out.count(" PASSED")
        if run.code != 0 or passed != EXPECTED_PASSING or "test_doc_claims.py::" not in run.out:
            print(f"BASELINE IS NOT GREEN ({passed}/{EXPECTED_PASSING} passed, exit {run.code}) -- "
                  "fix that before trusting any mutation result.\n")
            print(run.out[-2500:])
            return 2
        print(f"baseline green in {root}: {passed}/{EXPECTED_PASSING} passed\n")

        results: list[tuple[str, str]] = []
        for label, rel, old, new, expected in MUTATIONS:
            if not (REPO / rel).is_file():
                print(f"  {'SKIP':17} {label} ({rel} is not in this tree)")
                continue
            fresh_copy(root)
            path = root / rel
            raw = path.read_bytes()
            n, mutated = mr.mutate_bytes(raw, old, new)
            if n == 0 and label in PRIVATE_ONLY:
                print(f"  {'SKIP':17} {label} (its anchor is only in the private {rel})")
                continue
            if n != 1:
                print(f"  ERROR             {label}: pattern found {n} times in {rel}, expected 1")
                results.append((label, "PATTERN"))
                continue
            path.write_bytes(mutated)
            run = run_suite(root)
            fired = run.code != 0 and not run.timed_out
            named = re.search(rf"::{re.escape(expected)} FAILED", run.out) is not None
            verdict = ("TIMEOUT" if run.timed_out else
                       "FIRED" if fired and named else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in run.out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))

    bad = [r for r in results if r[1] != "FIRED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired the right test "
          f"(the real tree was never written)")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
