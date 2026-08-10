"""Resolve a per-word image: generated → clipart → DDG search → placeholder.

The clipart search + resize primitives live in edu_media_core.images; this
module owns the curated query overrides, the colored placeholder, and the
Word/assets glue.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from edu_media_core import images as core_images

from .words import Word

CLIPART_DIR = Path(__file__).parent.parent.parent / "assets" / "clipart"
GENERATED_DIR = Path(__file__).parent.parent.parent / "assets" / "generated"
_TARGET_SIZE = (300, 300)

# Curated search queries override the LLM-generated ones for best clipart results
_QUERY_OVERRIDES: dict[str, str] = {
    "jab": "fist punch cartoon",
    "lag": "snail slow cartoon",
    "zap": "lightning bolt cartoon",
    "ram": "ram sheep animal cartoon",
    "tan": "sun beach tan cartoon",
    "wax": "candle wax cartoon",
    "pep": "cheerleader energy cartoon",
    "web": "spider web cartoon",
    "vet": "cartoon vet doctor pet",
    "hem": "sewing needle thread cartoon",
    "gel": "hair gel tube cartoon",
    "den": "bear cave den cartoon",
    "rim": "wheel rim cartoon",
    "lid": "jar lid cartoon",
    "zip": "zipper cartoon",
    "wig": "colorful wig cartoon",
    "dip": "chip dip bowl cartoon",
    "fig": "fig fruit cartoon",
    "sob": "crying tears cartoon child",
    "rod": "fishing rod cartoon",
    "cot": "camping cot bed cartoon",
    "jog": "person jogging cartoon",
    "mob": "crowd people cartoon",
    "lop": "lop ear rabbit cartoon",
    "tub": "bathtub cartoon",
    "gum": "bubble gum cartoon",
    "bud": "flower bud cartoon",
    "hut": "small hut house cartoon",
    "mug": "coffee mug cartoon",
    "pun": "joke laugh cartoon child",
}


def _data_uri(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _make_placeholder(word: Word) -> bytes:
    """Generate a simple colored placeholder as PNG bytes."""
    color = word.color["primary"]
    light = word.color["light"]
    img = Image.new("RGB", _TARGET_SIZE, light)
    draw = ImageDraw.Draw(img)

    # Colored border
    draw.rectangle([0, 0, 299, 299], outline=color, width=8)

    # Word text centered — use default font, scale by word length
    try:
        font_size = max(48, 96 - len(word.en) * 8)
        font = ImageFont.truetype("arial.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()

    draw.text((150, 130), word.en.upper(), fill=color, font=font, anchor="mm")
    draw.text((150, 200), word.es or "?", fill="#555555", font=font, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def fetch_image(word: Word, force: bool = False) -> str:
    """
    Return a data URI PNG for the word image.
    Check generated/ first, then clipart/, then search, then placeholder.
    """
    CLIPART_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    generated_path = GENERATED_DIR / f"{word.en}.png"
    clipart_path = CLIPART_DIR / f"{word.en}.png"

    # Prefer AI-generated image if available, then cached clipart
    if generated_path.exists() and not force:
        return _data_uri(generated_path.read_bytes())
    if clipart_path.exists() and not force:
        return _data_uri(clipart_path.read_bytes())

    # Attempt download
    query = _QUERY_OVERRIDES.get(word.en, word.image_query or word.en + " cartoon")
    raw = core_images.search_clipart(query)

    if raw:
        try:
            png = core_images.resize_png(raw, _TARGET_SIZE)
            clipart_path.write_bytes(png)
            return _data_uri(png)
        except Exception:
            pass

    # Placeholder — save so we don't re-fetch on next run
    png = _make_placeholder(word)
    clipart_path.write_bytes(png)
    return _data_uri(png)


def fetch_all(words: list[Word], verbose: bool = True) -> None:
    """Fetch images for all words. Mutates word.image_b64 in-place."""
    for i, word in enumerate(words, 1):
        if verbose:
            status = "generated" if (GENERATED_DIR / f"{word.en}.png").exists() \
                     else "cached" if (CLIPART_DIR / f"{word.en}.png").exists() \
                     else "fetching"
            print(f"  [{i}/{len(words)}] {word.en}: {status}...", end=" ", flush=True)

        word.image_b64 = fetch_image(word)

        if verbose:
            print("ok")
