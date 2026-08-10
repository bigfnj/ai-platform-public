"""SDXL-Turbo cartoon images for CVC words.

The SDXL engine lives in edu_media_core.images; this module maps the Word model
to a subject string and owns the assets/generated/ layout. image_fetcher prefers
that directory over clipart, so generated images automatically win once present.
"""
from __future__ import annotations

from pathlib import Path

from edu_media_core import images as core_images

from .words import Word

GENERATED_DIR = Path(__file__).parent.parent.parent / "assets" / "generated"


def _subject(word: Word) -> str:
    """Human-readable subject for the prompt, derived from the image query."""
    q = (word.image_query or word.en).replace("cartoon", "").replace("clipart", "").strip()
    return q or word.en


def generate_image(word: Word, force: bool = False) -> Path | None:
    """Generate assets/generated/{word}.png. Returns the path, or None on failure."""
    return core_images.generate_image(
        _subject(word), GENERATED_DIR / f"{word.en}.png", force=force
    )


def generate_all(words: list[Word], force: bool = False, verbose: bool = True) -> None:
    """Generate images for all words into assets/generated/."""
    for i, word in enumerate(words, 1):
        out_path = GENERATED_DIR / f"{word.en}.png"
        if out_path.exists() and not force:
            if verbose:
                print(f"  [{i}/{len(words)}] {word.en}: cached")
            continue
        if verbose:
            print(f"  [{i}/{len(words)}] {word.en}: generating '{_subject(word)}'...",
                  end=" ", flush=True)
        result = generate_image(word, force=force)
        if verbose:
            print("ok" if result else "failed")
