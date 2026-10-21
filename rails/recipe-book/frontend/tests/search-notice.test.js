// What recipe-book's search box says about the AI index (src/search_notice.ts).
//
//   node rails/recipe-book/frontend/tests/search-notice.test.js
//
// The browser half (the note renders under the search box) is web/tests/browser/railcheck.py
// rb-search-notice. Mutation-tested by mutate_search_notice.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { searchNotice } from '../src/search_notice.ts'

const ok = { built: true, count: 841, model: 'qwen3-embedding:0.6b' }

test('a healthy index says nothing', () => {
  assert.equal(searchNotice(ok), null)
  assert.equal(searchNotice(null), null)
})

test('a stale index says search is matching by keyword, and why', () => {
  const n = searchNotice({ ...ok, stale: 'built with bge-m3; @embed is now qwen3-embedding:0.6b' })
  assert.match(n, /matching by keyword/)
  assert.match(n, /built with bge-m3/)
})

test('a failed rebuild is named, with the reason, whether or not the index is stale', () => {
  assert.match(searchNotice({ ...ok, last_error: 'BrokerError: 502' }), /rebuild failed \(BrokerError: 502\)/)
  const both = searchNotice({ ...ok, stale: 'model changed', last_error: 'BrokerError: 502' })
  assert.match(both, /matching by keyword/)
  assert.match(both, /The last rebuild failed: BrokerError: 502/)
})

test('no index at all is said too', () => {
  assert.match(searchNotice({ built: false, count: 0, model: 'm' }), /no index yet/)
})
