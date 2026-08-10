# TeachTown Adventures — interactive HTML

A single-file, offline interactive experience that turns TeachTown enCORE
curriculum worksheets into child-friendly "missions" for Middle School and High
School special-education students.

## What this is

- **[index.html](index.html)** — the deliverable. A self-contained SPA (inline
  data, CSS, and logic) with unit/week/subject navigation, weekly learning
  summaries, picture-supported vocabulary, and interactive activities
  (multiple-choice, typing, sorting). Worksheet missions render the original
  fillable PDF with direct annotation tools (type / check / X / circle / drag /
  undo / clear) and a numbered answer panel.
- **[worksheet-renderer.js](worksheet-renderer.js)** + **[vendor/](vendor)** —
  PDF.js renderer. Replaces the browser's built-in PDF viewer, which caused
  clipping, nested scrollbars, and annotation misalignment.
- **[serve.js](serve.js)** — a tiny static file server on
  `http://127.0.0.1:8765/`. Required so PDF.js can fetch the local worksheet
  PDFs; opening `index.html` from the filesystem can block PDF loading.
- **[public/worksheets/intake/](public/worksheets)** — copies of the genuine
  student worksheet PDFs the site references.
- `annotation.css`, `vocabulary.css`, `worksheet-size.css` — styles for the
  annotation layer, vocabulary cards, and worksheet sizing.

## Run it

Double-click **[Open TeachTown.bat](Open%20TeachTown.bat)** (starts the server
and opens the browser), or from a terminal:

```sh
node serve.js
# then open http://127.0.0.1:8765/
```

Requires Node.js on PATH (or a standard Windows install at
`%ProgramFiles%\nodejs`). The launcher resolves Node automatically.

## Worksheet source policy

Only genuine standalone student worksheet PDFs are shown as worksheets. Teacher
guides, companion texts, invented content, and visually-similar placeholders are
never substituted. Missions without a real worksheet were removed rather than
faked.

## Legacy / not used

- **[_abandoned/](.)** context: `app/` plus the Next.js/vinext config files
  (`package.json`, `next.config.ts`, `vite.config.ts`, `tsconfig.json`,
  `drizzle*`, `worker/`, `db/`, `examples/`, `tests/`, `build/`, `.openai/`,
  `.vinext/`) are an **abandoned earlier React/Next port** of this app. It used
  the rejected `<object>` PDF embed and an older activity set, and is superseded
  by `index.html`. `app/page.tsx` is kept only as reference for a possible future
  React rebuild; the rest is regenerable vinext starter scaffolding slated for
  removal.

See the repo-root `PROJECT_MEMORY.md` for full project context.
