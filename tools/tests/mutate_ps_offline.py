"""Mutation harness for the PowerShell stand-ins (tools/tests/conftest.py: PS_OFFLINE, run_ps's
`offline`, offline_script). NOT a pytest file: it rewrites files in this tree one at a time and
restores them byte for byte.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_ps_offline.py [label-substring ...]

A stand-in that is gone fails open in silence: a script under test calls the gateway, the broker
or Ollama by URL, and code built to survive a service that does not answer passes either way.
Each mutation breaks one part and names the test in test_run_ps_offline.py that must notice, and
runs THAT TEST ONLY: with a stand-in gone, the file's three proofs would send their call for real
(the gateway on :1111, Ollama on :11434, NSSM's download), while the named tests' calls go to
192.0.2.1 (TEST-NET-1), which goes nowhere, or start nothing at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

from mutation_runner import (mutate_bytes, named_failed, parse_harness_args, restore_exact,
                             run_bounded)

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
CF = "tools/tests/conftest.py"
SUITE = "tools/tests/test_run_ps_offline.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("run_ps writes no stand-ins, so every body's web call goes out",
     CF, '+ (PS_OFFLINE if offline else "") + body', "+ body",
     "test_every_stand_in_throws_naming_its_call"),
    ("Invoke-RestMethod has no stand-in",
     CF, 'function global:Invoke-RestMethod { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Invoke-RestMethod $args" }\n',
     "", "test_every_stand_in_throws_naming_its_call"),
    ("a stand-in throws without naming its call",
     CF, 'Invoke-WebRequest { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE): Invoke-WebRequest $args" }',
     'Invoke-WebRequest { throw "no network in a test (tools/tests/conftest.py PS_OFFLINE)" }',
     "test_every_stand_in_throws_naming_its_call"),
    ("offline=False is ignored, so a test's own server cannot be reached",
     CF, '+ (PS_OFFLINE if offline else "") + body', "+ PS_OFFLINE + body",
     "test_offline_false_leaves_the_cmdlets_alone"),
    ("offline_script carries no stand-ins into the child",
     CF, '        f.write_text(PS_OFFLINE + f"$global:LASTEXITCODE = 0\\n', '        f.write_text(f"$global:LASTEXITCODE = 0\\n',
     "test_a_script_started_as_its_own_process_needs_offline_script"),
    ("offline_script exits with the last native command's code, not -File's",
     CF, '                     "if ($?) { exit 0 }\\nexit $LASTEXITCODE\\n"', '                     "exit $LASTEXITCODE\\n"',
     "test_offline_script_exits_as_file_would"),
    ("a script the tests run reaches out through a .NET client",
     "deploy/installer/lib-apply.ps1",
     "            try { $names = (Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5).models.name } catch { }\n",
     "            try { $names = (New-Object System.Net.WebClient).DownloadString('http://127.0.0.1:11434/api/tags') } catch { }\n",
     "test_the_scripts_tests_run_call_out_only_through_the_stand_ins"),
]


def run_suite(only: str | None = None):
    sel = ["-k", only] if only else []
    return run_bounded([PY, "-m", "pytest", SUITE, "-q", "--no-header", "-p", "no:cacheprovider", *sel],
                       cwd=REPO)


def main() -> int:
    args = parse_harness_args(MUTATIONS, filter=True)
    picked = [m for m in MUTATIONS if not args.only or any(o in m[0] for o in args.only)]
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in picked:
        path = REPO / relpath
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite(expected)
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
