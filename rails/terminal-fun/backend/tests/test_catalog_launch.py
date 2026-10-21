"""Every catalog item has exactly ONE launch source.

A tunable toy launches from tunables.TUNABLES[id].base plus its validated params; anything else
from its catalog argv; a web toy from neither (it is an iframe, not a PTY). The seven tunable
items also carried a catalog argv that main.launch() never read (recorded 2026-09-11), and two
had drifted from what actually ran: cmatrix "-ab" in the catalog, a bare "cmatrix" base.
"""
from __future__ import annotations

from terminal_fun_app.catalog import ITEMS
from terminal_fun_app.tunables import TUNABLES, build_launch, defaults, is_tunable


def test_tunable_items_carry_no_dead_argv():
    assert [i.id for i in ITEMS if is_tunable(i.id) and i.argv] == []


def test_every_other_terminal_item_has_an_argv():
    """Without one, PtySession.start() would index an empty list and the tile would fail."""
    assert [i.id for i in ITEMS if not i.web and not is_tunable(i.id) and not i.argv] == []


def test_web_toys_have_no_launch_line():
    assert [i.id for i in ITEMS if i.web and (i.argv or is_tunable(i.id))] == []


def test_every_tunable_is_in_the_catalog_and_builds_a_launch_line():
    catalog = {i.id for i in ITEMS}
    for tid in TUNABLES:
        assert tid in catalog, f"tunable {tid!r} has no catalog tile"
        argv, _env = build_launch(tid, defaults(tid))
        assert argv and argv[: len(TUNABLES[tid].base)] == list(TUNABLES[tid].base)
