// The shell-to-frame light/dark hand-off (src/frameTheme.ts).
//
//   node --test rails/openmaic/frontend/tests/frame-theme.test.js
//
// Plain node: the module has no runtime imports, so node's type stripping loads it directly.
// Driven from pytest by tests/test_frontend_helpers.py.
//
// The defect: the framed OpenMAIC app followed the OS prefers-color-scheme, never the shell's
// chosen mode, so Dark on a light OS framed a white app. Then two more, found once that was
// fixed: every load painted the app's server-rendered page light until it hydrated,
// and a live shell switch moved the frame's colours but left the app's own theme button on the
// icon it mounted with. The browser half of this, the real module and a real frame, is
// railcheck's om-frame-theme and om-frame-boot.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  FRAME_THEME_KEY, HOLD_MS, WATCH_MS, findAppTheme, holdFrameClass, isAppTheme, resolvedMode,
  shellMode, syncFrameTheme, watchFrameDocuments,
} from '../src/frameTheme.ts'

function fakes() {
  const store = new Map()
  const classes = new Set()
  return {
    store,
    classes,
    storage: { setItem: (k, v) => store.set(k, v) },
    root: { classList: { toggle: (t, on) => (on ? classes.add(t) : classes.delete(t), on) } },
  }
}

test('the key is the one upstream reads', () => {
  // OpenMAIC lib/hooks/use-theme.tsx: localStorage.getItem('theme').
  assert.equal(FRAME_THEME_KEY, 'theme')
})

test('data-theme is the shell mode, and its absence is System', () => {
  assert.equal(shellMode('dark'), 'dark')
  assert.equal(shellMode('light'), 'light')
  assert.equal(shellMode(null), 'system')
  assert.equal(shellMode(undefined), 'system')
  assert.equal(shellMode('sepia'), 'system')
})

test('System resolves through the OS; an explicit mode ignores it', () => {
  assert.equal(resolvedMode('system', true), 'dark')
  assert.equal(resolvedMode('system', false), 'light')
  assert.equal(resolvedMode('dark', false), 'dark')
  assert.equal(resolvedMode('light', true), 'light')
})

test('shell Dark on a light OS: the frame is told dark, in storage and on its root', () => {
  const f = fakes()
  syncFrameTheme('dark', false, f.storage, f.root)
  assert.equal(f.store.get('theme'), 'dark')
  assert.ok(f.classes.has('dark'))
})

test('shell Light on a dark OS: the frame is told light, and a dark class it had is removed', () => {
  const f = fakes()
  f.classes.add('dark')
  syncFrameTheme('light', true, f.storage, f.root)
  assert.equal(f.store.get('theme'), 'light')
  assert.ok(!f.classes.has('dark'))
})

test('shell System is passed through as system, so the app keeps following the OS itself', () => {
  const f = fakes()
  syncFrameTheme('system', true, f.storage, f.root)
  assert.equal(f.store.get('theme'), 'system')
  assert.ok(f.classes.has('dark'))
})

test('no frame yet: storage is still written; blocked storage: the class still applies', () => {
  const a = fakes()
  syncFrameTheme('dark', false, a.storage, null)
  assert.equal(a.store.get('theme'), 'dark')
  const b = fakes()
  const throwing = { setItem: () => { throw new Error('QuotaExceededError') } }
  assert.doesNotThrow(() => syncFrameTheme('dark', false, throwing, b.root))
  assert.ok(b.classes.has('dark'))
  assert.doesNotThrow(() => syncFrameTheme('dark', false, null, null))
})

// --- the app's own theme state (its theme icon stayed where it mounted) ----------------------

const ctx = (theme = 'dark', resolvedTheme = 'dark') => {
  const calls = []
  return { theme, resolvedTheme, setTheme: (t) => calls.push(t), calls }
}

test('isAppTheme takes exactly the shape upstream\'s ThemeProvider hands out', () => {
  assert.ok(isAppTheme(ctx()))
  assert.ok(isAppTheme(ctx('system', 'light')))
  assert.ok(!isAppTheme(null))
  assert.ok(!isAppTheme({ theme: 'dark', resolvedTheme: 'dark' }))             // no setter
  assert.ok(!isAppTheme({ ...ctx(), theme: 'sepia' }))
  assert.ok(!isAppTheme({ ...ctx(), resolvedTheme: 'system' }))
  assert.ok(!isAppTheme({ locale: 'en-US', setLocale() {}, t() {} }))         // the i18n context
})

// A DOM element as React leaves it: an own __reactFiber$<suffix> property whose fiber climbs
// through `return` to the providers above it.
function fiberChain(...values) {
  let f = null
  for (const v of values) f = { memoizedProps: v === undefined ? {} : { value: v }, return: f }
  return f
}
const el = (fiber) => (fiber ? { ['__reactFiber$x1y2']: fiber, other: 1 } : { other: 1 })
const body = (...els) => ({ querySelectorAll: () => els })

test('findAppTheme climbs from a rendered element to the theme provider above it', () => {
  const theme = ctx()
  // outermost first: the theme provider, then the i18n provider, then the element's own fiber
  const found = findAppTheme(body(el(null), el(fiberChain(theme, { locale: 'en-US' }, undefined))))
  assert.equal(found, theme)
})

test('findAppTheme skips an element outside the provider (a portal) and tries the next', () => {
  const theme = ctx()
  const portal = el(fiberChain(undefined, undefined))
  assert.equal(findAppTheme(body(portal, el(fiberChain(theme, undefined)))), theme)
  assert.equal(findAppTheme(body(portal, el(fiberChain(theme))), 1), null)    // maxElements
})

test('findAppTheme is null before hydration, for a reshaped context, and on a dying document', () => {
  assert.equal(findAppTheme(body(el(null), el(null))), null)                   // no fibers yet
  assert.equal(findAppTheme(body(el(fiberChain({ mode: 'dark', set() {} }), undefined))), null)
  assert.equal(findAppTheme(null), null)
  assert.equal(findAppTheme({ querySelectorAll: () => { throw new Error('gone') } }), null)
})

test('a live switch calls the app\'s own setTheme with the shell mode, System included', () => {
  for (const mode of ['light', 'dark', 'system']) {
    const f = fakes()
    const app = ctx()
    syncFrameTheme(mode, false, f.storage, f.root, app)
    assert.deepEqual(app.calls, [mode])
    assert.equal(f.store.get('theme'), mode)
  }
})

test('an app setter that throws leaves the storage and the class done', () => {
  const f = fakes()
  const app = { ...ctx(), setTheme: () => { throw new Error('boom') } }
  assert.doesNotThrow(() => syncFrameTheme('dark', false, f.storage, f.root, app))
  assert.equal(f.store.get('theme'), 'dark')
  assert.ok(f.classes.has('dark'))
})

// --- before the first paint (the server-rendered page painted light) -------------------------

function clock() {
  const q = new Map()
  let next = 1
  const c = {
    t: 0,
    raf: (fn) => { const id = next++; q.set(id, fn); return id },
    caf: (id) => q.delete(id),
    now: () => c.t,
    pending: () => q.size,
    frame(ms = 16) {                     // one animation frame of the host page
      c.t += ms
      const due = [...q.values()]
      q.clear()
      for (const fn of due) fn()
    },
  }
  return c
}

function doc(url = 'http://x/openmaic/api/app') {
  const handlers = []
  return {
    URL: url,
    documentElement: { classList: { toggle() {}, contains() { return false } } },
    defaultView: { addEventListener: (type, fn) => handlers.push([type, fn]) },
    unload() { for (const [type, fn] of handlers) if (type === 'pagehide') fn() },
  }
}

test('each new frame document is handed over once, and about:blank never', () => {
  const c = clock()
  const frame = { contentDocument: doc('about:blank') }
  const seen = []
  watchFrameDocuments(() => frame, (d) => seen.push(d), c)
  c.frame()
  assert.deepEqual(seen, [])
  const first = doc()
  frame.contentDocument = first
  c.frame()
  c.frame()
  assert.deepEqual(seen, [first])
  assert.equal(c.pending(), 0, 'the watch sleeps once its document is found')
  first.unload()                         // the frame navigates: watch for the next one
  const second = doc()
  frame.contentDocument = second
  c.frame()
  assert.deepEqual(seen, [first, second])
})

test('a document whose <html> is not parsed yet is picked up on a later frame', () => {
  const c = clock()
  const d = doc()
  const html = d.documentElement
  d.documentElement = null
  const frame = { contentDocument: d }
  const seen = []
  watchFrameDocuments(() => frame, (x) => seen.push(x), c)
  c.frame()
  assert.deepEqual(seen, [])
  d.documentElement = html
  c.frame()
  assert.deepEqual(seen, [d])
})

test('the watch gives up after WATCH_MS without a document, and stop() cancels it', () => {
  const c = clock()
  watchFrameDocuments(() => ({ contentDocument: null }), () => {}, c)
  c.frame(WATCH_MS + 1)
  c.frame()
  assert.equal(c.pending(), 0)
  const c2 = clock()
  const stop = watchFrameDocuments(() => ({ contentDocument: null }), () => {}, c2)
  assert.equal(c2.pending(), 1)
  stop()
  assert.equal(c2.pending(), 0)
})

// The frame's <html> and a MutationObserver as the hold sees them. A mutation is recorded only
// while the observer is attached. The app's add()/remove() record one even when nothing changes
// (Chromium does); toggle(token, force) records one only when it changes the class, which is what
// the hold relies on to tell its own corrections.
function holdFakes() {
  const classes = new Set()
  let pending = 0
  let cb = null
  let disconnected = false
  const timers = []
  const record = () => { if (cb && !disconnected) pending++ }
  const root = {
    classList: {
      contains: (t) => classes.has(t),
      toggle: (t, on) => { if (classes.has(t) !== on) { record(); on ? classes.add(t) : classes.delete(t) } return on },
    },
  }
  const app = {
    add: () => { classes.add('dark'); record() },
    remove: () => { classes.delete('dark'); record() },
  }
  const env = {
    observe: (_root, onRecords) => { cb = onRecords; return { disconnect: () => { disconnected = true } } },
    setTimeout: (fn, ms) => { timers.push([fn, ms]); return timers.length },
    clearTimeout: () => {},
  }
  return {
    root, app, env, classes, timers,
    deliver() { if (!disconnected && pending) { const n = pending; pending = 0; cb(n) } else pending = 0 },
    get released() { return disconnected },
  }
}

test('the hold sets the class at once and puts it back when the app\'s first render removes it', () => {
  const h = holdFakes()
  holdFrameClass(h.root, () => true, h.env)
  assert.ok(h.classes.has('dark'), 'set before the document can paint')
  h.deliver()                            // only the hold's own change: nothing to do
  assert.ok(!h.released)
  h.app.remove()                         // upstream's first render: 'system' resolved light
  h.deliver()
  assert.ok(h.classes.has('dark'), 'put back before the next paint')
  assert.ok(!h.released)
  h.deliver()                            // the hold's own correction is not read as the app's
  assert.ok(!h.released)
  h.app.add()                            // the re-render with the stored mode
  h.deliver()
  assert.ok(h.released, 'let go once the app applies the same mode itself')
  h.app.remove()                         // the user's own pick in the app's menu
  h.deliver()
  assert.ok(!h.classes.has('dark'), 'after the release the class is the app\'s')
})

test('under a light shell the app\'s first render already agrees, so the hold lets go at once', () => {
  const h = holdFakes()
  holdFrameClass(h.root, () => false, h.env)
  h.app.remove()
  h.deliver()
  assert.ok(h.released)
})

test('the hold reads the wanted mode when it acts, and HOLD_MS ends a hold the app never ends', () => {
  const h = holdFakes()
  let want = true
  holdFrameClass(h.root, () => want, h.env)
  h.deliver()
  want = false                           // the shell switched while the frame loaded
  h.app.add()                            // the app applies dark: not what the shell wants now
  h.deliver()
  assert.ok(!h.classes.has('dark'))
  assert.ok(!h.released)
  assert.deepEqual(h.timers.map(([, ms]) => ms), [HOLD_MS])
  h.timers[0][0]()
  assert.ok(h.released)
})

test('a hold on a document that is going away releases instead of throwing', () => {
  const h = holdFakes()
  const env = { ...h.env, observe: () => { throw new Error('gone') } }
  let release
  assert.doesNotThrow(() => { release = holdFrameClass(h.root, () => true, env) })
  assert.doesNotThrow(() => release())
})
