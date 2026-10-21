// Mutation harness for the model-chip view. NOT a test file: it rewrites the source.
//
//     node web/tests/mutate_model_chips.mjs
//
// Each mutation is a plausible wrong edit to src/model_chip_view.ts or src/styles.css, and the
// question is whether model-chips.test.js goes red AND the red test is the one that guards that
// claim. styles.css is in scope because the first version of the Claude chip passed every test
// here and still painted its dot the same blue as a cold one: the class was right and the colour
// behind it was the palette accent, which no test read.
//
// "Named" here means the expected test is reported as FAILED (`not ok N - <name>` in TAP).
// A check that only looks for the name in the output cannot fail: node prints every test's
// name, passing or not, so it would read FIRED for any mutation that broke anything at all.
// The baseline also has to report each expected test as `ok`, which is the witness that the
// suite ran the code at all; a 0/N or N/N against a suite that never executed means nothing.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// The shared runner: a timeout kills the whole tree (node --test's per-file child
// included, which spawnSync's own timeout left running), and a restore is proven.
import { restoreExact, runBoundedSync } from '../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from './mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const SRC = join(REPO, 'web', 'src', 'model_chip_view.ts')
const CSS = join(REPO, 'web', 'src', 'styles.css')
const SUITE = join(REPO, 'web', 'tests', 'model-chips.test.js')
// Broker's CLI methods (the blockers it reports among them), which broker.py runs as its own.
const BROKER = join(REPO, 'services', 'broker', 'app', 'broker_cli.py')
// A suite that hangs (an import that never settles, a stray server) must not hang the harness.
const SUITE_TIMEOUT_MS = 60_000

// `file` defaults to SRC.
const MUTATIONS = [
  {
    label: 'a claude chip shows the local state text instead of "Claude"',
    old: "  if (isClaudeChip(m)) return m.busy ? 'Claude · working' : 'Claude'",
    new: '  if (isClaudeChip(m)) return MODEL_STATE_TEXT[m.state]',
    expect: 'a claude chip reads "Claude" in every state',
  },
  {
    label: 'a busy claude chip says only "Claude" (activity left to the pulse alone)',
    old: "  if (isClaudeChip(m)) return m.busy ? 'Claude · working' : 'Claude'",
    new: "  if (isClaudeChip(m)) return 'Claude'",
    expect: 'a busy claude chip says so in its text',
  },
  {
    label: 'busy is ignored on a claude chip (no pulse)',
    old: "  if (isClaudeChip(m)) return `rail-chip-dot claude${m.busy ? ' busy' : ''}`",
    new: '  if (isClaudeChip(m)) return `rail-chip-dot claude`',
    expect: 'busy adds the pulse class on a claude chip',
  },
  {
    label: 'the fallback warning fires for ok:true as well',
    old: '      !!e[1] && e[1].ok === false)',
    new: '      !!e[1])',
    expect: 'no warning when the latest call succeeded',
  },
  {
    label: 'an interrupted stream reads as a fallback again',
    old: '  const cut = (o: Extract<CliRoleOutcome, { ok: false }>) => o.reason === CLI_INTERRUPTED',
    new: '  const cut = (o: Extract<CliRoleOutcome, { ok: false }>) => o.reason === "cli_interrupted"',
    expect: 'an interrupted stream is named with its cause, never as a fallback',
  },
  {
    label: 'an interrupted stream loses its cause',
    old: "  const cause = (o: Extract<CliRoleOutcome, { ok: false }>) => o.cause || 'unknown'",
    new: "  const cause = (o: Extract<CliRoleOutcome, { ok: false }>) => o.reason || 'unknown'",
    expect: 'an interrupted stream is named with its cause, never as a fallback',
  },
  {
    label: 'an interruption beside a fallback hides the fallback',
    old: "    ...(reasons.length ? [`fell back (${reasons.join(', ')})`] : []),",
    new: "    ...(reasons.length && !causes.length ? [`fell back (${reasons.join(', ')})`] : []),",
    expect: 'an interrupted stream is named with its cause, never as a fallback',
  },
  {
    label: 'a row is rendered when the cli key is absent',
    old: '  if (!cli || !Array.isArray(cli.active)) return []',
    new: "  if (!cli || !Array.isArray(cli.active)) return [{ key: 'ghost', label: 'Claude', "
      + "sub: null, elapsed: '0s', requester: null, title: '' }]",
    expect: 'no rows when the cli key is absent',
  },
  {
    label: 'busy leaks onto a local chip (the old-broker path changes)',
    old: '  return `rail-chip-dot ${m.state}`',
    new: "  return `rail-chip-dot ${m.state}${m.busy ? ' busy' : ''}`",
    expect: 'an ollama chip is unchanged, and busy does nothing to it',
  },
  {
    label: 'an unknown backend value is treated as Claude',
    old: "  return m.backend === 'claude'",
    new: "  return !!m.backend && m.backend !== 'ollama'",
    expect: 'an ollama chip is unchanged, and busy does nothing to it',
  },
  // --- a slot served off the broker (backend "external") -------------------------------------
  {
    label: "an external chip shows the rail's guessed state as residency",
    old: "  if (isExternalChip(m)) return 'external'\n",
    new: '',
    expect: 'an external chip says where it runs, not a residency nobody measured',
  },
  {
    label: 'an external chip paints the residency dot of its guessed state',
    old: "  if (isExternalChip(m)) return 'rail-chip-dot external'\n",
    new: '',
    expect: 'an external chip says where it runs, not a residency nobody measured',
  },
  {
    label: 'an external chip shows "@external" beside the word external',
    old: '  if (isExternalChip(m)) return null\n',
    new: '',
    expect: 'an external chip says where it runs, not a residency nobody measured',
  },
  {
    label: 'an external chip takes the remote tooltip, which calls it another box of the platform',
    old: '  if (isExternalChip(m)) {\n',
    new: '  if (isExternalChip(m) && false) {\n',
    expect: 'an external chip says where it runs, not a residency nobody measured',
  },
  {
    label: 'every backend nobody sends is read as external',
    old: "  return m.backend === 'external'",
    new: "  return !!m.backend && m.backend !== 'claude' && m.backend !== 'ollama'",
    expect: 'an ollama chip is unchanged, and busy does nothing to it',
  },
  {
    label: 'the external dot is painted the cold blue',
    file: CSS,
    old: '.rail-chip-dot.external { background: var(--text-secondary); }',
    new: '.rail-chip-dot.external { background: var(--chip-cold); }',
    expect: 'the external dot is the neutral ink, never a residency or Claude colour',
  },
  {
    label: 'the external dot borrows the --cli ring',
    file: CSS,
    old: '.rail-chip-dot.external { background: var(--text-secondary); }',
    new: '.rail-chip-dot.external { background: var(--text-secondary); box-shadow: 0 0 0 2px var(--cli-soft); }',
    expect: 'the external dot is the neutral ink, never a residency or Claude colour',
  },
  {
    label: 'the external dot is painted with the --grid hairline colour (under 3:1)',
    file: CSS,
    old: '.rail-chip-dot.external { background: var(--text-secondary); }',
    new: '.rail-chip-dot.external { background: var(--grid); }',
    expect: 'every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode',
  },
  {
    label: 'the claude tooltip stops naming the fallback state',
    old: "      + ` · local fallback: ${m.model ?? '?'} (${fallbackState})`",
    new: "      + ` · local fallback: ${m.model ?? '?'}`",
    expect: 'the claude tooltip names the backend and the local fallback with its state',
  },
  {
    label: 'an old-broker chip loses its model name',
    old: "  return `${m.label}${m.model ? ` (${m.model})` : ''}`",
    new: '  return m.label',
    expect: 'an entry with no backend renders exactly as before',
  },
  // --- the trigger pill --------------------------------------------------------------------
  {
    label: 'the trigger pill shows with no call in flight',
    old: '  if (rows.length === 0) return null\n',
    new: '',
    expect: 'the trigger pill appears only while a Claude call is in flight',
  },
  {
    label: 'the trigger pill stops counting calls',
    old: "    text: rows.length === 1 ? 'Claude · working' : `Claude ×${rows.length} · working`,",
    new: "    text: 'Claude · working',",
    expect: 'the trigger pill counts calls and names each one',
  },
  {
    label: 'the trigger pill title drops whose call it is',
    old: "      + `${r.requester ? ` for ${r.requester}` : ''}, ${r.elapsed}`)",
    new: '      + `, ${r.elapsed}`)',
    expect: 'the trigger pill counts calls and names each one',
  },
  // --- styles.css --------------------------------------------------------------------------
  {
    label: 'the claude chip dot goes back to the palette accent',
    file: CSS,
    old: '.rail-chip-dot.claude  { background: var(--cli);',
    new: '.rail-chip-dot.claude  { background: var(--accent);',
    expect: 'the claude dot and the widget paint with --cli, not the accent',
  },
  {
    label: "the widget's Claude row dot goes back to the palette accent",
    file: CSS,
    old: '.mw-job-dot.claude { color: var(--cli); }',
    new: '.mw-job-dot.claude { color: var(--accent); }',
    expect: 'the claude dot and the widget paint with --cli, not the accent',
  },
  {
    label: 'the cli badge dot goes back to the palette accent',
    file: CSS,
    old: '.badge-cli .badge-dot { background: var(--cli); }',
    new: '.badge-cli .badge-dot { background: var(--accent); }',
    expect: 'the claude dot and the widget paint with --cli, not the accent',
  },
  {
    // The default palette's accent, i.e. the original bug. It still clears 3:1 in light mode,
    // so only distinctness can catch it (a paler blue would be caught by contrast first).
    label: '--cli repainted the accent blue (next to the cold dot)',
    file: CSS,
    old: '  --cli: #a855f7;',
    new: '  --cli: #2a78d6;',
    expect: '--cli is distinct from every residency colour',
  },
  {
    label: 'a palette redefines --cli',
    file: CSS,
    old: "[data-palette='dracula'] {\n",
    new: "[data-palette='dracula'] {\n  --cli: #bd93f9;\n",
    expect: '--cli and the chip tokens come from the :root rules alone, never from a palette',
  },
  // --- dot contrast (WCAG 1.4.11) and the per-mode tokens ------------------------------------
  {
    label: 'a palette page darkens past the --cli dot (solarized light)',
    file: CSS,
    old: "[data-palette='solarized'] {\n  --page: #fdf6e3;",
    new: "[data-palette='solarized'] {\n  --page: #d8d2c0;",
    expect: 'every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode',
  },
  {
    label: 'the light amber dot goes back to the old --warning value',
    file: CSS,
    old: '  --chip-warming: #a46a00;',
    new: '  --chip-warming: #fab219;',
    expect: 'every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode',
  },
  {
    label: 'the warming dot rule reads the fixed status token again',
    file: CSS,
    old: '.rail-chip-dot.warming { background: var(--chip-warming);',
    new: '.rail-chip-dot.warming { background: var(--warning);',
    expect: 'every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode',
  },
  {
    label: '--info is undefined again in light mode',
    file: CSS,
    old: '  --info: #1668d8;          /* informational blue; the cold dot. Was used, never defined. */\n',
    new: '',
    expect: 'every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode',
  },
  {
    label: 'the dark --cli is set in the OS-dark block only, not for an explicit Dark',
    file: CSS,
    // 2-space indent after the newline: the explicit block (the OS-dark copy has 4).
    old: '\n  --cli: #bf6bff;\n  --good-text: #56d364;',
    new: '\n  --good-text: #56d364;',
    expect: 'the dark dots are the same whether dark comes from the OS or from the theme menu',
  },
  {
    // The 2-space indent after the newline is the explicit block; the OS-dark copy has 4.
    label: 'explicit Dark (no palette) loses --grad-brand again',
    file: CSS,
    old: '\n  --grad-brand: linear-gradient(150deg, #3987e5, #8b7cff);\n  --shadow: none;\n',
    new: '\n  --shadow: none;\n',
    expect: 'explicit Dark resolves every token exactly as System does on a dark OS, on every palette',
  },
  {
    label: "nord's explicit Dark page drifts from its OS-dark page",
    file: CSS,
    old: "[data-palette='nord'][data-theme='dark'] {\n  color-scheme: dark;\n  --page: #2e3440;",
    new: "[data-palette='nord'][data-theme='dark'] {\n  color-scheme: dark;\n  --page: #2e3441;",
    expect: 'explicit Dark resolves every token exactly as System does on a dark OS, on every palette',
  },
  // --- badge dots, tag dots, the widget run marker (3:1 against what they sit on) ----------
  {
    label: 'the warning badge dot goes back to the fixed --warning',
    file: CSS,
    old: '.badge-warning .badge-dot { background: var(--chip-warming); }',
    new: '.badge-warning .badge-dot { background: var(--warning); }',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  {
    label: 'the accent badge dot loses its mix toward the ink (ember light 2.99:1)',
    file: CSS,
    old: '.badge-accent .badge-dot { background: color-mix(in oklab, var(--accent) 85%, var(--text-primary)); }',
    new: '.badge-accent .badge-dot { background: var(--accent); }',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  {
    // This used to put the dot back on --muted. --muted now reaches 4.5:1 as text on every
    // surface, so it clears 3:1 as a dot too and that edit is no longer wrong; --grid, the
    // chart hairline colour, is the faint token somebody could still reach for.
    label: 'the neutral badge dot is painted with the --grid hairline colour',
    file: CSS,
    old: 'border-radius: 50%; background: var(--text-secondary); }',
    new: 'border-radius: 50%; background: var(--grid); }',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  {
    label: 'the widget run marker goes back to the fixed --good',
    file: CSS,
    old: '.mw-job-dot.active { color: var(--chip-loaded); }',
    new: '.mw-job-dot.active { color: var(--good); }',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  {
    // Explicit-Dark copy only (2-space indent after the newline): nord's dark surface-2 is the
    // one place the old green sat under 3:1 (2.997) once tag dots were measured.
    label: 'the dark green dot token goes back to #0ca30c',
    file: CSS,
    old: '\n  --chip-loaded: #12a812;',
    new: '\n  --chip-loaded: #0ca30c;',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  // --- status TEXT (4.5:1) -------------------------------------------------------------------
  {
    label: 'a status text rule goes back on --warning',
    file: CSS,
    old: '.rail-slot-warn { color: var(--warning-text);',
    new: '.rail-slot-warn { color: var(--warning);',
    expect: 'every status text rule reaches 4.5:1 against what it sits on, on every palette and mode',
  },
  {
    label: 'the light --critical-text goes back to the fixed red',
    file: CSS,
    old: '  --critical-text: #ab0f21;',
    new: '  --critical-text: #d03b3b;',
    expect: 'every status text rule reaches 4.5:1 against what it sits on, on every palette and mode',
  },
  {
    label: 'the explicit-Dark --warning-text dims below 4.5:1',
    file: CSS,
    old: '\n  --warning-text: #f5c451;',
    new: '\n  --warning-text: #a07a20;',
    expect: 'every status text rule reaches 4.5:1 against what it sits on, on every palette and mode',
  },
  {
    label: 'the mic-picker error reads the undefined --danger again',
    file: CSS,
    old: '.vc-fine.vc-err { color: var(--critical-text); }',
    new: '.vc-fine.vc-err { color: var(--danger, #e5534b); }',
    expect: 'no web-core rule paints text with a fixed status token or an undefined one',
  },
  {
    label: 'a palette sets --good-text',
    file: CSS,
    old: "[data-palette='gold'] {\n",
    new: "[data-palette='gold'] {\n  --good-text: #3a7d00;\n",
    expect: '--cli and the chip tokens come from the :root rules alone, never from a palette',
  },
  {
    label: "the danger button's hover border goes back on --critical",
    file: CSS,
    old: '.btn-danger:hover:not(:disabled) { border-color: var(--critical-text);',
    new: '.btn-danger:hover:not(:disabled) { border-color: var(--critical);',
    expect: 'every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on',
  },
  // --- the VRAM bar fills (3:1 against the painted track) ------------------------------------
  {
    label: 'the warning fill goes back on the fixed --warning',
    file: CSS,
    old: '.mw .mw-fill.warning { background: var(--warning-text); }',
    new: '.mw .mw-fill.warning { background: var(--warning); }',
    expect: "the VRAM bar's warning and critical fills reach 3:1 against the track and the surface",
  },
  {
    label: 'the critical fill uses the dot token (fails on tinted dark tracks)',
    file: CSS,
    old: '.mw .mw-fill.critical { background: var(--critical-text); }',
    new: '.mw .mw-fill.critical { background: var(--chip-missing); }',
    expect: "the VRAM bar's warning and critical fills reach 3:1 against the track and the surface",
  },
  {
    label: 'the explicit-Dark --critical-text goes back to #ff8a80 (fine as text, not as a fill)',
    file: CSS,
    old: '\n  --critical-text: #ffb4ab;',
    new: '\n  --critical-text: #ff8a80;',
    expect: "the VRAM bar's warning and critical fills reach 3:1 against the track and the surface",
  },
  {
    // The model reads the wash from the rule (--surface-1 at N% over the gradient, so the wash
    // is 100 - N); a heavier wash must change the verdict.
    label: "the top bar's gradient wash gets much heavier",
    file: CSS,
    old: 'color-mix(in srgb, var(--surface-1) 78%, transparent),\n'
      + '                    color-mix(in srgb, var(--surface-1) 78%, transparent)) padding-box,',
    new: 'color-mix(in srgb, var(--surface-1) 10%, transparent),\n'
      + '                    color-mix(in srgb, var(--surface-1) 10%, transparent)) padding-box,',
    expect: "the VRAM bar's warning and critical fills reach 3:1 against the track and the surface",
  },
  {
    // A background of another shape (the old ::before wash, say) is refused, not measured as
    // if it were this one.
    label: "the top bar's background stops being the modelled wash",
    file: CSS,
    old: '    var(--grad-accent) padding-box,\n    var(--surface-1);\n',
    new: '    var(--surface-1);\n',
    expect: "the VRAM bar's warning and critical fills reach 3:1 against the track and the surface",
  },
  {
    label: 'the light amber dot drifts to red (still 3:1, no longer distinct)',
    file: CSS,
    old: '  --chip-warming: #a46a00;',
    new: '  --chip-warming: #c8402a;',
    expect: 'the four residency dots stay distinct from each other, on every palette and mode',
  },
  {
    label: 'the busy claude dot keeps pulsing under reduced motion',
    file: CSS,
    old: '  .rail-chip-dot.claude.busy,\n',
    new: '',
    expect: 'every rail-chip-pulse stops under prefers-reduced-motion',
  },
  {
    label: 'the trigger pill dot keeps pulsing under reduced motion',
    file: CSS,
    old: '  .rail-chip-dot.claude.busy,\n  .mw-trigger .badge-cli .badge-dot { animation: none; }',
    new: '  .rail-chip-dot.claude.busy { animation: none; }',
    expect: 'every rail-chip-pulse stops under prefers-reduced-motion',
  },
  // The palette's own text tokens. `all` is how many copies the old value lives in (dark is
  // written twice, and restoring one copy would also trip the Dark-equals-System test and prove
  // less); `mention` must appear in the failure output, so the test has to NAME the palette,
  // the mode and the surface, not only go red.
  {
    label: "solarized's old dark --text-secondary comes back (3.90:1 on its --surface-2)",
    file: CSS,
    old: '  --text-secondary: #abb9b9;',
    new: '  --text-secondary: #93a1a1;',
    all: 2,
    expect: "every palette's text tokens reach 4.5:1 against every surface it defines, in every mode",
    mention: 'solarized dark on a light OS: --text-secondary #93a1a1 on --surface-2 #0a4653 = 3.90:1',
  },
  {
    label: "solarized's old light --text-secondary comes back (4.39:1 on its --surface-2)",
    file: CSS,
    old: '  --text-secondary: #485d64;',
    new: '  --text-secondary: #586e75;',
    expect: "every palette's text tokens reach 4.5:1 against every surface it defines, in every mode",
    mention: 'solarized light on a light OS: --text-secondary #586e75 on --surface-2 #eee8d5 = 4.39:1',
  },
  {
    label: "the default light --muted goes back to #898781 (3.18:1 on --surface-2)",
    file: CSS,
    old: '  --muted: #6f6d68;',
    new: '  --muted: #898781;',
    expect: "every palette's text tokens reach 4.5:1 against every surface it defines, in every mode",
    mention: '(no palette) light on a light OS: --muted #898781 on --surface-2 #f2f1ec = 3.18:1',
  },
  // The accent as words reads --accent-text, each palette's accent moved in lightness to 4.5:1.
  {
    label: 'the active admin tab goes back to painting its words with --accent',
    file: CSS,
    old: '.admin-tab.on { color: var(--accent-text);',
    new: '.admin-tab.on { color: var(--accent);',
    expect: 'every accent text rule reaches 4.5:1 against what it sits on, on every palette and mode',
    mention: 'ember light on a light OS: .admin-tab.on #e0731f on --page #faf8f7',
  },
  {
    label: "solarized's dark --accent-text goes back to the accent itself (both copies)",
    file: CSS,
    old: '  --accent-text: #6dcaff;',
    new: '  --accent-text: #268bd2;',
    all: 2,
    expect: 'every accent text rule reaches 4.5:1 against what it sits on, on every palette and mode',
    mention: 'solarized dark on a light OS: .rail-app.on #268bd2 on --accent-soft over --rail',
  },
  {
    label: "nord's light block drops --accent-text (it would inherit the default palette's blue)",
    file: CSS,
    old: '  --accent-text: #3f6089;\n',
    new: '',
    expect: 'every block that sets --accent sets --accent-text and --on-accent beside it',
    mention: "[data-palette='nord']",
  },
  {
    label: 'the active rail app gets a solid accent fill behind its accent words',
    file: CSS,
    old: '.rail-app.on { background: var(--accent-soft); color: var(--accent-text); }',
    new: '.rail-app.on { background: var(--accent); color: var(--accent-text); }',
    expect: 'every accent text rule reaches 4.5:1 against what it sits on, on every palette and mode',
    mention: '.rail-app.on: the modelled wash is --accent-soft',
  },
  {
    // An earlier value: right for every web-core accent rule, wrong on --surface-2, where rails
    // put accent words too. Only the token-level test can see it.
    label: "the default dark --accent-text goes back to the earlier #4997f6 (both copies)",
    file: CSS,
    old: '--accent-text: #53a0ff;',
    new: '--accent-text: #4997f6;',
    all: 2,
    expect: '--accent-text reaches 4.5:1 on every surface, bare and under --accent-soft, on every palette and mode',
    mention: '(no palette) dark on a light OS: --accent-text #4997f6 on --accent-soft over --surface-2 #283645 = 4.13:1',
  },
  // Words ON the accent read --on-accent; every stop of the gradient is measured.
  {
    label: 'the primary button goes back to white words',
    file: CSS,
    old: '.btn-default { background: var(--grad-accent); color: var(--on-accent); }',
    new: '.btn-default { background: var(--grad-accent); color: #fff; }',
    expect: 'every web-core rule with words on an accent fill reaches 4.5:1 on every stop of it',
    mention: '(no palette) dark on a light OS: .btn-default #ffffff on #6f8bff = 3.08:1',
  },
  {
    label: "evergreen's dark --on-accent becomes white (both copies)",
    file: CSS,
    old: '  --accent-soft: rgba(52, 211, 153, 0.2);\n  --accent-text: var(--accent);\n  --on-accent: var(--page);',
    new: '  --accent-soft: rgba(52, 211, 153, 0.2);\n  --accent-text: var(--accent);\n  --on-accent: #ffffff;',
    all: 2,
    expect: '--on-accent reaches 4.5:1 on every stop of the accent gradient, on every palette and mode',
    mention: 'evergreen dark on a light OS: --on-accent #ffffff on --grad-accent stop #a3e635 = 1.51:1',
  },
  {
    label: "the default light gradient's darkened stops go back (white then misses on them)",
    file: CSS,
    old: '  --grad-accent: linear-gradient(135deg, #2675d2, #4b65f9);',
    new: '  --grad-accent: linear-gradient(135deg, #2a78d6, #4f6bff);',
    expect: '--on-accent reaches 4.5:1 on every stop of the accent gradient, on every palette and mode',
    mention: '(no palette) light on a light OS: --on-accent #ffffff on --grad-accent stop #4f6bff = 4.30:1',
  },
  {
    label: "nord's light block drops --on-accent",
    file: CSS,
    old: '  --accent-text: #3f6089;\n  --on-accent: var(--text-primary);\n',
    new: '  --accent-text: #3f6089;\n',
    expect: 'every block that sets --accent sets --accent-text and --on-accent beside it',
    mention: "[data-palette='nord']",
  },
  // The status text tokens hold on every surface and under a 10% tint of their hue.
  {
    label: "the light --good-text goes back to the earlier #1a6b2f",
    file: CSS,
    old: '  --good-text: #12652a;',
    new: '  --good-text: #1a6b2f;',
    expect: 'the status text tokens reach 4.5:1 on every surface, bare and under a 10% tint of their hue',
    mention: 'tokyo light on a light OS: --good-text #1a6b2f on a 10% --good tint over --surface-2',
  },
  // Every CLI blocker slug the broker emits reaches the admin's readiness title.
  {
    label: 'a slug map that forgets workdir_unset',
    old: "  const blockers = cli.blockers.length ? cli.blockers.join(', ') : 'unknown'\n",
    new: "  const blockers = cli.blockers.filter((b) => b !== 'workdir_unset').join(', ') || 'unknown'\n",
    expect: 'every blocker slug the broker can emit reaches the admin',
  },
  {
    label: 'every role switched off reads as a fault (a warning badge for a decision)',
    old: "  if (cli.blockers.length === 1 && cli.blockers[0] === 'cli_all_roles_off') {\n",
    new: "  if (cli.blockers.length === 1 && cli.blockers[0] === 'cli_all_roles_off' && false) {\n",
    expect: 'every role switched off in the Rails tab reads as off, unless a fault sits beside it',
  },
  {
    label: 'the calm all-off badge hides a real fault beside it',
    old: "  if (cli.blockers.length === 1 && cli.blockers[0] === 'cli_all_roles_off') {\n",
    new: "  if (cli.blockers.includes('cli_all_roles_off')) {\n",
    expect: 'every role switched off in the Rails tab reads as off, unless a fault sits beside it',
  },
  {
    label: 'the slug list is no longer read from the broker (its newest slug goes unseen)',
    file: BROKER,
    old: '            out.append("workdir_unset")\n',
    new: '            out.append("workdir_missing")\n',
    expect: 'every blocker slug the broker can emit reaches the admin',
  },
]

// --list names the cases and exits; an unknown option exits 2. Before anything runs.
parseHarnessArgs({ cases: MUTATIONS })

function runSuite() {
  const p = runBoundedSync(process.execPath, ['--test-reporter=tap', SUITE],
    { cwd: REPO, timeoutMs: SUITE_TIMEOUT_MS })
  return { code: p.status, out: p.out }
}

// TAP lines are `ok 3 - name` / `not ok 3 - name`, possibly indented.
function results(out) {
  const passed = new Set()
  const failed = new Set()
  for (const line of out.split(/\r?\n/)) {
    const m = line.match(/^\s*(not ok|ok) \d+ - (.+?)\s*$/)
    if (!m) continue
    ;(m[1] === 'ok' ? passed : failed).add(m[2])
  }
  return { passed, failed }
}

// Line endings are normalised to \n for matching, so a CRLF checkout cannot turn a multi-line
// pattern into a PATTERN error; the file is written back with its own endings.
// The bytes are the source of truth for the restore; the text is only for matching.
const originals = new Map()
const originalBytes = new Map()
for (const f of new Set(MUTATIONS.map((m) => m.file ?? SRC))) {
  originalBytes.set(f, readFileSync(f))
  originals.set(f, originalBytes.get(f).toString('utf8'))
}
const eol = (text) => (text.includes('\r\n') ? '\r\n' : '\n')

const base = runSuite()
const baseR = results(base.out)
if (base.code !== 0 || baseR.failed.size > 0) {
  console.log('BASELINE IS NOT GREEN -- fix that before trusting any mutation result.')
  console.log(base.out.slice(-1500))
  process.exit(2)
}
const missing = [...new Set(MUTATIONS.map((m) => m.expect))].filter((e) => !baseR.passed.has(e))
if (missing.length) {
  console.log('WITNESS FAILED: the baseline never reported these tests as passing, so a mutation')
  console.log('"firing" them would prove nothing:')
  for (const e of missing) console.log(`  - ${e}`)
  process.exit(2)
}
console.log(`baseline: model-chips.test.js green, ${baseR.passed.size} tests reported ok\n`)

const verdicts = []
for (const m of MUTATIONS) {
  const file = m.file ?? SRC
  const original = originals.get(file)
  const nl = eol(original)
  const text = original.replace(/\r\n/g, '\n')
  const n = text.split(m.old).length - 1
  if (n !== (m.all ?? 1)) {
    console.log(`  ERROR    ${m.label}: pattern found ${n} times, expected ${m.all ?? 1}`)
    verdicts.push([m.label, 'PATTERN'])
    continue
  }
  try {
    writeFileSync(file, text.split(m.old).join(m.new).replace(/\n/g, nl), 'utf8')
    const { code, out } = runSuite()
    const { failed } = results(out)
    const named = failed.has(m.expect)
    const said = !m.mention || out.includes(m.mention)
    const verdict = code === 'TIMEOUT' ? 'TIMEOUT' : code !== 0 && named && said ? 'FIRED' : 'SURVIVED'
    verdicts.push([m.label, verdict])
    console.log(`  ${verdict.padEnd(9)}${m.label}`)
    console.log(`           expected red: "${m.expect}" -> ${named ? 'red' : 'NOT red'}`)
    if (m.mention) console.log(`           names it: ${said ? 'yes' : 'NO'} ("${m.mention}")`)
    console.log(`           all red: ${[...failed].join(' | ') || '(none)'}`)
    if (verdict !== 'FIRED') console.log(out.slice(-1200))
  } finally {
    restoreExact(file, originalBytes.get(file))
  }
}

const after = runSuite()
const restored = [...originalBytes].every(([f, buf]) => readFileSync(f).equals(buf))
console.log(`\nrestored tree: ${after.code === 0 && restored ? 'green' : 'STILL RED OR CHANGED -- TREE IS DIRTY'}`)
const bad = verdicts.filter(([, v]) => v !== 'FIRED')
console.log(`${verdicts.length - bad.length}/${verdicts.length} mutations fired the right test`)
process.exit(bad.length === 0 && after.code === 0 && restored ? 0 : 1)
