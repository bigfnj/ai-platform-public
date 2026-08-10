# Translation Service — Build Plan

## Learner Context (read this first)

- Student: high school age, autism, 2nd-grade education level, from Mexico
- Cannot read or write — audio is the **primary** and only delivery mechanism
- Mexican Spanish dialect only (`es_MX`)
- Register: `tú` throughout, short declarative sentences, no idioms, no lists read aloud
- Voice must be consistent all year — same reference audio clips every run
- Audio pattern per slide: English term → English sentences → 1.5s pause → Spanish term → Spanish sentences

## Decisions Log

- Translation engine: **Claude API** (anthropic SDK)
- TTS engine: **Coqui XTTS v2** (local, RTX 4090 24GB)
- Audio format: **One combined .wav per content slide** (English + Spanish, pause between)
- Input: **PDF for now** — Google Slides API is Backlog Item #1
- Dependency manager: **uv** with `pyproject.toml`
- Slide classification: slides with no bullet points = header → skip

---

## Phase 0 — Environment Setup

> Run once before anything else. All ML packages go inside the project venv via `uv` — never system-wide.

- [x] `uv init` inside `/home/bigfnj/projects/translation-service` (creates `pyproject.toml`)
- [x] Install PyTorch with CUDA 12.4 support (must come before Coqui TTS):

  ```bash
  uv add torch torchaudio --index https://download.pytorch.org/whl/cu124
  ```

- [x] Install remaining project dependencies:

  ```bash
  uv add TTS anthropic pdfplumber python-dotenv
  ```

  > Note: Coqui TTS requires Python < 3.12 — project pinned to Python 3.11 via `uv python pin 3.11`
- [x] Verify CUDA is visible to PyTorch → `True` (RTX 4090 confirmed)
- [x] Verify Coqui TTS imports cleanly → `TTS OK`
- [x] Verify Anthropic SDK imports cleanly → `Anthropic SDK OK, version: 0.107.1`
- [ ] Note: XTTS v2 model weights (~1.8GB) download automatically on first audio generation run — not during install

> **No local LLM needed.** Translation uses Claude API. XTTS v2 is a speech synthesis model, not an LLM.

---

## Phase 1 — Project Scaffold

- [x] Create `pyproject.toml` with uv (`uv init`)
- [x] Create `src/translation_service/` package structure
- [x] Create `voices/` directory with placeholder README for reference audio clips
- [x] Create `output/` directory with `.gitkeep`
- [x] Create `.env.example` with `ANTHROPIC_API_KEY=` placeholder
- [x] Create `CLAUDE.md` documenting learner profile, linguistic rules, voice settings, and pipeline overview
- [x] Initialize git repo and first commit

---

## Phase 2 — PDF Parser

- [x] Add `pdfplumber` or `pypdf` as dependency via `uv add`
- [x] Implement `src/translation_service/pdf_reader.py`
  - [x] Extract text content per page (each page = one slide)
  - [x] Return structured list: `[{slide_number, title, bullets, paragraphs, raw_text}]`
  - [x] Handles both Week 1 bullet format and Weeks 2–4 paragraph format
- [x] Implement `src/translation_service/classifier.py`
  - [x] Rule: title-only or empty = header → `type: "header"` or `"empty"`
  - [x] Rule: any content after title = content → `type: "content"`
  - [x] Deduplicates exact-match repeated pages → `type: "duplicate"` (Line Cook, Kitchen)
  - [x] Tracks current week number and assigns to each content slide
- [x] Write test: ran classifier against full PDF → 38 content, 22 headers, 26 empty, 2 duplicates — verified correct

---

## Phase 3 — Translation Engine

- [x] Add `ollama` Python package as dependency via `uv add` (switched from Anthropic API to local Qwen2.5 32B)
- [x] Implement `src/translation_service/translator.py`
  - [x] System prompt encodes all linguistic rules:
    - Mexican Spanish (`es_MX`)
    - 2nd-grade vocabulary ceiling
    - Short declarative sentences (subject → verb → object)
    - `tú` register
    - No subordinate clauses, no idioms, no comma-separated lists read aloud
    - Repeat the key term at least once in the body sentences
    - Concrete, literal language only
  - [x] Input: English term + bullet points
  - [x] Output: structured dict `{term_es, sentences_es: []}`
  - [x] Cache translations to `output/.translation_cache.json` — never re-translate a slide that hasn't changed
- [x] Implement `src/translation_service/script_builder.py`
  - [x] Input: English content + translated Spanish content
  - [x] Output: ordered list of audio segments with language tags
  - [x] Pause segment = 1.5 second silence injected between English and Spanish blocks
- [x] Write test: translated Host + 4 other slides across all 4 weeks — quality verified

> **Decision:** Switched from Claude API to local Qwen2.5 32B (Q3_K_M) via Ollama. No API cost.
> Model runs with `num_gpu=40` (40/64 layers on GPU ~9.5 GB VRAM, rest on CPU) to avoid WSL2 VRAM contention.
> Known limitation: PDF line-wraps create orphaned fragments in English script — fix in Phase 7.

---

## Phase 4 — TTS Engine (Coqui XTTS v2)

- [x] `TTS` (coqui-tts) already installed as dependency
- [x] Reference audio clips provided:
  - [x] `voices/english_reference.wav` — 11.6s, 48kHz stereo
  - [x] `voices/spanish_reference.wav` — 7.9s, 48kHz stereo
- [x] Implement `src/translation_service/tts_engine.py`
  - [x] Load XTTS v2 model once at startup (model stays in VRAM for batch runs)
  - [x] `synthesize_segment(text, lang)` → synthesizes using language-appropriate reference clip
  - [x] `generate_silence(duration_seconds)` → returns silence audio array
  - [x] `combine_and_save(segments, output_path)` → concatenates all segments, saves as `.wav`
  - [x] `generate_slide_audio(script_segments, output_path)` → full slide in one call
- [x] Write test: generated `output/test/slide_04_host.wav` — all 11 segments (EN + pause + ES)

> **Fixes applied:**
>
> - `transformers` pinned to `==4.44.2` — v5.x requires PyTorch 2.7+ (`float8_e8m0fnu`)
> - `torch.load` monkey-patched during model load to use `weights_only=False` — PyTorch 2.6 tightened defaults, breaking Coqui's checkpoint loading (safe: XTTS v2 is a trusted public model)
> - `COQUI_TOS_AGREED=1` env var required to skip interactive license prompt

---

## Phase 5 — Pipeline Orchestrator

- [x] Implement `src/translation_service/pipeline.py` — full orchestrator with progress logging, error-per-slide isolation, returns generated count
- [x] Implement `cli.py` — `--pdf`, `--watch`, `--dry-run`, `--week`, `--slide` flags
- [x] Dry run verified across all 38 slides — translation quality reviewed and approved

---

## Phase 6 — First Full Run

- [x] Dry run reviewed — all 38 scripts printed and approved
- [x] All 38 audio files generated — `output/week1` through `output/week4`
- [x] 5 slides spot-checked across all 4 weeks — English clarity, Spanish naturalness, pause timing all approved by teacher

---

## Phase 7 — Intake Watcher (added)

- [x] `src/translation_service/watcher.py` — watchdog-based folder monitor
- [x] Drop PDF into `intake/` → auto-processes → audio in `output/<filename> - MM-DD-YYYY/`
- [x] On success: PDF moves to `intake/processed/`
- [x] On zero output (image-only PDF, wrong format): PDF stays in `intake/` with journal warning
- [x] `translation-watcher.service` — systemd service, starts on WSL boot, logs via journalctl
- [x] `PYTHONUNBUFFERED=1` — watcher stdout visible in `journalctl -u translation-watcher -f`

---

## Phase 8 — Robustness + Teacher UX (committed 2026-06-08)

- [x] **Line-wrap fix** — `pdf_reader.py` now joins consecutive lines that don't end with sentence-terminal punctuation (`.!?:`). Fixes broken English scripts for "Teamwork", "Wants and needs", "Advertisement", "Assembly Line".
- [x] **Extended bullet detection** — handles `●•-*`, numbered (`1.`, `2)`), and lettered (`a.`, `(b)`) list formats.
- [x] **TTS retry logic** — `tts_engine.py` retries up to 2 times with 3s delay on synthesis failure.
- [x] **Idempotent audio skip** — pipeline skips WAV generation if the output file already exists AND the translation is cache-hit. Avoids regenerating slides that haven't changed.
- [x] **`--force-regen` flag** — CLI flag to override idempotent skip and regenerate all audio.
- [x] **End-of-run timing** — pipeline logs wall-clock duration after every run.
- [x] **Text report** — `report.py` writes `_report.txt` to the output folder after each full unfiltered run.
- [x] **HTML audio player** — `report.py` writes `index.html` with `<audio controls>` per slide, organized by week. Open in any browser; works without a server.
- [x] **Windows toast notification** — `notifier.py` fires a balloon notification via PowerShell after a full run completes. Fire-and-forget, silently skipped if PowerShell unavailable.
- [x] **Dry-run intake convention** — name any PDF `my_deck.dryrun.pdf` when dropping into intake to trigger translation-only mode (no audio generated, file stays in intake for inspection).

---

## Polish & Hardening

- [x] Fix PDF line-wrap artifacts — done in Phase 8
- [ ] Translation prompt drift — "Decision Making" slide produced a `cuando` subordinate clause. Consider tightening the system prompt or adding a post-check pass with sentence length validation.
- [ ] Final test: run a fresh PDF through the complete pipeline start to finish

---

## Backlog (Future Enhancements)

- [ ] **#1 — Google Slides API integration**: replace PDF input with direct Google Slides URL. One-time OAuth setup via Google Cloud Console. Teacher pastes the Slides URL, pipeline reads live deck. No PDF export step needed.
- [ ] **#2 — Slide change detection**: compare current deck against last run, only re-process slides that changed. Saves time when teacher adds a few slides mid-year.
- [ ] **#3 — Gradio web UI**: teacher uploads PDF → audio ZIP downloads automatically. No terminal required.
- [ ] **#4 — Multi-student profiles**: store separate voice/register/complexity settings per student if other students are added to the program.
- [ ] **#5 — Voice warmth upgrade**: evaluate ElevenLabs voice clone of a known speaker vs. XTTS v2 reference. Run quality comparison before committing.
- [ ] **#6 — Subtitle/transcript export**: alongside each `.wav`, write a `.txt` with the spoken script for teacher review or accessibility.
- [ ] **#7 — Intake ZIP support**: allow dropping a ZIP of PDFs into intake so an entire semester can be queued in one drop.

---

## Current Status
>
> **Pipeline fully operational with robustness + teacher UX features.** All audio for the Food Service deck is generating correctly. Next: translation prompt drift fix, then Gradio web UI.
