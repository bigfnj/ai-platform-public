# Enhancement & optimization backlog

Live items only; a clear-out is in progress (see `~/.claude/plans/`). Resolved / superseded
items are listed at the bottom for the record.

## Engine & apps (open)

- **Just Translate word-level "karaoke ball" (Tier 2).** Sentence-level read-along ships in the
  book reader (per-page EN + ES tracks, spoken sentence highlights in the text panel). A true
  word-by-word ball needs per-word timings XTTS doesn't provide — do it with **forced alignment
  (whisperX on the generated audio)** for accuracy; a proportional estimate drifts and desyncs.
  Deferred until asked.
- **Just Translate audio scale for long books + a preview mode (recommended).** The reader does
  whole-book-upfront audio; two 8-page books ≈ 33 min, and a 54-page book is ~1h of TTS (484 clips).
  Every reader fix currently costs a full audio pass just to eyeball the result — so a **skip-audio
  preview mode** (validate text/layout/chapters in seconds, generate audio only once it's right) is
  the cheapest win. Longer term, per-page-on-demand audio (reader calls the broker; no longer a
  static offline bundle) or a page-range cap. Recommended after the user hit the ~1h cost twice.
- **Symbol-label noise in read text.** AAC symbol captions can leak into the extracted text (e.g. a
  `RIP` tombstone label lands mid-sentence on a Great Expectations page) and get read aloud. Filter
  standalone symbol-label tokens — carefully, so real words/acronyms aren't eaten.
- **TTS model note — XTTS alternatives (informational, no migration planned).** User is happy
  with the current XTTS es_MX audio (confirmed 2026-07-23) — recorded only as a fallback if the
  commercial-license constraint ever matters. XTTS v2 is frozen (Coqui folded 2024) and its license
  forbids commercial use; the leading MIT drop-in would be Chatterbox Multilingual (23 langs incl
  Spanish, ~5s zero-shot clone), with CosyVoice2/Zonos also viable. Any swap is broker-side (the
  platform repo's VRAM-safe TTS worker), not edu-suite; A/B on real es_MX passages before committing.
- **Broker XTTS-wedge watch (not root-caused).** A CVC run once wedged the broker — SDXL images
  fine, then XTTS hung with the broker idle; a **broker restart cleared it**. Media calls now
  fast-fail (`BROKER_MEDIA_TIMEOUT`=480s + 1 retry) so a wedge fails in minutes, not ~1h. If it
  recurs, catch it live (media-worker process + broker logs) and fix the media-worker lock.
- **Cross-app HTML convergence** (Phase 8 spike): shared audio-button atom + tokens, adopted per
  app; no unified skin. See [`docs/cross-app-html-convergence.md`](docs/cross-app-html-convergence.md).

## Resolved / superseded

- **DONE 2026-07-23 — Just Translate rebuilt into an offline bilingual BOOK READER.** The old
  workflow was text-only (extract → translate → side-by-side EN/ES text + audio) and dropped the
  page images entirely — the reported regression ("uploaded 3 books, it just extracted text"). The
  page-image "book" the user remembered lives in the sibling slide-audio (old translation-service)
  and was never ported here. Rebuilt `just_translate.py`: each uploaded PDF → a *book* whose every
  page is **rendered to an image** (text + pictures intact — the real page, not harvested text),
  text pulled per page (OCR fallback), translated per page, and given per-page EN + ES audio with
  sentence offsets. Chapters detected in tiers: **PDF outline → text-marker (running-header–aware)
  → one model pass over page openings → single-chapter fallback**. Output is a self-contained static
  reader (`index.html` with data inlined + `<book>/images` + `<book>/audio`): book selector on top,
  chapter + page-thumbnail rail on the left, the page image centered, EN/ES readback buttons under
  it, and the spoken sentence highlighted in a side text panel (EN amber / ES blue). Added
  `edu_media_core.pdf.get_toc`. Verified: live E2E on the two 8-page books (16 pages, all images on
  disk, 241 clips → 32 tracks), reader render-checked in-browser (Playwright), regression green
  (dashboard 16/16, core 24/24). **Chapter bug caught + fixed in verification:** a repeating
  "Unit 76…" page banner tripped the marker tier → 8 identical chapters/book; marker tier now
  rejects single-title/most-pages headers → falls to the model tier (each test book → 1 real
  chapter). **Playtest fixes (2026-07-23):** dedupe double-struck title glyphs
  (`GGrreeaatt`→`Great`, core `read_slides` via `dedupe_chars`); strip footer page-number
  lines so they aren't read; peel a chapter heading into its own sentence so it isn't read
  glued to the first body sentence (`just_translate._clean_page_text`, applied after chapter
  detection so rail titles stay short); strip running headers/footers (`_strip_boilerplate`) —
  a line repeating across most pages (top banner / copyright / running title + page number) is
  page furniture, not book content, matched order- and page-number-insensitively so a footer
  that flips order by page parity still unifies; only header/footer zones are candidates so the
  page-1 title and body are never touched; drop sub-body-size glyphs
  (`read_slides(drop_small_text=True)` → `_drop_small_glyphs`) so picture-symbol captions — a
  9pt `RIP` over a tombstone icon, fine print — aren't translated or read, judged by the *font
  size* they're drawn at (body = the page's dominant size; larger text like titles is kept),
  scoped to Just Translate and a no-op on OCR pages that carry no font metadata. **Multi-file
  upload → one job per file** (Just Translate only): each uploaded file is its own book, so the
  dashboard creates one job per file named after the filename; the existing serialized queue runs
  the first and queues the rest (first book viewable while the others process). TeachTown/CVC (folder
  = one unit / word list) and the queue mechanism are untouched. Open follow-ons: word-level karaoke
  (Tier 2), long-book audio scale, TTS-model eval.
- **DONE 2026-07-23 — Fewer TeachTown worksheet fallbacks.** Image-heavy worksheets
  (e.g. "Causes of World War I") degraded to annotate-on-image because the text/image gate
  was binary and text-only won: a sheet with a little incidental/OCR text cleared the 8-word
  threshold, so the vision model never saw the actual picture and returned `worksheet`. Four
  fixes, all in `apps/teachtown/builder.py`: (1) **image-
  escalation retry** — a `worksheet` result from a text-only pass retries ONCE with the rendered
  page (cheap text-first, vision only when needed); (2) **higher-res page** for vision (`_page_image`
  now renders at 1600px, was 1024) so map/diagram labels stay legible; (3) **looser validator** — `_norm()` normalizes
  (casefold/space/punct) before drag-drop/highlight membership checks and snaps the answer to the
  canonical option/item, so a complete-but-string-mismatched activity survives instead of
  downgrading; (4) **softer prompt** — prefer the simplest fitting activity, use `worksheet` only
  for genuinely no-answer sheets. Verified: 15/15 deterministic validator/fold checks + a live
  broker smoke on the real fixtures (Causes of WWI→drag-drop, WWI Matching→match, Earths Cycles
  Label→drag-drop via the retry) + regression suites green (dashboard 16/16, core 24/24).
- **Shipped 2026-07-23 — TeachTown Builder overhaul + Just Translate read-along + robustness.**
  TeachTown: Lesson-Plan files → vocabulary (verbatim defs, reads the whole plan), other files →
  interactive worksheets; per-subject Words-to-Know; always-bilingual (toggle removed); vocab-card
  audio-button layout fix; content+vision **subject** classification; worksheets **never dropped**
  (annotate-on-image fallback); word→definition vocab audio; **Phase B interactive activities**
  (match / drag-drop / highlight / fill-in — a validated `activity` per worksheet rendered as a
  widget beside the reference image, fallback to annotate). Just Translate: **sentence read-along**
  (one EN + one ES track per doc, highlight follows the playhead) + fixed the audio read-timeout
  (sub-batch TTS) + honest BrokerTimeout + media fast-fail retry. Store: deterministic job ordering
  (rowid tiebreak, fixes a flaky test). Runner: `job_finished` emitted only after status flips to
  done (no more "finished but Running"). UI: Builder always-bilingual + Start Job below the folder
  preview. Commits: 1c6651b, 3c1928b, 2d01dc6, 548a1ab, 0923ad7, 1c06759, 962b1a5, 365ff79,
  61241ae, 24ee378, e04591a, e20b93f.
- **SPIKED (Phase 8, 2026-07-22):** cross-app HTML convergence — inventoried the three
  hand-rolled outputs (cvc print worksheet / teachtown warm SPA / slide-audio dark player) and
  scoped an incremental path (shared audio-button atom → per-app adoption → shared tokens; no
  unified skin, which fights each output's purpose). Implementation is a follow-up. See
  `docs/cross-app-html-convergence.md`.
- **DONE (Phase 5, 2026-07-22):** real console scripts — `cvc`, `slide-audio`, `edu-dashboard`
  via `[project.scripts]` + a hatchling build backend on each app (packages installed into the
  workspace venv). `main()` moved into each package (`<pkg>.cli` / `dashboard.serve`); the
  top-level `cli.py`/`serve.py` are thin shims so `python cli.py` + the `.bat` still work.
  NOTE: sync the workspace with `uv sync --all-packages` (plain `uv sync` drops the torch/TTS
  stack the broker's media worker needs). The hosted container is unaffected (it pip-installs).
- **DONE (Phase 7, 2026-07-22):** teachtown mission-grid cards are bilingual — the prompt
  now renders `en-only`/`es-only` via `enrMission()`, matching the dialog, so it follows the
  EN/ES/Both toggle (title stays as the short identifier).
- **DONE (Phase 6, 2026-07-22):** slide/thumbnail rendering consolidated into core
  (`edu_media_core/pdf.py::render_slides`, full JPEG + thumbnail); slide-audio's
  `slide_renderer.py` removed and `pipeline.py` now calls core.
- **DONE (Phase 4, 2026-07-22):** audience/task profiles — `edu_media_core/profiles.py`
  (`Profile` = system prompt + options + required keys + a `voice` slot for future
  per-learner voices) + a registry. The 5 es_MX prompts (cvc_phonics, translate_special_ed,
  teachtown_vocab, teachtown_text, slide_autism_grade2) are now named registered profiles
  their callers look up, instead of loose module constants.
- **DONE (Phase 3, 2026-07-22):** one shared, content-addressed translation cache
  (`edu_media_core/cache.py`) keyed by `(model, system-prompt, content)`; `translate.py`
  and `broker_media.py` default to it, and every caller (cvc, just_translate, teachtown
  enrich, slide-audio) dropped its per-app/per-job cache. On a persistent `edu_cache`
  volume (`EDU_CACHE_DIR=/cache`) so it survives rebuilds and is shared across workflows.
- **DONE (Phase 2, 2026-07-22):** `_join_wrapped` no longer fuses distinct bullets —
  wrap-joining is paragraph-only (`edu-media-core/pdf.py`); characterization test updated.
- **DONE (Phase 1, 2026-07-22):** structured output bundle — no more base64-embedded single
  file. Bundles are now `<job-name>.html` + `images/` + `en-audio/` + `mx-audio/` for cvc,
  `en-audio/`+`mx-audio/` for just_translate, files for echo; teachtown_builder was already a
  multi-file site and is exempt from the HTML rename (its `index.html` is a served, self-loading
  app shell). Root HTML named after the job via `library.bundle_basename` (shared with the
  download name). CVC template stays conditional so the standalone CLI still emits one embedded file.
- **DONE (2026-07-22):** teachtown data-driven from `data.json` (no inline data); EN/ES choice
  persists in `localStorage`; teachtown audio via the broker (no manual `enrich.py`); `content/`
  dedupe + `scripts/ingest_content.py` manifest; core test suite (`packages/edu-media-core/tests`);
  `anthropic` dropped from slide-audio; uv workspace + `uv.lock` at root.
- **SUPERSEDED:** VRAM sequencing — the platform GPU/Model Broker owns one-heavy-model eviction
  across all apps; dashboard workflows declare no `required_model` and the runner builds an empty
  `ModelManager()`. (The standalone slide-audio CLI still loads locally, off the broker by design.)
- **Legacy:** `apps/teachtown/interactive-html/tools/extract-data.js` — `index.html` now holds
  empty `let …={}` stubs filled at runtime; the dashboard builder's `full_data()` is the source.
  Safe to remove.
