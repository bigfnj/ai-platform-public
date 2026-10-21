// The Brief tab's data flow (briefsync.ts) and the item grid's type filter, under plain
// `node --test` with fakes. No bundler and no React: both modules have no runtime imports.
//
// Recorded defects, each reproduced against the fakes:
//   - switching lens tabs mid-synthesis killed the status poll with `busy` still true, so
//     Re-synthesize stayed disabled and "Synthesizing…" stayed up until a remount;
//   - the staleness auto-trigger ignored `started: false` (the cooldown), so the page polled a
//     run that did not exist and re-triggered every 3 s for the whole 15-minute window;
//   - switching to Archive kept a type filter the archive had no option for: a blank select
//     filtering everything out.
import { test } from 'node:test'
import assert from 'node:assert/strict'

const { BriefSync, POLL_MS, lensQuery } = await import('../src/briefsync.ts')
const { shownFilter } = await import('../src/types.ts')

const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)) }

/** A fake backend: briefs per query string, a status the test flips, and a log of calls. */
function backend({ stale = false } = {}) {
  const log = { briefs: [], refreshes: [], statuses: 0 }
  const timers = { next: 1, live: new Map() }
  const be = {
    running: false,
    refreshAnswer: { started: true },
    refreshError: null,
    stale,
  }
  const deps = {
    getBrief: async (qs) => {
      log.briefs.push(qs)
      // Every lens reports the same staleness: it is the inbox that changed, so a lane brief
      // is just as stale as the merge. Only the controller may decide who acts on it.
      return { exists: true, attention: [], stale_source: be.stale, source: qs || null }
    },
    postRefresh: async (path) => {
      log.refreshes.push(path)
      if (be.refreshError) throw be.refreshError
      if (be.refreshAnswer.started) be.running = true
      return be.refreshAnswer
    },
    getStatus: async () => { log.statuses++; return { running: be.running, last_error: null } },
    setInterval: (fn, ms) => { const id = timers.next++; timers.live.set(id, { fn, ms }); return id },
    clearInterval: (id) => { timers.live.delete(id) },
  }
  const tickAll = async () => {
    for (const { fn } of [...timers.live.values()]) fn()
    await flush()
  }
  return { deps, log, be, timers, tickAll }
}

function sync(opts) {
  const b = backend(opts)
  const s = new BriefSync(b.deps, () => {})
  return { s, ...b }
}

test('switching lens mid-synthesis keeps the poll, and the run still finishes the button', async () => {
  const { s, be, timers, tickAll, log } = sync()
  s.load()
  await flush()
  s.refresh('Top 10')
  await flush()
  assert.equal(s.state.busy, true)
  assert.equal(timers.live.size, 1, 'the run is being waited on')

  s.setLens('email')                    // the lane tab, mid-run
  await flush()
  assert.equal(timers.live.size, 1, 'switching lens killed the poll')
  assert.equal(s.state.busy, true)

  be.running = false                    // the run finishes
  await tickAll()
  assert.equal(s.state.busy, false, 'Re-synthesize stays disabled for good')
  assert.equal(timers.live.size, 0)
  assert.equal(log.briefs.at(-1), '?source=email', 'the lens on screen is the one reloaded')
})

test('a cooldown answer to the auto-trigger is not waited on, and does not loop', async () => {
  const { s, be, timers, tickAll, log } = sync({ stale: true })
  be.refreshAnswer = { started: false, reason: 'cooldown' }
  s.load()
  await flush()
  assert.deepEqual(log.refreshes, ['/api/brief/refresh?auto=1'])
  assert.equal(s.state.busy, false)
  assert.equal(timers.live.size, 0, 'it polled a run that did not exist')
  for (let i = 0; i < 5; i++) await tickAll()
  assert.equal(log.refreshes.length, 1, 'it re-triggered on every poll')
  assert.equal(s.state.note, '')
})

test('an auto-trigger that started a run is waited on and reloads when it lands', async () => {
  const { s, be, timers, tickAll, log } = sync({ stale: true })
  s.load()
  await flush()
  assert.equal(s.state.busy, true)
  assert.equal(timers.live.size, 1)
  be.running = false
  be.stale = false
  await tickAll()
  assert.equal(s.state.busy, false)
  assert.equal(log.briefs.length, 2)
  assert.equal(log.refreshes.length, 1)
})

test('an auto-trigger refused with 409 waits for the run already in flight', async () => {
  const { s, be, timers } = sync({ stale: true })
  be.refreshError = Object.assign(new Error('a synthesis run is already in progress'), { status: 409 })
  be.running = true
  s.load()
  await flush()
  assert.equal(s.state.busy, true)
  assert.equal(timers.live.size, 1)
})

test('only the Top 10 view auto-refreshes', async () => {
  const { s, log } = sync({ stale: true })
  s.setLens('email')
  await flush()
  assert.deepEqual(log.refreshes, [])
})

test('a run started before the tab opened is found and waited on', async () => {
  const { s, be, timers } = sync()
  be.running = true
  s.attach()
  await flush()
  assert.equal(s.state.busy, true)
  assert.equal(timers.live.size, 1)
})

test('after dispose nothing polls and nothing changes', async () => {
  const { s, timers, be } = sync()
  s.refresh()
  await flush()
  s.dispose()
  assert.equal(timers.live.size, 0)
  be.running = false
  await s.tick()
  assert.equal(s.state.busy, true, 'a disposed controller still wrote state')
})

test('a slower load for the previous lens cannot overwrite the current one', async () => {
  const { s, deps } = sync()
  const pending = []
  deps.getBrief = (qs) => new Promise((resolve) => pending.push({ qs, resolve }))
  s.load()                               // 'all'
  s.setLens('email')
  pending[1].resolve({ exists: true, attention: [], source: 'email' })
  pending[0].resolve({ exists: true, attention: [], source: null })
  await flush()
  assert.equal(s.state.brief.source, 'email')
})

test('a status read that fails stops the poll and replaces the "Synthesizing" banner', async () => {
  const { s, deps, timers, tickAll } = sync()
  s.refresh()
  await flush()
  deps.getStatus = async () => { throw new Error('502') }
  await tickAll()
  assert.equal(timers.live.size, 0)
  assert.equal(s.state.busy, false)
  assert.ok(!s.state.note.startsWith('Synthesizing'), `stale banner: ${s.state.note}`)
})

test('the poll interval and lens queries are the documented ones', () => {
  assert.equal(POLL_MS, 3000)
  assert.equal(lensQuery('all'), '')
  assert.equal(lensQuery('teams'), '?source=teams')
})

// --- the item grid's type filter ------------------------------------------------------------------

test('a type the loaded set does not offer reads as "all", never as a blank filter', () => {
  assert.equal(shownFilter('conflict', ['action', 'conflict']), 'conflict')
  assert.equal(shownFilter('conflict', ['action']), 'all')
  assert.equal(shownFilter('all', []), 'all')
})
