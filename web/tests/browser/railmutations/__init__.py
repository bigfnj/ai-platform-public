"""mutate_railcheck.py's mutations, one module per rail, beside railchecks/ and split the same way.

railmutations/<rail>.py (with `-` spelled `_`) holds MUTATIONS for the checks railchecks/<rail>.py
defines, and goes wherever that rail goes: the public snapshot withholds a withheld rail's module
here as it does there (tools/publish.json). mutate_railcheck.py's header says how they are found.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Mutation:
    label: str
    rail: str
    file: str          # relative to the rail's frontend/src ("web/..." is web-core, from the repo)
    old: str
    new: str
    expect: str        # the railcheck check that must FAIL
    # Further (old, new) edits of the same file, for a plant that needs more than one. Each is
    # applied after the ones before it, and each must match exactly once.
    also: tuple[tuple[str, str], ...] = ()
