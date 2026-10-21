// Which assistant replies still belong on screen (src/chatGuard.ts).
//
//   node --test rails/terminal-fun/frontend/tests/chat-guard.test.js
//
// Plain node, like web/tests/*.test.js: the module has no runtime imports, so node's type
// stripping loads it directly. Driven from pytest by backend/tests/test_frontend_helpers.py.
//
// The defect: a reply asked from toy A that landed after toy B opened was shown in
// B's conversation, and its set_params was merged into B's params and sent on B's socket.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { settleReply } from '../src/chatGuard.ts'

const tune = (item, params) => ({ type: 'set_params', item, params })

test('a reply to the screen still open is shown, and its tuning applied over the current params', () => {
  const a = { item: 'cmatrix' }
  const s = settleReply(a, a, tune('cmatrix', { color: 'red' }), { color: 'green', speed: 5 })
  assert.deepEqual(s, { show: true, apply: { color: 'red', speed: 5 } })
})

test('a reply that lands after ANOTHER toy opened is dropped: not shown, not applied', () => {
  const a = { item: 'cmatrix' }
  const b = { item: 'cbonsai' }
  assert.deepEqual(settleReply(a, b, tune('cmatrix', { color: 'red' }), { leaves: 4 }),
    { show: false, apply: null })
})

test('the same toy re-opened after a detour is a new screen, so the old reply is dropped too', () => {
  // Its conversation was cleared on the way; an answer to a question nobody can see is noise.
  const first = { item: 'cmatrix' }
  const again = { item: 'cmatrix' }
  assert.deepEqual(settleReply(first, again, tune('cmatrix', { color: 'red' }), {}),
    { show: false, apply: null })
})

test('a reply asked from the menu is shown when the menu is still open, with nothing to apply', () => {
  const menu = { item: null }
  assert.deepEqual(settleReply(menu, menu, tune('cmatrix', { color: 'red' }), {}),
    { show: true, apply: null })
})

test('tuning for a different item than the one asked from is never applied', () => {
  const a = { item: 'cmatrix' }
  assert.deepEqual(settleReply(a, a, tune('cbonsai', { leaves: 9 }), {}), { show: true, apply: null })
})

test('a plain answer is shown and changes nothing', () => {
  const a = { item: 'cmatrix' }
  assert.deepEqual(settleReply(a, a, null, { color: 'green' }), { show: true, apply: null })
  assert.deepEqual(settleReply(a, a, { type: 'other' }, {}), { show: true, apply: null })
})
