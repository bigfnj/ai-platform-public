# Module theming contract

The platform has **one** theming system: a palette (color family) + mode (light/dark)
chosen in the admin theme menu, applied as `data-palette` + `data-theme` on `<html>`.
Every color is a CSS custom property (design token) defined on `:root` in
[`web/src/styles.css`](./src/styles.css). Because custom properties inherit through the
DOM and every federated module renders into the same document, a module gets the whole
palette **for free** — as long as it reads the shared tokens instead of hardcoding
colors.

A new rail is palette-aware automatically if it follows the seven rules below. Reference
implementations: `edu-suite`, `job-aid`, `workstation` (already token-derived),
`finance` (charts kept semantic), `recipe-book` (full adoption + its own typography).

## The shared tokens (the contract surface)

Read these; do not reinvent them.

| Token | Use |
|---|---|
| `--page`, `--surface-1`, `--surface-2`, `--rail` | backgrounds (page → cards → chips) |
| `--text-primary`, `--text-secondary`, `--muted` | text, in falling emphasis. All three reach 4.5:1 against all four backgrounds above, on every palette in both modes, so `--muted` is fine for words. A palette that misses fails `model-chips.test.js` by palette, mode and surface; fix its text token, not the surface |
| `--border` | borders / dividers |
| `--accent` | the palette accent (a single color): fills, borders, dots, focus rings. Not words |
| `--accent-text` | the palette accent for WORDS (the active tab, a link button, a selected pill's label): each palette's accent moved in lightness only to 4.6:1 on all four backgrounds above, bare and under its own `--accent-soft` wash, so a rail can put accent words on any of them; `var(--accent)` where the accent already clears that. Every palette block sets it beside `--accent`. As text, `--accent` itself missed 4.5:1 on 1080 of 2112 palette/mode/surface pairs in web-core alone. In every frontend, `tools/tests/test_status_text_scan.py` fails any `color` that reaches `--accent` |
| `--grad-accent`, `--grad-brand` | the palette gradient (for action surfaces) |
| `--on-accent` | the words ON an action surface: 4.5:1 on every stop of the palette's `--grad-accent`. White where white reads, or where moving the gradient by at most 0.06 in OKLab makes it read; otherwise the palette's own dark ink (`--text-primary` in light, `--page` in dark). Every palette block sets it beside `--accent` |
| `--good`, `--warning`, `--critical`, `--star` | **semantic** — stable across every palette |
| `--info` | **semantic**: informational blue. A light and a dark value; no palette sets it |
| `--chip-missing`, `--chip-cold`, `--chip-warming`, `--chip-loaded` | status DOTS (red / blue / amber / green): the model-chip residency dots, Badge and TagChip dots, the widget's ▶ run marker. A light and a dark value, each 3:1 on every palette's surfaces; no palette sets them |
| `--good-text`, `--warning-text`, `--critical-text` | status as TEXT: the same hues, 4.5:1 on all four backgrounds above, bare and under a 10% tint of their own hue (`color-mix(in srgb, var(--warning) 10%, transparent)`), on every palette in both modes. Warning and critical also fill the top-bar VRAM meter (3:1 against its tinted track). A light and a dark value; no palette sets them |
| `--cli`, `--cli-soft` | **semantic**: "answered by Claude" (the broker's CLI backend); violet on every palette, a brighter `--cli` in dark mode |
| `--shadow`, `--radius` | elevation / corner radius |

## The rules

1. **Derive local tokens from the shared ones, with a fallback** so standalone dev still
   renders. Do this once on your module's wrapper:

   ```css
   .my-rail {
     --ink:     var(--text-primary, #1f2733);
     --muted-c: var(--text-secondary, #68738a);
     --s1:      var(--surface-1, #ffffff);
     --s2:      var(--surface-2, #f2f1ec);
     --bd:      var(--border, #e2e8f0);
     /* --accent / --muted / --good share the platform names → let them inherit,
        do NOT redefine them here. */
     --ac:      var(--accent, #4f7cff);            /* fills, borders, dots */
     --ac-text: var(--accent-text, #4f7cff);       /* WORDS in the accent */
   }
   ```

   Words in the accent read `--accent-text` (or your alias of it), never `--accent`: the scan
   in `tools/tests/test_status_text_scan.py` fails any `color` in any frontend that reaches
   `--accent` through any `var()` chain, fallbacks included. Give the alias your rail's own
   literal as its fallback, not `var(--accent)`, which the scan counts as reaching the accent.

   A colour your rail owns outright (a brand, a data-viz scale, a status hue of its own) is
   measured where it sits: `tools/tests/test_status_fill_contrast.py` holds every rule that
   paints words in one, or puts words on a fill of one, to 4.5:1 on every palette's page and
   surfaces in both modes. If your wrapper sets `--page` and never paints it, your words sit on
   the SHELL's page, and that is what is measured.

2. **Never hardcode surface/accent colors** and never define your own light/dark palette
   or `[data-theme]` block. Don't redefine `--accent`, `--muted`, or `--good` — they
   inherit the chosen palette directly. Where a rail does keep a dark block of its own (a
   data-viz ramp that has to invert), it needs BOTH copies with the same values: the
   `[data-theme='dark']` one for the menu's Dark and a `prefers-color-scheme: dark` one for
   System on a dark OS. With only the first, System-dark keeps the light values;
   `test_status_fill_contrast.py` fails a rail whose two copies differ.

3. **Action surfaces use the gradient.** Primary buttons, active tabs, selected chips,
   checked boxes:

   ```css
   .my-rail .btn.primary,
   .my-rail .tab.on { background: var(--grad-accent, var(--accent)); color: var(--on-accent, #fff); border-color: transparent; }
   ```

   The words read `--on-accent`, never a literal white: white on the accent gradient missed
   4.5:1 on 104 of 132 palette/mode stops (worst 1.51:1, evergreen dark). A solid
   `var(--accent)` fill under words is not an action surface: use the gradient, which is what
   `--on-accent` is held to. `tools/tests/test_status_fill_contrast.py` measures every rule
   with words on an accent fill, in every frontend, on every stop of it.

4. **Data-viz and status colors stay semantic.** Chart series get their own stable scale
   (see finance's `--series-1..8`); status uses `--good` / `--warning` / `--critical`.
   Do **not** repaint these with the palette — data must stay readable and meaningful on
   every theme.

   The shared model chips use five colours that no palette touches. The four residency dots
   (`missing` red / `cold` blue / `warming` amber / `loaded` green) are status and read the
   `--chip-*` tokens. A slot whose `backend` is `claude` gets `.rail-chip-dot.claude`, painted
   `--cli` (violet) with a `--cli-soft` ring, and the top-bar widget's Claude rows, elapsed
   badges and "Claude · working" pill use the same token (`<Badge tone="cli">`).

   All five have a light value on `:root` and a dark value in BOTH dark `:root` blocks (the
   `prefers-color-scheme` one and `[data-theme='dark']`; `--chip-cold` gets its dark value
   through `--info`, which it reads), and no palette sets them. A dot is a
   graphic, so it needs 3:1 against `--page` and `--surface-1` (WCAG 1.4.11), and one colour
   per state cannot get there on every palette: tokyo's light page caps a dot's luminance at
   0.221 and nord's dark surface floors it at 0.231. The dots used to read `--critical` /
   `--warning` / `--good`, which are one colour for both modes; amber and blue missed 3:1 on
   every light palette. `web/tests/model-chips.test.js` resolves every palette in every mode
   and fails by name. A new palette that fails it needs a dot value of its own, not a lower bar.

   Every other small status graphic follows the same rule. `<Badge>` and `<TagChip>` dots of tone
   good / warning / critical read the matching `--chip-*`, the neutral dot reads
   `--text-secondary` (chosen when `--muted` still missed 3:1 against the page), and the accent dot is
   `color-mix(in oklab, var(--accent) 85%, var(--text-primary))`: the palette owns `--accent`,
   which sat at 2.80-2.99:1 on ember light, tokyo light and solarized dark, and mixing toward the
   ink raises contrast in either mode. The same test holds them to 3:1 against what they sit on.
   Status colour as TEXT reads `--good-text` / `--warning-text` / `--critical-text`, held to
   4.5:1 (WCAG 1.4.3) the same way. As text, `--good` / `--warning` / `--critical` missed 4.5:1 on
   53, 44 and 53 of 88 palette/mode/surface pairs. web-core's seven text rules (`.error-line`,
   `.rail-slot-saved`, `.rail-slot-warn`, `.btn-danger:hover`, `.mw-x:hover`, `.vc-speak.rec`,
   `.vc-fine.vc-err`) read the text tokens, and `web/tests/model-chips.test.js` fails any web-core
   rule that sets `color:` from a fixed status token.

   **Every rail's status text reads them too (migrated 2026-10-07).** That includes text that
   reached a fixed token through a rail alias (co-worker's `--p1` / `--p2` / `--ok`,
   ai-playground's `--ok`), which now has a `-text` twin beside it, and two undefined names that
   only their fallback literals were painting (`--success`, `--danger`). An icon coloured with
   `color:` is a graphic, so it reads the `--chip-*` dot tokens instead. Before the migration,
   Chromium measured 2154 of 3080 rail text checks under 4.5:1 across six rails. After it, none.
   `tools/tests/test_status_text_scan.py` reads every frontend source (CSS, style objects, JSX
   attributes, CSS inside template literals), follows `var()` chains and JS values to what they
   can be, and fails any `color` on `--good` / `--warning` / `--critical`, on an undefined status
   name, or on anything it cannot classify, naming the file and line.

   A status TINT behind those words stays at about 10% or less: at 14-18% the 4.5:1 text tokens
   fell to 4.17-4.42:1 on tokyo's darker light page.

   Two more rules, held by `tools/tests/test_status_fill_contrast.py` on every palette in both
   modes:
   - **Words ON a status fill** reach 4.5:1 against the fill, whatever their colour. White on
     `--warning` is 1.83:1, so an amber pill takes dark ink. The ink can come from another rule
     for the same element (`.bq-mode` plus `.bq-mode-florist`), and the test pairs them.
   - **A status graphic** (a dot, a stroke, a card marker, an outline) reaches 3:1, so it reads
     the `--chip-*` dot tokens. A rail alias gets a graphic twin beside its text twin
     (co-worker `--p1-mark`, ai-playground `--ok-dot`).

   What still reads the fixed tokens is what they are for: solid fills behind dark or white
   words that clear 4.5:1 on them (an urgency pill, a danger button), and tints behind words
   held to 4.5:1 by the same test.

   Two wrong turns to avoid for the Claude dot. `var(--accent)` was the first version, and on the
   default palette the accent is blue, so a Claude dot read as a cold one. `--good` would claim
   the local model is resident when the chip is saying something else. A busy Claude chip
   pulses (`.claude.busy`, the warming pulse) and also SAYS "Claude · working", because every
   `rail-chip-pulse` stops under `prefers-reduced-motion` and a still screenshot has no pulse.

5. **Style the element, not just its container.** If you give an `<input>` a wrapper
   class, `.wrapper input` won't match the element itself and it falls back to the
   browser's gray. Style `input.my-class` directly. (This was a real bug.)

6. **Don't hand-roll the header — use `<RailHeader>`** from `@web-core`. Every rail's title
   block is the same shape (icon · bold title · muted subtext · model chips · a rule, with the
   rail's own controls *below* the rule), and it is the most visible surface on the platform, so
   a near-copy per rail drifts within a week — which is exactly what happened before it was
   extracted. Pass `icon` / `title` / `subtitle`, `chips` if the rail has a foreground model
   (omit them if it doesn't — workstation and meeting-atlas legitimately have none), and
   `actions` for the controls row.

   ```tsx
   import { RailHeader, ModelChips } from '@web-core'
   <RailHeader icon="🍳" title="Recipe Book" subtitle="…" chips={…} actions={…} />
   ```

   Consuming `@web-core` from a rail needs BOTH halves or the build fails confusingly: a
   `resolve.alias` + `server.fs.allow` in the rail's `vite.config.ts` (so vite bundles and serves
   it) *and* a `paths` entry in its `tsconfig.json` (so `tsc` resolves it) — including the
   `react`/`react-dom` remaps, since `web/src` is compiled from outside its own folder and would
   otherwise fail TS2307. **RC017** enforces the component; **RC010** enforces the federation
   wiring around it.

7. **Leave these alone**: black modal backdrops / shadows (`rgba(0,0,0,…)`). White text on the
   accent gradient is not one of them any more (rule 3: `--on-accent`).

## How to check a module

Grep the module's CSS for violations:

```bash
# hardcoded surface/accent hex (should be tokens) — ignore #fff-on-gradient + rgba backdrops
rg -n '#[0-9a-fA-F]{3,6}' path/to/module
# solid accent on an action surface (should be the gradient)
rg -n 'background:\s*var\(--accent\)|background:\s*var\(--ac\b'
# a module redefining a shared token (breaks inheritance)
rg -n '^\s*--(accent|muted|good|info|cli|chip-[a-z]+)\s*:'
```

Legit hits: `color:#fff` on gradient fills, `rgba(0,0,0,…)` overlays, chart series /
`--good`/`--critical`. Everything else is a finding.
