"""Model classification.

Two classes matter for the VRAM policy:

- ``embed``  : an embedding model. Light (~1 GB); allowed to stay resident
               alongside one heavy model. Cannot be driven via /api/generate.
- ``heavy``  : a generative model. Subject to the one-heavy-model-at-a-time
               policy — loading one evicts any other heavy model.
"""

from __future__ import annotations

from typing import Literal

ModelClass = Literal["embed", "heavy"]


def is_embedding_model(name: str, embed_hints: list[str]) -> bool:
    """True if the model name looks like an embedding model."""
    lowered = name.lower()
    return any(hint in lowered for hint in embed_hints)


def classify(name: str, embed_hints: list[str]) -> ModelClass:
    return "embed" if is_embedding_model(name, embed_hints) else "heavy"
