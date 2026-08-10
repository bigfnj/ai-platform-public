"""KB loader + alias resolution — runs fully offline against the seeded corpus."""
from __future__ import annotations

from bouquet import kb


def test_loads_the_full_corpus():
    flowers = kb.all_flowers()
    # 50 real profiles (the _TEMPLATE.md is skipped).
    assert len(flowers) == 50
    slugs = {f.slug for f in flowers}
    assert "_template" not in slugs
    assert {"rose", "tulip", "ranunculus", "eucalyptus"} <= slugs


def test_profile_is_parsed():
    rose = kb.get_flower("rose")
    assert rose is not None
    assert rose.title == "Rose"
    assert rose.oneliner  # the blockquote identity line
    assert "Symbolism & meaning" in rose.sections
    assert "Reference images" not in rose.sections  # stripped; served via /images
    # 4 licensed reference photos, wired to their gateway URLs + attribution.
    assert len(rose.images) == 4
    assert rose.images[0]["url"].startswith("/bouquet/api/flowers/rose/images/")
    assert rose.images[0]["license"]


def test_resolve_aliases():
    assert kb.resolve("rose") == "rose"
    assert kb.resolve("Roses") == "rose"
    assert kb.resolve("coral garden rose") == "rose"       # cultivar + color words
    assert kb.resolve("peruvian lily") == "alstroemeria"   # trade name
    assert kb.resolve("mum") == "chrysanthemum"
    assert kb.resolve("baby's breath") == "babys-breath"
    assert kb.resolve("parrot tulip") == "tulip"
    assert kb.resolve("bridal wreath") == "spirea"
    assert kb.resolve("calla") == "calla-lily"
    assert kb.resolve("fern") == "leatherleaf-fern"        # vision says just "fern"
    assert kb.resolve("leatherleaf fern") == "leatherleaf-fern"


def test_resolve_unprofiled_returns_none():
    assert kb.resolve("plastic flamingo") is None
    assert kb.resolve("") is None


def test_references_present():
    refs = {r["slug"] for r in kb.list_references()}
    assert {"color-symbolism", "occasions-and-events",
            "bouquet-types", "floriography-and-history"} <= refs
    assert "Color" in (kb.get_reference("color-symbolism") or "")
    assert kb.get_reference("nope") is None


def test_image_file_guards_traversal():
    assert kb.image_file("rose", "rose-01.jpg") is not None
    assert kb.image_file("rose", "../../secret") is None
    assert kb.image_file("rose", "nope.jpg") is None
