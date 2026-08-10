import json
import re
import time
from pathlib import Path

from .pdf_reader import read_slides
from .classifier import classify_slides
from .translator import translate_slide, is_cached
from .script_builder import build_script, format_script_preview
from .tts_engine import generate_slide_audio
from edu_media_core import pdf as core_pdf  # slide/thumbnail rendering lives in core now
from .report import write_report, write_html_player
from .notifier import notify


def _term_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")[:30]


def run(pdf_path: str, output_dir: str = "output", dry_run: bool = False,
        week_filter: int = None, slide_filter: int = None,
        force_regen: bool = False) -> int:
    """
    Full pipeline: read PDF → classify → translate → render images → generate audio.

    Returns the number of audio files written (0 if dry_run or nothing to process).
    """
    t_start = time.monotonic()
    pdf_name = Path(pdf_path).stem

    print(f"Reading PDF: {pdf_path}")
    slides = read_slides(pdf_path)
    classified = classify_slides(slides)

    content = [s for s in classified if s["type"] == "content"]
    n_headers  = sum(1 for s in classified if s["type"] == "header")
    n_empty    = sum(1 for s in classified if s["type"] == "empty")
    n_dupes    = sum(1 for s in classified if s["type"] == "duplicate")

    print(f"  {len(content)} content slides, {n_headers + n_empty + n_dupes} skipped "
          f"({n_headers} headers, {n_empty} empty, {n_dupes} duplicates)")

    if week_filter is not None:
        content = [s for s in content if s["week"] == week_filter]
        print(f"  Filtered to week {week_filter}: {len(content)} slides")

    if slide_filter is not None:
        content = [s for s in content if s["slide_number"] == slide_filter]
        print(f"  Filtered to slide {slide_filter}: {len(content)} slides")

    if not content:
        print("No content slides found — PDF may be image-only or not a vocabulary deck.")
        return 0

    # Render slide images once up front (idempotent — skips existing files)
    if not dry_run:
        print("  Rendering slide images...")
        image_map = core_pdf.render_slides(pdf_path, content, output_dir)
    else:
        image_map = {}

    total     = len(content)
    generated = 0
    cached    = 0
    errors    = []
    player_slides = []

    for i, slide in enumerate(content, 1):
        slug  = f"slide_{slide['slide_number']:02d}_{_term_slug(slide['title'])}"
        label = f"[{i}/{total}] {slug}"

        try:
            translation = translate_slide(slide)
            script = build_script(slide, translation)

            if dry_run:
                print(format_script_preview(slide, script))
                print()
                continue

            week_dir = Path(output_dir) / f"week{slide['week']}"
            out_file = week_dir / f"{slug}.wav"
            timing_file = week_dir / f"{slug}.json"
            rel_wav  = f"week{slide['week']}/{out_file.name}"

            imgs = image_map.get(slide["slide_number"], {})

            if not force_regen and out_file.exists() and is_cached(slide):
                print(f"{label} [skip — up to date]")
                generated += 1
                cached += 1
                # Load saved timings so the HTML player has sync data
                timings = json.loads(timing_file.read_text(encoding="utf-8")) if timing_file.exists() else []
                player_slides.append({
                    "week": slide["week"],
                    "slide_number": slide["slide_number"],
                    "title": slide["title"],
                    "wav_path": rel_wav,
                    "image": imgs.get("image", ""),
                    "thumb": imgs.get("thumb", ""),
                    "segments": timings,
                })
                continue

            print(f"{label}")
            timings = generate_slide_audio(script, str(out_file))
            timing_file.write_text(json.dumps(timings), encoding="utf-8")
            generated += 1
            player_slides.append({
                "week": slide["week"],
                "slide_number": slide["slide_number"],
                "title": slide["title"],
                "wav_path": rel_wav,
                "image": imgs.get("image", ""),
                "thumb": imgs.get("thumb", ""),
                "segments": timings,
            })

        except Exception as e:
            print(f"{label} ERROR: {e}")
            errors.append((slide["slide_number"], slide["title"], str(e)))

    if dry_run:
        print(f"Dry run complete — {total} scripts printed, no audio generated.")
        return total

    duration = time.monotonic() - t_start
    print()
    print(f"Done. {generated}/{total} audio files in {output_dir}/  "
          f"({cached} cached, {len(errors)} errors)")

    # Write report and HTML player for full (non-filtered) runs
    if not week_filter and not slide_filter and generated:
        stats = {
            "generated": generated, "total": total,
            "skipped_headers": n_headers, "skipped_empty": n_empty,
            "skipped_dupes": n_dupes, "cached": cached,
            "errors": errors,
        }
        write_report(output_dir, pdf_name, stats, duration)
        write_html_player(output_dir, pdf_name, player_slides)

        mins, secs = divmod(int(duration), 60)
        body = f"{generated} slides · {mins}m {secs}s" if mins else f"{generated} slides · {secs}s"
        if errors:
            body += f" · {len(errors)} error(s)"
        notify(f"✅ {pdf_name}", body)

    return generated
