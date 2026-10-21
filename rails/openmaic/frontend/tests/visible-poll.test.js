// The capabilities poll skips a hidden page and catches up on return (src/visiblePoll.ts).
//
//   node --test rails/<rail>/frontend/tests/visible-poll.test.js
//
// Plain node: the module has no runtime imports. Driven from pytest by the rail's
// test_frontend_helpers.py. The same test file sits beside each of the three copies.
//
// The defect: the 6 s poll ignored document.hidden, so a background tab cost
// three broker round trips (one spawning nvidia-smi) ten times a minute, indefinitely.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { startVisiblePoll } from '../src/visiblePoll.ts'

// A fake page: its visibility, its listeners, and the caller's interval (`every`).
function fakePage(hidden) {
  const page = {
    isHidden: hidden,
    timers: [],
    listeners: [],
    hidden: () => page.isHidden,
    onVisibilityChange(fn) {
      page.listeners.push(fn)
      return () => { page.listeners = page.listeners.filter((f) => f !== fn) }
    },
    every: (fn) => {
      const t = { fn, live: true }
      page.timers.push(t)
      return () => { t.live = false }
    },
    tick() { for (const t of page.timers) if (t.live) t.fn() },
    setHidden(h) { page.isHidden = h; for (const f of [...page.listeners]) f() },
  }
  return page
}

function counter() {
  const c = { n: 0, load: () => { c.n += 1 } }
  return c
}

test('a visible page loads at once and on every tick', () => {
  const page = fakePage(false)
  const c = counter()
  startVisiblePoll(c.load, page.every, page)
  assert.equal(c.n, 1)
  assert.equal(page.timers.length, 1, 'the caller\'s interval was not started')
  page.tick()
  page.tick()
  assert.equal(c.n, 3)
})

test('a hidden page skips its ticks', () => {
  const page = fakePage(false)
  const c = counter()
  startVisiblePoll(c.load, page.every, page)
  page.setHidden(true)             // going hidden is a visibility change too: no load for it
  page.tick()
  page.tick()
  page.tick()
  assert.equal(c.n, 1, 'a background tab still polled')
})

test('becoming visible loads once, at once', () => {
  const page = fakePage(true)
  const c = counter()
  startVisiblePoll(c.load, page.every, page)
  assert.equal(c.n, 0, 'a page mounted hidden asked anyway')
  page.tick()
  assert.equal(c.n, 0)
  page.setHidden(false)
  assert.equal(c.n, 1, 'the chips were not refreshed on return')
  page.tick()
  assert.equal(c.n, 2)
})

test('stop() ends the interval and the visibility subscription', () => {
  const page = fakePage(false)
  const c = counter()
  const stop = startVisiblePoll(c.load, page.every, page)
  stop()
  page.tick()
  page.setHidden(true)
  page.setHidden(false)
  assert.equal(c.n, 1)
  assert.equal(page.listeners.length, 0, 'the visibilitychange listener outlived the component')
})
