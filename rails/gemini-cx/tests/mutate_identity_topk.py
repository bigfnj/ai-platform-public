"""Mutation harness for the two RAG rails' audit-residue fixes. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\gemini-cx\\tests\\mutate_identity_topk.py

Covers gemini-cx AND smb-partner-enablement, because the fixes are the same five in both
(generated identity, bounded top_k, gated health, the seed pass that survives one bad
collection, the lifespan). Each rail's suite runs in its own pytest process: both have a
`tests/test_api.py`, which one pytest invocation cannot collect twice.

Each mutation names the test that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
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

REPO = Path(__file__).resolve().parents[3]
PY = sys.executable
RUN_TIMEOUT = 300

RAILS = {
    "gemini-cx": (REPO / "rails" / "gemini-cx" / "src", "gemini_cx", "rails/gemini-cx/tests"),
    "smb": (REPO / "rails" / "smb-partner-enablement" / "src", "smb_partner",
            "rails/smb-partner-enablement/tests"),
}

GA = "rails/gemini-cx/src/gemini_cx/api.py"
GI = "rails/gemini-cx/src/gemini_cx/ingest.py"
GD = "rails/gemini-cx/src/gemini_cx/identity.py"
SA = "rails/smb-partner-enablement/src/smb_partner/api.py"
SI = "rails/smb-partner-enablement/src/smb_partner/ingest.py"
SD = "rails/smb-partner-enablement/src/smb_partner/identity.py"

TOPK = ("    top_k: int = Field(default=0, ge=0, le=MAX_TOP_K)\n", "    top_k: int = 0\n")
CATCH = ("        except Exception as exc:  # noqa: BLE001 - one bad collection must not stop the pass\n",
         "        except broker.BrokerError as exc:  # noqa: BLE001\n")
NOBODY = ("            return Identity(None, True)\n", "            return Identity(\"standalone\", True)\n")
ADMIN = ("                    (x_platform_admin or \"\").strip().lower() in (\"1\", \"true\", \"yes\"))\n",
         "                    x_platform_admin == \"1\")\n")
LIFESPAN = ("                  lifespan=lifespan)\n", "                  )\n")

# (rail, label, [(file, old, new), ...], tests that must fail). Every `old` occurs exactly once.
MUTATIONS: list[tuple[str, str, list[tuple[str, str, str]], list[str]]] = [
    ("gemini-cx", "top_k is unbounded again", [(GA, *TOPK)],
     ["test_a_top_k_outside_the_bounds_is_refused_before_retrieval",
      "test_the_socket_refuses_an_unbounded_top_k_as_a_bad_request"]),
    ("gemini-cx", "/api/health is un-gated again",
     [(GA, "    def health(who: Identity = Depends(identity)) -> dict[str, Any]:\n",
           "    def health(who: Identity = Depends(lambda: Identity(None, True)))"
           " -> dict[str, Any]:\n")],
     ["test_no_identity_header_is_401",
      "test_health_never_reaches_the_broker_for_an_un_gated_caller"]),
    ("gemini-cx", "the seed pass catches BrokerError only", [(GI, *CATCH)],
     ["test_a_file_that_is_not_utf8_costs_its_collection_not_the_pass",
      "test_an_embedder_short_of_vectors_costs_its_collection_not_the_pass"]),
    ("gemini-cx", "standalone resolves to the literal user 'standalone'", [(GD, *NOBODY)],
     ["test_a_standalone_caller_is_nobody_not_the_string_standalone"]),
    ("gemini-cx", "X-Platform-Admin counts only the exact '1'", [(GD, *ADMIN)],
     ["test_the_admin_flag_reads_like_every_other_rail"]),
    ("gemini-cx", "the lifespan is not attached, so the boot ingest never runs", [(GA, *LIFESPAN)],
     ["test_the_boot_ingest_task_is_retained_and_health_says_so_until_it_finishes"]),
    ("smb", "top_k is unbounded again", [(SA, *TOPK)],
     ["test_a_top_k_outside_the_bounds_is_refused_before_retrieval",
      "test_the_socket_refuses_an_unbounded_top_k_as_a_bad_request"]),
    ("smb", "/api/health is un-gated again",
     [(SA, "    async def health(_: Identity = Depends(identity)) -> dict[str, Any]:\n",
           "    async def health() -> dict[str, Any]:\n")],
     ["test_health_is_gated_like_every_other_route", "test_no_identity_header_is_401"]),
    ("smb", "the seed pass catches BrokerError only", [(SI, *CATCH)],
     ["test_a_file_that_is_not_utf8_costs_its_collection_not_the_pass",
      "test_an_embedder_short_of_vectors_costs_its_collection_not_the_pass"]),
    ("smb", "standalone resolves to the literal user 'standalone'", [(SD, *NOBODY)],
     ["test_a_standalone_caller_is_nobody_not_the_string_standalone"]),
    ("smb", "X-Platform-Admin counts only the exact '1'", [(SD, *ADMIN)],
     ["test_the_admin_flag_reads_like_every_other_rail"]),
    ("smb", "the lifespan is not attached, so the boot ingest never runs", [(SA, *LIFESPAN)],
     ["test_the_boot_ingest_task_is_retained_and_health_says_so_until_it_finishes"]),
    ("gemini-cx", "a dead PORT setting comes back",
     [("rails/gemini-cx/src/gemini_cx/config.py", "_PKG_ROOT = Path(__file__)",
       "PORT = 8880\n_PKG_ROOT = Path(__file__)")],
     ["test_the_dead_settings_are_gone"]),
    ("smb", "a dead AUDIO_CACHE_DIR setting comes back",
     [("rails/smb-partner-enablement/src/smb_partner/config.py", "_PKG_ROOT = Path(__file__)",
       "AUDIO_CACHE_DIR = None\n_PKG_ROOT = Path(__file__)")],
     ["test_the_dead_settings_are_gone"]),
    ("smb", "a hard rule's key names an option no question offers",
     [("rails/smb-partner-enablement/src/smb_partner/generate.py",
       "    (\"headcount\", \"More than 300\"):\n", "    (\"headcount\", \"More than 301\"):\n")],
     ["test_every_hard_rule_names_a_real_question_and_option"]),
    ("smb", "a licence path's key names no headcount answer",
     [("rails/smb-partner-enablement/src/smb_partner/generate.py",
       "    \"More than 300\":\n", "    \"More than 301\":\n")],
     ["test_every_licence_path_names_a_real_headcount_option"]),
]


def _env(rail: str) -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: an editable install elsewhere would be tested instead.
    env["PYTHONPATH"] = str(RAILS[rail][0])
    # No .pyc: a mutation and its restore can share an mtime and a size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness(rail: str) -> str | None:
    """Prove the package under test resolves to THIS tree before any verdict means anything."""
    src, pkg, _ = RAILS[rail]
    p = mr.run_tree([PY, "-B", "-c", f"import {pkg}.api as a, {pkg}.identity as i;"
                     "print(a.__file__); print(i.__file__)"],
                    cwd=REPO, capture_output=True, text=True, env=_env(rail), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(src):
            return f"imported {f}, which is not under {src}"
    return None


def run_suite(rail: str) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", RAILS[rail][2], "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(rail),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED/ERROR lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if (ln.startswith("FAILED ") or ln.startswith("ERROR ")) and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def main() -> int:
    # Optional substrings narrow the run to the mutations whose "[rail] label" contains one.
    only = mr.parse_harness_args([f"[{m[0]}] {m[1]}" for m in MUTATIONS], filter=True).only
    for rail in RAILS:
        bad_import = witness(rail)
        if bad_import:
            print(f"WRONG TREE ({rail}) -- {bad_import}")
            return 2
        code, out = run_suite(rail)
        if code != 0:
            print(f"BASELINE IS NOT GREEN ({rail}) -- fix that before trusting any result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green ({rail}): {out.strip().splitlines()[-1]}")
    print()

    results: list[tuple[str, str]] = []
    for rail, label, edits, expected in MUTATIONS:
        label = f"[{rail}] {label}"
        if only and not any(o in label for o in only):
            continue
        original_bytes: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                n, mutated = mr.mutate_bytes(path.read_bytes(), old, new)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                path.write_bytes(mutated)
            if not pattern_ok:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite(rail)
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    still_red = [r for r in RAILS if run_suite(r)[0] != 0]
    print(f"\nrestored tree: {'green' if not still_red else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not still_red else 1


if __name__ == "__main__":
    sys.exit(main())
