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
| `--text-primary`, `--text-secondary`, `--muted` | text |
| `--border` | borders / dividers |
| `--accent` | the palette accent (a single color) |
| `--grad-accent`, `--grad-brand` | the palette gradient (for action surfaces) |
| `--good`, `--warning`, `--critical`, `--star` | **semantic** — stable across every palette |
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
   }
   ```

2. **Never hardcode surface/accent colors** and never define your own light/dark palette
   or `[data-theme]` block. Don't redefine `--accent`, `--muted`, or `--good` — they
   inherit the chosen palette directly.

3. **Action surfaces use the gradient.** Primary buttons, active tabs, selected chips,
   checked boxes:

   ```css
   .my-rail .btn.primary,
   .my-rail .tab.on { background: var(--grad-accent, var(--accent)); color: #fff; border-color: transparent; }
   ```

4. **Data-viz and status colors stay semantic.** Chart series get their own stable scale
   (see finance's `--series-1..8`); status uses `--good` / `--warning` / `--critical`.
   Do **not** repaint these with the palette — data must stay readable and meaningful on
   every theme.

5. **Style the element, not just its container.** If you give an `<input>` a wrapper
   class, `.wrapper input` won't match the element itself and it falls back to the
   browser's gray. Style `input.my-class` directly. (This was a real bug.)

6. **Don't hand-roll the header — use `<RailHeader>`** from `@web-core`. Every rail's title
   block is the same shape (icon · bold title · muted subtext · model chips · a rule, with the
   rail's own controls *below* the rule), and it is the most visible surface on the platform, so
   fourteen near-copies drift within a week — which is exactly what happened before it was
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

7. **Leave these alone** — they read on every palette: white text on gradient fills
   (`color: #fff`), and black modal backdrops / shadows (`rgba(0,0,0,…)`).

## How to check a module

Grep the module's CSS for violations:

```bash
# hardcoded surface/accent hex (should be tokens) — ignore #fff-on-gradient + rgba backdrops
rg -n '#[0-9a-fA-F]{3,6}' path/to/module
# solid accent on an action surface (should be the gradient)
rg -n 'background:\s*var\(--accent\)|background:\s*var\(--ac\b'
# a module redefining a shared token (breaks inheritance)
rg -n '^\s*--(accent|muted|good)\s*:'
```

Legit hits: `color:#fff` on gradient fills, `rgba(0,0,0,…)` overlays, chart series /
`--good`/`--critical`. Everything else is a finding.
