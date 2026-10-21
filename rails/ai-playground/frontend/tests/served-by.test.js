// The RAG demo's "who answered" line (src/demos/rag/servedBy.ts).
//
//   node --test rails/ai-playground/frontend/tests/served-by.test.js
//
// Plain node, like lab-helpers.test.js: the module has no runtime imports, so node's type
// stripping loads it directly. Driven from pytest by tests/test_frontend_helpers.py.
//
// What is pinned: an answer the Claude CLI gave says so (the generation label above it names the
// local model, which did not answer it), a Claude fallback says the local model answered and why,
// and nothing else the socket sends (a ping, a token, a malformed frame) produces a line.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { servedByLabel } from '../src/demos/rag/servedBy.ts'

test('a Claude answer says Claude, with the model the broker named', () => {
  assert.equal(servedByLabel({ type: 'backend', backend: 'claude', model: 'claude-haiku-5-5' }),
    'answered by Claude · claude-haiku-5-5')
})

test("a Claude answer with no model id, or the broker's placeholder, says Claude alone", () => {
  assert.equal(servedByLabel({ type: 'backend', backend: 'claude', model: '' }),
    'answered by Claude')
  assert.equal(servedByLabel({ type: 'backend', backend: 'claude', model: 'claude-cli' }),
    'answered by Claude')
  assert.equal(servedByLabel({ type: 'backend', backend: 'claude' }), 'answered by Claude')
})

test('a Claude fallback says the local model answered, and why', () => {
  assert.equal(servedByLabel({ type: 'backend', backend: 'ollama', reason: 'busy' }),
    'answered by the local model: Claude was unavailable (busy)')
  assert.equal(servedByLabel({ type: 'backend', backend: 'ollama' }),
    'answered by the local model: Claude was unavailable')
})

test('every other frame gives no line', () => {
  for (const frame of [{ type: 'ping' }, { type: 'token', text: 'hi' }, { type: 'done' },
    { type: 'backend', backend: 'nim' }, { backend: 'claude' }, null, 'backend', 5]) {
    assert.equal(servedByLabel(frame), null, JSON.stringify(frame))
  }
})
