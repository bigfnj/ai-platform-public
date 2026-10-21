"""Re-export of tools/tests/mutation_runner.py, the one runner every mutation harness shares.

The broker's harnesses import `_suite_runner` by this name (this directory is sys.path[0] when
they run as scripts). The implementation moved to tools/tests, which ships with the public
snapshot like this directory, so the rails' and the gateway's harnesses can import the same
code. Change it THERE; tools/tests/mutate_mutation_runner.py mutation-tests it.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_TESTS = Path(__file__).resolve().parents[3] / "tools" / "tests"
if str(_TOOLS_TESTS) not in sys.path:
    sys.path.insert(0, str(_TOOLS_TESTS))

from mutation_runner import (  # noqa: E402,F401
    DEFAULT_TIMEOUT,
    REAP_TIMEOUT,
    SuiteRun,
    mutate_bytes,
    named_failed,
    parse_harness_args,
    restore_exact,
    run_bounded,
    run_tree,
    write_like,
)
