// The node mutation harnesses' twin of mutation_runner.py: run a suite under a hard limit that
// kills the whole process TREE, and restore a mutated file byte for byte, PROVEN.
//
// Why a watchdog process. The harnesses are synchronous scripts built on spawnSync, and
// spawnSync's own `timeout` kills the direct child only. `node --test` runs each test file in a
// child of its own, so a hung suite left that grandchild running after the "timeout", and on
// Windows a grandchild holding the stdout pipe keeps spawnSync waiting as well. spawnSync
// cannot kill a tree, so runBoundedSync spawnSyncs THIS FILE as a watchdog, which spawns the
// suite asynchronously, relays its output, and on timeout kills the tree (taskkill /T on
// Windows, the process group elsewhere), prints a TIMEOUT marker and exits 124.
//
// Tested from Python by tools/tests/test_mutation_runner_js.py; mutation-tested by
// tools/tests/mutate_mutation_runner.py.

import { spawn, spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SELF = fileURLToPath(import.meta.url)
export const TIMEOUT_EXIT = 124
export const TIMEOUT_MARKER = '[TIMEOUT: the suite was still running after'
export const DEFAULT_TIMEOUT_MS = Number(process.env.MUTATION_SUITE_TIMEOUT ?? 300) * 1000
// After the kill, how long the pipes may stay open before the watchdog gives up on them.
const REAP_MS = 15_000

function killTree(pid) {
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { stdio: 'ignore' })
  } else {
    try { process.kill(-pid, 'SIGKILL') } catch { /* already gone */ }
  }
}

// Run `cmd args` to completion or for at most `timeoutMs`. Returns
// { status, out, timedOut }, where status is the exit code, or 'TIMEOUT'.
export function runBoundedSync(cmd, args, { cwd, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const p = spawnSync(process.execPath, [SELF, String(timeoutMs), cmd, ...args], {
    encoding: 'utf8', cwd, maxBuffer: 256 * 1024 * 1024,
    // A backstop only, for a watchdog that itself could not finish.
    timeout: timeoutMs + REAP_MS + 30_000,
  })
  const out = (p.stdout || '') + (p.stderr || '')
  const timedOut = (p.status === TIMEOUT_EXIT && out.includes(TIMEOUT_MARKER)) ||
    Boolean(p.error && p.error.code === 'ETIMEDOUT')
  return { status: timedOut ? 'TIMEOUT' : p.status, out, timedOut }
}

// Write `original` (a Buffer) back to `file` and PROVE it round-trips; throws "TREE IS DIRTY"
// otherwise. `fs` is injectable so a test can make the round trip fail.
export function restoreExact(file, original, fs = { readFileSync, writeFileSync }) {
  fs.writeFileSync(file, original)
  if (!Buffer.from(fs.readFileSync(file)).equals(original)) {
    throw new Error(`${file} was not restored byte for byte -- TREE IS DIRTY`)
  }
}

function sameFile(a, b) {
  const x = resolve(a)
  const y = resolve(b)
  return process.platform === 'win32' ? x.toLowerCase() === y.toLowerCase() : x === y
}

// --- the watchdog, when this file is run as a script -------------------------------------------
if (process.argv[1] && sameFile(process.argv[1], SELF)) {
  const [ms, cmd, ...args] = process.argv.slice(2)
  const limit = Number(ms)
  const child = spawn(cmd, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    // Its own process group, so process.kill(-pid) takes the grandchildren too. Windows needs
    // no equivalent: taskkill /T walks the tree by parent PID.
    detached: process.platform !== 'win32',
  })
  child.stdout.pipe(process.stdout)
  child.stderr.pipe(process.stderr)
  let timedOut = false
  const finish = (code) => {
    if (timedOut) {
      process.stderr.write(`\n${TIMEOUT_MARKER} ${limit / 1000} s and was killed]\n`)
      process.exitCode = TIMEOUT_EXIT
    } else {
      process.exitCode = code ?? 1
    }
  }
  const timer = setTimeout(() => {
    timedOut = true
    killTree(child.pid)
    // Something outside the tree may still hold the pipes; do not wait on it for ever.
    setTimeout(() => { finish(null); process.exit(TIMEOUT_EXIT) }, REAP_MS).unref()
  }, limit)
  child.on('error', (err) => { clearTimeout(timer); process.stderr.write(`${err}\n`); finish(1) })
  child.on('close', (code) => { clearTimeout(timer); finish(code) })
}
