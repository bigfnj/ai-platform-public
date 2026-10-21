"""A missing test tool FAILS the suite; it does not fold a module into one quiet skip.

tools/tests/test_rail_manifest_schema.py used to `importorskip("jsonschema")` at module level, so
on a venv without it all of its tests became "1 skipped" inside a green core target, and the
manifest schema went unchecked on every local run. Each case here runs the module in a child
pytest with the package made unimportable (sys.modules[name] = None, which is what `import`
sees for a package that is not installed) and requires a failure that names the fix.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

CHILD = """
import sys
sys.modules[{mod!r}] = None
import pytest
sys.exit(pytest.main([{path!r}, "-q", "-p", "no:cacheprovider", "--no-header"] + {extra!r}))
"""


@pytest.mark.parametrize("module, path, extra, hint", [
    ("jsonschema", "tools/tests/test_rail_manifest_schema.py", [], "pip install jsonschema"),
    # The pure-Python test in that module, not a PowerShell one: on Linux CI every PowerShell test
    # skips at its `shell` fixture before pyyaml is ever imported, so a PowerShell target made this
    # case pass there as "1 skipped" and prove nothing (CI run 37730295649).
    ("yaml", "tools/tests/test_rebuild_stale.py", ["-k", "compose_file_names_services_to_build"],
     "pip install pyyaml"),
], ids=["jsonschema", "pyyaml"])
def test_a_missing_test_tool_fails_the_suite_instead_of_skipping(module, path, extra, hint):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / p) for p in (
        "services/broker", "apps/platform/backend", "packages/platform_core",
        "packages/edu-media-core/src"))
    r = subprocess.run([sys.executable, "-c", CHILD.format(mod=module, path=path, extra=extra)],
                       cwd=REPO, capture_output=True, text=True, timeout=300, env=env)
    summary = (r.stdout.strip().splitlines() or [""])[-1]
    assert r.returncode == 1, f"exit {r.returncode}: {summary}\n{r.stdout[-1500:]}{r.stderr[-500:]}"
    # "1 failed", not "N deselected" or "1 skipped": the child must have RUN the test that needs the
    # tool and seen it fail, on whatever OS this is.
    assert "skipped" not in summary and ("failed" in summary or "error" in summary), summary
    assert hint in r.stdout, r.stdout[-1500:]
