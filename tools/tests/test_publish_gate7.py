"""Publish gate 7 runs the conformance checker in --strict mode, with exactly two named allowances.

Gate 7 used to run the checker plainly, which exits on FAILs only. Every "this rule could not
look" warning announced itself and the gate printed `clean`: make catalog.py's _ENTRIES a
non-literal and RC003 warned on every rail while the snapshot passed. The checker's side of the
fix (--strict, VACUOUS, --allow) is tested in test_rail_conformance.py::TestStrict; this file
tests the wiring, by putting a stand-in checker where gate 7 looks for the real one and reading
back the argv it was handed.

Kept out of test_publish.py on purpose: that file is withheld from the public snapshot, and this
one guards itself the same way test_rail_smoke_roles.py does, keyed on the withheld FILE.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_PUBLISH = Path(__file__).resolve().parents[1] / "publish.py"
if not _PUBLISH.is_file():
    pytest.skip(
        "tools/publish.py is withheld from the public snapshot, so there is no gate 7 here to "
        "test. This file is unchanged and runs in the private repo; see publish.json "
        "paths.exclude.",
        allow_module_level=True,
    )

# Records the argv it was given beside itself, then exits 1 only under --strict: the shape of
# a tree whose only findings are warnings, which is exactly the case gate 7 used to pass.
_STUB = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "Path(__file__).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
    "print('WARN RC003  stand-in  could not read the catalog')\n"
    "print('0 fail, 1 warn')\n"
    "sys.exit(1 if '--strict' in sys.argv else 0)\n"
)


def _snapshot(tmp_path: Path) -> Path:
    tools = tmp_path / "out" / "tools"
    tools.mkdir(parents=True)
    (tools / "rail_conformance.py").write_text(_STUB, encoding="utf-8")
    return tmp_path / "out"


def _argv(out: Path) -> list[str]:
    return json.loads((out / "tools" / "argv.json").read_text(encoding="utf-8"))


def _allows(argv: list[str]) -> list[str]:
    return sorted(argv[i + 1] for i, a in enumerate(argv) if a == "--allow")


def test_gate7_runs_the_checker_strict(publish, tmp_path):
    out = _snapshot(tmp_path)
    publish.conformance(out)
    argv = _argv(out)
    assert "--strict" in argv, argv
    assert "--warn-only" not in argv, argv


def test_gate7_allows_exactly_the_two_ci_vacuous_conditions(publish, tmp_path):
    """Named, and no more than these. Each holds in every snapshot by construction: dist/ is
    gitignored so no rail is built (RC016 is inert), and a rail's gitignored local/ is never
    copied (RC018 cannot read its names). A third allowance here would need the same argument."""
    out = _snapshot(tmp_path)
    publish.conformance(out)
    assert _allows(_argv(out)) == ["pseudonyms-absent", "unbuilt-tree"]


def test_every_allowance_gate7_passes_is_one_the_checker_defines(publish, conformance,
                                                                  tmp_path):
    """A renamed condition would otherwise surface as an argparse error at publish time."""
    out = _snapshot(tmp_path)
    publish.conformance(out)
    assert set(_allows(_argv(out))) <= set(conformance.VACUOUS)


def test_gate7_refuses_a_snapshot_whose_only_findings_are_warnings(publish, tmp_path):
    out = _snapshot(tmp_path)
    ok, report = publish.conformance(out)
    assert ok is False
    assert "could not read the catalog" in report
