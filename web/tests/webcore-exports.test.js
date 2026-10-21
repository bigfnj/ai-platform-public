// Every value web-core's barrel exports has an importer somewhere outside web-core.
//
//   node web/tests/webcore-exports.test.js
//
// The audits found the barrel carrying exports no rail or shell file ever imported (ThemeToggle,
// ThemeMenu, playWav, record, stopSpeaking, the device setters, seven design primitives each
// rail had replaced with its own), and every one of them read as API somebody depended on. A
// value export now needs an importer, or a line in KEEP saying why it stays. Types are not
// checked: a type costs nothing at runtime and documents a shape. Mutation-tested by
// web/tests/mutate_webcore_exports.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
// web/ ships in the public snapshot, which withholds some rails and tools/publish.py itself (the
// file's absence is the tell, as in services/broker/tests/test_role_map.py). An export whose only
// importers are withheld rails is not dead, so "imported by nothing" cannot be decided there.
const PUBLISHED = !existsSync(join(REPO, 'tools', 'publish.py'))

// startPoll was here, "for a poller that lives outside a component", until 2026-10-14: no such
// poller ever imported it, so the reason was a forecast, and it left the barrel (usePoll stays).
const KEEP = {
  MODEL_STATE_TEXT: 'the shared four-state chip text; gemini-cx still ships its own copy',
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name.startsWith('dist-')) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p)
  }
  return out
}

function barrelValues() {
  const src = readFileSync(join(REPO, 'web', 'src', 'index.ts'), 'utf8')
  const out = []
  for (const m of src.matchAll(/^export\s+\{([^}]*)\}\s+from/gm)) {
    for (const n of m[1].split(',')) {
      const name = n.trim().split(/\s+as\s+/).pop()
      if (name) out.push(name)
    }
  }
  return out
}

function imported() {
  const used = new Set()
  const roots = [join(REPO, 'apps'), join(REPO, 'rails')]
  for (const root of roots) {
    for (const f of walk(root)) {
      const src = readFileSync(f, 'utf8')
      for (const m of src.matchAll(/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+['"]@web-core['"]/g)) {
        for (const n of m[1].split(',')) {
          const name = n.trim().replace(/^type\s+/, '').split(/\s+as\s+/)[0].trim()
          if (name) used.add(name)
        }
      }
    }
  }
  return used
}

test('the scan sees the barrel and the importers', () => {
  const values = barrelValues()
  const used = imported()
  assert.ok(values.includes('RailHeader') && values.includes('usePoll'), `barrel parse: ${values}`)
  assert.ok(used.has('RailHeader') && used.has('ModelChips'), 'importer scan found nothing')
})

test('every value export has an importer outside web-core, or a reason to stay', { skip: PUBLISHED &&
  'published snapshot: some importers are withheld rails, so this runs in the private repo' }, () => {
  const used = imported()
  const dead = barrelValues().filter((n) => !used.has(n) && !(n in KEEP))
  assert.deepEqual(dead, [], `exported and imported by nothing: ${dead.join(', ')}`)
})

test('every KEEP entry is still exported and still unimported', () => {
  const values = new Set(barrelValues())
  const used = imported()
  const stale = Object.keys(KEEP).filter((n) => !values.has(n) || used.has(n))
  assert.deepEqual(stale, [], `KEEP entries no longer needed: ${stale.join(', ')}`)
})
