# edu-suite — project memory

## What this is

A monorepo consolidating three previously separate projects that all produce
bilingual (EN / Mexican-Spanish `es_MX`) special-education instructional
materials on a shared local-first stack. See [README.md](README.md) for layout
and usage.

## IEP Present Levels (2026-07)

A fourth dashboard workflow. Upload a SEIS Present-Levels PDF → OCR-extract the 8
sections (`packages/edu-media-core/.../present_levels.py`: PyMuPDF render + tesseract,
because the SEIS PDF text layer is corrupt — a broken ligature CMap) → a two-column
review form (extracted "current" vs. the teacher's new input) → the local model
(`qwen3.6` via the broker, **English-only**) elaborates each section into a fuller
present-levels narrative + an Areas-of-Need list, as a printable HTML draft to paste
into SEIS. Missing data becomes `[bracketed placeholders]`; provided data is kept
verbatim (never fabricated).

Deployed as its **own platform rail** for student-PII isolation: a second instance of
the dashboard image with `IEP_ONLY=1` (the default/content instance hides the
workflow), its own library/DB/entitlement, and a separate federation remote
(`apps/dashboard/frontend/vite.config.iep.ts` → `dist-iep/`, remote name `iep_app`).
The rail wiring (catalog, gateway config, compose `iep` service) lives in the platform
repo — see its `docs/checkpoint.md`.

## Origin

Merged from three standalone repos, kept as private snapshots:

- `bigfnj/translation-service` → `apps/slide-audio`
- `bigfnj/cvc-words` → `apps/cvc-worksheets`
- `bigfnj/teachtown-units` → `apps/teachtown`

Brought in with `git subtree` so full commit history is preserved. Before the
merge each was committed and pushed (teachtown-units had zero prior commits).

## Migration log

- **Phase 1** — Monorepo scaffolded (uv workspace); three apps imported via
  subtree; XTTS voices consolidated to `shared/voices` and both audio engines
  repointed there (via `$VOICES_DIR` override); teachtown's abandoned vinext/Next
  scaffold removed.
- **Phase 2** — Extracted `packages/edu-media-core` (translate, tts, images, pdf,
  classify); `slide-audio` and `cvc-worksheets` now import it and keep only their
  domain glue. Apps reach the core via a `sys.path` bootstrap in each package
  `__init__` (works with or without `uv sync`). Dropped unused `anthropic` dep.
- **Phase 3** — teachtown gained bilingual es_MX + audio. `enrich.py` uses the
  core to produce `enrichment.json` (es text + EN/ES audio) from `data.json`;
  `index.html` got an EN/ES/Both toggle, Spanish text, and audio buttons; it
  degrades to English-only when no enrichment is present. Verified via headless
  Playwright render.
- **Phase 4** — Curriculum source moved into a shared `content/` pool;
  `scripts/ingest_content.py` scans it and writes `content/manifest.json`
  (units/weeks/subjects/types). This is the old teachtown "auto-import" backlog.
- **Phase 5** — Docs (this file, root + per-app READMEs) and cleanup.

## Verification status

Live GPU smoke test PASSED (2026-07-20, RTX 4090). `uv sync --all-packages`
installs the workspace (torch 2.6.0+cu124, CUDA available). All three engines
run live through the shared core: Ollama translate (butterfly→mariposa), XTTS v2
audio (EN + es_MX), and SDXL-Turbo image gen. Also verified earlier:
`py_compile` of every module, the core unit tests (`packages/edu-media-core/tests`),
and a headless Playwright render of teachtown.

Two things the smoke test surfaced and fixed:
- **`uv sync` alone installs nothing** (the workspace root has no deps) — use
  `uv sync --all-packages`.
- **diffusers had to be capped `<0.32`**: newer diffusers imports pipelines
  needing a transformers newer than XTTS's `==4.44.2` pin
  (`Dinov2WithRegistersConfig`). Pinned in `edu-media-core/pyproject.toml`.

VRAM note (24GB 4090): qwen-32B-q3 (~15GB) + XTTS + SDXL do not all fit at once;
run the engines sequentially (unload Ollama with `ollama stop <model>` between).

## Constraints / gotchas

- XTTS pins `transformers==4.44.2`; torch/torchaudio come from the cu124 index
  (declared at root and in `edu-media-core`).
- Reference voice clips live in `shared/voices` and are gitignored; copy them in
  locally. teachtown's generated `enrichment.json`/`public/audio` are gitignored
  (the committed `enrichment.sample.json` documents the format).
- `apps/teachtown/PROJECT_MEMORY.md` is the *old* standalone teachtown memory,
  kept for history; this file supersedes it for suite-level context.

Backlog of optimizations/enhancements: [ENHANCEMENTS.md](ENHANCEMENTS.md).
