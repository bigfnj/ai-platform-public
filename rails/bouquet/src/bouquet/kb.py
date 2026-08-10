"""The flower knowledge base — the rail's read-only corpus.

Loads the committed Markdown profiles (one per flower), the cross-cutting
reference docs, and the per-image attribution manifest, and indexes them for
lookup by the free-text names a vision model returns ("garden rose", "peruvian
lily", "mum"). Everything here is read-only; nothing is written back.

Loaded once and cached (the corpus is static for the life of the process).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from bouquet import config

# Free-text names a vision model tends to emit → canonical profile slug. Covers
# the common cultivar / trade / colloquial names and the classic look-alikes the
# playbook warns about. Matched as whole-word substrings, longest alias first.
_ALIASES: dict[str, str] = {
    "garden rose": "rose", "spray rose": "rose", "hybrid tea": "rose",
    "david austin": "rose", "cabbage rose": "rose",
    "mum": "chrysanthemum", "chrysanth": "chrysanthemum",
    "peruvian lily": "alstroemeria", "inca lily": "alstroemeria",
    "gypsophila": "babys-breath", "baby breath": "babys-breath",
    "babys breath": "babys-breath", "gyp": "babys-breath",
    "parrot tulip": "tulip", "double tulip": "tulip",
    "larkspur": "delphinium",
    "pincushion": "scabiosa", "pincushion flower": "scabiosa",
    "bridal wreath": "spirea", "spiraea": "spirea",
    "narcissus": "daffodil", "paperwhite": "daffodil", "jonquil": "daffodil",
    "eustoma": "lisianthus", "texas bluebell": "lisianthus",
    "phalaenopsis": "orchid", "dendrobium": "orchid", "cymbidium": "orchid",
    "moth orchid": "orchid",
    "seeded eucalyptus": "eucalyptus", "silver dollar": "eucalyptus",
    "italian ruscus": "ruscus",
    "gerbera daisy": "gerbera", "gerber daisy": "gerbera",
    "shasta daisy": "daisy", "marguerite": "daisy",
    "calla": "calla-lily", "arum lily": "calla-lily",
    "sweet pea": "sweet-pea",
    "lily of the valley": "lily-of-the-valley", "muguet": "lily-of-the-valley",
    "bells of ireland": "bells-of-ireland", "molucella": "bells-of-ireland",
    "wax flower": "wax-flower", "waxflower": "wax-flower",
    "bird of paradise": "bird-of-paradise", "strelitzia": "bird-of-paradise",
    "leatherleaf": "leatherleaf-fern", "leather leaf": "leatherleaf-fern",
    "fern": "leatherleaf-fern",  # the only fern profiled; vision usually says just "fern"
    "peruvian": "alstroemeria",
    "sunflower": "sunflower", "cockscomb": "celosia",
    "amaranth": "amaranthus", "love-lies-bleeding": "amaranthus",
    "statice": "statice", "limonium": "statice",
}

# Section headings we surface individually (matched case-insensitively on the
# "## <heading>" line). The rest of the profile is still available as raw markdown.
_SECTION_ORDER = [
    "Botanical identity", "Native region & origin", "Visual identification",
    "History & cultural background", "Symbolism & meaning",
    "Cultural meanings across the world", "Typical pairings",
    "Occasions & events", "Seasonality & availability", "Quick facts",
]


@dataclass
class Flower:
    slug: str
    title: str
    oneliner: str
    common_names: list[str]
    sections: dict[str, str]
    markdown: str
    images: list[dict] = field(default_factory=list)  # {file,url,descriptor,author,license,...}

    def summary(self) -> dict:
        """Compact card shape for the library grid."""
        return {
            "slug": self.slug,
            "title": self.title,
            "oneliner": self.oneliner,
            "thumb": self.images[0]["url"] if self.images else None,
            "image_count": len(self.images),
        }

    def detail(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "oneliner": self.oneliner,
            "common_names": self.common_names,
            "sections": self.sections,
            "markdown": self.markdown,
            "images": self.images,
        }


def _slugify_word(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace/punctuation to single spaces — for matching."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _parse_sections(md: str) -> dict[str, str]:
    """Split a profile into {heading: body} on ``## `` headings (body trimmed,
    without the reference-images block, which is handled separately)."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    # Drop the machine-managed reference-images section (served via /images).
    out.pop("Reference images", None)
    # Canonicalize the handful of headings we key on (tolerate the "(for reading a
    # photo)" suffix etc.) so callers can look them up by the short name.
    canon: dict[str, str] = {}
    for heading, body in out.items():
        short = next((s for s in _SECTION_ORDER if heading.lower().startswith(s.lower())), heading)
        canon[short] = body
    return canon


def _parse_common_names(sections: dict[str, str]) -> list[str]:
    bot = sections.get("Botanical identity", "")
    m = re.search(r"common name\(s\):\*\*\s*(.+)", bot, re.IGNORECASE)
    if not m:
        return []
    raw = m.group(1)
    # "Rose (garden rose, spray rose, hybrid tea)" → each name/paren item.
    parts = re.split(r"[(),/]", raw)
    return [p.strip() for p in parts if p.strip()]


@lru_cache(maxsize=1)
def _manifest_by_flower() -> dict[str, list[dict]]:
    path = config.KB_DIR / "images" / "image-manifest.json"
    if not path.is_file():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    by: dict[str, list[dict]] = {}
    for e in entries:
        flower = e.get("flower")
        rel = e.get("file", "")
        fname = rel.rsplit("/", 1)[-1]
        if not flower or not fname:
            continue
        by.setdefault(flower, []).append({
            "file": fname,
            "url": f"/bouquet/api/flowers/{flower}/images/{fname}",
            "descriptor": (e.get("descriptor") or "").split("\n")[0].strip(),
            "author": e.get("author"),
            "license": e.get("license"),
            "license_url": e.get("license_url"),
            "source_page": e.get("source_page"),
        })
    for imgs in by.values():
        imgs.sort(key=lambda i: i["file"])
    return by


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Flower], list[tuple[str, str]]]:
    """Load every profile and build the alias index. Returns (by_slug, index)
    where index is a list of (alias, slug) sorted by alias length descending so a
    longer, more specific alias wins over a shorter substring."""
    flowers_dir = config.KB_DIR / "flowers"
    manifest = _manifest_by_flower()
    by_slug: dict[str, Flower] = {}
    alias_map: dict[str, str] = {}

    for path in sorted(flowers_dir.glob("*.md")):
        if path.stem.startswith("_"):  # _TEMPLATE.md and friends
            continue
        slug = path.stem
        md = path.read_text(encoding="utf-8")
        title_m = re.search(r"^#\s+(.+)$", md, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else slug
        quote = re.findall(r"^>\s?(.*)$", md, re.MULTILINE)
        oneliner = " ".join(q.strip() for q in quote).strip()
        sections = _parse_sections(md)
        common = _parse_common_names(sections)
        flower = Flower(
            slug=slug, title=title, oneliner=oneliner, common_names=common,
            sections=sections, markdown=md.strip(), images=manifest.get(slug, []),
        )
        by_slug[slug] = flower

        # Aliases from the profile itself: slug, de-hyphenated slug, title, common names.
        for a in {slug, slug.replace("-", " "), title, *common}:
            alias_map.setdefault(_normalize(a), slug)

    # Hand-curated aliases override/extend (only if they point at a real profile).
    for alias, slug in _ALIASES.items():
        if slug in by_slug:
            alias_map[_normalize(alias)] = slug

    index = sorted(alias_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    return by_slug, index


def all_flowers() -> list[Flower]:
    by_slug, _ = _load()
    return [by_slug[s] for s in sorted(by_slug)]


def get_flower(slug: str) -> Flower | None:
    return _load()[0].get(slug)


def resolve(name: str) -> str | None:
    """Map a free-text flower name to a profile slug, or None if unprofiled.

    Tries an exact alias hit first, then the longest alias that appears as a
    whole word inside the name (so "coral garden rose" → rose, "peruvian lily" →
    alstroemeria), then a singularized retry ("roses" → rose)."""
    by_slug, index = _load()
    q = _normalize(name)
    if not q:
        return None
    exact = dict(index)
    if q in exact:
        return exact[q]
    padded = f" {q} "
    for alias, slug in index:  # already longest-first
        if f" {alias} " in padded:
            return slug
    singular = re.sub(r"s\b", "", q)
    if singular != q and singular in exact:
        return exact[singular]
    return None


# --- cross-cutting reference docs ------------------------------------------

_REFERENCES = {
    "color-symbolism": "Color Symbolism",
    "occasions-and-events": "Occasions & Events",
    "bouquet-types": "Bouquet Types & Design Roles",
    "floriography-and-history": "Floriography & History",
}


def list_references() -> list[dict]:
    out = []
    for slug, label in _REFERENCES.items():
        if (config.KB_DIR / "reference" / f"{slug}.md").is_file():
            out.append({"slug": slug, "label": label})
    return out


def get_reference(slug: str) -> str | None:
    if slug not in _REFERENCES:
        return None
    path = config.KB_DIR / "reference" / f"{slug}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def reference_excerpt(slug: str, limit: int = 6000) -> str:
    """A length-bounded reference body for use as LLM context."""
    md = get_reference(slug) or ""
    return md[:limit]


def image_file(slug: str, filename: str):
    """Absolute path to a reference image, or None. Guards against traversal:
    the filename must be a bare name inside the flower's own image folder."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    if get_flower(slug) is None:
        return None
    path = config.KB_DIR / "images" / slug / filename
    return path if path.is_file() else None
