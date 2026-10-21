// The Embedding Lab's results merge, kept free of React and of imports so plain node can test it
// (frontend/tests/lab-helpers.test.js).

/** Fill the table's pre-seeded placeholder rows with the run's results, in placeholder order,
 *  and APPEND every result that has no placeholder.
 *
 *  The append is the fix. The merge used to be a map over the placeholders, so a row the
 *  backend produced and the plan did not predict was dropped on the floor. The one that matters
 *  is the reranker's failure row (`<reranker>|+rerank`, carrying the real reason it could not
 *  load): it was silently discarded, the rerank callout found no pair and rendered nothing, and
 *  the run arrives as `done`, so no error was ever shown either (recorded 2026-09-11). */
export function mergeResults<T extends { id: string }>(placeholders: T[], results: T[]): T[] {
  const byId = new Map(results.map((r) => [r.id, r] as const))
  const merged = placeholders.map((p) => byId.get(p.id) ?? p)
  const planned = new Set(placeholders.map((p) => p.id))
  return [...merged, ...results.filter((r) => !planned.has(r.id))]
}
