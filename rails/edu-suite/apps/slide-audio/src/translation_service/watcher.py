import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from .pipeline import run as run_pipeline

_INTAKE_DIR = Path("intake")
_PROCESSED_DIR = _INTAKE_DIR / "processed"
_OUTPUT_DIR = Path("output")
_STABLE_CHECKS = 3
_STABLE_INTERVAL = 1.0
_MISSING_BUDGET = 5  # consecutive "file not found" polls tolerated before giving up


def _wait_for_stable(path: Path) -> bool:
    """Block until the file size is stable across _STABLE_CHECKS polls.

    Tolerates the file briefly not existing (watchdog can fire on_created before the
    writer has flushed, or during a copy/rename race). Returns False if the file never
    materializes within the missing budget, so the caller can skip it — rather than
    letting a FileNotFoundError escape and kill the observer thread (the old bug).
    """
    last_size = -1
    stable = 0
    missing = 0
    while stable < _STABLE_CHECKS:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            missing += 1
            if missing >= _MISSING_BUDGET:
                return False
            stable, last_size = 0, -1
            time.sleep(_STABLE_INTERVAL)
            continue
        missing = 0
        if size == last_size:
            stable += 1
        else:
            stable, last_size = 0, size
        time.sleep(_STABLE_INTERVAL)
    return True


def _is_dry_run(pdf_path: Path) -> bool:
    """Filename contains .dryrun. → translate + print scripts, no audio, file stays in intake."""
    return ".dryrun." in pdf_path.name.lower()


def _output_dir_for(pdf_path: Path) -> str:
    # Strip .dryrun from stem if present so the output folder name stays clean
    stem = pdf_path.stem.replace(".dryrun", "").replace(".DRYRUN", "")
    date = datetime.now().strftime("%m-%d-%Y")
    return str(_OUTPUT_DIR / f"{stem} - {date}")


def _process(pdf_path: Path) -> None:
    dry_run = _is_dry_run(pdf_path)
    mode = "DRY RUN" if dry_run else "full"

    print(f"\n[intake] Detected ({mode}): {pdf_path.name}")
    print(f"[intake] Waiting for file to finish writing...")
    if not _wait_for_stable(pdf_path):
        print(f"[intake] Skipping {pdf_path.name}: file disappeared before it stabilized.")
        return

    output_dir = _output_dir_for(pdf_path)
    if not dry_run:
        print(f"[intake] Output → {output_dir}")

    try:
        generated = run_pipeline(
            pdf_path=str(pdf_path),
            output_dir=output_dir,
            dry_run=dry_run,
        )

        if dry_run:
            print(f"[intake] Dry run complete — translations printed to journal.")
            print(f"[intake] File left in intake/. Rename (remove .dryrun) to generate audio.")
        elif generated:
            dest = _PROCESSED_DIR / pdf_path.name
            shutil.move(str(pdf_path), str(dest))
            print(f"[intake] Moved to processed: {dest}")
        else:
            print(f"[intake] WARNING: no audio generated for {pdf_path.name}.")
            print(f"[intake] PDF may be image-only or not a vocabulary slide deck.")
            print(f"[intake] File left in intake/ — move or delete it manually.")

    except Exception as e:
        print(f"[intake] ERROR processing {pdf_path.name}: {e}")
        print(f"[intake] File left in intake/ for retry.")


def _safe_process(pdf_path: Path) -> None:
    """Run _process but never let an exception escape into the watchdog observer
    thread — an unhandled error there permanently kills file-watching. Log and continue.
    """
    try:
        _process(pdf_path)
    except Exception as e:
        print(f"[intake] ERROR handling {pdf_path.name}: {e} — watcher continues.")
        traceback.print_exc()


class _PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if isinstance(event, FileCreatedEvent) and event.src_path.lower().endswith(".pdf"):
            _safe_process(Path(event.src_path))

    def on_moved(self, event):
        if hasattr(event, "dest_path") and event.dest_path.lower().endswith(".pdf"):
            _safe_process(Path(event.dest_path))


def watch(intake_dir: str = str(_INTAKE_DIR)) -> None:
    """
    Start the intake folder watcher. Blocks until KeyboardInterrupt.
    Processes any PDFs already in intake/ on startup, then watches for new arrivals.

    Dry-run mode: name the file  my_deck.dryrun.pdf  to translate and print
    scripts without generating audio. File stays in intake/ afterwards.
    """
    intake = Path(intake_dir)
    intake.mkdir(parents=True, exist_ok=True)
    _PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    existing = list(intake.glob("*.pdf"))
    if existing:
        print(f"[intake] Found {len(existing)} PDF(s) already in intake/ — processing now.")
        for pdf in existing:
            _safe_process(pdf)

    observer = Observer()
    observer.schedule(_PDFHandler(), str(intake), recursive=False)
    observer.start()

    print(f"\n[intake] Watching {intake.resolve()} for new PDFs...")
    print(f"[intake] Dry-run tip: name file  my_deck.dryrun.pdf  to preview translations only.")
    print(f"[intake] Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n[intake] Watcher stopped.")

    observer.join()
