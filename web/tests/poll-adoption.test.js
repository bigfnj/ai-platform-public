// No raw setInterval in the frontends that poll through web-core's usePoll.
//
//   node web/tests/poll-adoption.test.js
//
// The audit counted 18 pollers and 16 ignored document.hidden (the shell's 5 s status poll
// spawns nvidia-smi on the broker every time). The shell, web-core and the rails below now poll
// through usePoll / startPoll (web/src/poll.ts: no tick while hidden, none overlapping, stop on
// a verdict or unmount). A setInterval reappearing in one of them is a poller that skipped all
// of that. Timers that are not network polls are listed in ALLOWED with the reason.
//
// The other rails (ai-playground, co-worker, gemini-cx, meeting-atlas, openmaic,
// smb-partner-enablement, terminal-fun, workstation) are not held to it yet; add each here as it
// adopts usePoll. Mutation-tested by web/tests/mutate_poll_adoption.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const rel = (p) => relative(REPO, p).split(sep).join('/')

const RAIL_IDS = ['edu-suite', 'recipe-book', 'finance', 'bouquet', 'iep', 'job-aid', 'ai-voice',
  'course-builder', 'servicenow']
// edu-suite keeps its tiles' frontend under apps/dashboard/; the rest at <rail>/frontend.
const SRC_AT = { 'edu-suite': 'apps/dashboard/frontend/src' }
const railRoot = (id) => `rails/${id}/${SRC_AT[id] ?? 'frontend/src'}`

// web/ ships in the public snapshot, which withholds most of RAIL_IDS and withholds
// tools/publish.py too: that file's absence is how a test knows it is running there (as
// services/broker/tests/test_role_map.py does). There a rail with no directory at all is skipped
// and named on every run. In this repo nothing is skipped, so a moved src still fails below.
const PUBLISHED = !existsSync(join(REPO, 'tools', 'publish.py'))
const ABSENT = PUBLISHED ? RAIL_IDS.filter((id) => !existsSync(join(REPO, 'rails', id))) : []

const ROOTS = ['web/src', 'apps/platform/frontend/src',
  ...RAIL_IDS.filter((id) => !ABSENT.includes(id)).map(railRoot)]

if (ABSENT.length) {
  test('rails this published tree does not carry', {
    skip: `published snapshot: ${ABSENT.join(', ')} withheld, so their pollers are not checked here` }, () => {})
}

const ALLOWED = {
  'web/src/voice.ts': 'the recording level meter, a 40 ms local timer that is not a request',
  'apps/platform/frontend/src/deskpet/DeskPet.tsx': "the desk pet's idle chatter, an animation timer",
}

// Code only: a comment that mentions usePoll( or setInterval( is not a poller.
const code = (f) => readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist') continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p)
  }
  return out
}

test('every covered root exists', () => {
  const missing = ROOTS.filter((r) => !existsSync(join(REPO, r)))
  assert.deepEqual(missing, [])
})

test('no raw setInterval outside the allowed timers', () => {
  const hits = []
  for (const r of ROOTS) {
    for (const f of walk(join(REPO, r))) {
      const src = code(f)
      if (/\bsetInterval\s*\(/.test(src) && !(rel(f) in ALLOWED)) hits.push(rel(f))
    }
  }
  assert.deepEqual(hits, [], `poll through usePoll instead: ${hits.join(', ')}`)
})

test('every allowed timer is still there (a stale allowance hides the next one)', () => {
  const stale = Object.keys(ALLOWED).filter((f) => !/\bsetInterval\s*\(/.test(readFileSync(join(REPO, f), 'utf8')))
  assert.deepEqual(stale, [])
})

// ai-voice has no interval poller: its one loop is the job poll, pollJob, which takes an abort
// signal of its own (that rail's own poll-job.test.js).
const NO_INTERVAL_POLLER = ['ai-voice'].map(railRoot)

test('the rails that poll do so through usePoll', () => {
  const pollers = ROOTS.filter((r) => r.startsWith('rails/') && !NO_INTERVAL_POLLER.includes(r)).filter((r) =>
    !walk(join(REPO, r)).some((f) => /\busePoll\s*\(/.test(code(f))))
  assert.deepEqual(pollers, [], 'a covered rail with no usePoll at all: its polls went somewhere else')
})
