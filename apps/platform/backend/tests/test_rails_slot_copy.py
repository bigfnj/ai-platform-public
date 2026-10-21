"""The admin Rails tab prints each slot's label and description as written: they are UI copy.

Two of them read "course content -- the slides" and "optional -- skip it", an ASCII double hyphen
standing in for punctuation in text an admin reads, beside descriptions that use real punctuation.
Every slot in the catalog is checked, so the next one fails here, where it is written, rather than
on the screen of the admin who reads it.
"""
from __future__ import annotations

from platform_gateway_app.rails_models import RAIL_MODEL_SLOTS


def test_no_slot_copy_uses_an_ascii_double_hyphen():
    checked = 0
    bad = []
    for rid, slots in RAIL_MODEL_SLOTS.items():
        for s in slots:
            for key in ("label", "description"):
                text = s.get(key) or ""
                checked += 1
                if "--" in text:
                    bad.append(f"{rid}/{s['slot']} {key}: {text}")
    # Witness: the catalog was read. Even the public snapshot, which withholds rails, keeps more
    # than five slots.
    assert checked > 10, f"only {checked} strings checked"
    assert bad == []
