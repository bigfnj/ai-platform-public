"""Mutation harness for test_env_example.py and test_compose_env_scope.py. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_env_example.py

It never touches this tree. Each run copies what the two tests read (the compose file, the
example, both tests, and job-aid's source when the tree has it) into a temp directory with the
same layout, mutates the COPY, and runs the copied tests there, so an interrupted run cannot
leave the real compose file carrying a variable nobody declared.

Both guards fail silently in the direction that matters: a knob added to compose and not to the
example, or a secret handed to a container that never reads it, looks exactly like a passing
suite. So every mutation below is something a real edit does, and each must turn the suite red
AND name the test that owns that property.

The public snapshot has no job-aid (a withheld rail), so its mutations are reported there as
SKIP with that reason and left out of the count, never passed off as fired.
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
C = "deploy/docker-compose.yml"
E = "deploy/.env.example"
T = "tools/tests/test_env_example.py"
S = "tools/tests/test_compose_env_scope.py"
JOB_AID_SRC = "rails/job-aid/src"
FILES = (C, E, T, S)
TESTS = (T, S)
HAS_JOB_AID = (REPO / JOB_AID_SRC).is_dir()
# The witness that the copied suites really collected and ran: 6 example tests + 6 scope tests,
# of which 4 need job-aid and SKIP (with a reason) in a tree without it.
EXPECTED_PASSING = 12 if HAS_JOB_AID else 8
EXPECTED_SKIPPED = 0 if HAS_JOB_AID else 4
TIMEOUT_S = 120
AA = f"{JOB_AID_SRC}/job_aid/api/app.py"
LS = f"{JOB_AID_SRC}/job_aid/research/location_scope.py"

# Every compose anchor below sits in a PUBLISHED rail's service (gemini-cx, openmaic,
# smb-partner-enablement), because this harness ships in the public snapshot too, whose compose
# has the withheld rails' services removed. The first version anchored on edu-suite and
# servicenow lines and reported PATTERN for both there; build the snapshot and run it after
# changing an anchor.
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "compose gains a knob the example never mentions",
        C,
        "      GEMINI_CX_VOICE_SPEAKER: ${GEMINI_CX_VOICE_SPEAKER:-af_heart}\n",
        "      GEMINI_CX_VOICE_SPEAKER: ${GEMINI_CX_VOICE_SPEAKER:-af_heart}\n"
        "      NEW_KNOB: ${UNDOCUMENTED:-1}\n",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "compose gains an undocumented name only as a NESTED default",
        C,
        "${OPENMAIC_LLM_API_KEY:-${OPENMAIC_SHIM_TOKEN:-openmaic-local-dev}}",
        "${OPENMAIC_LLM_API_KEY:-${UNDOCUMENTED_INNER:-openmaic-local-dev}}",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "compose gains an undocumented bind-mount variable in a volumes list",
        C,
        "      - smb_partner_data:/srv/var\n",
        "      - smb_partner_data:/srv/var\n      - ${UNDOCUMENTED_MOUNT:-x}:/srv/x:ro\n",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "the example drops an active key compose reads",
        E,
        # Anchored at the line start: `# JOB_AID_NIGHTLY_TZ=America/Los_Angeles` contains the
        # bare text too, and the count-of-1 check is what caught that.
        "\nTZ=America/Los_Angeles\n",
        "\n",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "the example drops a commented-default key compose reads",
        E,
        "# WORKSTATION_RDP_PORT=3389\n",
        "",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "a key survives only as a prose mention, not as a line to uncomment",
        E,
        "# GEMINI_CX_VOICE_SPEAKER=af_heart\n",
        "# The speaker is GEMINI_CX_VOICE_SPEAKER, af_heart by default.\n",
        "test_every_compose_variable_is_documented_in_the_example",
    ),
    (
        "a secret gets a value on an active line",
        E,
        "\nBROKER_AUTH_TOKEN=\n",
        "\nBROKER_AUTH_TOKEN=change-me\n",
        "test_secrets_in_the_example_are_empty",
    ),
    (
        # A published rail's key: the public snapshot's copy of the example keeps only the
        # sections of the services it ships, so a job-aid key is not there to anchor on.
        "a secret gets a value on a COMMENTED line",
        E,
        "\nAI_PLAYGROUND_NVIDIA_API_KEY=\n",
        "\n# AI_PLAYGROUND_NVIDIA_API_KEY=sk-pasted-by-mistake\n",
        "test_secrets_in_the_example_are_empty",
    ),
    (
        "the parser stops reading nested defaults",
        T,
        "            names.add(m.group(2) or m.group(3))",
        "            names.add(m.group(2) or m.group(3))\n"
        "            break",
        "test_compose_parser_reads_every_reference_form_and_nothing_else",
    ),
    (
        "the parser reads compose comments as references",
        T,
        "        for m in _REF.finditer(_strip_comment(line)):",
        "        for m in _REF.finditer(line):",
        "test_compose_parser_reads_every_reference_form_and_nothing_else",
    ),
    (
        "the parser stops honouring the $$ escape",
        T,
        "            if m.group(1):          # `$$`, an escaped literal dollar\n"
        "                continue",
        "            if m.group(1):          # `$$`, an escaped literal dollar\n"
        "                names.add('ESCAPED')\n"
        "                continue",
        "test_compose_parser_reads_every_reference_form_and_nothing_else",
    ),
    (
        "commented keys stop counting, so `# KEY=default` reads as undocumented",
        T,
        r'_ASSIGN = re.compile(r"^[ \t]*(#[ \t]*)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.M)',
        r'_ASSIGN = re.compile(r"^[ \t]*()([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.M)',
        "test_example_parser_counts_commented_keys_but_not_prose",
    ),
    # --- what compose hands a container (test_compose_env_scope.py), any tree -------------------
    (
        "a published service starts loading .env whole",
        C,
        "    image: gemini-cx-backend:latest\n",
        "    image: gemini-cx-backend:latest\n    env_file:\n      - .env\n",
        "test_no_service_loads_the_shared_env_file",
    ),
    (
        "the source scanner stops seeing os.getenv reads",
        S,
        "    r\"\"\"(?:getenv|environ\\.get|",
        "    r\"\"\"(?:environ\\.get|",
        "test_the_read_scanner_finds_every_form_and_nothing_else",
    ),
]

# Need job-aid's compose block and source; reported as SKIP in a tree without them.
JOB_AID_MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "job-aid goes back to loading .env whole",
        C,
        "    environment:\n      JOB_AID_DB: /srv/var/job_aid.sqlite3\n",
        "    env_file:\n      - ./.env\n    environment:\n      JOB_AID_DB: /srv/var/job_aid.sqlite3\n",
        "test_no_service_loads_the_shared_env_file",
    ),
    (
        "job-aid is handed another rail's secret under that secret's own name",
        C,
        "      THEMUSE_API_KEY: ${THEMUSE_API_KEY:-}\n",
        "      THEMUSE_API_KEY: ${THEMUSE_API_KEY:-}\n"
        "      RECIPE_BOOK_TOKEN_KEY: ${RECIPE_BOOK_TOKEN_KEY:-}\n",
        "test_job_aid_receives_no_secret_it_does_not_read",
    ),
    (
        "a key job-aid reads is fed another component's secret as its VALUE",
        C,
        "      JOOBLE_API_KEY: ${JOOBLE_API_KEY:-}\n",
        "      JOOBLE_API_KEY: ${PLATFORM_ADMIN_PASSWORD:-}\n",
        "test_job_aid_receives_no_secret_it_does_not_read",
    ),
    (
        "a credential job-aid reads vanishes from compose (how env_file came to exist)",
        C,
        "      CAREERJET_API_KEY: ${CAREERJET_API_KEY:-}\n",
        "",
        "test_job_aid_receives_exactly_the_keys_it_reads",
    ),
    (
        "a key excluded on purpose is passed anyway",
        C,
        "      THEMUSE_API_KEY: ${THEMUSE_API_KEY:-}\n",
        "      THEMUSE_API_KEY: ${THEMUSE_API_KEY:-}\n"
        "      PLATFORM_STANDALONE: ${PLATFORM_STANDALONE:-}\n",
        "test_job_aid_cannot_be_given_an_excluded_key",
    ),
    (
        "the source starts reading a key nobody wired into compose",
        LS,
        '    return (os.environ.get("JOB_AID_LOCATION_SCOPE") or DEFAULT_SCOPE)',
        '    return (os.environ.get("JOB_AID_LOCATION_SCOPE") or os.environ.get("JOB_AID_NEW_KNOB")'
        " or DEFAULT_SCOPE)",
        "test_job_aid_receives_exactly_the_keys_it_reads",
    ),
    (
        "an exclusion goes stale because the source stopped reading that key",
        AA,
        'os.getenv("PLATFORM_STANDALONE", "")',
        'os.getenv("PLATFORM_STANDALONE_MODE", "")',
        "test_every_exclusion_is_still_read",
    ),
]


def run_suite(root: Path) -> tuple[int, str]:
    env = dict(os.environ)
    # Nothing here imports a platform package; the copied tree is the only root.
    env["PYTHONPATH"] = str(root)
    try:
        p = mr.run_tree([PY, "-m", "pytest", *TESTS, "-v", "--no-header", "-p", "no:cacheprovider"],
                        cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT after {TIMEOUT_S}s\n{exc.stdout or ''}{exc.stderr or ''}"
    return p.returncode, p.stdout + p.stderr


def fresh_copy(dest: Path) -> None:
    for rel in FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest / rel)
    if HAS_JOB_AID:
        shutil.copytree(REPO / JOB_AID_SRC, dest / JOB_AID_SRC, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))


def main() -> int:
    mr.parse_harness_args(MUTATIONS + JOB_AID_MUTATIONS)
    real = mr.snapshot(REPO / rel for rel in
                       {*FILES, *(m[1] for m in MUTATIONS + JOB_AID_MUTATIONS)}
                       if (REPO / rel).is_file())
    with tempfile.TemporaryDirectory(prefix="mutate-env-example-") as tmp:
        root = Path(tmp)
        fresh_copy(root)
        code, out = run_suite(root)
        passed, skipped = out.count(" PASSED"), out.count(" SKIPPED")
        # The copy, not the real tree, is what ran: the -v lines carry the copied path.
        if (code != 0 or passed != EXPECTED_PASSING or skipped != EXPECTED_SKIPPED
                or "test_env_example.py::" not in out or "test_compose_env_scope.py::" not in out):
            print(f"BASELINE IS NOT GREEN ({passed}/{EXPECTED_PASSING} passed, {skipped}/"
                  f"{EXPECTED_SKIPPED} skipped, exit {code}) -- fix that before trusting any "
                  "mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green in {root}: {passed}/{EXPECTED_PASSING} passed, "
              f"{skipped} skipped\n")

        results: list[tuple[str, str]] = []
        runnable = MUTATIONS + (JOB_AID_MUTATIONS if HAS_JOB_AID else [])
        if not HAS_JOB_AID:
            for label, *_ in JOB_AID_MUTATIONS:
                print(f"  {'SKIP':17} {label} (job-aid is not in this tree)")
        for label, relpath, old, new, expected in runnable:
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
            named = f"{expected} FAILED" in out
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
          f"(the real tree was never written)"
          + ("" if HAS_JOB_AID else f"; {len(JOB_AID_MUTATIONS)} job-aid mutation(s) SKIPPED"))
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
