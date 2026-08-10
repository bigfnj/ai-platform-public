# Cross-app HTML convergence — design spike

Backlog Phase 8. The three generated HTML outputs are hand-rolled, independent HTML/CSS
systems. This spike inventories them, separates what's genuinely shareable from what's
intentionally different, and proposes an **incremental** convergence — not a big-bang
rewrite (which would regress three working, shipping outputs).

## Inventory

| Output | Where | Theme | Structure | Bilingual UX | Audio control |
|---|---|---|---|---|---|
| **CVC worksheet** | `cvc-worksheets/templates/index.html.j2` (~573 ln) | Light, **print-first**; per-vowel color palettes (`VOWEL_COLORS`) | One block per word: image, EN/ES labels, trace + write areas | EN + ES labels shown together | `▶ English` / `▶ Español` buttons → hidden `<audio>` |
| **TeachTown site** | `teachtown/interactive-html/index.html` (inline CSS+JS) | Warm light (`#fffaf0`), hero gradient | Interactive SPA: hero → weeks → subjects → mission cards → dialog; annotation layer | **EN / ES / Both** toggle (`.langbar`), `.en-only`/`.es-only` | `audioBtn()` 🔊 → `new Audio(src)` |
| **Slide-audio player** | `slide-audio/report.py::write_html_player` (~490 ln) | **Dark** (`#0d1117` GitHub-dark) | Karaoke player: nav sidebar + side-by-side EN/ES columns + transport controls | Side-by-side EN/ES columns | `.ctrl-btn` transport (prev/next/play) |

## What's genuinely shared vs intentionally different

**Shared (worth converging):**
- **Audio play button** — all three hand-roll a play control over an `<audio>`/`new Audio`. One styled atom + one tiny play helper would replace three.
- **Language presentation primitives** — the `.en-only`/`.es-only` + a toggle already exist in teachtown; cvc and slide-audio express the same idea differently. A shared `lang-toggle` + visibility classes could unify the *mechanism* (not the layout).
- **Design tokens** — color, spacing, radius, font stacks are re-declared in each. A shared set of CSS custom properties (`--radius`, `--space-*`, font stacks, a status palette) would cut duplication and drift.

**Intentionally different (do NOT converge):**
- **Theme**: a print worksheet, a warm kid-facing SPA, and a dark karaoke player *should* look different. Forcing one skin would hurt each use case.
- **Layout/structure**: print blocks vs SPA navigation vs transport player are fundamentally different UIs. No shared page shell.

## Recommended path (incremental, low-risk)

Ship as small, independently-verifiable steps — never a simultaneous rewrite:

1. **Shared atoms, not a skin.** Add a tiny shared CSS/JS snippet pair (e.g. `edu_media_core`
   ships an `assets/` with `audio-button.css` + a 6-line `play-audio.js`, or a Jinja/py helper
   that emits them). Scope: the audio button + the `new Audio(src).play()` handler only.
2. **Adopt per app, one at a time**, re-verifying each output after: teachtown → cvc → slide-audio.
   Each adoption is a self-contained change with a visual check; roll back independently if it regresses.
3. **Tokens second.** Once the atom is shared, extract a small `tokens.css` (custom properties)
   and have each app import it and map its theme onto the tokens (keeping its own palette values).
4. **Stop there.** Do NOT attempt a shared page shell or unified theme — that's the "big/not
   near-term" part the backlog flagged, and it fights each output's purpose.

## Effort / risk

- Steps 1–2 (audio atom): ~half a day, low risk (additive; per-app verification).
- Step 3 (tokens): ~half a day, low risk.
- Regression surface: each app's generated HTML is user-facing output — every adoption needs a
  visual check (open the bundle) before committing.

## Status

Spiked and scoped. Implementation is a **follow-up** to be picked up incrementally (steps 1→3
above), not part of the current backlog clear-out — consistent with the item's original
"big; not near-term" framing. Tracked here rather than as a vague one-liner.
