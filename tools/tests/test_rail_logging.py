"""Do a rail's own INFO lines reach the container log? And the template that makes them.

Under uvicorn's default logging configuration the root logger has no handler and sits at
WARNING, so `logging.getLogger("gemini_cx.api").info(...)` reaches nothing and is dropped. Four
rails had 17 such call sites (seed-ingest reports, warm skips, upload and ingest notes), every
one writing to nowhere. tools/rail_templates/logsetup.py.tmpl is the fix, generated into each of
them as `<package>/logsetup.py` and called first thing in the app factory.

The rail tests here are PROCESS tests, on purpose: each starts a fresh interpreter, applies
uvicorn's own LOGGING_CONFIG exactly as `uvicorn ...` does at startup, builds the app through
the factory named in the rail's deploy/Dockerfile CMD (the artefact that actually runs), and
looks for an INFO line on stderr. A control run that skips the factory must NOT see the line,
which is what proves the probe can fail.

Its mutation harness, mutate_rail_logging.py, is maintainer tooling and not in the public
snapshot; maintainers run it after changing the template, a rail's factory, or this file.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "tools" / "rail_templates" / "logsetup.py.tmpl"
# The rails whose module loggers log at INFO with no handler of their own. A rail joins this set
# by carrying <package>/logsetup.py AND calling it; the set is pinned so that deleting the file
# from one rail fails here rather than quietly shrinking what is checked.
RAILS = ("bouquet", "gemini-cx", "servicenow", "smb-partner-enablement")

PROBE = """
import importlib, logging, logging.config, sys
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)          # exactly what `uvicorn ...` does first
target, factory, package, build = sys.argv[1], sys.argv[2] == "1", sys.argv[3], sys.argv[4]
if build == "1":
    module, _, attr = target.partition(":")
    obj = getattr(importlib.import_module(module), attr)
    if factory:
        obj()
log = logging.getLogger(package + ".probe")
log.info("RAIL-INFO-PROBE")
log.debug("RAIL-DEBUG-PROBE")
"""


def _rail(tpl, rail_id: str):
    """`tpl` is tools/rail_template.py (conftest's `template` fixture)."""
    manifest = REPO / "rails" / rail_id / "rail.json"
    if not manifest.is_file():
        pytest.skip(f"rails/{rail_id} is not in this tree (withheld from this snapshot)")
    return tpl.Rail(manifest)


def _first_present() -> str:
    present = [r for r in RAILS if (REPO / "rails" / r / "rail.json").is_file()]
    if not present:
        pytest.skip(f"none of {RAILS} is in this tree (all withheld from this snapshot)")
    return present[0]


def _entrypoint(rail) -> tuple[str, bool]:
    """(module:attr, is_factory) from the rail's Dockerfile CMD, the command that really runs."""
    text = (rail.root / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^CMD\s+(\[.*?\])\s*$", text, re.M | re.S)
    assert m, f"no exec-form CMD in {rail.id}'s Dockerfile"
    argv = json.loads(m.group(1))
    target = next(a for a in argv if ":" in a and not a.startswith("-"))
    return target, "--factory" in argv


def _run(rail, build: bool, tmp_path: Path, **env_extra) -> subprocess.CompletedProcess:
    target, factory = _entrypoint(rail)
    env = {k: v for k, v in os.environ.items() if k not in ("LOG_LEVEL", "PLATFORM_STANDALONE")}
    prefix = str(rail.data.get("env_prefix") or "")
    # Each rail's mutable state goes to a throwaway dir, never into the tree.
    env.update({f"{prefix}DATA_DIR": str(tmp_path / "data"),
                "PYTHONPATH": str(rail.package_dir.parent),
                "PYTHONDONTWRITEBYTECODE": "1", **env_extra})
    return subprocess.run(
        [sys.executable, "-c", PROBE, target, "1" if factory else "0",
         str(rail.data.get("package")), "1" if build else "0"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("rail_id", RAILS)
def test_the_rail_carries_logsetup_as_a_tier_one_file(template, rail_id):
    rail = _rail(template, rail_id)
    assert rail.logsetup_path() == rail.package_dir / "logsetup.py"
    assert ("logsetup.py", rail.package_dir / "logsetup.py") in template.invariants(rail), \
        "rail_template.invariants() no longer covers logsetup.py, so nothing checks the copies"
    have = (rail.package_dir / "logsetup.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == template.render("logsetup.py", rail)


@pytest.mark.parametrize("rail_id", RAILS)
def test_the_deployed_factory_makes_info_lines_reach_stderr(template, rail_id, tmp_path):
    rail = _rail(template, rail_id)
    p = _run(rail, build=True, tmp_path=tmp_path)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "RAIL-INFO-PROBE" in p.stderr, f"{rail_id}: INFO still dropped:\n{p.stderr[-1500:]}"
    # The default level is INFO: debug stays quiet unless LOG_LEVEL asks for it.
    assert "RAIL-DEBUG-PROBE" not in p.stderr


def test_without_the_factory_uvicorns_config_drops_the_line(template, tmp_path):
    """The control. If this ever sees the line, the probe above proves nothing."""
    rail = _rail(template, _first_present())
    p = _run(rail, build=False, tmp_path=tmp_path)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "RAIL-INFO-PROBE" not in p.stderr


def test_log_level_reaches_a_real_rail(template, tmp_path):
    rail = _rail(template, _first_present())
    p = _run(rail, build=True, tmp_path=tmp_path, LOG_LEVEL="debug")
    assert "RAIL-DEBUG-PROBE" in p.stderr, p.stderr[-1500:]


# --- the template itself, loaded straight from tools/rail_templates ---------------------------


@pytest.fixture()
def logsetup(monkeypatch):
    """The template as a module of a throwaway package, so it configures a logger nobody else
    uses. Its handlers are removed afterwards so no test leaks one into the next."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    pkg = f"lsprobe{uuid.uuid4().hex[:8]}"
    loader = importlib.machinery.SourceFileLoader(f"{pkg}.logsetup", str(TEMPLATE))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    yield mod
    logger = logging.getLogger(pkg)
    for h in list(logger.handlers):
        logger.removeHandler(h)


def _marked(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, "platform_rail_stderr", False)]


def test_it_configures_its_own_package_logger(logsetup):
    logger = logsetup.configure()
    assert logger.name == logsetup.PACKAGE == logsetup.__name__.rpartition(".")[0]
    assert len(_marked(logger)) == 1 and logger.level == logging.INFO


def test_calling_it_again_never_adds_a_second_handler(logsetup):
    for _ in range(3):
        logger = logsetup.configure()
    assert len(_marked(logger)) == 1


def test_it_writes_to_stderr_as_it_is_when_the_record_is_emitted(logsetup, monkeypatch):
    """Bound at emit time, not construction: a stream swapped in later still gets the line."""
    logsetup.configure()
    swapped = io.StringIO()
    monkeypatch.setattr(sys, "stderr", swapped)
    logging.getLogger(f"{logsetup.PACKAGE}.api").info("seed ingest: %s", {"done": 3})
    assert "seed ingest: {'done': 3}" in swapped.getvalue()
    assert f"INFO {logsetup.PACKAGE}.api:" in swapped.getvalue()


@pytest.mark.parametrize("raw,level", [("DEBUG", logging.DEBUG), ("warning", logging.WARNING),
                                       (" error ", logging.ERROR), ("15", 15), ("", logging.INFO)])
def test_log_level_is_honoured(logsetup, monkeypatch, raw, level):
    monkeypatch.setenv("LOG_LEVEL", raw)
    assert logsetup.configure().level == level


def test_a_log_level_that_is_not_one_says_so_and_uses_info(logsetup, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    swapped = io.StringIO()
    monkeypatch.setattr(sys, "stderr", swapped)
    assert logsetup.configure().level == logging.INFO
    assert "LOG_LEVEL='LOUD' is not a logging level" in swapped.getvalue()


def test_records_still_propagate_so_caplog_and_root_handlers_see_them(logsetup, caplog):
    logsetup.configure()
    with caplog.at_level(logging.INFO):
        logging.getLogger(f"{logsetup.PACKAGE}.ingest").info("ingested %s: %d chunks", "kb", 4)
    assert "ingested kb: 4 chunks" in caplog.text
