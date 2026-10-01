"""Shared loading for the tools suite.

The tools are SCRIPTS, not a package: `tools/` has no __init__.py and each file is run as
`python tools/<name>.py`. So they are loaded by path rather than imported by name, which also
keeps the suite runnable from run-tests.ps1's `core` target with an empty PYTHONPATH.

One non-obvious detail: the loaded module MUST be registered in sys.modules BEFORE
exec_module. rail_conformance.py defines @dataclass classes, and dataclasses resolves
`cls.__module__` through sys.modules while processing the class — an unregistered module
raises `AttributeError: 'NoneType' object has no attribute '__dict__'` at import. The two
loaders inside the tools themselves (rail_conformance._rail_template, and
rail_extractability.load_publish) skip this step and get away with it only because neither
rail_template.py nor publish.py happens to use a dataclass.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent


def load_tool(name: str):
    """Load tools/<name>.py as a module, once per session."""
    mod_name = f"_tools_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = TOOLS / f"{name}.py"
    assert path.is_file(), f"expected a tool at {path}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # see the module docstring: dataclasses needs this
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def smoke():
    return load_tool("rail_smoke")


@pytest.fixture(scope="session")
def publish():
    return load_tool("publish")


@pytest.fixture(scope="session")
def template():
    return load_tool("rail_template")


@pytest.fixture(scope="session")
def conformance():
    return load_tool("rail_conformance")


@pytest.fixture(scope="session")
def real_baseline() -> dict:
    """tools/smoke-baseline.json as recorded. Read-only; nothing here writes it."""
    import json
    return json.loads((TOOLS / "smoke-baseline.json").read_text(encoding="utf-8"))
