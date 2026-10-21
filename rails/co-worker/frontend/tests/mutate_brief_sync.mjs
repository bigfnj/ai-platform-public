// Mutation harness for tests/brief-sync.test.js. NOT a test file -- it rewrites the source.
//
//     node rails/co-worker/frontend/tests/mutate_brief_sync.mjs
//
// Each mutation puts one recorded defect back into briefsync.ts or types.ts and names the test
// that must go red for it. "Named" means the test is reported as FAILED (`not ok N - <name>` in
// TAP); the baseline must report each named test as `ok` first, the witness that the suite ran
// the code at all. The CONTROLS are the harness testing itself: a no-op and a wrong-test
// mutation, both of which must come back SURVIVED.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { restoreExact, runBoundedSync } from '../../../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')
const SUITE = join(HERE, 'brief-sync.test.js')
const SUITE_NAME = 'brief-sync.test.js'
const SUITE_TIMEOUT_MS = 60_000

const MUTATIONS = [
  {
    label: 'changing the lens stops the synthesis poll again (the deadlock)',
    file: 'briefsync.ts',
    old: "    this.set({ lens })\n    this.load()\n",
    new: "    this.set({ lens })\n    this.stopPolling()\n    this.load()\n",
    expect: 'switching lens mid-synthesis keeps the poll, and the run still finishes the button',
  },
  {
    label: 'the auto-trigger ignores `started` again (the cooldown loop)',
    file: 'briefsync.ts',
    old: "      if (r.started) {\n",
    new: "      if (true) {\n",
    expect: 'a cooldown answer to the auto-trigger is not waited on, and does not loop',
  },
  {
    label: 'an auto-trigger 409 is dropped instead of waited on',
    file: 'briefsync.ts',
    old: "      if (statusOf(e) === 409) {\n        // A run is already in flight",
    new: "      if (false) {\n        // A run is already in flight",
    expect: 'an auto-trigger refused with 409 waits for the run already in flight',
  },
  {
    label: 'lane views auto-refresh too',
    file: 'briefsync.ts',
    old: "if (b.stale_source && lens === 'all' && !this.state.busy)",
    new: "if (b.stale_source && !this.state.busy)",
    expect: 'only the Top 10 view auto-refreshes',
  },
  {
    label: 'a run already in flight at mount is not found',
    file: 'briefsync.ts',
    old: "      if (this.disposed || !s.running) return\n",
    new: "      return\n",
    expect: 'a run started before the tab opened is found and waited on',
  },
  {
    label: 'dispose leaves the controller live',
    file: 'briefsync.ts',
    old: "    this.disposed = true\n    this.stopPolling()\n",
    new: "    this.stopPolling()\n",
    expect: 'after dispose nothing polls and nothing changes',
  },
  {
    label: 'a stale load answer can overwrite the lens on screen',
    file: 'briefsync.ts',
    old: "      if (this.disposed || seq !== this.loadSeq) return\n      this.set({ brief: b,",
    new: "      if (this.disposed) return\n      this.set({ brief: b,",
    expect: 'a slower load for the previous lens cannot overwrite the current one',
  },
  {
    label: 'the type filter applies a type the loaded set does not offer',
    file: 'types.ts',
    old: "  return chosen === 'all' || available.includes(chosen) ? chosen : 'all'\n",
    new: "  return chosen\n",
    expect: 'a type the loaded set does not offer reads as "all", never as a blank filter',
  },
  {
    label: 'a failed status read leaves the stale "Synthesizing" banner up',
    file: 'briefsync.ts',
    old: "      this.set({ busy: false,\n"
       + "                 note: 'Lost track of the synthesis run (its status did not answer). Reload to check.' })\n",
    new: "      this.set({ busy: false })\n",
    expect: 'a status read that fails stops the poll and replaces the "Synthesizing" banner',
  },
]

const CONTROLS = [
  {
    label: 'CONTROL no-op: a comment added, behaviour unchanged',
    file: 'briefsync.ts',
    old: "export const POLL_MS = 3000\n",
    new: "export const POLL_MS = 3000 // control: no behaviour change\n",
    expect: 'the poll interval and lens queries are the documented ones',
  },
  {
    label: 'CONTROL wrong test: breaks the poll constant, names the deadlock test',
    file: 'briefsync.ts',
    old: "export const POLL_MS = 3000\n",
    new: "export const POLL_MS = 3001\n",
    expect: 'switching lens mid-synthesis keeps the poll, and the run still finishes the button',
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
