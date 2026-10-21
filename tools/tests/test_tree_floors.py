"""tree_floors: a whole-tree scan is held to the floor of the tree it runs in, and in the private
tree the share of it publish.py ships is held to a snapshot's floor as well.

Each rule on a tree of its own (a directory with or without tools/publish.py) and a stand-in for
publish's selection; the last test asks the real one. The scans that use these floors show them
failing on their own files cut below the floor: test_mutation_harness_cli.py and
test_node_tests_reached.py.
"""
from __future__ import annotations

import pytest

import tree_floors

F = tree_floors.Floor("widgets", private=5, published=3)
SEVEN = ["a", "b", "c", "d", "e", "f", "g"]


def every(paths):
    return list(paths)


def tree(tmp_path, private: bool):
    """A tree with tools/ in it; with tools/publish.py when it stands for the private one."""
    tool = tmp_path / "tools" / "publish.py"
    tool.parent.mkdir(parents=True)
    if private:
        tool.write_text("", encoding="utf-8")
    assert tool.is_file() is private
    return tmp_path


def test_publish_py_tells_the_private_tree_from_a_snapshot(tmp_path):
    snap = tree(tmp_path / "snap", private=False)
    priv = tree(tmp_path / "priv", private=True)
    assert tree_floors.is_snapshot(snap) and not tree_floors.is_snapshot(priv)
    assert (tree_floors.own(F, snap), tree_floors.own(F, priv)) == (3, 5)


def test_the_private_tree_is_held_to_its_own_floor(tmp_path):
    repo = tree(tmp_path, private=True)
    tree_floors.reach(SEVEN[:5], F, repo=repo, shipped=every)            # at the floor
    with pytest.raises(AssertionError, match="4 widgets in the private tree, under its floor of 5"):
        tree_floors.reach(SEVEN[:4], F, repo=repo, shipped=every)


def test_the_private_tree_holds_publishs_share_to_a_snapshots_floor(tmp_path):
    repo = tree(tmp_path, private=True)
    tree_floors.reach(SEVEN, F, repo=repo, shipped=lambda p: p[:3])      # the share at the floor
    with pytest.raises(AssertionError, match="ships 2 of these 7 widgets, under a snapshot's floor of 3"):
        tree_floors.reach(SEVEN, F, repo=repo, shipped=lambda p: p[:2])


def test_a_snapshot_is_held_to_its_own_floor_and_asks_publish_nothing(tmp_path):
    repo = tree(tmp_path, private=False)
    asked = []
    tree_floors.reach(SEVEN[:3], F, repo=repo, shipped=asked.append)    # at the floor
    with pytest.raises(AssertionError, match="2 widgets in this snapshot, under its floor of 3"):
        tree_floors.reach(SEVEN[:2], F, repo=repo, shipped=asked.append)
    assert asked == []


def test_the_share_is_publishs_own_selection():
    if not (tree_floors.REPO / "tools" / "publish.py").is_file():
        pytest.skip("tools/publish.py is withheld from a snapshot, so there is no selection to "
                    "ask here; this runs in the private repo")
    share = tree_floors.published_share()
    # A published rail's file ships; a withheld rail's, publish itself and an excluded test do not.
    asked = ["rails/openmaic/rail.json", "rails/servicenow/rail.json", "tools/publish.py",
             "tools/tests/test_publish.py", "web/tests/poll.test.js"]
    assert share(asked) == ["rails/openmaic/rail.json", "web/tests/poll.test.js"]
