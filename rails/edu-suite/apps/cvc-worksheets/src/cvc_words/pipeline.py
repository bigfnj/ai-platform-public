"""Orchestrate translation → images → audio → render."""
from __future__ import annotations

from .words import load_words, save_words
from . import translator, image_fetcher, image_gen, audio_gen, renderer


def run(
    *,
    worksheet: int | None = None,
    skip_audio: bool = False,
    skip_images: bool = False,
    gen_images: bool = False,
    force_gen: bool = False,
    dry_run: bool = False,
    retranslate: bool = False,
    verbose: bool = True,
) -> None:
    words = load_words()

    if worksheet is not None:
        words = [w for w in words if w.worksheet == worksheet]

    # 1 — Translate
    print("\n[1/4] Translating words...")
    if retranslate:
        # Clear existing translations and the cache so the LLM re-runs every word
        translator.clear_cache()
        for w in words:
            w.es = ""
            w.image_query = ""
    translator.translate_all(words, verbose=verbose)

    if dry_run:
        print("\nDry run complete. Translated words (NOT saved):")
        for w in words:
            print(f"  {w.en:6} -> {w.es:15}  (image: {w.image_query})")
        return

    # Persist translations back to words.json, preserving any words not in
    # the current (possibly --worksheet-filtered) run.
    save_words(_merge_translations(words))

    # 2 — Images
    if not skip_images:
        # Optionally generate images with SDXL-Turbo first; image_fetcher then
        # prefers assets/generated/ over clipart, so generated images win.
        if gen_images:
            print("\n[2/4] Generating images (SDXL-Turbo)...")
            image_gen.generate_all(words, force=force_gen, verbose=verbose)
        print("\n[2/4] Resolving images...")
        image_fetcher.fetch_all(words, verbose=verbose)
    else:
        print("\n[2/4] Images skipped.")
        for w in words:
            w.image_b64 = ""

    # 3 — Audio
    if not skip_audio:
        print("\n[3/4] Generating audio...")
        audio_gen.generate_all(words, verbose=verbose)
    else:
        print("\n[3/4] Audio skipped.")

    # 4 — Render
    print("\n[4/4] Rendering index.html...")
    out = renderer.render(words, verbose=verbose)
    print(f"\nDone. Open: {out}")


def _merge_translations(translated: list) -> list:
    """Re-load all words and apply translations from the in-memory list."""
    from .words import load_words as _load
    all_words = _load()
    by_en = {w.en: w for w in translated}
    for w in all_words:
        if w.en in by_en:
            src = by_en[w.en]
            w.es = src.es
            w.image_query = src.image_query
    return all_words
