// The shell's two pure decisions: what a status-poll answer does to the model widget
// (src/shell_status.ts), and where an insecure visit is sent (src/canonical.ts). And, read from
// src/App.tsx, that the theme state it keeps holds nothing it never reads.
//
//   node apps/platform/frontend/tests/shell-status.test.js
//
// Plain node: both modules have no runtime imports. Their wiring into the BUILT shell (a 401
// reaching the login form, no poll while hidden, the rail not re-rendered by the poll, a failed
// Unload explained) is proven in Chromium by web/tests/browser/railcheck.py shell.
// Mutation-tested by apps/platform/frontend/tests/mutate_shell_status.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { nextStatus, OFFLINE_AFTER } from '../src/shell_status.ts'
import { canonicalRedirect } from '../src/canonical.ts'

const UP = { broker_reachable: true, ollama_reachable: true }
const DOWN = { broker_reachable: false, ollama_reachable: false }
const fresh = { status: null, misses: 0 }

test('a 401 is a lost session, not a broker outage', () => {
  const s = nextStatus({ status: UP, misses: 0 }, { ok: false, httpStatus: 401 })
  assert.equal(s.sessionLost, true)
  assert.equal(s.status, null)
})

test('other failures are outages, and only the third in a row reads as offline', () => {
  let s = { status: UP, misses: 0 }
  for (let i = 1; i < OFFLINE_AFTER; i++) {
    s = nextStatus(s, { ok: false, httpStatus: 502 })
    assert.equal(s.sessionLost, false)
    assert.equal(s.status, UP, `miss ${i} already blanked the widget`)
  }
  s = nextStatus(s, { ok: false })
  assert.equal(s.status, null, 'still showing the last good status after three misses')
  assert.equal(s.sessionLost, false)
  assert.equal(OFFLINE_AFTER, 3)
})

test('an unreachable broker counts as a miss, and its own answer is shown once offline', () => {
  let s = nextStatus({ status: UP, misses: 0 }, { ok: true, status: DOWN })
  assert.equal(s.status, UP)
  s = nextStatus(nextStatus(s, { ok: true, status: DOWN }), { ok: true, status: DOWN })
  assert.equal(s.status, DOWN)
})

test('a good answer resets the count', () => {
  let s = nextStatus(nextStatus(fresh, { ok: false }), { ok: false })
  assert.equal(s.misses, 2)
  s = nextStatus(s, { ok: true, status: UP })
  assert.deepEqual({ status: s.status, misses: s.misses, lost: s.sessionLost }, { status: UP, misses: 0, lost: false })
})

const LAN = { href: 'http://192.168.1.20:8700/#/finance', origin: 'http://192.168.1.20:8700' }

test('no canonical URL configured means no redirect', () => {
  assert.equal(canonicalRedirect(undefined, LAN, false), null)
  assert.equal(canonicalRedirect('', LAN, false), null)
})

test('an insecure visit goes to the configured HTTPS URL, keeping its path and hash', () => {
  assert.equal(canonicalRedirect('https://platform.example.com', LAN, false), 'https://platform.example.com/#/finance')
})

test('never from a secure context, never to the same origin, never to http', () => {
  assert.equal(canonicalRedirect('https://platform.example.com', LAN, true), null)
  assert.equal(canonicalRedirect('http://192.168.1.20:8700', LAN, false), null)
  assert.equal(canonicalRedirect('http://lan-host.example.com', LAN, false), null)
  assert.equal(canonicalRedirect('not a url', LAN, false), null)
})

test('main.tsx hands the build variable over as is, with no default of its own', () => {
  const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
  const calls = [...src.matchAll(/canonicalRedirect\(([^,]+),/g)].map((m) => m[1].trim())
  assert.deepEqual(calls, ['import.meta.env.VITE_PLATFORM_CANONICAL_URL'])
  assert.doesNotMatch(src, /VITE_PLATFORM_CANONICAL_URL\s*(\?\?|\|\|)/, 'main.tsx gives the variable a fallback')
  assert.doesNotMatch(src, /https:\/\/[a-z]/i, 'main.tsx names a URL itself')
})

// The theme state App.tsx keeps from the server's answer. It stored the server's palette list
// beside what the menu reads, and nothing read it (the menu offers FRONTEND_PALETTES); a field
// stored and never read looks like one the menu depends on (audit 2026-10-14).
test('the theme state App.tsx keeps holds only what the shell reads', () => {
  const src = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const decl = /const \[themeMeta, setThemeMeta\] = useState<\{([^}]*)\}>/.exec(src)
  assert.ok(decl, 'the themeMeta state is not declared as App.tsx did')
  const keys = [...decl[1].matchAll(/(\w+)\s*:/g)].map((m) => m[1])
  assert.ok(keys.includes('hasOverride'), `the declaration parse found ${keys}`)
  const unread = keys.filter((k) => !new RegExp(`themeMeta\\.${k}\\b`).test(src))
  assert.deepEqual(unread, [], `stored in themeMeta and never read: ${unread.join(', ')}`)
  assert.match(src, /palettes: FRONTEND_PALETTES,/, 'the menu offers the shell\'s own list')
})
