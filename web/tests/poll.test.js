// The shared poller every frontend's polls go through (web/src/poll.ts).
//
//   node web/tests/poll.test.js
//
// Plain node with a fake clock and a fake page: poll.ts takes its timers and its visibility
// from an injected env, so each rule below is driven exactly, with no real waiting.
// Mutation-tested by web/tests/mutate_poll.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startPoll } from '../src/poll.ts'

// A clock that only moves when told to, and a page whose visibility the test sets.
function fakeEnv({ hidden = false } = {}) {
  let now = 0
  let seq = 0
  const timers = new Map()
  const listeners = new Set()
  const env = {
    hiddenNow: hidden,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + ms, fn }); return id },
    clearTimeout(id) { timers.delete(id) },
    hidden: () => env.hiddenNow,
    onVisibility(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    // Advance time, firing due timers in order, letting each tick's promise chain settle.
    async advance(ms) {
      const until = now + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0])
        now = due[1].at
        due[1].fn()
        await flush()
      }
      now = until
      await flush()
    },
    async setHidden(h) { env.hiddenNow = h; for (const fn of [...listeners]) fn(); await flush() },
    pending: () => timers.size,
    listening: () => listeners.size,
  }
  return env
}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

// A tick whose answer the test releases by hand, to hold a request "in flight".
function deferredTick() {
  const calls = []
  const tick = (signal) => new Promise((resolve) => calls.push({ resolve, signal }))
  return { tick, calls }
}

test('ticks now and then once per interval', async () => {
  const env = fakeEnv()
  let n = 0
  const stop = startPoll(() => { n++ }, 1000, env)
  await flush()
  assert.equal(n, 1, 'no tick at start')
  await env.advance(999)
  assert.equal(n, 1)
  await env.advance(1)
  assert.equal(n, 2)
  await env.advance(3000)
  assert.equal(n, 5)
  stop()
})

test('a slow tick is never overlapped: the next one waits for it to settle', async () => {
  const env = fakeEnv()
  const { tick, calls } = deferredTick()
  const stop = startPoll(tick, 1000, env)
  await flush()
  assert.equal(calls.length, 1)
  await env.advance(5000)          // five intervals pass with the first answer still out
  assert.equal(calls.length, 1, 'a second request went out while the first was in flight')
  calls[0].resolve()
  await flush()
  await env.advance(1000)
  assert.equal(calls.length, 2)
  stop()
})

test('nothing is fetched while the page is hidden, and the owed tick runs on return', async () => {
  const env = fakeEnv()
  let n = 0
  const stop = startPoll(() => { n++ }, 1000, env)
  await flush()
  assert.equal(n, 1)
  await env.setHidden(true)
  await env.advance(10_000)
  assert.equal(n, 1, `${n - 1} ticks ran while hidden`)
  await env.setHidden(false)
  assert.equal(n, 2, 'the owed tick did not run when the page came back')
  await env.advance(1000)
  assert.equal(n, 3, 'polling did not resume its interval after the page came back')
  stop()
})

test('a poll started in a hidden page waits for it to be visible', async () => {
  const env = fakeEnv({ hidden: true })
  let n = 0
  const stop = startPoll(() => { n++ }, 1000, env)
  await env.advance(5000)
  assert.equal(n, 0)
  await env.setHidden(false)
  assert.equal(n, 1)
  stop()
})

test('a visibility change with no tick owed does not add one', async () => {
  const env = fakeEnv()
  let n = 0
  const stop = startPoll(() => { n++ }, 1000, env)
  await flush()
  await env.setHidden(true)
  await env.setHidden(false)       // back before the next tick came due
  assert.equal(n, 1, 'an extra tick ran on a visibility change that owed nothing')
  await env.advance(1000)
  assert.equal(n, 2)
  stop()
})

test("a tick that returns 'stop' ends the poll and lets go of the page", async () => {
  const env = fakeEnv()
  let n = 0
  startPoll(() => { n++; return n === 2 ? 'stop' : undefined }, 1000, env)
  await flush()
  await env.advance(10_000)
  assert.equal(n, 2)
  assert.equal(env.pending(), 0, 'a timer is still pending after stop')
  assert.equal(env.listening(), 0, 'the visibility listener was not removed')
})

test('a tick that throws does not end the poll', async () => {
  const env = fakeEnv()
  let n = 0
  const stop = startPoll(() => { n++; throw new Error('transient') }, 1000, env)
  await flush()
  await env.advance(2000)
  assert.equal(n, 3)
  stop()
})

test('stop() aborts the in-flight request and nothing is scheduled after it settles', async () => {
  const env = fakeEnv()
  const { tick, calls } = deferredTick()
  const stop = startPoll(tick, 1000, env)
  await flush()
  stop()
  assert.equal(calls[0].signal.aborted, true, 'the signal handed to the tick was not aborted')
  calls[0].resolve()
  await flush()
  await env.advance(5000)
  assert.equal(calls.length, 1, 'a tick ran after stop()')
  assert.equal(env.pending(), 0)
  assert.equal(env.listening(), 0)
})

test('stallDetector: true once N answers in a row change nothing, and a change resets it', async () => {
  const { stallDetector } = await import('../src/poll.ts')
  const stalled = stallDetector(3)
  assert.deepEqual(['a', 'a', 'a'].map(stalled), [false, false, false])
  assert.equal(stalled('a'), true, 'three unchanged answers after the first did not stall')
  assert.equal(stalled('b'), false, 'a change did not reset the count')
  assert.deepEqual(['b', 'b'].map(stalled), [false, false])
  assert.equal(stalled('b'), true)
})
