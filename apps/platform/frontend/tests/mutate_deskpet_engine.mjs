// Mutation harness for the desk pet engine's two local fixes. NOT a test file: it rewrites
// src/deskpet/esheep.js and puts it back.
//
//     node apps/platform/frontend/tests/mutate_deskpet_engine.mjs
//
// Each mutation is a plausible wrong edit, and the question is whether deskpet-engine.test.js
// goes red AND the red test is the one that guards that claim. "Red" means the expected test is
// reported `not ok` in TAP; a check for the name anywhere in the output cannot fail, because node
// prints every test's name. The baseline must report each expected test as `ok`, the witness
// that the suite ran the engine at all.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// The shared runner: a timeout kills the whole tree (node --test's per-file child
// included, which spawnSync's own timeout left running), and a restore is proven.
import { restoreExact, runBoundedSync } from '../../../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src', 'deskpet', 'esheep.js')
const SUITE = join(HERE, 'deskpet-engine.test.js')
const SUITE_TIMEOUT_MS = 60_000

const T = {
  blackSheep: 'the black-sheep repeat expression evaluates as the desktop engine does, with no error',
  convert: 'Convert follows .NET Convert.ToInt32(double): nearest, ties to even, overflow throws',
  modulo: '% keeps the sign of the dividend, at the precedence of * and /',
  refused: 'anything outside arithmetic and that one Convert form is still refused',
  stranded: 'a sprite one size beyond an edge is an entrance; further out it is stranded',
  portrait: 'on a portrait window the bathtub spawn is dropped exactly when its tub is stranded',
  pick: '_spawnESheep picks from the candidates: portrait never dives, landscape picks as before',
  fallback: 'if every spawn were stranded, the pet still spawns rather than vanishing',
  still: 'checking candidates moves nothing',
}

const MUTATIONS = [
  // --- 1. Convert(...,System.Int32) and % ----------------------------------------------------
  {
    label: 'the character check stops letting the Convert form through',
    old: '    const bare = value.replace(/Convert\\s*\\(/g, "(").replace(/,\\s*System\\.Int32\\s*\\)/g, ")");',
    new: '    const bare = value;',
    expect: T.blackSheep,
  },
  {
    label: 'Convert truncates instead of rounding half to even',
    old: '  const r = frac > 0.5 ? floor + 1 : frac < 0.5 ? floor : (floor % 2 === 0 ? floor : floor + 1);',
    new: '  const r = Math.trunc(v);',
    expect: T.convert,
  },
  {
    label: 'Convert rounds a tie up (JS Math.round), not to even',
    old: '  const r = frac > 0.5 ? floor + 1 : frac < 0.5 ? floor : (floor % 2 === 0 ? floor : floor + 1);',
    new: '  const r = Math.round(v);',
    expect: T.convert,
  },
  {
    label: 'Convert overflow no longer throws',
    old: '  if(r < -2147483648 || r > 2147483647) throw new Error("Convert: value is outside Int32");\n',
    new: '',
    expect: T.convert,
  },
  {
    label: '% is dropped from the product loop',
    old: '      while(peek() === "*" || peek() === "/" || peek() === "%")',
    new: '      while(peek() === "*" || peek() === "/")',
    expect: T.modulo,
  },
  {
    label: 'the security check is widened to letters and commas',
    old: '    if(/[^0-9.eE+\\-*/%()\\s]/.test(bare))',
    new: '    if(/[^0-9.eE+\\-*/%()\\sA-Za-z,]/.test(bare))',
    expect: T.refused,
  },
  // --- 2. the spawn filter -------------------------------------------------------------------
  {
    label: 'the filter is removed (every spawn is a candidate again)',
    old: '    const visible = all.filter(spawn => !this._spawnStrandsChild(spawn));',
    new: '    const visible = all;',
    expect: T.portrait,
  },
  {
    label: 'an entrance one size outside counts as stranded',
    old: '  return x <= -2 * w || x >= screenW + w || y <= -2 * h || y >= screenH + h;',
    new: '  return x <= -w || x >= screenW || y <= -h || y >= screenH;',
    expect: T.stranded,
  },
  {
    label: '_spawnESheep picks from every spawn, not the candidates',
    old: '    const spawns = this._spawnCandidates();',
    new: "    const spawns = this.xmlDoc.getElementsByTagName('spawns')[0].getElementsByTagName('spawn');",
    expect: T.pick,
  },
  {
    label: 'no fallback when every spawn is stranded',
    old: '    return visible.length ? visible : all;',
    new: '    return visible;',
    expect: T.fallback,
  },
  {
    label: "the check leaves the parent where the spawn would put it",
    old: '      this.imageX = keepX;\n      this.imageY = keepY;\n',
    new: '',
    expect: T.still,
  },
]

// --list names the cases and exits; an unknown option exits 2. Before anything runs.
parseHarnessArgs({ cases: MUTATIONS })

function runSuite() {
  const p = runBoundedSync(process.execPath, ['--test-reporter=tap', SUITE],
    { cwd: HERE, timeoutMs: SUITE_TIMEOUT_MS })
  return { code: p.status, out: p.out }
}

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

// The bytes are the source of truth for the restore; the text is only for matching.
const originalBytes = readFileSync(SRC)
const original = originalBytes.toString('utf8')
const nl = original.includes('\r\n') ? '\r\n' : '\n'
const text = original.replace(/\r\n/g, '\n')

const base = runSuite()
const baseR = results(base.out)
if (base.code !== 0 || baseR.failed.size > 0) {
  console.log('BASELINE IS NOT GREEN -- fix that before trusting any mutation result.')
  console.log(base.out.slice(-1500))
  process.exit(2)
}
const missing = [...new Set(MUTATIONS.map((m) => m.expect))].filter((e) => !baseR.passed.has(e))
if (missing.length) {
  console.log('WITNESS FAILED: the baseline never reported these tests as passing:')
  for (const e of missing) console.log(`  - ${e}`)
  process.exit(2)
}
console.log(`baseline: deskpet-engine.test.js green, ${baseR.passed.size} tests reported ok\n`)

const verdicts = []
for (const m of MUTATIONS) {
  const n = text.split(m.old).length - 1
  if (n !== 1) {
    console.log(`  ERROR    ${m.label}: pattern found ${n} times, expected 1`)
    verdicts.push([m.label, 'PATTERN'])
    continue
  }
  try {
    writeFileSync(SRC, text.replace(m.old, m.new).replace(/\n/g, nl), 'utf8')
    const { code, out } = runSuite()
    const { failed } = results(out)
    const named = failed.has(m.expect)
    const verdict = code === 'TIMEOUT' ? 'TIMEOUT' : code !== 0 && named ? 'FIRED' : 'SURVIVED'
    verdicts.push([m.label, verdict])
    console.log(`  ${verdict.padEnd(9)}${m.label}`)
    console.log(`           expected red: "${m.expect}" -> ${named ? 'red' : 'NOT red'}`)
    console.log(`           all red: ${[...failed].join(' | ') || '(none)'}`)
    if (verdict !== 'FIRED') console.log(out.slice(-1200))
  } finally {
    restoreExact(SRC, originalBytes)
  }
}

const after = runSuite()
const restored = readFileSync(SRC).equals(originalBytes)
console.log(`\nrestored tree: ${after.code === 0 && restored ? 'green' : 'STILL RED OR CHANGED -- TREE IS DIRTY'}`)
const bad = verdicts.filter(([, v]) => v !== 'FIRED')
console.log(`${verdicts.length - bad.length}/${verdicts.length} mutations fired the right test`)
process.exit(bad.length === 0 && after.code === 0 && restored ? 0 : 1)
