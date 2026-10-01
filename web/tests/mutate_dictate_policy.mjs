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
// go red? Two of the three are the inversion itself, from opposite directions.
import { readFileSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const SRC = join(REPO, 'web', 'src', 'dictate_policy.ts')
const SUITE = join(REPO, 'web', 'tests', 'dictate-policy.test.js')

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
]

function runSuite() {
  const p = spawnSync(process.execPath, ['--test', SUITE], { encoding: 'utf8', cwd: REPO })
  return { code: p.status, out: (p.stdout || '') + (p.stderr || '') }
}

const original = readFileSync(SRC, 'utf8')
let base = runSuite()
if (base.code !== 0) {
  console.log('BASELINE IS NOT GREEN -- fix that before trusting any mutation result.')
  console.log(base.out.slice(-1500))
  process.exit(2)
}
console.log('baseline: dictate-policy.test.js green\n')

const results = []
for (const m of MUTATIONS) {
  const n = original.split(m.old).length - 1
  if (n !== 1) {
    console.log(`  ERROR  ${m.label}: pattern found ${n} times, expected 1`)
    results.push([m.label, 'PATTERN'])
    continue
  }
  try {
    writeFileSync(SRC, original.replace(m.old, m.new), 'utf8')
    const { code, out } = runSuite()
    const named = out.includes(m.expect)
    const verdict = code !== 0 && named ? 'FIRED' : 'SURVIVED'
    results.push([m.label, verdict])
    console.log(`  ${verdict.padEnd(9)} ${m.label}\n            (named "${m.expect}": ${named})`)
  } finally {
    writeFileSync(SRC, original, 'utf8')
  }
}

const after = runSuite()
console.log(`\nrestored tree: ${after.code === 0 ? 'green' : 'STILL RED -- TREE IS DIRTY'}`)
const bad = results.filter(([, v]) => v !== 'FIRED')
console.log(`${results.length - bad.length}/${results.length} mutations fired the right test`)
process.exit(bad.length === 0 && after.code === 0 ? 0 : 1)
