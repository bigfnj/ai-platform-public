"""CVC Words workflow: build a bilingual phonics worksheet.

Stages: parse words -> (qwen) translate -> (SDXL) images -> (XTTS) audio -> render.
The ModelManager swaps one heavy model in at a time between stages. Uploads may
be a .txt/.csv word list (one CVC word per line); with no upload it uses the
built-in 30-word set. Reuses cvc-worksheets' Word model + Jinja template.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from edu_media_core import broker_media  # image + audio via the platform broker
from edu_media_core.jobs import JobContext, Step

from ..extract import extract_text  # any text-bearing doc: PDF / Word / txt / csv / md
from . import Workflow, register

_WORD_RE = re.compile(r"[A-Za-z]+")

# Make the cvc-worksheets package importable (monorepo sibling).
_CVC_SRC = Path(__file__).resolve().parents[4] / "cvc-worksheets" / "src"
if str(_CVC_SRC) not in sys.path:
    sys.path.insert(0, str(_CVC_SRC))

from cvc_words.words import Word, load_words  # noqa: E402
from cvc_words import translator as cvc_translator  # noqa: E402
from cvc_words import renderer as cvc_renderer  # noqa: E402
from cvc_words import image_gen as cvc_image_gen  # noqa: E402
from cvc_words import image_fetcher as cvc_image_fetcher  # noqa: E402

_VOWELS = "aeiou"


def _detect_vowel(word: str) -> str:
    for c in word:
        if c in _VOWELS:
            return c
    return "a"


def _words_from_list(raw: list[str]) -> list[Word]:
    """One worksheet per vowel, six words per page (keeps the template's per-
    worksheet single-vowel assumption valid)."""
    by_vowel: dict[str, list[str]] = defaultdict(list)
    seen = set()
    for w in raw:
        if w in seen:
            continue
        seen.add(w)
        by_vowel[_detect_vowel(w)].append(w)
    words: list[Word] = []
    for ws_num, (vowel, ws_words) in enumerate(sorted(by_vowel.items()), start=1):
        for i, w in enumerate(ws_words):
            words.append(Word(en=w, es="", image_query="", worksheet=ws_num,
                              page=i // 6 + 1, vowel=vowel))
    return words


def _parse(ctx: JobContext) -> None:
    # Accept any text-bearing document (PDF, Word, .txt, .csv, .md): extract the text,
    # then pull out the words. A one-word-per-line list still works; so does prose.
    raw: list[str] = []
    seen: set[str] = set()
    for f in ctx.state["input_files"]:
        for tok in _WORD_RE.findall(extract_text(f)):
            w = tok.lower()
            if len(w) >= 2 and w not in seen:
                seen.add(w)
                raw.append(w)
    if raw:
        words = _words_from_list(raw)
        ctx.stages[-1].message = f"{len(words)} word(s) from upload"
    elif ctx.state["params"].get("sample"):
        words = load_words()
        ctx.stages[-1].message = f"{len(words)} word(s) (built-in sample set)"
    else:
        raise ValueError(
            "No words found. Upload a document with your CVC words (PDF, Word, .txt, "
            ".csv, or .md), or check 'Use the sample word set'.")
    ctx.state["words"] = words


def _translate(ctx: JobContext) -> None:
    # Validated, in-scope guidance from /api/interpret (Additional instructions):
    # nudges each word's Spanish choice + picture subject.
    guidance = (ctx.state["params"].get("guidance") or "").strip()
    if guidance:
        ctx.progress(f"applying your instructions: {guidance}")
    pending = [w for w in ctx.state["words"] if w.needs_translation]
    for w in pending:
        ctx.progress(w.en)
        r = cvc_translator.translate_word(w, guidance=guidance)
        w.es = r["word_es"]
        w.image_query = r["image_query"]
    ctx.stages[-1].message = f"{len(pending)} translated, {len(ctx.state['words']) - len(pending)} cached"


def _images(ctx: JobContext) -> None:
    words = ctx.state["words"]
    img_dir = ctx.state["output_dir"] / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    ctx.progress(f"generating {len(words)} image(s)")
    # One broker call for all words -> SDXL loads once, not per word. Files are written
    # straight into the bundle (images/<word>.png) and referenced, not embedded as base64.
    subjects = [cvc_image_gen._subject(w) for w in words]
    out_paths = [img_dir / f"{w.en}.png" for w in words]
    paths = broker_media.generate_images(subjects, out_paths)
    ok = 0
    for w, path in zip(words, paths):
        if path and path.exists():
            ok += 1
        else:  # write the placeholder to the same path so the reference resolves
            (img_dir / f"{w.en}.png").write_bytes(cvc_image_fetcher._make_placeholder(w))
        w.image_path = f"images/{w.en}.png"
    ctx.stages[-1].message = f"{ok}/{len(words)} generated (rest placeholder)"


def _audio(ctx: JobContext) -> None:
    words = ctx.state["words"]
    en_dir = ctx.state["output_dir"] / "en-audio"
    mx_dir = ctx.state["output_dir"] / "mx-audio"
    en_dir.mkdir(parents=True, exist_ok=True)
    mx_dir.mkdir(parents=True, exist_ok=True)
    ctx.progress(f"synthesizing audio for {len(words)} word(s)")
    # Collect every clip, then one broker call -> XTTS loads once for the batch. WAVs are
    # written into the bundle (en-audio/<word>.wav, mx-audio/<word>.wav) and referenced.
    items: list[dict] = []
    out_paths: list = []
    targets: list = []  # (word, "en"|"es") aligned with items/out_paths
    for w in words:
        items.append({"lang": "en", "text": w.en})
        out_paths.append(en_dir / f"{w.en}.wav")
        targets.append((w, "en"))
        if w.es:
            items.append({"lang": "es", "text": w.es})
            out_paths.append(mx_dir / f"{w.en}.wav")
            targets.append((w, "es"))
    broker_media.synthesize_wavs(items, out_paths)
    for w, lang in targets:
        if lang == "en":
            w.audio_en_path = f"en-audio/{w.en}.wav"
        else:
            w.audio_es_path = f"mx-audio/{w.en}.wav"
    ctx.stages[-1].message = f"{len(words)} word(s)"


def _render(ctx: JobContext) -> None:
    out = ctx.state["output_dir"] / "index.html"
    cvc_renderer.render(ctx.state["words"], verbose=False, out_path=out)
    ctx.stages[-1].message = "index.html"


def _build(ctx: JobContext) -> list[Step]:
    return [
        Step("parse", "Read word list", _parse),
        # No required_model on the model stages: the broker owns residency now
        # (each broker call loads + evicts as needed), so the local ModelManager
        # is bypassed. Model work goes through broker_media.
        Step("translate", "Translate words", _translate),
        Step("images", "Generate images", _images),
        Step("audio", "Generate audio", _audio),
        Step("render", "Build worksheet", _render),
    ]


register(Workflow(
    key="cvc",
    label="CVC Words",
    description="Bilingual phonics worksheet (images + EN/ES audio) from an uploaded "
                "document of CVC words (PDF, Word, .txt, .csv, .md), or check 'Use the sample word set'.",
    build=_build,
))
