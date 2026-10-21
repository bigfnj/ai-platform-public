// The loop the node mutation harnesses in web/ share (mutate_poll.mjs and the ones after it).
// NOT a test file. Built on tools/tests/mutation_runner.mjs, which bounds each suite run and
// kills the whole process tree on a hang, and proves each restore byte for byte.
//
// A verdict is FIRED only when the NAMED test is reported failed in TAP (node prints every
// test's name, passing or not, so looking for the name alone could not fail), and only after
// the unmutated baseline reported every named test as `ok`: that is the witness that the suite
// ran the code at all, so a clean N/N means something.
//
// A harness whose cases quote rails keeps each rail's cases in a module of their own,
// web/tests/mutations/<harness>/<rail>.mjs, loaded by railCases() below, so the public snapshot
// withholds a withheld rail's cases with the rail (tools/publish.json) instead of shipping the
// code they quote. `node <harness> --list` names every case and where it lives, and runs nothing.
//
// parseHarnessArgs() below is every node harness's command line, the ones that keep their own
// loop as well: the twin of parse_harness_args in tools/tests/mutation_runner.py.
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { basename, join, relative } from 'node:path'
import { pathToFileURL } from 'node:url'
import { restoreExact, runBoundedSync } from '../../tools/tests/mutation_runner.mjs'

const TIMEOUT_MS = 60_000

const HELP = '--list names every case (and each control, marked) and runs nothing. Without it the ' +
  'harness REWRITES files in this tree, runs its suite, and restores them.'

/**
 * Read a node mutation harness's command line, before it reads, writes or runs anything.
 *
 * `--list` prints `case<TAB>label` for each of `cases` and `control<TAB>label` for each of
 * `controls` (each a { label }), then a count, and exits 0. With `filter` the positional
 * arguments are the harness's label filter (substrings), and `--list` names only what they
 * select. `flags` are the harness's own switches, returned in `.flags`. Anything else exits 2
 * with a usage line: an option the harness does not take, and a positional argument to a
 * harness with no filter. Both used to be ignored, so a mistyped flag ran every mutation.
 * `cases: null` is for a harness that prints its own listing (runMutations): `--list` is
 * validated like the rest and returned in `.listing`. Returns { listing, only, flags }.
 */
export function parseHarnessArgs({ cases = null, controls = [], flags = [], filter = false,
  argv = process.argv.slice(2) } = {}) {
  const prog = basename(process.argv[1] ?? 'harness')
  const usage = `usage: node ${prog} [--list]${flags.map((f) => ` [${f}]`).join('')}` +
    (filter ? ' [LABEL-FILTER ...]' : '')
  const refuse = (why) => {
    console.error(`${prog}: ${why}\n${usage}`)
    process.exit(2)
  }
  let listing = false
  const only = []
  const given = new Set()
  for (const a of argv) {
    if (a === '-h' || a === '--help') {
      console.log(`${usage}\n${HELP}`)
      process.exit(0)
    }
    if (a === '--list') listing = true
    else if (flags.includes(a)) given.add(a)
    else if (a.startsWith('-') && a !== '-') refuse(`unknown option '${a}'`)
    else if (filter) only.push(a)
    else refuse(`takes no label filter, so '${a}' would have been ignored and every case run`)
  }
  if (listing && cases) {
    const keep = (m) => only.length === 0 || only.some((o) => m.label.includes(o))
    const picked = cases.filter(keep)
    const pickedControls = controls.filter(keep)
    for (const m of picked) console.log(`case\t${m.label}`)
    for (const m of pickedControls) console.log(`control\t${m.label}`)
    console.log(`${picked.length} mutations` +
      (controls.length ? `, ${pickedControls.length} controls` : '') + '; listed, nothing run')
    process.exit(0)
  }
  return { listing, only, flags: given }
}

function results(out) {
  const passed = new Set()
  const failed = new Set()
  const skipped = new Set()
  for (const line of out.split(/\r?\n/)) {
    const m = line.match(/^\s*(not ok|ok) \d+ - (.+?)(\s+# SKIP\b.*)?\s*$/)
    if (!m) continue
    ;(m[3] ? skipped : m[1] === 'ok' ? passed : failed).add(m[2])
  }
  return { passed, failed, skipped }
}

const isPublished = (repo) => !existsSync(join(repo, 'tools', 'publish.py'))

/**
 * The cases of `rails` (rail ids) for one harness: `dir`/<rail>.mjs each exports MUTATIONS, with
 * each `file` relative to the repo and inside rails/<rail>/. Returns { mutations, withheld }:
 * the cases with `file` made absolute and `rail` set, and the file names of the modules this
 * tree withholds, each printed as SKIPPED (by file name only: its cases quote the rail's code).
 *
 * A module may be absent only from a published tree, and only where that tree has no
 * rails/<rail> either; anything else is a module lost by mistake. That, a module `rails` does
 * not name, and a case that rewrites another rail's file would each move cases out of every
 * run without a word, so each exits 2, --list included.
 */
export async function railCases({ repo, dir, rails }) {
  const published = isPublished(repo)
  const here = relative(repo, dir).split('\\').join('/')
  const refuse = (why) => {
    console.log(`${here}: ${why}`)
    process.exit(2)
  }
  const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.mjs')) : []
  const stray = files.filter((f) => !rails.includes(f.slice(0, -'.mjs'.length)))
  if (stray.length) refuse(`${stray.join(', ')} names no rail the harness loads, so its cases would never run`)
  const mutations = []
  const withheld = []
  for (const rail of rails) {
    const file = join(dir, `${rail}.mjs`)
    if (!existsSync(file)) {
      if (!published || existsSync(join(repo, 'rails', rail))) refuse(`${rail}.mjs is missing, so ${rail}'s cases cannot run`)
      withheld.push(`${rail}.mjs`)
      console.log(`  SKIPPED  ${here}/${rail}.mjs: its rail is not in this published tree`)
      continue
    }
    const cases = (await import(pathToFileURL(file).href)).MUTATIONS
    const wrong = cases.filter((m) => !m.file.startsWith(`rails/${rail}/`)).map((m) => m.label)
    if (wrong.length) refuse(`${rail}.mjs holds cases that rewrite another rail's files: ${wrong.join('; ')}`)
    mutations.push(...cases.map((m) => ({ ...m, rail, file: join(repo, ...m.file.split('/')) })))
  }
  return { mutations, withheld }
}

/**
 * mutations: [{ label, file, old, new, expect, rail? }]; withheld: what railCases() returned.
 * Exits the process with the verdict. With --list on the command line it prints one line per
 * case (where it lives, tab, label) and exits 0 without running anything; any other argument
 * exits 2 (parseHarnessArgs).
 */
export function runMutations({ repo, suite, mutations: all, withheld = [] }) {
  if (parseHarnessArgs().listing) {
    for (const m of all) console.log(`${m.rail ?? 'core'}\t${m.label}`)
    console.log(`${all.length} mutations` + (withheld.length ? `; ${withheld.length} modules withheld` : ''))
    process.exit(0)
  }
  // web/ ships in the public snapshot, which withholds several rails and tools/publish.py itself
  // (its absence is the tell). There a mutant whose file is not in the tree, or whose test the
  // suite itself skips there, is skipped and NAMED. In this repo every file must exist (a missing
  // one crashes the read below) and a skipped expected test is a witness failure.
  const published = isPublished(repo)
  let mutations = published ? all.filter((m) => existsSync(m.file)) : all
  for (const m of all.filter((x) => !mutations.includes(x))) {
    console.log(`  SKIPPED  ${m.label}: ${relative(repo, m.file)} is not in this published tree`)
  }
  const run = () => {
    const p = runBoundedSync(process.execPath, ['--test-reporter=tap', suite], { cwd: repo, timeoutMs: TIMEOUT_MS })
    return { code: p.status, out: p.out }
  }
  const originals = new Map()
  for (const f of new Set(mutations.map((m) => m.file))) originals.set(f, readFileSync(f))

  const base = run()
  const baseR = results(base.out)
  if (base.code !== 0 || baseR.failed.size > 0) {
    console.log('BASELINE IS NOT GREEN -- fix that before trusting any mutation result.')
    console.log(base.out.slice(-1500))
    process.exit(2)
  }
  if (published) {
    for (const m of mutations.filter((x) => baseR.skipped.has(x.expect))) {
      console.log(`  SKIPPED  ${m.label}: "${m.expect}" is skipped in this published tree`)
    }
    mutations = mutations.filter((x) => !baseR.skipped.has(x.expect))
  }
  const missing = [...new Set(mutations.map((m) => m.expect))].filter((e) => !baseR.passed.has(e))
  if (missing.length) {
    console.log('WITNESS FAILED: the baseline never reported these tests as passing:')
    for (const e of missing) console.log(`  - ${e}`)
    process.exit(2)
  }
  console.log(`baseline: green, ${baseR.passed.size} tests reported ok\n`)

  const verdicts = []
  for (const m of mutations) {
    const original = originals.get(m.file).toString('utf8')
    const nl = original.includes('\r\n') ? '\r\n' : '\n'
    const text = original.replace(/\r\n/g, '\n')
    const n = text.split(m.old).length - 1
    if (n !== 1) {
      console.log(`  ERROR    ${m.label}: pattern found ${n} times, expected 1`)
      verdicts.push('PATTERN')
      continue
    }
    try {
      writeFileSync(m.file, text.split(m.old).join(m.new).replace(/\n/g, nl), 'utf8')
      const { code, out } = run()
      const { failed } = results(out)
      const named = failed.has(m.expect)
      const verdict = code === 'TIMEOUT' ? 'TIMEOUT' : code !== 0 && named ? 'FIRED' : 'SURVIVED'
      verdicts.push(verdict)
      console.log(`  ${verdict.padEnd(9)}${m.label}`)
      console.log(`           expected red: "${m.expect}" -> ${named ? 'red' : 'NOT red'}`)
      console.log(`           all red: ${[...failed].join(' | ') || '(none)'}`)
      if (verdict !== 'FIRED') console.log(out.slice(-1200))
    } finally {
      restoreExact(m.file, originals.get(m.file))
    }
  }
  const after = run()
  const restored = [...originals].every(([f, buf]) => readFileSync(f).equals(buf))
  console.log(`\nrestored tree: ${after.code === 0 && restored ? 'green' : 'STILL RED OR CHANGED -- TREE IS DIRTY'}`)
  const bad = verdicts.filter((v) => v !== 'FIRED').length
  const skipped = all.length - mutations.length
  console.log(`${verdicts.length - bad}/${verdicts.length} mutations fired the right test` +
    (skipped ? `; ${skipped} SKIPPED in this published tree` : '') +
    (withheld.length ? `; skipped with their rails: ${withheld.join(', ')}` : ''))
  process.exit(bad === 0 && after.code === 0 && restored ? 0 : 1)
}
