from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent.parent / "data" / "words.json"

VOWEL_COLORS: dict[str, dict[str, str]] = {
    "a": {"primary": "#3B82F6", "light": "#EFF6FF", "border": "#BFDBFE"},
    "e": {"primary": "#10B981", "light": "#ECFDF5", "border": "#A7F3D0"},
    "i": {"primary": "#8B5CF6", "light": "#F5F3FF", "border": "#DDD6FE"},
    "o": {"primary": "#F59E0B", "light": "#FFFBEB", "border": "#FDE68A"},
    "u": {"primary": "#EF4444", "light": "#FEF2F2", "border": "#FECACA"},
}


@dataclass
class Word:
    en: str
    es: str
    image_query: str
    worksheet: int
    page: int
    vowel: str
    image_b64: str = field(default="", repr=False)
    audio_en_b64: str = field(default="", repr=False)
    audio_es_b64: str = field(default="", repr=False)
    # File-based alternative to the embedded base64 above: the dashboard writes assets to
    # the bundle (images/, en-audio/, mx-audio/) and sets these relative paths; the
    # standalone CLI keeps using the _b64 fields for a single self-contained HTML.
    image_path: str = field(default="", repr=False)
    audio_en_path: str = field(default="", repr=False)
    audio_es_path: str = field(default="", repr=False)

    @property
    def color(self) -> dict[str, str]:
        return VOWEL_COLORS[self.vowel]

    @property
    def needs_translation(self) -> bool:
        return not self.es or not self.image_query

    @property
    def has_image(self) -> bool:
        return bool(self.image_path or self.image_b64)

    @property
    def has_audio(self) -> bool:
        return bool((self.audio_en_path or self.audio_en_b64)
                    and (self.audio_es_path or self.audio_es_b64))


def load_words() -> list[Word]:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return [Word(**{k: v for k, v in entry.items()}) for entry in raw]


def save_words(words: list[Word]) -> None:
    data = [
        {
            "en": w.en,
            "es": w.es,
            "image_query": w.image_query,
            "worksheet": w.worksheet,
            "page": w.page,
            "vowel": w.vowel,
        }
        for w in words
    ]
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def group_by_worksheet(words: list[Word]) -> dict[int, list[Word]]:
    groups: dict[int, list[Word]] = {}
    for w in words:
        groups.setdefault(w.worksheet, []).append(w)
    return dict(sorted(groups.items()))


def group_by_page(words: list[Word]) -> dict[tuple[int, int], list[Word]]:
    groups: dict[tuple[int, int], list[Word]] = {}
    for w in words:
        key = (w.worksheet, w.page)
        groups.setdefault(key, []).append(w)
    return groups
