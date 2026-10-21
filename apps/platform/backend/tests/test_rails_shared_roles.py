"""A Rails-tab slot says which other installed rails its role also feeds.

Repointing a slot rewrites its ROLE, and some slots name roles other rails use too: iep-goals'
writer is @iep (shared with IEP Present Levels and EDU-Suite's vocabulary fallback) and its
translator @edu (shared with EDU-Suite's content).
A change made on iep-goals moved those rails as well, while the tab said it repointed only that
rail. `shared_with` carries the truth for the tab to show: the labels of the OTHER installed
rails on the same role, [] when there are none.

Written against RAIL_MODEL_SLOTS itself rather than a hand-picked pair, because the public
snapshot de-wires the withheld rails out of that catalog and a fixed pair would not exist there.
"""
from __future__ import annotations

import pytest

from platform_gateway_app.rails_models import RAIL_MODEL_SLOTS, build_rails_view


def _users() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rid, slots in RAIL_MODEL_SLOTS.items():
        for s in slots:
            out.setdefault(s["role"], set()).add(rid)
    return out


def _view(enabled):
    rails = build_rails_view([], set(enabled))
    labels = {r["id"]: r["label"] for r in rails}
    return {(r["id"], s["slot"]): (s["role"], s["shared_with"])
            for r in rails for s in r["slots"]}, labels


def _shared_roles() -> dict[str, set[str]]:
    shared = {role: ids for role, ids in _users().items() if len(ids) > 1}
    if not shared:
        pytest.skip("no two rails share a role in this tree's catalog, so there is nothing to "
                    "report; the private fleet has some (iep-goals with iep and edu-suite)")
    return shared


def test_every_shared_role_names_the_other_rails_both_ways():
    shared = _shared_roles()
    view, labels = _view(set(RAIL_MODEL_SLOTS))
    for (rid, _slot), (role, shared_with) in view.items():
        expect = sorted({labels[o] for o in shared.get(role, set()) if o != rid})
        assert shared_with == expect, (rid, role, shared_with)


def test_a_rail_that_is_not_installed_is_not_named():
    role, ids = next(iter(_shared_roles().items()))
    only = sorted(ids)[0]
    view, _labels = _view({only})
    rows = [sw for (_rid, _s), (r, sw) in view.items() if r == role]
    assert rows, f"the installed rail draws no slot on {role!r}, so nothing was checked"
    assert all(sw == [] for sw in rows)


def test_an_unshared_slot_says_so():
    view, _labels = _view(set(RAIL_MODEL_SLOTS))
    lone = [sw for (_rid, _s), (role, sw) in view.items() if len(_users()[role]) == 1]
    assert lone, "every slot shares a role; the check would pass on anything"
    assert all(sw == [] for sw in lone)


@pytest.mark.skipif("iep-goals" not in RAIL_MODEL_SLOTS,
                    reason="iep-goals is withheld from the public snapshot")
def test_iep_goals_names_the_rails_it_shares_with():
    view, _labels = _view({"iep-goals", "iep", "edu-suite"})
    assert view[("iep-goals", "draft")][1] == ["EDU-Suite", "IEP Present Levels"]
    assert view[("iep-goals", "translate")][1] == ["EDU-Suite"]
    # EDU-Suite's TeachTown vocabulary fallback is on @iep, beside its content slot on @edu.
    assert view[("edu-suite", "vocab-fallback")][1] == ["IEP Goals", "IEP Present Levels"]
    assert view[("edu-suite", "content")][1] == ["IEP Goals"]
