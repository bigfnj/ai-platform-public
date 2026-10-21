// Every place a frontend turns text into HTML goes through the sanitiser.
//
//   node web/tests/markdown-sinks.test.js
//
// Plain node over the SOURCE, like the other web-core suites: there is no DOM here, and
// DOMPurify without one returns its input unchanged, so whether the sanitiser WORKS is proven in
// a real browser against the built bundles (web/tests/browser/railcheck.py xss). What this file
// holds is the part a browser check cannot: that no sink anywhere in the 19 frontends skips it.
// The audit found five, in three rails, with nothing between `marked.parse` and
// `dangerouslySetInnerHTML`; one of them restated text quoted from an uploaded document, so the
// upload could run script in the session of whoever opened it.
//
// The checks assert CONDITIONS, not that a statement exists: which expression each sink is fed,
// which files import marked at all, and the order of the sanitiser's own branches. Mutation-
// tested by web/tests/mutate_markdown_sinks.mjs, whose cases live per rail in
// web/tests/mutations/markdown-sinks/.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const rel = (p) => relative(REPO, p).split(sep).join('/')

// web/ ships in the public snapshot, which withholds finance, bouquet and edu-suite
// (tools/publish.json) and withholds tools/publish.py too: that file's absence is how a test knows
// it is running there, as services/broker/tests/test_role_map.py does. In a published tree a
// withheld rail is skipped and named on every run; in this repo a missing one is still a crash.
const PUBLISHED = !existsSync(join(REPO, 'tools', 'publish.py'))
const present = (p) => !PUBLISHED || existsSync(p)
const ABSENT = ['finance', 'bouquet', 'edu-suite'].filter((r) => !present(join(REPO, 'rails', r)))

// Every frontend source root. Derived from the tree rather than listed, so a new rail is in
// scope the day it lands.
function frontendRoots() {
  const edu = join(REPO, 'rails', 'edu-suite', 'apps', 'dashboard', 'frontend', 'src')
  const roots = [join(REPO, 'web', 'src'), join(REPO, 'apps', 'platform', 'frontend', 'src'),
    ...(present(edu) ? [edu] : [])]
  for (const r of readdirSync(join(REPO, 'rails'))) {
    for (const sub of ['src', 'mobile']) {
      const d = join(REPO, 'rails', r, 'frontend', sub)
      if (existsSync(d)) roots.push(d)
    }
  }
  return roots
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name.startsWith('dist-')) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(tsx?|jsx?|mjs)$/.test(name)) out.push(p)
  }
  return out
}

const ROOTS = frontendRoots()
const FILES = ROOTS.flatMap((r) => walk(r))
const read = (p) => readFileSync(p, 'utf8').replace(/\r\n/g, '\n')
// Comments name the sinks too (this one does), so the scans read code only. `//` counts as a
// comment after whitespace or at line start, which leaves "http://..." inside strings alone.
const code = (p) => read(p).replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
const SANITISERS = ['recipe-book', 'finance', 'bouquet']
  .map((r) => join(REPO, 'rails', r, 'frontend', 'src', 'lib', 'markdown.ts')).filter(present)
// The audit's sinks, one component per rail.
const EXPECTED_SINKS = [['bouquet', 'components/ui.tsx'], ['finance', 'components/Markdown.tsx'],
  ['recipe-book', 'components/RecipeModal.tsx']]
  .map(([r, f]) => `rails/${r}/frontend/src/${f}`).filter((p) => present(join(REPO, p)))

// A sink fed by something other than a direct markdownToSafeHtml(...) call is allowed only
// with a rule that proves where the value comes from. recipe-book keeps the reply in state
// (it is rendered after an await), so every `html:` it ever stores must be "" or the
// sanitiser's output.
const INDIRECT = {
  'rails/recipe-book/frontend/src/components/RecipeModal.tsx': {
    expr: 'assist.html',
    proof: (src) => {
      const stores = [...src.matchAll(/setAssist\(\{[^)]*?\bhtml:\s*([^,}\n]+)/g)].map((m) => m[1].trim())
      const bad = stores.filter((v) => v !== '""' && !v.startsWith('md.markdownToSafeHtml('))
      return { stores, bad }
    },
  },
}

if (ABSENT.length) {
  test('rails this published tree does not carry', {
    skip: `published snapshot: ${ABSENT.join(', ')} withheld, so their sinks are not scanned here` }, () => {})
}

test('the scan reaches every frontend, including the three that render model markdown', () => {
  // The floor is this repo's (the published tree carries about 100 files); every root has to
  // yield files in either.
  if (!PUBLISHED) assert.ok(FILES.length > 150, `only ${FILES.length} frontend files found; the walk is broken`)
  const empty = ROOTS.filter((r) => !FILES.some((f) => f.startsWith(r + sep))).map(rel)
  assert.deepEqual(empty, [], 'a frontend root the walk found nothing in')
  for (const r of ['recipe-book', 'finance', 'bouquet', 'smb-partner-enablement'].filter((x) => !ABSENT.includes(x))) {
    assert.ok(FILES.some((f) => rel(f).startsWith(`rails/${r}/frontend/`)), `${r} not scanned`)
  }
})

test('every dangerouslySetInnerHTML is fed by the sanitiser', () => {
  const sinks = []
  const problems = []
  for (const f of FILES) {
    const src = code(f)
    if (!src.includes('dangerouslySetInnerHTML')) continue
    const uses = [...src.matchAll(/dangerouslySetInnerHTML/g)].length
    const fed = [...src.matchAll(/dangerouslySetInnerHTML=\{\{\s*__html:\s*([\s\S]+?)\s*\}\}\s*\/?>/g)]
      .map((m) => m[1].trim())
    if (fed.length !== uses) {
      problems.push(`${rel(f)}: ${uses} dangerouslySetInnerHTML, ${fed.length} in the {{ __html: x }} form this test can read`)
      continue
    }
    for (const expr of fed) {
      sinks.push(`${rel(f)}: ${expr}`)
      if (/^markdownToSafeHtml\([^()]*\)$/.test(expr)) continue
      const rule = INDIRECT[rel(f)]
      if (rule && rule.expr === expr) {
        const { stores, bad } = rule.proof(src)
        if (!stores.length) problems.push(`${rel(f)}: ${expr} has no store this test can see`)
        for (const b of bad) problems.push(`${rel(f)}: ${expr} is stored from ${b}, not the sanitiser`)
        continue
      }
      problems.push(`${rel(f)}: __html is ${expr}, not markdownToSafeHtml(...)`)
    }
  }
  assert.deepEqual(problems, [])
  // Witness: the audit's five sinks now sit behind one component per rail. If a refactor moves
  // them this says so, rather than the test passing over nothing; a NEW sanitised sink belongs
  // in this list too, so adding one is a reviewed change.
  assert.deepEqual(sinks.map((s) => s.split(':')[0]).sort(), EXPECTED_SINKS)
})

test('only the sanitiser modules import marked', () => {
  const importers = FILES.filter((f) => /from\s+["']marked["']|import\(\s*["']marked["']\s*\)|require\(\s*["']marked["']\s*\)/.test(code(f)))
    .map(rel).sort()
  assert.deepEqual(importers, SANITISERS.map(rel).sort())
})

test('no frontend writes HTML by any other route', () => {
  const hits = []
  for (const f of FILES) {
    const src = code(f)
    for (const pat of [/\.innerHTML\s*[+]?=/, /\.outerHTML\s*=/, /insertAdjacentHTML\s*\(/, /document\.write(ln)?\s*\(/, /createContextualFragment\s*\(/]) {
      if (pat.test(src)) hits.push(`${rel(f)}: ${pat}`)
    }
  }
  assert.deepEqual(hits, [])
})

test('the three sanitiser modules are byte-identical', { skip: SANITISERS.length < 2 &&
  'published snapshot: one sanitiser module in this tree, nothing to compare it with' }, () => {
  const [first, ...rest] = SANITISERS.map(read)
  for (const [i, s] of rest.entries()) {
    assert.equal(s, first, `${rel(SANITISERS[i + 1])} differs from ${rel(SANITISERS[0])}`)
  }
})

test('the sanitiser fails closed, then returns marked output only through DOMPurify', () => {
  const src = read(SANITISERS[0])
  const start = src.indexOf('export function markdownToSafeHtml(')
  assert.ok(start >= 0, 'markdownToSafeHtml not found')
  const body = src.slice(start, src.indexOf('\n}\n', start))
  const returns = [...body.matchAll(/\breturn\b([^;]*);/g)].map((m) => m[1].trim())
  assert.equal(returns.length, 2, `expected two returns, found: ${returns.join(' | ')}`)
  // The no-DOM branch is first, and it escapes rather than parsing.
  const guard = body.indexOf('if (!DOMPurify.isSupported) return ')
  const parse = body.indexOf('marked.parse(')
  assert.ok(guard >= 0, 'the isSupported guard is gone')
  assert.ok(parse > guard, 'marked.parse runs before the isSupported guard')
  assert.match(returns[0], /^`<p>\$\{escapeHtml\(text\)\}<\/p>`$/)
  // The parsed HTML leaves only through sanitize(), with the allowlists.
  const raw = /const (\w+) = marked\.parse\(/.exec(body)?.[1]
  assert.ok(raw, 'marked.parse result is not bound to a name')
  assert.match(returns[1], new RegExp(`^DOMPurify\\.sanitize\\(${raw}, \\{ ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false \\}\\)$`))
  assert.equal([...body.matchAll(new RegExp(`\\b${raw}\\b`, 'g'))].length, 2,
    `${raw} is used somewhere other than its sanitize() call`)
})

test('the allowlists hold no element or attribute that can run, load or restyle', () => {
  const src = read(SANITISERS[0])
  const list = (name) => {
    const m = new RegExp(`const ${name} = \\[([\\s\\S]*?)\\];`).exec(src)
    assert.ok(m, `${name} not found`)
    return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
  }
  const tags = list('ALLOWED_TAGS')
  const attrs = list('ALLOWED_ATTR')
  for (const t of ['script', 'img', 'svg', 'math', 'iframe', 'object', 'embed', 'style', 'link',
    'form', 'input', 'button', 'textarea', 'select', 'details', 'video', 'audio', 'source', 'base', 'meta']) {
    assert.ok(!tags.includes(t), `ALLOWED_TAGS lets <${t}> through`)
  }
  for (const a of attrs) {
    assert.ok(!/^on|^style$|^class$|^id$|^src|^srcdoc$|^action$|^formaction$|^xlink/i.test(a),
      `ALLOWED_ATTR lets ${a} through`)
  }
  assert.ok(tags.includes('table') && tags.includes('strong'), 'the allowlist no longer renders markdown')
})
