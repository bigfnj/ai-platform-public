// Mutation harness for tests/voice-flows.test.js. NOT a test file -- it rewrites the source.
//
//     node rails/smb-partner-enablement/frontend/tests/mutate_voice_flows.mjs
//
// Each mutation puts one recorded defect back into askflow.ts, voicesession.ts, readaloud.ts,
// api.ts or voice.ts and names the test that must go red for it. "Named" means the test is
// reported as FAILED (`not ok N - <name>` in TAP); the baseline must report each named test as
// `ok` first, the witness that the suite ran the code at all. The CONTROLS are the harness
// testing itself: a no-op and a wrong-test mutation, both of which must come back SURVIVED.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { restoreExact, runBoundedSync } from '../../../../tools/tests/mutation_runner.mjs'
import { parseHarnessArgs } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')
const SUITE = join(HERE, 'voice-flows.test.js')
const SUITE_TIMEOUT_MS = 60_000

const MUTATIONS = [
  {
    label: 'the mic checks `hearing` before the recorder, so a recording cannot be stopped',
    file: 'voicesession.ts',
    old: "    if (this.recorder) { this.stopRecording(); return }\n" +
         "    if (this.listener) { this.cancelListening(); return }\n" +
         "    if (this.starting || this.state.busy || this.state.thinking) return\n",
    new: "    if (this.state.hearing || this.starting || this.state.busy || this.state.thinking) return\n" +
         "    if (this.recorder) { this.stopRecording(); return }\n" +
         "    if (this.listener) { this.cancelListening(); return }\n",
    expect: 'a second press stops the recording the first started, and sends it',
  },
  {
    label: 'dispose leaves the answer in flight',
    file: 'voicesession.ts',
    old: "    this.disposed = true\n    this.abortAsk?.()\n",
    new: "    this.disposed = true\n",
    expect: 'dispose mid-answer closes the stream and the watchdog never fires the buffered ask',
  },
  {
    label: 'the abort leaves the watchdog armed (the gemini-cx Stop defect)',
    file: 'askflow.ts',
    old: "    aborted = true\n    settled = true\n    clear()\n    cancelWs?.()\n",
    new: "    aborted = true\n    settled = true\n    cancelWs?.()\n",
    expect: 'dispose mid-answer closes the stream and the watchdog never fires the buffered ask',
  },
  {
    label: 'a buffered answer that lands after the abort is still delivered',
    file: 'askflow.ts',
    old: "      (r) => { if (!aborted) h.onDone(",
    new: "      (r) => { if (true) h.onDone(",
    expect: 'abort silences a buffered answer already in flight',
  },
  {
    label: 'a disposed session still asks what a late transcription heard',
    file: 'voicesession.ts',
    old: "    if (!q || this.state.busy || this.disposed) return\n",
    new: "    if (!q || this.state.busy) return\n",
    expect: 'a recording transcribed after the tab closed asks nothing and says nothing',
  },
  {
    label: 'a recording granted after the tab closed is kept, not released',
    file: 'voicesession.ts',
    old: "    if (this.disposed) { r?.cancel(); return }\n",
    new: "    if (false) { r?.cancel(); return }\n",
    expect: 'a recording whose permission prompt outlived the tab is released, not started',
  },
  {
    label: 'a cancelled recognition\'s late final result still submits',
    file: 'voicesession.ts',
    old: "      onResult: (t, final) => {\n        if (!live()) return\n",
    new: "      onResult: (t, final) => {\n",
    expect: 'a browser recognition can be cancelled, and a late result from it submits nothing',
  },
  {
    label: 'Read aloud speaks after its button is gone',
    file: 'readaloud.ts',
    old: "    if (this.disposed || mine !== this.generation) return\n",
    new: "    if (false) return\n",
    expect: 'a button that is gone before synthesis returns says nothing',
  },
  {
    label: 'unmounting any Read aloud stops whatever is playing',
    file: 'readaloud.ts',
    old: "    if (playing) this.deps.stopSpeaking()\n",
    new: "    this.deps.stopSpeaking()\n",
    expect: 'unmounting a button that is not playing stops nobody else\'s audio',
  },
  {
    label: 'the answer stream has no onclose again',
    file: 'api.ts',
    old: "    else if (msg.type === 'done') end(() => handlers.onDone?.({ answer: msg.answer, voice: msg.voice }))\n" +
         "    else if (msg.type === 'error') end(() => handlers.onError?.(msg.detail))\n" +
         "  }\n" +
         "  ws.onerror = () => end(() => handlers.onError?.('connection failed'))\n" +
         "  ws.onclose = () => end(() => handlers.onError?.(CLOSED_EARLY))\n",
    new: "    else if (msg.type === 'done') end(() => handlers.onDone?.({ answer: msg.answer, voice: msg.voice }))\n" +
         "    else if (msg.type === 'error') end(() => handlers.onError?.(msg.detail))\n" +
         "  }\n" +
         "  ws.onerror = () => end(() => handlers.onError?.('connection failed'))\n",
    expect: 'ask: a clean close with no terminal frame is an error, reported once',
  },
  {
    label: 'a stream can end twice (onerror and then onclose)',
    file: 'api.ts',
    old: "    if (over) return\n    over = true\n",
    new: "    over = true\n",
    expect: 'scenario: a transport error then its close is ONE error',
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
  {
    label: 'an aborted recognition still delivers its result',
    file: 'voice.ts',
    old: "    if (aborted) return\n    let transcript = ''\n",
    new: "    let transcript = ''\n",
    expect: 'an aborted recognition delivers no result and no error',
  },
  {
    label: 'the rail opts in to the cloud recogniser',
    file: 'voicesession.ts',
    old: "export const ALLOW_CLOUD_STT = false\n",
    new: "export const ALLOW_CLOUD_STT = true\n",
    expect: 'no on-device pack (null) and no media worker: no cloud recogniser, and it says so',
  },
  {
    label: 'no local recogniser falls through to the browser anyway (the old fallback)',
    file: 'voicesession.ts',
    old: "    if (path === 'broker') {\n      this.set({ err: NO_LOCAL_STT })\n",
    new: "    if (false) {\n      this.set({ err: NO_LOCAL_STT })\n",
    expect: 'no on-device pack (downloadable) and no media worker: no cloud recogniser, and it says so',
  },
  {
    label: 'the on-device path is not told to stay local',
    file: 'voicesession.ts',
    old: "      processLocally,\n    })\n",
    new: "    })\n",
    expect: 'an on-device pack is used, and the recogniser is told to stay local',
  },
  {
    label: 'a tab closed during the availability check still starts a recognition',
    file: 'voicesession.ts',
    old: "    if (this.disposed) return\n    const path = this.deps.chooseSttPath({\n",
    new: "    const path = this.deps.chooseSttPath({\n",
    expect: 'a tab closed while availability was being asked starts nothing',
  },
  {
    label: 'listen() drops processLocally',
    file: 'voice.ts',
    old: "    try { rec.processLocally = true } catch { /* read-only on older builds */ }\n",
    new: "",
    expect: 'listen() asked to stay local sets processLocally on the recogniser',
  },
  {
    label: 'an unknown availability answer is passed through as if it meant something',
    file: 'voice.ts',
    old: "    return got === 'available' || got === 'downloadable' || got === 'downloading'\n"
       + "      ? got : 'unavailable'\n",
    new: "    return got as SttAvailability\n",
    expect: 'availability is what the browser reports, and a downloadable pack is fetched',
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
    expect: 'a button that is gone before synthesis returns says nothing',
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
console.log(`baseline: voice-flows.test.js green, ${baseR.passed.size} tests reported ok\n`)

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
