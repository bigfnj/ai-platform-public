# cvc-worksheets

Generates a self-contained, printable **bilingual (English / Mexican Spanish)
phonics worksheet** for young children (ages 4-7) learning CVC words
(consonant-vowel-consonant, e.g. "jab", "web", "cot"). Each word gets a Spanish
translation, a cartoon illustration, English + Spanish audio, letter tracing,
and handwriting lines. Output is one `output/index.html` with all assets inlined.

## Run

```sh
uv run python cli.py                 # full pipeline, all 5 worksheets
uv run python cli.py --worksheet 3   # one worksheet
uv run python cli.py --dry-run       # translate + print only
uv run python cli.py --gen-images    # SDXL-Turbo generate before resolving
uv run python cli.py --skip-audio --skip-images   # fast layout iteration
uv run python cli.py --retranslate   # clear cache, re-query the LLM
```

Open `output/index.html` in a browser (no server needed) — designed for both
screen use and printing.

## How it works

`pipeline.run()` does translate → images → audio → render. The translation,
XTTS audio, and SDXL/clipart image work are provided by
[`edu-media-core`](../../packages/edu-media-core); this app keeps only:

- `words.py` — the `Word` model + `data/words.json` (the 30 curated words).
- `translator.py` — the word-specific es_MX prompt (delegates to the core).
- `audio_gen.py` — per-word WAV caching + base64 (engine is `edu_media_core.tts`).
- `image_gen.py` / `image_fetcher.py` — Word↔subject glue, curated query
  overrides, and the colored placeholder (engine is `edu_media_core.images`).
- `renderer.py` + `templates/index.html.j2` — the Jinja2 single-file output.

## Requirements

- Ollama running with `qwen2.5:32b-instruct-q3_K_M`.
- For audio/images: the torch/CUDA stack (`uv sync` at the repo root) and XTTS
  reference clips in `shared/voices/`. Skip with `--skip-audio --skip-images`.
- Set `OLLAMA_HOST` in `.env` if Ollama is not on `localhost:11434`.
