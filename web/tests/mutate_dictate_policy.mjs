// Mutation harness for the dictation privacy default. NOT a test file -- it rewrites the source.
//
//     node web/tests/mutate_dictate_policy.mjs
//
// This guard has no visible failure mode, which is exactly why it needs one. A dictation press
// that wrongly reaches Google's cloud STT transcribes perfectly; the only symptom is that a
// child's voice left the machine. The old opt-IN version of this was documented, working, and
// switched on at one of seven call sites for months, and nothing anywhere could tell.
//
// So the question each mutation asks is: if the default silently flipped back, does anything
// go red? Two of the four are the inversion itself, from opposite directions.
//
// "Named" means the expected test is reported as FAILED (`not ok N - <name>` in TAP). This
// harness used to check only that the name appeared somewhere in node's output, and node prints
// every test's name, passing or not, so that check could not fail: any mutation that broke ANY
// test read as FIRED for whichever test it claimed. The baseline must also report each expected
// test as `ok`, the witness that the suite ran the code at all.
//
// The CONTROLS below are the harness testing itself, every run. Each is a mutation that must
// come back SURVIVED: one changes nothing, one breaks a test other than the one it names. If
// either reads FIRED, the verdict logic has stopped discriminating and every FIRED above it is
// worthless, so the run fails. The second control is the exact case the old check got wrong.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// The shared runner: a timeout kills the whole tree (node --test's per-file child
// included, which spawnSync's own timeout left running), and a restore is proven.
import { restoreExact, runBoundedSync } from '../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from './mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const SRC = join(REPO, 'web', 'src', 'dictate_policy.ts')
const SUITE = join(REPO, 'web', 'tests', 'dictate-policy.test.js')
// A suite that hangs (an import that never settles) must not hang the harness.
const SUITE_TIMEOUT_MS = 60_000

const MUTATIONS = [
  {
    label: 'the default flips back to opt-OUT (cloud unless told otherwise)',
    old: "  return allowCloudStt ? 'cloud' : 'broker'",
    new: "  return allowCloudStt === false ? 'broker' : 'cloud'",
    expect: 'without the opt-in, nothing reaches a cloud recognizer',
  },
  {
    label: 'a falsy opt-in is treated as an opt-in',
    old: "  return allowCloudStt ? 'cloud' : 'broker'",
    new: "  return allowCloudStt !== true ? 'cloud' : 'cloud'",
    expect: 'a falsy opt-in is not an opt-in',
  },
  {
    label: 'on-device stops being allowed without the opt-in, costing the good path',
    old: "  if (availability === 'available') return 'ondevice'",
    new: "  if (availability === 'available' && allowCloudStt) return 'ondevice'",
    expect: 'on-device is chosen whenever it is ready, opt-in or not',
  },
  {
    label: 'a missing browser API is allowed to cloud anyway',
    old: "  if (!hasCtor) return 'broker'",
    new: "  if (!hasCtor && !allowCloudStt) return 'broker'",
    expect: 'no browser API at all falls to the broker, even with the opt-in',
  },
  {
    label: 'a chosen microphone is ignored again (browser STT on the OS default)',
    old: "  if (deviceChosen) return 'broker'\n",
    new: '',
    expect: 'a chosen microphone sends dictation to the broker, which can record from it',
  },
]

const CONTROLS = [
  {
    label: 'CONTROL no-op: a comment added, behaviour unchanged',
    old: "  if (!hasCtor) return 'broker'",
    new: "  if (!hasCtor) return 'broker' // control: no behaviour change",
    expect: 'without the opt-in, nothing reaches a cloud recognizer',
  },
  {
    // Turns ONLY "on-device is chosen ..." red. Under the old name-in-output check this read
    // FIRED, because the suite failed and the falsy-opt-in test's name was printed as passing.
    label: 'CONTROL wrong test: breaks on-device, names the falsy-opt-in test',
    old: "  if (availability === 'available') return 'ondevice'",
    new: "  if (availability === 'available') return 'broker'",
    expect: 'a falsy opt-in is not an opt-in',
  },
]

// --list names the cases and exits; an unknown option exits 2. Before anything runs.
parseHarnessArgs({ cases: MUTATIONS, controls: CONTROLS })

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

// Line endings are normalised to \n for matching, so a CRLF checkout cannot turn a pattern into
// a PATTERN error; the file is written back with its own endings.
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
const missing = [...new Set([...MUTATIONS, ...CONTROLS].map((m) => m.expect))]
  .filter((e) => !baseR.passed.has(e))
if (missing.length) {
  console.log('WITNESS FAILED: the baseline never reported these tests as passing, so a mutation')
  console.log('"firing" them would prove nothing:')
  for (const e of missing) console.log(`  - ${e}`)
  process.exit(2)
}
console.log(`baseline: dictate-policy.test.js green, ${baseR.passed.size} tests reported ok\n`)

function attempt(m) {
  const n = text.split(m.old).length - 1
  if (n !== 1) {
    console.log(`  ERROR    ${m.label}: pattern found ${n} times, expected 1`)
    return 'PATTERN'
  }
  try {
    writeFileSync(SRC, text.replace(m.old, m.new).replace(/\n/g, nl), 'utf8')
    const { code, out } = runSuite()
    const { failed } = results(out)
    const named = failed.has(m.expect)
    const verdict = code === 'TIMEOUT' ? 'TIMEOUT' : code !== 0 && named ? 'FIRED' : 'SURVIVED'
    console.log(`  ${verdict.padEnd(9)}${m.label}`)
    console.log(`           expected red: "${m.expect}" -> ${named ? 'red' : 'NOT red'}`)
    console.log(`           all red: ${[...failed].join(' | ') || '(none)'}`)
    return { verdict, out }
  } finally {
    restoreExact(SRC, originalBytes)
  }
}

const verdicts = []
for (const m of MUTATIONS) {
  const r = attempt(m)
  const verdict = r === 'PATTERN' ? r : r.verdict
  if (verdict === 'SURVIVED') console.log(r.out.slice(-1200))
  verdicts.push([m.label, verdict])
}
console.log('\ncontrols (each must be SURVIVED):')
const controlBad = []
for (const m of CONTROLS) {
  const r = attempt(m)
  const verdict = r === 'PATTERN' ? r : r.verdict
  if (verdict !== 'SURVIVED') controlBad.push(m.label)
}

const after = runSuite()
const restored = readFileSync(SRC).equals(originalBytes)
console.log(`\nrestored tree: ${after.code === 0 && restored ? 'green' : 'STILL RED OR CHANGED -- TREE IS DIRTY'}`)
const bad = verdicts.filter(([, v]) => v !== 'FIRED')
console.log(`${verdicts.length - bad.length}/${verdicts.length} mutations fired the right test`)
console.log(`${CONTROLS.length - controlBad.length}/${CONTROLS.length} controls survived`
  + (controlBad.length ? ' -- THE VERDICT LOGIC CANNOT DISCRIMINATE; IGNORE THE COUNT ABOVE' : ''))
process.exit(bad.length === 0 && controlBad.length === 0 && after.code === 0 && restored ? 0 : 1)
