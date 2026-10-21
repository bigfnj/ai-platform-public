"""Every third-party package the broker imports at module scope is declared in its pyproject.

`platform_core` was imported at the top of broker.py for weeks without being declared. Nothing
noticed, because every install path on this box pip-installs platform_core separately first;
`pip install -e services/broker` on its own produced a broker that died at import. The same
happened earlier with python-multipart. A missing declaration is invisible on the machine that
already has the package, so it is checked from the source instead: parse each module, collect
the imports that run at load time, and require each to map to a declared distribution.

Imports inside functions are deliberately NOT counted. media_worker's torch, diffusers and
kokoro imports run under the MEDIA interpreter, not the broker's, and declaring them here would
pull CUDA wheels into the broker's venv.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.requirements import Requirement

BROKER = Path(__file__).resolve().parents[1]

#: Import name -> distribution name, where they differ and the installed metadata cannot be
#: relied on to say so (an editable install of a sibling package often carries no top_level).
ALIASES = {
    "platform_core": "platform-core",
    "pydantic_settings": "pydantic-settings",
    "multipart": "python-multipart",
    "yaml": "pyyaml",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> set[str]:
    data = tomllib.loads((BROKER / "pyproject.toml").read_text(encoding="utf-8"))
    return {_norm(Requirement(d).name) for d in data["project"]["dependencies"]}


def _load_time_imports(tree: ast.Module) -> set[str]:
    """Top-level names imported when the module LOADS: its body, including `if`/`try` blocks
    at module level, but not function or class bodies and not `if TYPE_CHECKING:`."""
    found: set[str] = set()

    def walk(stmts):
        for node in stmts:
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
            elif isinstance(node, ast.If):
                if not (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"):
                    walk(node.body)
                walk(node.orelse)
            elif isinstance(node, ast.Try):
                walk(node.body)
                for h in node.handlers:
                    walk(h.body)
                walk(node.orelse)
                walk(node.finalbody)

    walk(tree.body)
    return found


def third_party_imports() -> dict[str, list[str]]:
    """import name -> the app modules that import it at load time."""
    out: dict[str, list[str]] = {}
    for path in sorted((BROKER / "app").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _load_time_imports(tree):
            if name in sys.stdlib_module_names or name in ("__future__", "app"):
                continue
            out.setdefault(name, []).append(path.name)
    return out


def test_every_load_time_import_is_a_declared_dependency():
    have = declared()
    dists = packages_distributions()
    missing = {}
    for name, where in third_party_imports().items():
        candidates = {_norm(d) for d in dists.get(name, [])} | {_norm(ALIASES.get(name, name))}
        if not candidates & have:
            missing[name] = where
    assert not missing, (
        "imported at module scope but not declared in services/broker/pyproject.toml "
        f"(so `pip install -e services/broker` alone yields a broker that cannot start): {missing}")


def test_a_guarded_module_level_import_is_still_seen():
    """`try: import x / except ImportError:` at module scope still runs at load time, and an
    optional import is exactly the kind that goes undeclared. A function body does not, and
    neither does a TYPE_CHECKING block."""
    tree = ast.parse(
        "try:\n    import guarded\nexcept ImportError:\n    guarded = None\n"
        "if TYPE_CHECKING:\n    import typing_only\n"
        "def f():\n    import lazy\n")
    assert _load_time_imports(tree) == {"guarded"}


def test_the_scan_sees_the_imports_it_exists_for():
    """Witness. A scan that found nothing would pass the test above for every pyproject."""
    seen = third_party_imports()
    assert {"fastapi", "httpx", "pydantic", "platform_core"} <= set(seen)
    assert "broker.py" in seen["platform_core"]
