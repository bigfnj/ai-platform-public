# edu-suite

A suite of local-first tools for producing **bilingual (English / Mexican
Spanish) special-education instructional materials** from curriculum content.
Everything runs offline on a local GPU (Ollama + CUDA); no cloud APIs, no
per-use cost.

## Layout

```
edu-suite/
├── packages/
│   └── edu-media-core/   Shared Python engine: translation, TTS audio,
│                         image generation, PDF ingest + slide classification
├── apps/
│   ├── slide-audio/      Curriculum slide PDFs → per-slide EN + es_MX audio
│   │                     + a karaoke-synced HTML player
│   ├── cvc-worksheets/   CVC word lists → printable bilingual illustrated
│   │                     phonics worksheets
│   ├── teachtown/        TeachTown enCORE curriculum → interactive worksheet
│   │                     "missions" HTML (now bilingual + audio)
│   └── dashboard/        Local web app fronting all workflows: upload → job
│                         queue with live staged status → download ZIP
├── shared/
│   └── voices/           XTTS reference clips (single source of truth; *.wav not committed)
├── content/              Shared curriculum source pool + manifest.json
└── scripts/
    └── ingest_content.py Scan content/ → manifest of units/weeks/subjects/types
```

## Why one repo

The three apps serve the same audience (special-ed, bilingual EN/es_MX) on a
heavily overlapping stack. `slide-audio` and `cvc-worksheets` had near-duplicate
Ollama-translation and Coqui-XTTS code; `cvc-worksheets` even reached across the
filesystem for `slide-audio`'s voices. Consolidating gives them one engine
(`edu-media-core`), one set of voices, and one content pool — and let `teachtown`
gain bilingual audio it never had.

## Stack

- Python 3.11, managed by [uv](https://docs.astral.sh/uv/) (workspace).
- Local models: Ollama `qwen2.5:32b-instruct-q3_K_M` (translation), Coqui XTTS v2
  (audio), SDXL-Turbo (images). CUDA 12.4, `transformers==4.44.2` (XTTS pin).
- `teachtown` is a standalone vanilla-JS + PDF.js app served by a tiny Node
  static server.

## Setup

```sh
# From the repo root — installs the whole workspace (core + both Python apps).
# --all-packages is required: the root project itself has no dependencies.
uv sync --all-packages
# Provide XTTS reference clips (not committed):
#   shared/voices/english_reference.wav
#   shared/voices/spanish_reference.wav
# Ensure Ollama is running with the model pulled:
ollama pull qwen2.5:32b-instruct-q3_K_M
```

## Dashboard (recommended entry point)

A local web app for non-technical instructors: pick a workflow (Just Translate,
CVC Words, **TeachTown Builder** — upload a unit's worksheets and AI drafts an
interactive lesson you review, edit, and build — or **IEP Present Levels** —
upload a SEIS Present-Levels PDF, review the 8 OCR-extracted sections beside your
own notes, and the local model elaborates a fuller **English** present-levels
narrative to paste into SEIS), upload documents, watch live staged progress
(including model load/unload), and download a self-contained ZIP (bilingual
worksheets ship as offline images). Finished work is kept in an ID-stamped,
searchable library outside the repo.

```sh
cd apps/dashboard && uv run python serve.py   # then open http://127.0.0.1:8800
# or double-click apps/dashboard/Open Dashboard.bat
```

Jobs run one at a time, each in its own subprocess so GPU memory is fully
reclaimed between jobs. See [apps/dashboard/PLAN.md](apps/dashboard/PLAN.md) and
the [educator smoke-test guide](apps/dashboard/EDUCATOR_TEST_PLAN.md).

**IEP Present Levels as its own app.** Setting `IEP_ONLY=1` restricts a dashboard
instance to only the IEP Present Levels workflow (English-only), and the default
(content) instance hides it — so IEP deploys as a **separate platform rail** with
its own library, DB, and entitlement, keeping student PII isolated from the
content apps. Its federation remote builds via `vite.config.iep.ts` → `dist-iep/`
(the model defaults to `qwen3.6*:27b` via `IEP_LLM_MODEL`). OCR-extraction of the
SEIS PDF uses PyMuPDF + tesseract (both already in the deploy image).

## Run each app directly (CLIs)

```sh
# slide-audio: PDF deck → bilingual audio + player
cd apps/slide-audio && uv run python cli.py --pdf path/to/deck.pdf

# cvc-worksheets: generate the phonics worksheet HTML
cd apps/cvc-worksheets && uv run python cli.py

# teachtown: interactive worksheets (add bilingual audio first, optional)
cd apps/teachtown && uv run python enrich.py          # es_MX + audio manifest
cd interactive-html && node serve.js                  # http://127.0.0.1:8765/

# inventory the shared content pool
python scripts/ingest_content.py
```

Each app also has its own README. Optimization/enhancement backlog is in
[ENHANCEMENTS.md](ENHANCEMENTS.md); project context and history in
[PROJECT_MEMORY.md](PROJECT_MEMORY.md).

## Origin repos

Migrated with full history (via `git subtree`) from three now-superseded repos,
which remain as private snapshots: `bigfnj/translation-service`,
`bigfnj/cvc-words`, `bigfnj/teachtown-units`. Work happens here now.
