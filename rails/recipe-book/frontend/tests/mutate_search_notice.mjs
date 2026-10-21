// Mutation harness for search-notice.test.js. NOT a test file: it rewrites src/search_notice.ts.
//
//     node rails/recipe-book/frontend/tests/mutate_search_notice.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..', '..')
const SRC = join(HERE, '..', 'src', 'search_notice.ts')

runMutations({
  repo: REPO,
  suite: join(HERE, 'search-notice.test.js'),
  mutations: [
    {
      label: 'stale is ignored',
      file: SRC,
      old: '  if (s.stale) return',
      new: '  if (false && s.stale) return',
      expect: 'a stale index says search is matching by keyword, and why',
    },
    {
      label: 'a failed rebuild on a good index is ignored',
      file: SRC,
      old: '  if (s.last_error) return `The last AI-search rebuild failed',
      new: '  if (false) return `The last AI-search rebuild failed',
      expect: 'a failed rebuild is named, with the reason, whether or not the index is stale',
    },
    {
      label: 'the failure is dropped from the stale note',
      file: SRC,
      old: "  const failed = s.last_error ? ` The last rebuild failed: ${s.last_error}.` : ''\n",
      new: "  const failed = ''\n",
      expect: 'a failed rebuild is named, with the reason, whether or not the index is stale',
    },
    {
      label: 'a healthy index gets a note',
      file: SRC,
      old: '  return null\n}',
      new: "  return 'AI search is on.'\n}",
      expect: 'a healthy index says nothing',
    },
    {
      label: 'no index is not mentioned',
      file: SRC,
      old: '  if (!s.built) return',
      new: '  if (false) return',
      expect: 'no index at all is said too',
    },
  ],
})
