"""How much a whole-tree scan must find, in the tree it runs in: this private checkout, or a public
snapshot tools/publish.py built from it.

A test that checks every file of a kind (every mutation harness, every node test file) passes in
silence when its scan finds nothing: a git call that failed, a walk that pruned the wrong
directory. So each one states a floor, "a scan that found a handful saw nothing". Those floors
were counted in the private tree, and a snapshot holds less of it by design, since publish.py
withholds rails and paths. On 2026-10-19 the public snapshot's core suite failed four tests on 78
mutation harnesses against a floor of 100 (62 Python ones against 80) and two on 26 node test
files against 30, with every file there and every one of them checked.

A floor is therefore a pair. `private` is the private tree's, as it was. `published` is a
snapshot's, and it answers to what publish withholds: in the private tree `reach()` holds the
whole scan to `private` AND the share of it publish.py would ship (its own `select()`, the rule
every snapshot is built by) to `published`. Withholding a rail or a path that would take a
snapshot under its floor fails here, in the private tree, before any snapshot is built. In a
snapshot, `published` is the floor.

Which tree this is: tools/publish.py is withheld from every snapshot (publish.json
paths.exclude) and tracked in every private checkout. Asking git cannot tell them apart: CI's dry
run checks the snapshot as a plain directory, and a public clone is a checkout of its own.

Not a test file; test_tree_floors.py is.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

REPO = Path(__file__).resolve().parents[2]
#: conftest.load_tool's module name for publish.py, so a test and this share one loaded copy.
PUBLISH_MODULE = "_tools_publish"


class Floor(NamedTuple):
    """The least a scan may find: `private` in the private tree, `published` in a snapshot and,
    in the private tree, in the share of the scan that publish.py ships."""
    what: str         # what the scan counts, as the failure message names it
    private: int
    published: int


def is_snapshot(repo: Path = REPO) -> bool:
    """True in a tree publish.py built: the tool withholds itself from every snapshot."""
    return not (repo / "tools" / "publish.py").is_file()


def own(floor: Floor, repo: Path = REPO) -> int:
    """The floor that applies to the scan of the tree at `repo`."""
    return floor.published if is_snapshot(repo) else floor.private


def published_share() -> Callable[[list[str]], list[str]]:
    """paths -> the ones publish.py's own select() puts in a snapshot. The private tree only."""
    tool = REPO / "tools" / "publish.py"
    if not tool.is_file():
        raise RuntimeError(f"{tool} is not here: a snapshot has no publish rules to ask")
    pub = sys.modules.get(PUBLISH_MODULE)
    if pub is None:
        spec = importlib.util.spec_from_file_location(PUBLISH_MODULE, tool)
        pub = importlib.util.module_from_spec(spec)
        sys.modules[PUBLISH_MODULE] = pub
        spec.loader.exec_module(pub)
    cfg = pub.load_config()
    return lambda paths: pub.select(cfg, list(paths))[0]


def reach(found: Iterable[str], floor: Floor, *, repo: Path = REPO,
          shipped: Callable[[list[str]], list[str]] | None = None) -> None:
    """Raise AssertionError unless `found`, a scan of the tree at `repo`, reaches that tree's
    floor and, in the private tree, the share of it publish.py ships reaches a snapshot's.
    `shipped` stands in for publish's selection (published_share() when not given)."""
    found = list(found)
    if is_snapshot(repo):
        if len(found) < floor.published:
            raise AssertionError(
                f"{len(found)} {floor.what} in this snapshot, under its floor of "
                f"{floor.published}: a scan that found so few saw nothing. {found}")
        return
    if len(found) < floor.private:
        raise AssertionError(
            f"{len(found)} {floor.what} in the private tree, under its floor of "
            f"{floor.private}: a scan that found so few saw nothing. {found}")
    share = (shipped or published_share())(found)
    if len(share) < floor.published:
        raise AssertionError(
            f"publish.py ships {len(share)} of these {len(found)} {floor.what}, under a "
            f"snapshot's floor of {floor.published}, so the public snapshot would fail this "
            f"check. Withhold less of them, or lower the snapshot's floor where the test "
            f"states it and say why. {share}")
