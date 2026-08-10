"""First-run hydration of the markdown corpus.

The recipe corpus (``<Category>/<Title>.md`` cards) is the source of truth, but it lives in
a mounted named volume, not the image. So a fresh deploy would come up empty. To make a clean
``git clone`` + ``docker compose up`` self-populate, we bundle a committed seed corpus
(``rails/recipe-book/seed/recipes/``) into the image and copy it into the volume the first time
the volume is empty. An existing corpus (a real install with the owner's edits) is never touched.
"""
from __future__ import annotations

import os
import shutil
import tarfile

from recipe_book import config


def hydrate_if_empty() -> int:
    """If the corpus dir has no cards, copy the bundled seed corpus into it.

    Returns the number of cards copied (0 if the corpus already has cards, or no seed is present)."""
    dst = config.RECIPES_DIR
    src = config.SEED_RECIPES_DIR
    if dst.exists() and any(dst.rglob("*.md")):
        return 0  # real install with a corpus already — leave it alone
    if not (src.exists() and any(src.rglob("*.md"))):
        return 0  # no seed bundled (e.g. running from a checkout without the seed dir)
    n = 0
    for p in src.rglob("*.md"):
        out = dst / p.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        n += 1
    return n


def hydrate_icons() -> int:
    """If ICONS_DIR has no PNGs yet, unpack the bundled seed-icon archive into it. Returns the
    number of icons extracted (0 if icons already present, or no archive is bundled). Extracts
    by basename only, so a crafted archive can't write outside ICONS_DIR."""
    dst = config.ICONS_DIR
    if dst.exists() and any(dst.glob("*.png")):
        return 0  # a real install already has icons — leave them
    arc = config.SEED_ICONS_ARCHIVE
    if not arc.exists():
        return 0  # no seed archive bundled (running from a checkout without it)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(arc, "r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.basename(m.name)
            if not m.isfile() or not name.lower().endswith(".png"):
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            (dst / name).write_bytes(f.read())
            n += 1
    return n
