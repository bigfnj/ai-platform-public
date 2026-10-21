"""Mutation harness for the offline guard (tools/tests/offline_guard.py). NOT a pytest file: it
rewrites files in this tree one at a time and restores them byte for byte.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_offline_guard.py [label-substring ...]

The guard fails OPEN in silence: a suite whose test reaches the broker still passes, because the
code under test was built to survive a broker that does not answer, and the only trace is GPU
time spent on synthetic data. So each mutation breaks one part of it and names the test in
test_offline_guard.py (or test_offline_guard_coverage.py) that must notice. A broken guard sends
the tests' connections nowhere: to TEST-NET-1 or to a loopback port the test holds unlistened.
"""
from __future__ import annotations

import sys
from pathlib import Path

from mutation_runner import (mutate_bytes, named_failed, parse_harness_args, restore_exact,
                             run_bounded)

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
G = "tools/tests/offline_guard.py"
SUITE = ["tools/tests/test_offline_guard.py", "tools/tests/test_offline_guard_coverage.py",
         "tools/tests/test_rail_template_scaffold.py"]
WINDOWS_ONLY = "on Windows only: elsewhere asyncio connects through socket.connect"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("connections are let through",
     G, "    if reached is None or _permitted(address):\n        return False\n",
     "    if True:\n        return False\n",
     "test_a_connection_is_refused_and_recorded"),
    ("loopback is exempt whatever listens there, so the broker is reached",
     G, "    return address[1] in ports\n", "    return True\n",
     "test_loopback_is_refused_where_this_process_does_not_listen"),
    ("connect_ex is not watched",
     G, "    if _refuse_connection(address):\n        return errno.ECONNREFUSED\n",
     "    if False:\n        return errno.ECONNREFUSED\n",
     "test_loopback_is_refused_where_this_process_does_not_listen"),
    ("a listening socket is not recorded, so a test's own server is refused",
     G, "    _real[\"listen\"](self, *args)\n    _LISTENERS.add(self)\n",
     "    _real[\"listen\"](self, *args)\n",
     "test_a_server_the_test_starts_is_reached_until_it_closes"),
    ("the asyncio proactor is not watched (" + WINDOWS_ONLY + ")",
     G, "    _windows_events.IocpProactor.connect = _proactor_connect\n", "",
     "test_an_event_loop_runs_and_its_connections_meet_the_guard"),
    ("host names are resolved",
     G, "    if reached is None:\n        return False\n    reached.append((_host(name), \"dns\"))\n",
     "    if True:\n        return False\n    reached.append((_host(name), \"dns\"))\n",
     "test_a_host_name_is_not_resolved"),
    ("every class is exempt",
     G, "    return cls is not None and bool(_LIVE.search(cls.__name__))\n", "    return True\n",
     "test_a_connection_is_refused_and_recorded"),
    ("\"live\" anywhere in a class name exempts it, Delivery included",
     G, '_LIVE = re.compile(r"Live(?![a-z])")\n', '_LIVE = re.compile(r"(?i)live")\n',
     "test_a_class_whose_name_merely_contains_live_is_watched"),
    ("a refusal the code swallowed passes",
     G, "    if reached:\n        pytest.fail(", "    if False:\n        pytest.fail(",
     "test_a_planted_call_errors_at_teardown_naming_the_test"),
    ("the window opens after setup, so a module fixture's connection goes unseen",
     G, "        _WINDOW.reached = reached\n    return (yield)\n",
     "    result = yield\n    _WINDOW.reached = item.stash.get(_OUTBOUND)\n    return result\n",
     "test_a_planted_call_errors_at_teardown_naming_the_test"),
    ("a suite's conftest loses the guard and nothing says so",
     "rails/openmaic/tests/conftest.py", "pytest_runtest_setup, pytest_runtest_teardown",
     "pytest_runtest_teardown",
     "test_every_suite_takes_the_guard"),
    # 2026-10-16: `rail_template.py new` seeds tests/conftest.py, so a new rail is born guarded.
    ("a new rail is scaffolded with no conftest again",
     "tools/rail_template.py", "    if not conftest.exists():\n        conftest.write_text(",
     "    if False:\n        conftest.write_text(",
     "test_a_new_rail_is_born_with_the_offline_guard"),
    ("the seeded conftest leaves out the guard's setup hook",
     "tools/rail_templates/conftest.py.tmpl",
     "from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown",
     "from offline_guard import outbound, pytest_runtest_teardown",
     "test_a_new_rail_is_born_with_the_offline_guard"),
]


def run_suite():
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header", "-p", "no:cacheprovider"],
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
        if WINDOWS_ONLY in label and sys.platform != "win32":
            print(f"  {'SKIPPED':17} {label}")
            continue
        path = REPO / relpath
        if not path.is_file():
            print(f"  {'SKIPPED':17} {label}: {relpath} is not in this tree")
            continue
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
