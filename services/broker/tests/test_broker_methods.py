"""Broker's methods written outside broker.py are Broker's own, and read app.broker's names.

broker.py runs app/broker_cli.py in its own namespace instead of importing it, and makes every
method of the `class Broker` written there one of Broker's (broker.py, "where Broker's methods
live"). Imported, the file would hold its own binding of every name it reads, and a test that
points app.broker's name at a fixture (`monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.1)`)
would pass while the method read the real value. These hold what running it there asks: every
file of methods runs, once; each method is Broker's own and reads app.broker's names; a file
imports nothing and binds no name broker.py's class binds, and the loader refuses one that does.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import time
from pathlib import Path

import pytest

from app import broker as broker_mod
from app.broker import Broker

APP = Path(broker_mod.__file__).resolve().parent


def _classes(path: Path) -> list[ast.ClassDef]:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    return [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Broker"]


def _bound(body: list[ast.stmt]) -> set[str]:
    """The names a class body binds."""
    out: set[str] = set()
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def _method_files() -> list[Path]:
    """Every file in app/ but broker.py that writes a top-level `class Broker`."""
    return [p.resolve() for p in sorted(APP.glob("*.py"))
            if p.name != "broker.py" and _classes(p)]


def test_every_file_of_methods_runs_once():
    found = _method_files()
    assert [p.name for p in found] == ["broker_cli.py"], found     # witness: the scan sees it
    ran = [Path(p).resolve() for p in broker_mod._METHOD_FILES]
    assert sorted(ran) == found, f"files that write Broker's methods but never ran: {found}, ran {ran}"
    assert len(set(ran)) == len(ran), f"a file of methods ran twice: {ran}"


def test_each_method_written_there_is_broker_s_own_and_reads_app_broker_s_names():
    seen = 0
    for path in map(Path, broker_mod._METHOD_FILES):
        (cls,) = _classes(path)
        for name in sorted(_bound(cls.body)):
            assert name in vars(Broker), f"{path.name}: {name} is not one of Broker's own"
            obj = vars(Broker)[name]
            fn = inspect.unwrap(getattr(obj, "__func__", obj))   # a staticmethod; a wrapped one
            assert fn.__globals__ is vars(broker_mod), f"{name} reads a namespace of its own"
            assert fn.__module__ == "app.broker", name
            assert fn.__qualname__ == f"Broker.{name}", fn.__qualname__
            assert Path(fn.__code__.co_filename).resolve() == path.resolve(), name
            seen += 1
    assert seen >= 15, seen                                        # witness: it read them
    assert Broker.__mro__ == (Broker, object)


def test_a_file_of_methods_imports_nothing_and_binds_nothing_broker_py_s_class_binds():
    (own,) = _classes(APP / "broker.py")
    for path in map(Path, broker_mod._METHOD_FILES):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Broker"]
        future = [n for n in tree.body
                  if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
        imports = [f"{path.name}:{n.lineno}" for n in [*tree.body, *cls.body]
                   if isinstance(n, (ast.Import, ast.ImportFrom)) and n not in future]
        assert imports == [], f"an import runs into a namespace no method reads: {imports}"
        rest = [type(n).__name__ for n in tree.body if n is not cls and n not in future
                and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        assert rest == [], f"{path.name}: statements outside its class run in vain: {rest}"
        both = sorted(_bound(cls.body) & _bound(own.body))
        assert both == [], f"{path.name} and broker.py both bind {both}"


def _host_and_file(tmp_path: Path, monkeypatch, source: str) -> None:
    """Point the loader at a file of methods in tmp_path (it reads beside app.broker's file)."""
    (tmp_path / "methods.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(broker_mod, "__file__", str(tmp_path / "broker.py"))
    monkeypatch.setattr(broker_mod, "_METHOD_FILES", [])


def test_a_file_that_binds_a_name_the_class_has_is_refused_and_changes_nothing(
        tmp_path, monkeypatch):
    _host_and_file(tmp_path, monkeypatch, "class Broker:\n"
                                          "    def chat(self):\n        return 'replaced'\n"
                                          "    def extra(self):\n        return 'extra'\n")

    class Host:
        def chat(self):
            return "own"

    with pytest.raises(TypeError, match="methods.py writes chat, which Host has already"):
        broker_mod._methods_from("methods.py")(Host)
    assert Host().chat() == "own" and not hasattr(Host, "extra")
    assert broker_mod._METHOD_FILES == []


def test_a_method_from_a_file_reads_app_broker_s_names_as_they_are_when_called(
        tmp_path, monkeypatch):
    """The control beside the refusal: a file that binds nothing the class has is taken whole,
    and its method reads app.broker's names at call time, a patch made after the load included."""
    _host_and_file(tmp_path, monkeypatch, "class Broker:\n"
                                          "    def tick(self):\n        return _GONE_TICK_S\n")

    class Host:
        pass

    broker_mod._methods_from("methods.py")(Host)
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.125)
    assert Host().tick() == 0.125
    assert broker_mod._METHOD_FILES == [tmp_path / "methods.py"]


def test_a_patch_of_app_broker_s_history_caps_reaches_the_method_broker_cli_py_writes(
        monkeypatch):
    """_record_cli_outcome is written in broker_cli.py and bounds its maps by app.broker's
    _CLI_HISTORY_REQUESTERS and _CLI_HISTORY_ROLES: patched there, it obeys the patch."""
    assert Broker._record_cli_outcome.__code__.co_filename.endswith("broker_cli.py")
    monkeypatch.setattr(broker_mod, "_CLI_HISTORY_REQUESTERS", 1)
    monkeypatch.setattr(broker_mod, "_CLI_HISTORY_ROLES", 1)
    b = Broker.__new__(Broker)
    b._cli_last_by_role, b._cli_last_by_requester = {}, {}
    b._record_cli_outcome("@a", {"ok": True, "requester": "one"})
    b._record_cli_outcome("@b", {"ok": True, "requester": "one"})
    assert b._cli_last_by_requester == {"one": {"@b": {"ok": True, "requester": "one"}}}
    b._record_cli_outcome("@b", {"ok": False, "requester": "two"})
    assert list(b._cli_last_by_requester) == ["two"]


async def test_a_patch_of_app_broker_s_tick_bounds_the_hang_up_question_in_broker_cli_py(
        monkeypatch):
    """_hung_up is written in broker_cli.py and bounds its question by app.broker's _GONE_TICK_S
    (2 s): patched to 0.05 s there, a caller that never answers is "no" well inside a second."""
    assert Broker._hung_up.__code__.co_filename.endswith("broker_cli.py")
    monkeypatch.setattr(broker_mod, "_GONE_TICK_S", 0.05)

    async def never() -> bool:
        await asyncio.sleep(30)
        return True

    b = Broker.__new__(Broker)
    started = time.monotonic()
    assert await b._hung_up(never) is False
    assert time.monotonic() - started < 1.0
