# TeachTown Units - Project Memory

Last updated: July 11, 2026

## Project purpose

This project converts TeachTown enCORE curriculum PDFs into organized, interactive instructional materials for both Middle School and High School use throughout the year.

## School-level routing

Source and generated files are separated by grade band:

```text
intake/
├── middle-school/
└── high-school/

outputs/
├── middle-school/
└── high-school/
```

Place each future unit in its own dated folder under the correct `intake` category. The intake and output folders contain README files with routing guidance.

## Current units

### Middle School

- Unit: Malala
- Source: `intake/middle-school/malala-07-10-2026/`
- Four weekly folders
- Subjects represented include ELA, Math, Science, and Social Studies
- PowerPoint output: `outputs/middle-school/malala-unit-by-week-and-subject.pptx`

### High School

- Unit: Two Different Beats
- Source: `intake/high-school/two-different-beats-07-10-2026/`
- Four weekly folders
- Subjects represented include ELA, Math, Science, and Social Studies
- PowerPoint output: `outputs/high-school/two-different-beats-unit-by-week-and-subject.pptx`

## Google Slides created

Native Google Slides versions were created earlier in the session:

- Malala: https://docs.google.com/presentation/d/1THMZ6AxGrB-sxMojVg5g6WwgcFPbwx0k1TugOJ9HtKk/edit
- Two Different Beats: https://docs.google.com/presentation/d/1lqUT0UWIfuzqV_fqHUvvS46TgX1_X_eTfwHELMND7jU/edit

The decks contain weekly and subject organization, learning objectives, AAC/core vocabulary, student-friendly definitions, and interactive activity slides. The HTML experience became the preferred direction after the Slides experiments.

## Interactive HTML

Location:

`interactive-html/`

Primary standalone file:

`interactive-html/index.html`

Recommended launcher:

`interactive-html/Open TeachTown.bat`

The launcher starts a local server and opens:

http://127.0.0.1:8765/

The local server is required for reliable PDF.js worksheet rendering. Opening `index.html` directly may prevent the browser from loading local PDF files correctly.

## HTML features

- Separate selectors for:
  - Middle School - Malala
  - High School - Two Different Beats
- Four-week navigation
- Subject filters
- Child-friendly mission-card design
- Weekly learning summaries
- Vocabulary words with student-friendly definitions
- Picture symbols for vocabulary support
- Original fillable worksheet PDFs
- PDF.js rendering instead of the browser's embedded PDF viewer
- Complete page rendering without hidden PDF-frame clipping
- Numbered editable answer panel beside the worksheet
- Direct worksheet annotation tools:
  - Type
  - Checkmark
  - X mark
  - Circle
  - Drag annotations
  - Undo
  - Clear
- Multiple-choice, typing, and sorting interactions
- Progress stars
- Responsive layouts for desktop and smaller screens

## Worksheet source policy

Only genuine standalone student worksheet PDFs should be shown as worksheets.

Do not substitute:

- Teacher-guide pages
- Invented worksheet content
- A worksheet from another subject
- A visually similar worksheet used as a placeholder

The standalone HTML currently references all 28 fillable worksheet PDFs found in the original intake materials. Science or Social Studies missions without a genuine standalone worksheet were removed rather than displaying fabricated replacements.

## Important technical decisions

### PDF rendering

The browser's built-in PDF `<object>` viewer caused nested scrollbars, clipping, independent scaling, and annotation misalignment. It was replaced with a locally bundled PDF.js renderer.

PDF.js files are stored under:

`interactive-html/vendor/`

The renderer is:

`interactive-html/worksheet-renderer.js`

The local server is:

`interactive-html/serve.js`

### Worksheet answers

Automatic answer boxes are displayed in a numbered panel beside the rendered worksheet. They are not placed over the PDF because responsive overlay coordinates could not remain accurate across browsers and screen sizes.

Manual annotation remains available for marking directly on the worksheet.

### Worksheet copies

The HTML project contains copied worksheet sources under:

`interactive-html/public/worksheets/intake/`

These copies preserve the existing site even though the project intake folders were later reorganized by school level.

## Known limitations

- New units dropped into `intake` are not automatically imported into the HTML experience.
- Future units must be reviewed, classified by school level, and added to the HTML data model.
- The worksheet answer panel is numbered but does not automatically grade every source worksheet.
- Annotation and progress state are session-local and are not stored in a database.
- The Next/Vinext project scaffold under `interactive-html/app/` was started, but the standalone HTML plus local server is the reliable deliverable used now.

## Recommended future workflow

1. Drop a new unit into either:
   - `intake/middle-school/`
   - `intake/high-school/`
2. Inventory the PDFs by week and subject.
3. Separate teacher guides, companion texts, and genuine student worksheets.
4. Add only genuine student worksheets to the interactive worksheet library.
5. Add weekly learning summaries and vocabulary with definitions and picture symbols.
6. Copy required worksheet PDFs into `interactive-html/public/worksheets/intake/` or update the site to read from a generated manifest.
7. Update the HTML unit data.
8. Validate every worksheet-to-subject mapping.
9. Launch with `Open TeachTown.bat` and visually test every week and subject.
10. Place generated exports in the matching `outputs` grade-band folder.

## Best next enhancement

Create an intake manifest and processing script that scans the Middle School and High School intake folders, records unit/week/subject/file type, and generates the HTML navigation automatically. This would make future unit drops substantially easier and reduce manual mapping errors.
