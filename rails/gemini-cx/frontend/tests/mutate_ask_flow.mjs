// Mutation harness for tests/ask-flow.test.js. NOT a test file -- it rewrites the source.
//
//     node rails/gemini-cx/frontend/tests/mutate_ask_flow.mjs
//
// Each mutation puts one recorded defect back into askflow.ts, readaloud.ts or api.ts and names
// the test that must go red for it. "Named" means the test is reported as FAILED (`not ok N -
// <name>` in TAP); the baseline must report each named test as `ok` first, the witness that the
// suite ran the code at all. The CONTROLS are the harness testing itself: a no-op and a
// wrong-test mutation, both of which must come back SURVIVED. Same engine as the
// smb-partner-enablement twin (tests/mutate_voice_flows.mjs there).
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { restoreExact, runBoundedSync } from '../../../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')
const SUITE = join(HERE, 'ask-flow.test.js')
const SUITE_NAME = 'ask-flow.test.js'
const SUITE_TIMEOUT_MS = 60_000

const MUTATIONS = [
  {
    label: 'Stop (the abort) leaves the watchdog armed again',
    file: 'askflow.ts',
    old: "    aborted = true\n    settled = true\n    clear()\n    cancelWs?.()\n",
    new: "    aborted = true\n    settled = true\n    cancelWs?.()\n",
    expect: 'Stop ends the watchdog too, so the cancelled question is never asked again',
  },
  {
    label: 'a buffered answer that lands after Stop is still shown',
    file: 'askflow.ts',
    old: "      (r) => { if (!aborted) h.onDone(",
    new: "      (r) => { if (true) h.onDone(",
    expect: 'a buffered answer that lands after Stop is dropped',
  },
  {
    label: 'a stream that ends mid-answer reads as a finished answer',
    file: 'askflow.ts',
    old: "      if (err) h.onError(err)   // a partial answer: say so rather than present it as whole\n"
       + "      else h.onDone()\n",
    new: "      h.onDone()\n",
    expect: 'a clean close after some tokens is reported, not shown as a finished answer',
  },
  {
    label: 'an error frame is retried over HTTP',
    file: 'askflow.ts',
    old: "      if (err && fromServer) {\n",
    new: "      if (false) {\n",
    expect: 'an error FRAME is shown, not retried',
  },
  {
    label: 'a close with no terminal frame reports nothing again',
    file: 'api.ts',
    old: "      onClose(CLOSED_EARLY, false)\n",
    new: "      onClose()\n",
    expect: 'a close with no done or error frame says the answer did not finish',
  },
  {
    label: 'frames after a cancel are still delivered',
    file: 'api.ts',
    old: "  ws.onmessage = (ev) => {\n    if (closed) return\n",
    new: "  ws.onmessage = (ev) => {\n",
    expect: 'nothing is reported after a cancel',
  },
  {
    label: 'Read aloud speaks after its button is gone',
    file: 'readaloud.ts',
    old: "    if (this.disposed || mine !== this.generation) return\n",
    new: "    if (false) return\n",
    expect: 'a Read aloud gone before synthesis returns says nothing',
  },
  {
    label: 'unmounting Read aloud stops audio it never started',
    file: 'readaloud.ts',
    old: "    if (playing) this.deps.stopSpeaking()\n",
    new: "    this.deps.stopSpeaking()\n",
    expect: 'a Read aloud that never played stops nothing when it goes',
  },
  {
    label: 'stopSpeaking does not tell the stopped clip\'s caller',
    file: 'voice.ts',
    old: "  currentEnd = null\n  end?.()\n}\n",
    new: "  currentEnd = null\n}\n",
    expect: 'a preempted clip fires its own onEnd, once',
  },
  {
    label: 'a clip\'s onEnd can fire twice',
    file: 'voice.ts',
    old: "    if (done) return\n    done = true\n",
    new: "    done = true\n",
    expect: 'a preempted clip fires its own onEnd, once',
  },
]

const CONTROLS = [
  {
    label: 'CONTROL no-op: a comment added, behaviour unchanged',
    file: 'askflow.ts',
    old: "export const WATCHDOG_MS = 12000\n",
    new: "export const WATCHDOG_MS = 12000 // control: no behaviour change\n",
    expect: 'the watchdog is the documented 12 s',
  },
  {
    label: 'CONTROL wrong test: breaks the watchdog constant, names a Read aloud test',
    file: 'askflow.ts',
    old: "export const WATCHDOG_MS = 12000\n",
    new: "export const WATCHDOG_MS = 13000\n",
    expect: 'a Read aloud gone before synthesis returns says nothing',
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
