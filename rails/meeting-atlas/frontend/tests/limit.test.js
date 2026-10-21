// Search's bounded fan-out (limit.ts), under plain `node --test`. It used to fetch every
// meeting's transcript at once: 250 meetings, 250 requests in flight, each a full transcript.
import { test } from 'node:test'
import assert from 'node:assert/strict'

const { mapLimit, SEARCH_CONCURRENCY } = await import('../src/limit.ts')

/** A fetch stand-in that records how many calls are in flight at the worst moment. */
function tracked() {
  let inFlight = 0
  const t = { peak: 0, calls: 0 }
  t.fn = async (x) => {
    t.calls++
    inFlight++
    t.peak = Math.max(t.peak, inFlight)
    await new Promise((r) => setTimeout(r, 1 + (x % 3)))
    inFlight--
    return x * 10
  }
  return t
}

test('no more than the limit are ever in flight, and every item is fetched', async () => {
  const t = tracked()
  const items = Array.from({ length: 250 }, (_, i) => i)
  const out = await mapLimit(items, SEARCH_CONCURRENCY, t.fn)
  assert.equal(t.calls, 250)
  assert.ok(t.peak <= SEARCH_CONCURRENCY, `peak ${t.peak} in flight`)
  assert.equal(t.peak, SEARCH_CONCURRENCY, 'and the limit is actually used, not one at a time')
  assert.deepEqual(out, items.map((x) => x * 10), 'results come back in input order')
})

test('fewer items than the limit run all at once; none at all is fine', async () => {
  const t = tracked()
  assert.deepEqual(await mapLimit([1, 2], 6, t.fn), [10, 20])
  assert.equal(t.peak, 2)
  assert.deepEqual(await mapLimit([], 6, t.fn), [])
})

test('Search keeps six fetches in flight', () => {
  assert.equal(SEARCH_CONCURRENCY, 6)
})
