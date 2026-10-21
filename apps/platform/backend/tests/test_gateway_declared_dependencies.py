"""Every third-party package the gateway imports at module scope is declared, and installed by its images.

main.py came to import `anyio` by name (the body ceiling's shielded cancel scope) with no
declaration anywhere: Starlette pulls it in, so every venv and image had it and nothing could
notice. starlette, pydantic, pydantic-settings and websockets were the same, each present only
through another package's requirements. A missing declaration is invisible on the machine that
already has the package, so it is checked from the source, as services/broker's
test_declared_dependencies.py does for the broker: parse each module, collect the imports that run
at load time, and require each to map to a dependency pyproject.toml declares.

The container does not install from pyproject.toml. Both gateway Dockerfiles restate the list as
one `pip install` line, so a declaration that never reached them still leaves an image that dies at
import. The second test holds each Dockerfile to the declared list.

Imports inside functions are not counted: they run only when called, and the broker's test makes
the same cut for the same reason.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.requirements import Requirement

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "platform_gateway_app"
REPO = BACKEND.parents[2]
DOCKERFILES = ("deploy/Dockerfile.gateway", "deploy/Dockerfile.gateway.bundled")
# The repo's own core package: the images COPY it in and put it on PYTHONPATH, never pip-install it.
COPIED = {"platform-core"}

#: Import name -> distribution name, where they differ and installed metadata may not say so
#: (an editable install of a sibling package often carries no top_level).
ALIASES = {
    "platform_core": "platform-core",
    "pydantic_settings": "pydantic-settings",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> set[str]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return {_norm(Requirement(d).name) for d in data["project"]["dependencies"]}


def _load_time_imports(tree: ast.Module) -> set[str]:
    """Top-level names imported when the module LOADS: its body, including `if`/`try` blocks at
    module level, but not function or class bodies and not `if TYPE_CHECKING:`."""
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
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _load_time_imports(tree):
            if name in sys.stdlib_module_names or name in ("__future__", "platform_gateway_app"):
                continue
            out.setdefault(name, []).append(path.relative_to(APP).as_posix())
    return out


def pip_installed(dockerfile: str) -> set[str]:
    """The distributions a Dockerfile's `RUN pip install` lines name, as quoted requirements."""
    # `* text=auto` checks these out CRLF on Windows and LF elsewhere; the continuation is `\` + EOL.
    text = (REPO / dockerfile).read_text(encoding="utf-8").replace("\r\n", "\n")
    names: set[str] = set()
    for m in re.finditer(r"RUN pip install(?:[^\n]*\\\n)*[^\n]*", text):
        names |= {_norm(Requirement(q).name) for q in re.findall(r'"([^"]+)"', m.group(0))}
    return names


def test_every_load_time_import_is_a_declared_dependency():
    have = declared()
    dists = packages_distributions()
    missing = {}
    for name, where in third_party_imports().items():
        candidates = {_norm(d) for d in dists.get(name, [])} | {_norm(ALIASES.get(name, name))}
        if not candidates & have:
            missing[name] = where
    assert not missing, (
        "imported at module scope but not declared in apps/platform/backend/pyproject.toml "
        f"(present today only because another package pulls it in): {missing}")


def test_each_gateway_image_installs_every_declared_dependency():
    want = declared() - COPIED
    lacking = {}
    for dockerfile in DOCKERFILES:
        got = pip_installed(dockerfile)
        assert got, f"read no `pip install` requirement from {dockerfile}, so this checks nothing"
        if want - got:
            lacking[dockerfile] = sorted(want - got)
    assert not lacking, f"declared in pyproject.toml, never installed by the image: {lacking}"


def test_a_guarded_module_level_import_is_still_seen():
    """`try: import x / except ImportError:` at module scope still runs at load time; a function
    body does not, and neither does a TYPE_CHECKING block."""
    tree = ast.parse(
        "try:\n    import guarded\nexcept ImportError:\n    guarded = None\n"
        "if TYPE_CHECKING:\n    import typing_only\n"
        "def f():\n    import lazy\n")
    assert _load_time_imports(tree) == {"guarded"}


def test_the_scans_see_what_they_exist_for():
    """Witnesses. A scan that found nothing would pass the tests above for any pyproject."""
    seen = third_party_imports()
    assert {"fastapi", "httpx", "sqlalchemy", "platform_core", "anyio"} <= set(seen), sorted(seen)
    assert "main.py" in seen["anyio"]
    for dockerfile in DOCKERFILES:
        assert {"fastapi", "uvicorn", "sqlalchemy"} <= pip_installed(dockerfile), dockerfile
