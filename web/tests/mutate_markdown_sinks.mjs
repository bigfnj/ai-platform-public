// Mutation harness for markdown-sinks.test.js. NOT a test file: it rewrites the source.
//
//     node web/tests/mutate_markdown_sinks.mjs
//     node web/tests/mutate_markdown_sinks.mjs --list     # name every case; run nothing
//
// Each mutation puts back one way the audit's XSS could return: a sink fed raw marked output,
// a sink fed the raw text, an error message interpolated into markup, a rail importing marked
// itself, a sanitiser that returns its input, a guard that runs after the parse, an allowlist
// that lets a loader or a style through, and an HTML write that is not dangerouslySetInnerHTML
// at all. The loop is web/tests/mutation_suite.mjs: FIRED only when the NAMED test is reported
// failed in TAP, after the baseline reported each named test `ok` (the witness that it ran).
//
// Every case quotes a rail's frontend, so each rail's cases live in
// web/tests/mutations/markdown-sinks/<rail>.mjs and go wherever that rail goes: the public
// snapshot withholds the withheld rails' modules (tools/publish.json), and there this harness
// names each withheld module by its file and runs the rest. RAILS names every module.
//
// The browser half (does the built bundle actually sanitise?) is web/tests/browser/
// mutate_railcheck.py, which has to rebuild a rail per mutation.
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { railCases, runMutations } from './mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const SUITE = join(REPO, 'web', 'tests', 'markdown-sinks.test.js')
const RAILS = ['recipe-book', 'finance', 'bouquet']

const { mutations, withheld } = await railCases({ repo: REPO, dir: join(HERE, 'mutations', 'markdown-sinks'), rails: RAILS })
runMutations({ repo: REPO, suite: SUITE, mutations, withheld })
