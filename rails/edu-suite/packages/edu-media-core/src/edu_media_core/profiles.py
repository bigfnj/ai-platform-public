"""Audience / task profiles.

A ``Profile`` bundles everything that makes one es_MX audience+task distinct: the system
prompt, model options, the expected output keys, and an optional voice reference (the home
for per-learner voices later). This generalizes the prompts that used to be scattered as
loose module constants into one named, enumerable registry.

Apps register their profile at import (right next to where the prompt is authored, so core
never imports the apps) and callers look them up by key:

    from edu_media_core import profiles
    p = profiles.get("cvc_phonics")
    core.translate_cached(system_prompt=p.system_prompt, options=p.options,
                          required_keys=p.required_keys, ...)

`all_profiles()` lists whatever has been imported — a catalog for a future audience picker.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    system_prompt: str
    options: dict = field(default_factory=dict)
    required_keys: tuple[str, ...] = ()
    voice: str | None = None  # optional per-audience voice reference (future per-learner voices)


_REGISTRY: dict[str, Profile] = {}


def register(p: Profile) -> Profile:
    """Register a profile and return it (so a module can `X = register(Profile(...))`)."""
    _REGISTRY[p.key] = p
    return p


def get(key: str) -> Profile:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown profile {key!r}; known: {sorted(_REGISTRY)}") from None


def all_profiles() -> list[Profile]:
    return list(_REGISTRY.values())
