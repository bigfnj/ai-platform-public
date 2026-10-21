// What the search box says about the AI (semantic) index. Pure, so node can test it
// (rails/recipe-book/frontend/tests/recipe-book.test.js).
//
// /api/search/status reports `stale` (the index was built with a different embedding model, so
// search answers by keyword until an admin rebuilds) and `last_error` (the last background
// rebuild failed). Neither was shown anywhere: the ✨ AI chip read "on" while every query was
// being matched by keyword.

export interface SearchStatus {
  built: boolean
  count: number
  model: string
  stale?: string
  last_error?: string
}

/** One short line for under the search box, or null when the AI index is healthy. */
export function searchNotice(s: SearchStatus | null): string | null {
  if (!s) return null
  const failed = s.last_error ? ` The last rebuild failed: ${s.last_error}.` : ''
  if (s.stale) return `AI search is matching by keyword for now: ${s.stale}.${failed} An admin rebuild brings it back.`
  if (!s.built) return `AI search has no index yet, so searches match by keyword.${failed}`
  if (s.last_error) return `The last AI-search rebuild failed (${s.last_error}); searches use the previous index.`
  return null
}
