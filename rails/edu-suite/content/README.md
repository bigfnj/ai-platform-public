# content

The shared curriculum source pool for the whole suite. Every app draws from
here instead of keeping its own `intake/`.

## Layout

```
content/
├── <grade>/<unit-name>-MM-DD-YYYY/
│   ├── Week 1/  Week 2/  Week 3/  Week 4/   (PDFs)
│   └── (unit-level PDFs, e.g. the companion text)
└── manifest.json   (generated — see below)
```

`<grade>` is `middle-school` or `high-school` (optional; units may also sit
directly under `content/`).

## Adding a unit

1. Drop the unit folder under the correct grade (one `Week N` folder per week).
2. Regenerate the inventory:

   ```sh
   python scripts/ingest_content.py
   ```

   This writes `content/manifest.json`: every unit → week → file, each classified
   by subject (ELA/Math/Science/Social Studies) and type (worksheet, reading,
   warmup, teacher-guide, answer-key, companion) from filename heuristics, plus a
   summary. It is the suite's single inventory of source material.

3. Apps consume the manifest (or the files directly). Turning a new unit into a
   teachtown interactive unit or a slide-audio deck is still an authoring step;
   the manifest tells you what is available and how it classifies.

`manifest.json` is committed so the inventory is visible without a scan.
