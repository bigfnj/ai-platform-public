// The two demo sockets' shared lifecycle (src/streamSocket.ts) and the Embedding Lab's results
// merge (src/demos/embed-bench/rows.ts).
//
//   node --test rails/ai-playground/frontend/tests/lab-helpers.test.js
//
// Plain node, like web/tests/*.test.js: both modules have no runtime imports, so node's type
// stripping loads them directly and nothing here needs a bundler or a React harness. Driven from
// pytest by tests/test_frontend_helpers.py, which is how the rail's suite runs it.
//
// What is pinned, each a defect that shipped (found 2026-10-08 and 2026-09-11):
//   * a stream that closes with no terminal frame is reported, once; it used to be silence;
//   * close() stops the socket and reports nothing afterwards, which is what an unmount needs;
//   * a result row the plan did not predict (the reranker's failure row) is kept, not dropped.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LOST_CLOSED, LOST_FAILED, runStream } from '../src/streamSocket.ts'
import { mergeResults } from '../src/demos/embed-bench/rows.ts'

class FakeSocket {
  constructor() {
    this.onopen = null
    this.onmessage = null
    this.onerror = null
    this.onclose = null
    this.sent = []
    this.closeCalls = 0
  }
  send(data) { this.sent.push(data) }
  close() { this.closeCalls += 1 }
  // What the browser does, driven by hand.
  open() { this.onopen?.() }
  frame(obj) { this.onmessage?.({ data: JSON.stringify(obj) }) }
  fail() { this.onerror?.() }
  closed() { this.onclose?.() }
}

function recorder() {
  const seen = { frames: [], lost: [] }
  const handlers = {
    onFrame(m) { seen.frames.push(m.type); return m.type === 'done' || m.type === 'error' },
    onLost(msg) { seen.lost.push(msg) },
  }
  return { seen, handlers }
}

test('the request is sent once the socket opens', () => {
  const ws = new FakeSocket()
  const { handlers } = recorder()
  runStream(ws, { corpus: 1, configs: [] }, handlers)
  assert.deepEqual(ws.sent, [])
  ws.open()
  assert.deepEqual(ws.sent, [JSON.stringify({ corpus: 1, configs: [] })])
})

test('a terminal frame finishes the stream: it is closed and its close is not an error', () => {
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  runStream(ws, {}, handlers)
  ws.open()
  ws.frame({ type: 'progress' })
  ws.frame({ type: 'done' })
  assert.equal(ws.closeCalls, 1)
  ws.closed()
  assert.deepEqual(seen.frames, ['progress', 'done'])
  assert.deepEqual(seen.lost, [])
})

test('a clean close with NO terminal frame is reported, once', () => {
  // The gateway closes the browser side with 1000 when the upstream dies, so a clean close is
  // exactly what a failed run looks like. The Lab had no onclose and stayed "Running..." for ever.
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  runStream(ws, {}, handlers)
  ws.open()
  ws.frame({ type: 'meta' })
  ws.closed()
  ws.closed()
  assert.deepEqual(seen.lost, [LOST_CLOSED])
})

test('an error then a close is ONE report, naming the failure', () => {
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  runStream(ws, {}, handlers)
  ws.fail()
  ws.closed()
  assert.deepEqual(seen.lost, [LOST_FAILED])
})

test('close() closes the socket and nothing is reported after it', () => {
  // An unmount. The Lab used to keep the socket in a local const, so leaving the tab could not
  // close it, and the backend's cancel never fired.
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  const h = runStream(ws, {}, handlers)
  ws.open()
  h.close()
  assert.equal(ws.closeCalls, 1)
  ws.frame({ type: 'progress' })
  ws.closed()
  assert.deepEqual(seen.frames, [])
  assert.deepEqual(seen.lost, [])
  h.close()
  assert.equal(ws.closeCalls, 1, 'a second close() must not close again')
})

test('close() before the socket opens means the request is never sent', () => {
  const ws = new FakeSocket()
  const { handlers } = recorder()
  runStream(ws, {}, handlers).close()
  ws.open()
  assert.deepEqual(ws.sent, [])
})

test('frames after the terminal one are ignored', () => {
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  runStream(ws, {}, handlers)
  ws.frame({ type: 'error' })
  ws.frame({ type: 'done' })
  assert.deepEqual(seen.frames, ['error'])
})

test('a frame that is not JSON is skipped, not fatal', () => {
  const ws = new FakeSocket()
  const { seen, handlers } = recorder()
  runStream(ws, {}, handlers)
  ws.onmessage({ data: 'not json' })
  ws.frame({ type: 'done' })
  assert.deepEqual(seen.frames, ['done'])
})

test('mergeResults fills the placeholders in order and APPENDS rows the plan did not predict', () => {
  const placeholders = [{ id: 'a|model|native' }, { id: 'a|model|native|+rerank' }, { id: 'b|model|native' }]
  const results = [
    { id: 'b|model|native', metrics: 1 },
    { id: 'a|model|native', metrics: 2 },
    // The reranker could not load: its failure row carries the reason and has no placeholder.
    { id: 'rr|+rerank', error: 'ValueError: reranker assets not fetched yet' },
  ]
  assert.deepEqual(mergeResults(placeholders, results), [
    { id: 'a|model|native', metrics: 2 },
    { id: 'a|model|native|+rerank' },
    { id: 'b|model|native', metrics: 1 },
    { id: 'rr|+rerank', error: 'ValueError: reranker assets not fetched yet' },
  ])
})

test('mergeResults keeps a placeholder that got no result rather than inventing one', () => {
  assert.deepEqual(mergeResults([{ id: 'x' }], []), [{ id: 'x' }])
})
