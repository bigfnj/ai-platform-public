"""Console entry point for the ``slide-audio`` command (and the top-level cli.py dev shim)."""
import argparse
import os

from dotenv import load_dotenv

from translation_service.pipeline import run
from translation_service.watcher import watch


def main() -> None:
    load_dotenv()
    os.environ.setdefault("COQUI_TOS_AGREED", "1")

    parser = argparse.ArgumentParser(
        description="Generate per-slide audio files from an educational slide deck PDF."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pdf", help="Path to a PDF file to process immediately")
    mode.add_argument("--watch", action="store_true",
                      help="Watch the intake/ folder and process PDFs automatically")
    parser.add_argument("--output", default="output",
                        help="Output directory for --pdf mode (default: output/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Translate and print scripts without generating audio (--pdf only)")
    parser.add_argument("--week", type=int, default=None,
                        help="Process only slides from this week number (--pdf only)")
    parser.add_argument("--slide", type=int, default=None,
                        help="Process only the slide with this page number (--pdf only)")
    parser.add_argument("--force-regen", action="store_true",
                        help="Regenerate audio even if WAV already exists (--pdf only)")
    args = parser.parse_args()

    if args.watch:
        watch()
    else:
        run(
            pdf_path=args.pdf,
            output_dir=args.output,
            dry_run=args.dry_run,
            week_filter=args.week,
            slide_filter=args.slide,
            force_regen=args.force_regen,
        )


if __name__ == "__main__":
    main()
