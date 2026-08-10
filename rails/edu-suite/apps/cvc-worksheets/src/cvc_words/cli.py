"""Console entry point for the ``cvc`` command (and the top-level cli.py dev shim)."""
import argparse
import sys

from dotenv import load_dotenv

from cvc_words.pipeline import run


def main() -> None:
    # Force UTF-8 stdout so Spanish characters (ñ, á) and arrows don't crash the
    # Windows cp1252 console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv()

    p = argparse.ArgumentParser(description="CVC Words worksheet generator")
    p.add_argument("--worksheet", type=int, choices=[1, 2, 3, 4, 5],
                   help="Generate only this worksheet number")
    p.add_argument("--skip-audio", action="store_true",
                   help="Skip TTS audio generation (faster iteration on layout)")
    p.add_argument("--skip-images", action="store_true",
                   help="Skip image fetching (use placeholders)")
    p.add_argument("--gen-images", action="store_true",
                   help="Generate images locally with SDXL-Turbo before resolving")
    p.add_argument("--force-gen", action="store_true",
                   help="With --gen-images, regenerate even if a generated image exists")
    p.add_argument("--dry-run", action="store_true",
                   help="Translate and print word data only, no images/audio/HTML")
    p.add_argument("--retranslate", action="store_true",
                   help="Clear cached translations and re-query the LLM for every word")
    p.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = p.parse_args()

    run(
        worksheet=args.worksheet,
        skip_audio=args.skip_audio,
        skip_images=args.skip_images,
        gen_images=args.gen_images,
        force_gen=args.force_gen,
        dry_run=args.dry_run,
        retranslate=args.retranslate,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
