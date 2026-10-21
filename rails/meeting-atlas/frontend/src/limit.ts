// A bounded fan-out, for Search. It fetched every meeting's transcript with one request per
// meeting and no cap: at 250 meetings, 250 requests at once, each carrying a full transcript,
// each of which also took the backend's build lock to check for a mount change. The browser's
// per-host limit (6 over HTTP/1.1) hid some of that locally; over HTTP/2 through Cloudflare
// there is no such limit.
//
// No runtime imports, so `node --test` loads it directly (tests/limit.test.js).

/** How many transcript fetches Search keeps in flight at once. */
export const SEARCH_CONCURRENCY = 6

/** `fn` over `items` with at most `limit` calls in flight; results in input order. `fn` is
 *  expected to settle (the caller maps its own failures), and a rejection rejects the whole. */
export async function mapLimit<T, R>(items: readonly T[], limit: number,
                                     fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  let next = 0
  const lanes = Math.max(1, Math.min(Math.floor(limit) || 1, items.length))
  const worker = async () => {
    while (next < items.length) {
      const i = next++
      out[i] = await fn(items[i])
    }
  }
  await Promise.all(Array.from({ length: lanes }, worker))
  return out
}
