# edu-media-core

The shared local-first media engine for the suite. It owns the pieces that were
previously copy-pasted (and drifting) between `slide-audio` and `cvc-worksheets`,
and that `teachtown` will reuse for bilingual output.

## Modules

- **`translate`** — Ollama (`qwen2.5:32b-instruct-q3_K_M`) JSON-mode chat with
  content-hash caching. Callers supply the system prompt, user message, cache
  path, per-call options, and required output keys. `translate_cached(...)` is
  the one-call helper; `chat_json`, `content_hash`, `load_cache`/`save_cache`,
  `cache_has`, `clear_cache` are the primitives.
- **`tts`** — Coqui XTTS v2 voice-clone engine: one in-memory model, the
  `weights_only` monkey-patch, reference-clip resolution (`shared/voices`, or
  `$VOICES_DIR`), `synthesize_segment(text, lang)`, `generate_silence`,
  `combine_and_save`, `wav_to_b64`, and `generate_timed_audio(script)` which
  returns per-segment `{lang,type,text,start,end}` timings.
- **`images`** — SDXL-Turbo generation (`generate_image(subject, out_path)`) and
  DuckDuckGo clipart search (`search_clipart`, `resize_png`).
- **`pdf`** — `read_slides(pdf_path)`: pdfplumber extraction with a PyMuPDF +
  tesseract OCR fallback for image-only pages, plus line-unwrapping.
- **`classify`** — `classify_slides(slides)`: labels header/content/empty/
  duplicate and assigns week numbers.

## Notes

- Heavy deps (`torch`, `TTS`, `diffusers`) are imported lazily inside the
  functions that need them, so importing `edu_media_core.translate` or
  `edu_media_core.classify` does not pull in CUDA.
- Reference voice clips live in `shared/voices` at the repo root and are not
  committed; see that folder's README.
