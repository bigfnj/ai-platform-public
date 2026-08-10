# teachtown

Turns TeachTown enCORE curriculum into a child-friendly interactive experience:
unit → week → subject navigation, weekly learning summaries, picture-supported
vocabulary, and interactive activities (multiple-choice, typing, sorting) with
worksheet rendering and annotation. Now **bilingual (EN / es_MX) with audio**.

## Run the site

```sh
cd interactive-html
node serve.js            # then open http://127.0.0.1:8765/
# or double-click "Open TeachTown.bat"
```

A local server is required so PDF.js can fetch the worksheet PDFs. The page
works English-only out of the box.

## Add bilingual audio (optional)

```sh
# from apps/teachtown/
uv run python enrich.py            # es_MX text + EN/ES audio (needs Ollama + XTTS/GPU)
uv run python enrich.py --no-audio # es_MX text only (no GPU)
```

`enrich.py` reads `interactive-html/data.json`, uses
[`edu-media-core`](../../packages/edu-media-core) to translate the vocabulary,
weekly summaries, and mission prompts to Mexican Spanish and synthesize audio,
and writes `interactive-html/enrichment.json` (+ WAVs under `public/audio/`).
The page loads it automatically and shows an **EN / ES / Both** toggle plus
per-item 🔊 buttons. Without it, the page stays English-only.

`enrichment.sample.json` shows the format. `data.json` is generated from the
page's inline content by `tools/extract-data.js` (`node tools/extract-data.js`).

## Files

- `interactive-html/index.html` — the single-file app (inline English data + the
  bilingual layer). `worksheet-renderer.js` + `vendor/` render worksheet PDFs.
- `interactive-html/public/worksheets/` — worksheet PDF copies the site serves.
- `enrich.py` — the bilingual/audio enrichment (uses the core).

Source curriculum PDFs live in the suite pool at `/content` (see its README).
`PROJECT_MEMORY.md` here is the pre-merge standalone history.
