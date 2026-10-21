"""Mutation harness for `requester` and the broker wait in terminal-fun. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\terminal-fun\\backend\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`, the
`/api/chat` call, `settings.broker_timeout` or the capabilities chip.

The role guard fails OPEN and SILENTLY, and the wait fails as a plain "unavailable" line that
looks like a flaky model rather than a timeout ordering bug. Its sibling in another rail is
rails/recipe-book/tests/mutate_cli_requester.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]           # rails/terminal-fun/backend
REPO = BACKEND.parents[2]
PY = sys.executable
PYTHONPATH = [str(BACKEND)]
TIMEOUT_S = 600

SUITE = [str(BACKEND / "tests" / "test_cli_requester.py")]

PKG = BACKEND / "terminal_fun_app"
BROKER = PKG / "broker.py"
CONFIG = PKG / "config.py"
MAIN = PKG / "main.py"

MUTATIONS: list[tuple[str, Path, str, str, str]] = [
    (
        "a model override rides the viewer's name to the CLI",
        BROKER,
        "    return requester if requester and model == settings.llm_model else None",
        "    return requester",
        "test_a_foreign_role_never_carries_the_name",
    ),
    (
        "the facade never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    if False:\n        payload["requester"] = who',
        "test_the_assistant_role_carries_the_requester",
    ),
    (
        "the facade sends an absent requester as null",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    payload["requester"] = who',
        "test_no_requester_means_none_on_the_wire",
    ),
    (
        "chat_json stops forwarding it",
        BROKER,
        '    content = chat(messages, fmt="json", requester=requester)',
        '    content = chat(messages, fmt="json")',
        "test_chat_json_puts_the_requester_on_the_wire",
    ),
    (
        "the assistant route drops it",
        MAIN,
        "        data = broker.chat_json(msgs, requester=ident.user)",
        "        data = broker.chat_json(msgs)",
        "test_the_assistant_names_the_viewer",
    ),
    # "The wait goes back under the CLI timeout" (480 -> 120) moved to the fleet smoke check's
    # own harness (maintainer tooling, not in the public snapshot): the ordering against
    # BROKER_CLI_TIMEOUT is now checked live there, from a registry held equal to this rail's
    # config.
    (
        "the wait outlasts the gateway's read timeout",
        CONFIG,
        "    broker_timeout: float = 480.0",
        "    broker_timeout: float = 900.0",
        "test_the_broker_wait_ends_before_the_gateways_and_is_the_one_the_call_uses",
    ),
    (
        "the call stops using the configured wait",
        BROKER,
        "timeout=settings.broker_timeout, headers=_AUTH)",
        "timeout=120.0, headers=_AUTH)",
        "test_the_broker_wait_ends_before_the_gateways_and_is_the_one_the_call_uses",
    ),
    (
        "the chip stops opting the assistant in, so an owner is told it runs locally",
        MAIN,
        "                              requester=ident.user, cli_slots=CLI_SLOTS)",
        "                              )",
        "test_an_owner_sees_the_assistant_on_claude",
    ),
    (
        "the chip judges a different identity from the one the call carries",
        MAIN,
        "                              requester=ident.user, cli_slots=CLI_SLOTS)",
        "                              requester=None, cli_slots=CLI_SLOTS)",
        "test_an_owner_sees_the_assistant_on_claude",
    ),
    (
        "the module's CLI_SLOTS opts nothing in",
        MAIN,
        'CLI_SLOTS = ("assistant",)',
        "CLI_SLOTS = ()",
        "test_an_owner_sees_the_assistant_on_claude",
    ),
]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if _read(path) != text:
        raise RuntimeError(f"{path} did not read back as written -- TREE IS DIRTY")


def _eol(pattern: str, text: str) -> str:
    """Patterns are written with \\n; match them in the file's own line ending."""
    return pattern.replace("\n", "\r\n") if "\r\n" in text else pattern


def witness() -> str | None:
    """Prove the suite imports THIS tree's terminal_fun_app, or say which one it imports."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    probe = ("import terminal_fun_app.broker as b, terminal_fun_app.config as c\n"
             "for m in (b, c): print('WITNESS', m.__file__)")
    p = mr.run_tree([PY, "-c", probe], cwd=REPO, capture_output=True, text=True, env=env,
                    timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in p.stdout.splitlines()
             if ln.startswith("WITNESS ")]
    if p.returncode != 0 or len(files) != 2:
        return f"import probe failed:\n{p.stdout}{p.stderr}"
    stray = [str(f) for f in files if REPO.resolve() not in f.parents]
    return f"imports resolve outside this tree: {stray}" if stray else None


def run_suite() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env.pop("PLATFORM_STANDALONE", None)
    env.pop("TERMINAL_FUN_BROKER_TIMEOUT", None)
    try:
        p = mr.run_tree([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=BACKEND, capture_output=True, text=True, env=env,
                        timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: the suite ran past {TIMEOUT_S}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    if (why := witness()) is not None:
        print(f"WITNESS FAILED -- {why}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, path, old, new, expected in MUTATIONS:
        original = _read(path)
        old, new = _eol(old, original), _eol(new, original)
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {path.name}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            _write(path, original.replace(old, new))
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
            _write(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
