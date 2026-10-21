"""Shared for edu-media-core's suite: the offline guard (tools/tests/offline_guard.py).

No test outside a live class reaches the network, loopback included: a connection goes only to a
socket this process listens on, a host name is not looked up, and a test that tried ERRORs at its
teardown. The rules and their reasons are in the guard's docstring.
"""
import sys
from pathlib import Path

# tools/tests, found the way the rails' mutation harnesses find mutation_runner.py. Appended, so
# nothing there shadows a module of this suite's.
sys.path.append(str(next(p for p in Path(__file__).resolve().parents
                         if (p / "tools" / "tests" / "offline_guard.py").is_file())
                    / "tools" / "tests"))
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: E402,F401
