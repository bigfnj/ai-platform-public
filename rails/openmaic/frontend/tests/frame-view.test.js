// What the OpenMAIC frame area shows (src/frameView.ts).
//
//   node --test rails/openmaic/frontend/tests/frame-view.test.js
//
// Plain node: the module has no runtime imports, so node's type stripping loads it directly.
// Driven from pytest by tests/test_frontend_helpers.py.
//
// The defect: one failed 5 s status probe unmounted the iframe, throwing away a class the user
// was in the middle of generating.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { frameView } from '../src/frameView.ts'

test('before the first poll the frame is mounted, not the error panel', () => {
  assert.equal(frameView(undefined, false), 'frame')
})

test('an app that has not answered in this mount shows the down panel', () => {
  assert.equal(frameView(false, false), 'down')
})

test('an app that answered and then failed ONE probe keeps the frame, with a warning', () => {
  assert.equal(frameView(false, true), 'frame-warning')
})

test('an app that answers shows the frame', () => {
  assert.equal(frameView(true, false), 'frame')
  assert.equal(frameView(true, true), 'frame')
})
