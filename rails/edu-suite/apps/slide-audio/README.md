# slide-audio

> Part of **edu-suite**. Formerly the standalone `translation-service`. The
> translation, XTTS audio, and PDF ingest/classify code now live in the shared
> [`edu-media-core`](../../packages/edu-media-core) package; this app keeps the
> slide prompt, script builder, renderer, watcher, report/player, and pipeline.
> Run from the workspace: `uv sync` at the repo root, then `uv run python cli.py`.

Converts educational slide decks (PDF) into per-slide audio files — English first, then Mexican Spanish. Built for a specific learner who cannot read or write and relies entirely on audio.

## Who this is for

A high school student with autism and a 2nd-grade education level, from Mexico. The teacher plays each slide's audio file while the student views the visual. Audio is the sole delivery mechanism — clarity and simplicity matter more than completeness.

All Spanish output uses `es_MX` (Mexican Spanish), `tú` register, 2nd-grade vocabulary, short declarative sentences, and repeats the key term in the body.

## What each audio file sounds like

```text
[English term, spoken clearly]
[English sentence 1]
[English sentence 2]
...
[1.5 second silence]
[Spanish term, spoken clearly]
[Spanish sentence 1]
[Spanish sentence 2]
...
```

One `.wav` file per content slide. Header slides (title-only) are skipped.

## Stack

| Component | Technology |
|---|---|
| Translation | Qwen2.5 32B-instruct-q3_K_M via Ollama (fully local) |
| Text-to-speech | Coqui XTTS v2 (local, RTX 4090) |
| PDF parsing | pdfplumber |
| Slide image rendering | PyMuPDF |
| Intake automation | watchdog + systemd |
| Runtime | Python 3.11 via uv, CUDA 12.4 |

No ongoing API costs.

## Setup

### Prerequisites

- Python 3.11 (`uv python pin 3.11`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- CUDA-capable GPU (tested on RTX 4090)
- [Ollama](https://ollama.com) with `qwen2.5:32b-instruct-q3_K_M` pulled
- Reference voice clips in `voices/` (see `voices/README.md`)

### Install

```bash
git clone <repo-url>
cd translation-service
uv sync
cp .env.example .env
# .env is not used by the local stack — kept for optional API key storage
```

### Pull the translation model

```bash
ollama pull qwen2.5:32b-instruct-q3_K_M
```

The XTTS v2 model (~1.8 GB) downloads automatically on first run.

## Usage

### Process a PDF immediately

```bash
# Translate and generate all audio
uv run python cli.py --pdf "My Deck.pdf"

# Week 1 slides only
uv run python cli.py --pdf "My Deck.pdf" --week 1

# One specific slide (by PDF page number)
uv run python cli.py --pdf "My Deck.pdf" --slide 5

# Preview translations only — no audio generated
uv run python cli.py --pdf "My Deck.pdf" --dry-run

# Force regenerate even if audio already exists
uv run python cli.py --pdf "My Deck.pdf" --force-regen

# Write to a custom output directory (default: output/)
uv run python cli.py --pdf "My Deck.pdf" --output some/dir
```

Exactly one of `--pdf` or `--watch` is required. `--output`, `--dry-run`, `--week`, `--slide`, and `--force-regen` apply to `--pdf` mode only.

Output lands under the output directory organized by week (`week1/`, `week2/`, etc.), with rendered slide images in `images/`. After a full (non-filtered) run, two extras are written:

- `_report.txt` — summary of what was generated, timing, and any errors
- `index.html` — an interactive two-panel audio player (slide image + highlighted segments); open in any browser

### Watch the intake folder directly

Run the watcher in the foreground without systemd (processes any PDFs already in `intake/` on startup, then watches for new arrivals):

```bash
uv run python cli.py --watch
```

### Linux service setup (intake automation)

The repo ships `translation-watcher.service` — a systemd unit that runs `cli.py --watch` to watch the `intake/` folder and process any PDF dropped there. Install it once after cloning:

```bash
# 1. Copy the unit file
sudo cp translation-watcher.service /etc/systemd/system/

# 2. Reload systemd and enable on boot
sudo systemctl daemon-reload
sudo systemctl enable translation-watcher

# 3. Start immediately
sudo systemctl start translation-watcher

# 4. Verify
sudo systemctl status translation-watcher
```

WSL2 note: WSL does not run systemd by default. Verify it is enabled in `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Then restart the WSL instance (`wsl --shutdown` from PowerShell) for the change to take effect.

### Drop-and-process (intake folder)

The systemd service watches `intake/` and processes any PDF dropped there automatically.

```text
intake/my_deck.pdf        → processes, moves to intake/processed/ on success
intake/my_deck.dryrun.pdf → translate-only preview, stays in intake/ for review
```

From Windows Explorer: `\\wsl.localhost\Ubuntu\home\bigfnj\projects\translation-service\intake\`

Monitor the service:

```bash
journalctl -u translation-watcher -f
```

Start / stop / restart:

```bash
sudo systemctl restart translation-watcher
sudo systemctl status translation-watcher
```

## Project structure

```text
src/translation_service/
  pdf_reader.py      — extracts slide content (title + bullets/paragraphs) from PDF
  classifier.py      — labels each slide: content, header, empty, or duplicate
  translator.py      — sends content to Qwen2.5 via Ollama, returns es_MX sentences
  script_builder.py  — orders segments: EN term → EN body → pause → ES term → ES body
  slide_renderer.py  — renders each content slide page to a JPEG + thumbnail (PyMuPDF)
  tts_engine.py      — Coqui XTTS v2 wrapper; synthesizes and concatenates segments
  pipeline.py        — orchestrates all steps; handles idempotent skip and timing
  watcher.py         — watchdog handler for the intake folder
  notifier.py        — Windows toast notification via PowerShell (WSL → Windows)
  report.py          — writes _report.txt and index.html after full runs

cli.py               — entry point
voices/              — reference audio clips (not committed; see voices/README.md)
intake/              — drop PDFs here for automatic processing
output/              — generated audio and reports land here
translation-watcher.service — systemd unit file
```

## Idempotent runs

The pipeline skips audio generation for any slide where:

1. The output `.wav` already exists, **and**
2. The translation is in the cache (content hash unchanged)

Re-running after adding a few slides only processes the new or changed ones. Use `--force-regen` to rebuild everything.

## Caveats

- **Python 3.11 required** — Coqui TTS does not support 3.12+
- **transformers pinned to 4.44.2** — v5.x requires PyTorch 2.7+ which breaks XTTS v2
- **WSL2 VRAM sharing** — Windows display driver takes ~4 GB of the GPU's VRAM; Ollama is configured to use 40/64 layers on GPU (~9.5 GB) with the rest on CPU
- Voice clips in `voices/` are not committed — they must be added manually. Once set, do not change them; voice consistency across the school year is a requirement.
