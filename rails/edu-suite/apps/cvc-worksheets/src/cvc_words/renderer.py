"""Render the single index.html from all word data using Jinja2."""
from __future__ import annotations

from pathlib import Path

import json as _json
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from .words import Word, VOWEL_COLORS, group_by_worksheet

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
OUTPUT_PATH = Path(__file__).parent.parent.parent / "output" / "index.html"


def _build_context(words: list[Word]) -> dict:
    by_worksheet = group_by_worksheet(words)

    worksheets = []
    for ws_num, ws_words in by_worksheet.items():
        vowel = ws_words[0].vowel
        pages = []
        for page_num in sorted(set(w.page for w in ws_words)):
            page_words = [w for w in ws_words if w.page == page_num]
            pages.append({"number": page_num, "words": page_words})

        worksheets.append({
            "number": ws_num,
            "vowel": vowel,
            "color": VOWEL_COLORS[vowel],
            "pages": pages,
            "words": ws_words,
        })

    word_to_worksheet = {w.en: w.worksheet for w in words}
    all_word_ids = [w.en for w in words]

    return {
        "worksheets": worksheets,
        "all_words": words,
        "all_word_ids": all_word_ids,
        "word_to_worksheet": word_to_worksheet,
    }


def render(words: list[Word], verbose: bool = True, out_path: Path | None = None) -> Path:
    """Render the worksheet HTML and return its path. Defaults to the app's
    output/index.html; pass out_path to render into a job/library directory."""
    OUTPUT_PATH_ = Path(out_path) if out_path else OUTPUT_PATH
    OUTPUT_PATH_.parent.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Markup tells Jinja2 "already safe" — prevents &#34; escaping inside <script> blocks
    env.filters["tojson_safe"] = lambda v: Markup(_json.dumps(v, ensure_ascii=False))

    template = env.get_template("index.html.j2")
    context = _build_context(words)
    html = template.render(**context)

    OUTPUT_PATH_.write_text(html, encoding="utf-8")
    if verbose:
        print(f"  Rendered -> {OUTPUT_PATH_}")
    return OUTPUT_PATH_
