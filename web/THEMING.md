# Module theming contract

The platform has **one** theming system: a palette (color family) + mode (light/dark)
chosen in the admin theme menu, applied as `data-palette` + `data-theme` on `<html>`.
Every color is a CSS custom property (design token) defined on `:root` in
[`web/src/styles.css`](./src/styles.css). Because custom properties inherit through the
DOM and every federated module renders into the same document, a module gets the whole
palette **for free** — as long as it reads the shared tokens instead of hardcoding
colors.

A new rail is palette-aware automatically if it follows the seven rules below. Reference
implementations: `edu-suite`, `workstation` (already token-derived), `ai-playground`
(charts kept semantic), `recipe-book` (full adoption + its own typography).

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
   (e.g. a dedicated `--series-1..8`); status uses `--good` / `--warning` / `--critical`.
   Do **not** repaint these with the palette — data must stay readable and meaningful on
   every theme.

5. **Style the element, not just its container.** If you give an `<input>` a wrapper
   class, `.wrapper input` won't match the element itself and it falls back to the
   browser's gray. Style `input.my-class` directly. (This was a real bug.)

6. **Give the header a rule.** Every rail closes its header with a 2px divider and 18px of
   breathing room above it, so the shell reads as one product rather than a set of apps that
   happen to be adjacent:

   ```css
   .my-rail .head { padding-bottom: 18px; border-bottom: 2px solid var(--ink); }
   ```

   Use your local `--ink` alias (derived from `--text-primary`), not a literal: the rule then
   inverts correctly with light/dark and needs no per-theme override. The 18px is not
   decoration — a rail with a status-chip row under its title crowds the line without it.
   Rails with no titled header are exempt: `workstation` is a full-height terminal whose top
   strip is a control toolbar, and a bold rule under it would just be noise.

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
