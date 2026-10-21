"""This suite's stand-ins for the heavy optional deps stay inside the imports that need them.

test_core.py, test_resource_safety.py and test_upkeep.py put empty modules for ollama, pdfplumber,
fitz, pytesseract and PIL into sys.modules when pytest collected them, and there they stayed for
the session. tools/test-targets.json runs this directory in core's one pytest process with
tools/tests, so a test there that imported one of those got the empty module, whatever was
installed (found by the ci-core lane, 2026-10-20). They now stand in only while the modules under
test are imported (edu_media_stand_ins.py).

The check runs this directory in a pytest of its own with a probe plugged in. It looks once when
collection is done, which is where every tools/tests test stands in core's process (pytest
collects every directory before it runs any test), and again when this directory's tests have
run, then importing each module: one that is not installed raises ImportError, which is what a
later test should see, and one that is an empty module is a stand-in left behind. A plugin rather
than a probe test file: a file outside the tree made pytest walk and stat every entry of the
directories between, the shared temp directory among them, where other suites create and delete
directories as it reads (a collection error, measured).
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

from edu_media_stand_ins import NAMES, stand_ins

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
# tools/tests, which conftest.py puts on sys.path for the offline guard: a timeout kills the tree.
import mutation_runner as mr  # noqa: E402

#: Every module a stand-in was ever put in for.
STOOD_IN = (*NAMES, "PIL", "PIL.Image")

PROBE = f'''"""A stand-in left in sys.modules, looked for after collection and after the run."""
import importlib
import json
import os
import sys

STOOD_IN = {STOOD_IN!r}
REPORT = {{}}


def _empty(module):
    return module is not None and module.__spec__ is None and getattr(module, "__file__", None) is None


def pytest_collection_finish(session):
    # Only looked at, not imported: an import here would change what the tests then run with.
    REPORT["collected"] = len(session.items)
    REPORT["after_collection"] = [n for n in STOOD_IN if _empty(sys.modules.get(n))]


def pytest_sessionfinish(session, exitstatus):
    left = []
    for name in STOOD_IN:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if _empty(module):
            left.append(name)
    REPORT["after_the_suite"] = left
    with open(os.environ["STAND_IN_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh)
'''


@pytest.fixture(scope="module")
def shared_process(tmp_path_factory):
    probe_dir = tmp_path_factory.mktemp("probe")
    (probe_dir / "edu_stand_in_probe.py").write_text(PROBE, encoding="utf-8")
    report = probe_dir / "report.json"
    core = json.loads((REPO / "tools" / "test-targets.json").read_text(encoding="utf-8"))["core"]
    env = dict(os.environ, STAND_IN_REPORT=str(report), PYTHONPATH=os.pathsep.join(
        [str(probe_dir), *(str(REPO / p) for p in core["pythonpath"])]))
    r = mr.run_tree([sys.executable, "-m", "pytest", str(HERE), "--ignore", str(Path(__file__).resolve()),
                     "-q", "-p", "no:cacheprovider", "-p", "edu_stand_in_probe", *core["pytest_args"]],
                    cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    got = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    return r, r.stdout + r.stderr, got


def test_no_stand_in_is_left_for_a_test_collected_beside_this_suite(shared_process):
    _r, out, got = shared_process
    assert got.get("collected", 0) >= 50, out             # witness: the suite was collected
    assert got["after_collection"] == [], got


def test_no_stand_in_is_left_once_this_suites_tests_have_run(shared_process):
    _r, out, got = shared_process
    assert "after_the_suite" in got, out
    assert got["after_the_suite"] == [], got


def test_the_suite_still_passes_beside_the_probe(shared_process):
    r, out, _got = shared_process
    assert r.returncode == 0, out


# --- the helper itself ---------------------------------------------------------------------------

def test_a_stand_in_is_there_inside_the_block_and_gone_after_it_even_after_a_raise():
    name = "edu_media_stand_ins_probe_module"
    with pytest.raises(RuntimeError):
        with stand_ins((name,), pil=False):
            assert isinstance(sys.modules[name], types.ModuleType)
            raise RuntimeError("inside the block")
    assert name not in sys.modules


def test_a_module_already_imported_is_neither_replaced_nor_removed(monkeypatch):
    real = types.ModuleType("edu_media_stand_ins_real")
    real.__file__ = "real.py"
    monkeypatch.setitem(sys.modules, "edu_media_stand_ins_real", real)
    with stand_ins(("edu_media_stand_ins_real",), pil=False):
        assert sys.modules["edu_media_stand_ins_real"] is real
    assert sys.modules["edu_media_stand_ins_real"] is real
