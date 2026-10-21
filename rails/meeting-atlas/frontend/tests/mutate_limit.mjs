// Mutation harness for tests/limit.test.js. NOT a test file -- it rewrites the source.
//
//     node rails/meeting-atlas/frontend/tests/mutate_limit.mjs
//
// Each mutation breaks Search's bounded fan-out (limit.ts) and names the test that must go red.
// "Named" means reported as FAILED in TAP, after the baseline reported it `ok`. The CONTROLS
// are the harness testing itself and must come back SURVIVED.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { restoreExact, runBoundedSync } from '../../../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')
const SUITE = join(HERE, 'limit.test.js')
const SUITE_NAME = 'limit.test.js'
const SUITE_TIMEOUT_MS = 60_000

const MUTATIONS = [
  {
    label: 'the fan-out is unbounded again (every meeting at once)',
    file: 'limit.ts',
    old: "  const lanes = Math.max(1, Math.min(Math.floor(limit) || 1, items.length))\n",
    new: "  const lanes = items.length\n",
    expect: 'no more than the limit are ever in flight, and every item is fetched',
  },
  {
    label: 'the fan-out is serial (bounded, but one at a time)',
    file: 'limit.ts',
    old: "  const lanes = Math.max(1, Math.min(Math.floor(limit) || 1, items.length))\n",
    new: "  const lanes = 1\n",
    expect: 'no more than the limit are ever in flight, and every item is fetched',
  },
  {
    label: 'results come back in completion order',
    file: 'limit.ts',
    old: "      out[i] = await fn(items[i])\n",
    new: "      out.push(await fn(items[i]))\n",
    expect: 'no more than the limit are ever in flight, and every item is fetched',
  },
]

const CONTROLS = [
  {
    label: 'CONTROL no-op: a comment added, behaviour unchanged',
    file: 'limit.ts',
    old: "export const SEARCH_CONCURRENCY = 6\n",
    new: "export const SEARCH_CONCURRENCY = 6 // control: no behaviour change\n",
    expect: 'Search keeps six fetches in flight',
  },
  {
    label: 'CONTROL wrong test: changes the constant, names the small-input test',
    file: 'limit.ts',
    old: "export const SEARCH_CONCURRENCY = 6\n",
    new: "export const SEARCH_CONCURRENCY = 7\n",
    expect: 'fewer items than the limit run all at once; none at all is fine',
  },
]

// --list names the cases and exits; an unknown option exits 2. Before anything runs.
parseHarnessArgs({ cases: MUTATIONS, controls: CONTROLS })

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

const base = runSuite()
const baseR = results(base.out)
if (base.code !== 0 || baseR.failed.size > 0) {
  console.log('BASELINE IS NOT GREEN -- fix that before trusting any mutation result.')
  console.log(base.out.slice(-1500))
  process.exit(2)
}
const missing = [...new Set([...MUTATIONS, ...CONTROLS].map((m) => m.expect))]
  .filter((e) => !baseR.passed.has(e))
if (missing.length) {
  console.log('WITNESS FAILED: the baseline never reported these tests as passing:')
  for (const e of missing) console.log(`  - ${e}`)
  process.exit(2)
}
console.log(`baseline: ${SUITE_NAME} green, ${baseR.passed.size} tests reported ok\n`)

function attempt(m) {
  const path = join(SRC, m.file)
  const originalBytes = readFileSync(path)
  const original = originalBytes.toString('utf8')
  const nl = original.includes('\r\n') ? '\r\n' : '\n'
  const text = original.replace(/\r\n/g, '\n')
  const n = text.split(m.old).length - 1
  if (n !== 1) {
    console.log(`  ERROR    ${m.label}: pattern found ${n} times in ${m.file}, expected 1`)
    return { verdict: 'PATTERN', out: '' }
  }
  try {
    writeFileSync(path, text.replace(m.old, m.new).replace(/\n/g, nl), 'utf8')
    const { code, out } = runSuite()
    const { failed } = results(out)
    const named = failed.has(m.expect)
    const verdict = code === 'TIMEOUT' ? 'TIMEOUT' : code !== 0 && named ? 'FIRED' : 'SURVIVED'
    console.log(`  ${verdict.padEnd(9)}${m.label}`)
    if (verdict !== 'FIRED') console.log(`           all red: ${[...failed].join(' | ') || '(none)'}`)
    return { verdict, out }
  } finally {
    restoreExact(path, originalBytes)
  }
}

const verdicts = MUTATIONS.map((m) => [m.label, attempt(m).verdict])
console.log('\ncontrols (each must be SURVIVED):')
const controlBad = CONTROLS.filter((m) => attempt(m).verdict !== 'SURVIVED').map((m) => m.label)

const after = runSuite()
console.log(`\nrestored tree: ${after.code === 0 ? 'green' : 'STILL RED -- TREE IS DIRTY'}`)
const bad = verdicts.filter(([, v]) => v !== 'FIRED')
console.log(`${verdicts.length - bad.length}/${verdicts.length} mutations fired the right test`)
console.log(`${CONTROLS.length - controlBad.length}/${CONTROLS.length} controls survived`
  + (controlBad.length ? ' -- THE VERDICT LOGIC CANNOT DISCRIMINATE; IGNORE THE COUNT ABOVE' : ''))
for (const [label, v] of bad) console.log(`  ${v}: ${label}`)
process.exit(bad.length === 0 && controlBad.length === 0 && after.code === 0 ? 0 : 1)
