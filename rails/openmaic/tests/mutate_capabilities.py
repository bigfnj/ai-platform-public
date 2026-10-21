"""Mutation harness for this rail's chip payload. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\openmaic\\tests\\mutate_capabilities.py [label-substring ...]

Run this, not just pytest, before trusting a change to api/app.py `capabilities`, the one place
this rail builds a chip entry itself: the reasoning slot's while OPENMAIC_LLM_BASE_URL sends
generation elsewhere. Every other entry comes from the generated modelstate.py, whose own guards
live with the template.

It fails SILENTLY. An entry missing a key is rendered from the shell's defaults, so the chip
shows the rail's guessed `cold` as residency on this box, and an entry claiming `ollama` says the
broker's local model answers a slot the broker never sees. Neither raises anywhere.

Each case names the test that must fail. CONTROLS are rewrites that change nothing the suite
can see, and must SURVIVE. The shell's half (how a `backend: "external"` chip reads) is
web/tests/mutate_model_chips.mjs.
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
RAIL = REPO / "rails" / "openmaic"
RAIL_SRC = RAIL / "backend"
PY = sys.executable
SUITE = ["tests/test_capabilities_override.py"]
RUN_TIMEOUT = 300

A = "rails/openmaic/backend/openmaic_app/api/app.py"

# (label, file, old, new, expected test)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the override's entry drops `upstream`",
        A,
        '            "upstream": EXTERNAL,\n',
        "",
        "test_the_override_entry_carries_every_key_the_brokers_entries_do",
    ),
    (
        "the override's entry drops `backend`",
        A,
        '            "backend": EXTERNAL,\n',
        "",
        "test_the_override_entry_carries_every_key_the_brokers_entries_do",
    ),
    (
        "the override's entry drops `busy`",
        A,
        '            "busy": False,\n',
        "",
        "test_the_override_entry_carries_every_key_the_brokers_entries_do",
    ),
    (
        "the override claims the broker's local model answers the slot",
        A,
        '            "backend": EXTERNAL,\n',
        '            "backend": "ollama",\n',
        "test_the_override_says_the_broker_is_not_in_the_path",
    ),
    (
        "the override names this box as the slot's upstream",
        A,
        '            "upstream": EXTERNAL,\n',
        '            "upstream": "local",\n',
        "test_the_override_says_the_broker_is_not_in_the_path",
    ),
    (
        "the override reports a call in flight",
        A,
        '            "busy": False,\n',
        '            "busy": True,\n',
        "test_the_override_says_the_broker_is_not_in_the_path",
    ),
    (
        "the broker is asked about the slot it does not serve, so it is reported twice",
        A,
        '    specs = [] if override else [("reasoning", "LLM", settings.llm_model)]',
        '    specs = [("reasoning", "LLM", settings.llm_model)]',
        "test_the_override_entry_carries_every_key_the_brokers_entries_do",
    ),
    (
        "the chip shows the whole endpoint URL, query string and all",
        A,
        '            "model": _host_of(override), "state": "cold",',
        '            "model": override, "state": "cold",',
        "test_the_override_says_the_broker_is_not_in_the_path",
    ),
]

#: Rewrites the suite cannot see. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "the three keys written in another order",
        A,
        '            "upstream": EXTERNAL,\n            "backend": EXTERNAL,\n'
        '            "busy": False,\n',
        '            "busy": False,\n            "backend": EXTERNAL,\n'
        '            "upstream": EXTERNAL,\n',
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = str(RAIL_SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "OPENMAIC_LLM_BASE_URL", "OPENMAIC_CLI_REQUESTER",
                "OPENMAIC_LLM_MODEL", "OPENMAIC_EMBED_MODEL"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c", "import openmaic_app.api.app as a; print(a.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header", "-rfE",
                         "-p", "no:cacheprovider", *extra],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _apply(rel: str, old: str, new: str, label: str):
    """(path, original bytes, mutated bytes), or None after reporting a PATTERN."""
    path = REPO / rel
    original = path.read_bytes()
    n, mutated = mr.mutate_bytes(original, old, new)
    if n != 1:
        print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
        return None
    return path, original, mutated


def main() -> int:
    only = mr.parse_harness_args(MUTATIONS, CONTROLS, filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    controls = [c for c in CONTROLS if not only or any(o in c[0] for o in only)]
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from pytest's summary of failures and
    # errors can mean anything.
    _, collected = run_suite(("--collect-only",))
    missing = sorted({m[4] for m in chosen if m[4] not in collected})
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, rel, old, new, expected in chosen:
        applied = _apply(rel, old, new, label)
        if applied is None:
            results.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            fired = code not in (0, -1)
            named = mr.named_failed(out, expected)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and named else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    survived: list[tuple[str, str]] = []
    for label, rel, old, new in controls:
        applied = _apply(rel, old, new, label)
        if applied is None:
            survived.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            verdict = "TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED"
            survived.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in survived if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(survived) - len(bad_controls)}/{len(survived)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
